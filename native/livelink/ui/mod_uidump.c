/* The UI dump probe (switch ui_dump, off; 2026-11-09, unverified in game; docs/ui-editor.md "Comparing with the
   game"): on Ctrl+Shift+U, and once a scene named in ui_dump_scenes has been open for ui_dump_delay_ms, every open
   scene's live widget tree is walked on the UI frame, under the scene client's own lock, and written as one JSON
   file per scene into log/ui-dump/. Read only: the walk reads the run-time widgets, calls the engine's own world
   matrix and built-in property readers (what a binding's read runs) and sets nothing. The pure walk and writer are
   uidump_logic.c. It also comes up in a dormant start. */
#include "hlib.h"
#include "targets.h"
#include "uidump_logic.h"
#include "uidump_unnamed.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* the variant types the property readers write (CustomPropertyType) */
enum { V_BOOL = 0, V_INT = 1, V_UINT = 2, V_FLOAT = 3, V_STRING = 4, V_VEC2 = 5, V_VEC3 = 6, V_OBJECT = 8 };
enum { V_COLOR = 18, V_EMPTY = 20, V_EMPTY2 = 24, V_INT64 = 25, V_UINT64 = 26 };
enum { CLASS_DEPTH = 8, CLASS_PROPS = 128, STRING_MAX = 4096, SCAN_MS = 200 };

typedef struct {
    CRITICAL_SECTION *cs;
    const void *prop_vt0; /* slot 0 of the properties' accessors (events and actions have another) */
    int getters, faults;
} Ctx;

static const uint32_t g_unnamed_fields[] = UD_UNNAMED_FIELDS, g_unnamed_types[] = UD_UNNAMED_TYPES;
static int g_unnamed = 1;
static int g_key = 'U', g_getters = 1, g_delay_ms = 1500, g_max = 20000;
static char g_scenes[512];
static void *g_container_update, *g_group_update;
static volatile LONG g_on, g_request;
static ULONGLONG g_due[T_UI_ID_LIMIT];
static unsigned char g_was_active[T_UI_ID_LIMIT];
static int n_written, n_fail;
static char (*g_files)[160]; /* the live link's request: the names of the files this dump wrote */
static int g_files_cap, g_files_n;

static int readable(const void *p) { return (uintptr_t)p >= 0x10000u && !((uintptr_t)p & 7); }

/* a Phoenix string block: length, then the text */
static int string_of(const char *h, char *out, int cap) {
    if (!readable(h)) return 0;
    unsigned len = *(const unsigned *)h;
    if (!len || len > STRING_MAX) return 0;
    int n = (int)len < cap - 1 ? (int)len : cap - 1;
    memcpy(out, h + T_PHX_STRING_TEXT, (size_t)n);
    out[n] = 0;
    return 1;
}

/* a type hash getter is `mov dword [rdx], imm32`: the hash is read from its code, nothing is called */
static uint32_t type_hash_of(const char *file) {
    void **vt = *(void ***)file;
    const unsigned char *fn = hlib_in_image(vt) ? (const unsigned char *)vt[0] : NULL;
    if (!fn || !hlib_in_image(fn) || fn[0] != T_TYPEHASH_OP || fn[1] != T_TYPEHASH_MODRM) return 0;
    return *(const uint32_t *)(fn + 2);
}

static int keeps_children(const char *file) {
    void **vt = readable(file) ? *(void ***)file : NULL;
    if (!vt || !hlib_in_image(vt)) return 0;
    return vt[T_VT_FILE_UPDATE] == g_container_update || vt[T_VT_FILE_UPDATE] == g_group_update;
}

static int children(void *ctx, const void *w, const void **out, int cap, int *cut) {
    const char *rt = (const char *)w;
    int got = 0;
    (void)ctx;
    __try {
        if (!keeps_children(*(char *const *)(rt + T_OFF_RT_FILE))) return 0;
        unsigned n = *(const unsigned *)(rt + T_OFF_RT_NCHILD);
        void *const *kids = *(void *const *const *)(rt + T_OFF_RT_CHILDREN);
        if (!readable(kids)) return 0;
        if (n > (unsigned)cap) *cut = 1, n = (unsigned)cap; /* the first cap are walked, the dump says it is cut */
        for (unsigned i = 0; i < n && got < cap; i++) {
            void **vt = readable(kids[i]) ? *(void ***)kids[i] : NULL;
            if (!vt || !hlib_in_image(vt)) continue;
            void *child = ((void *(*)(void *))vt[T_VT_RT_CHILD])(kids[i]);
            if (readable(child)) out[got++] = child;
        }
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "ui_dump: a widget's children could not be read (after %d)", got);
        *cut = 1;
    }
    return got;
}

static void take_variant(UdValue *v, const unsigned char *var) {
    v->type = var[T_OFF_VARIANT_TYPE];
    switch (v->type) {
    case V_BOOL: v->kind = UD_BOOL, v->i = var[0] != 0; break;
    case V_INT: v->kind = UD_INT, v->i = *(const int *)var; break;
    case V_UINT: v->kind = UD_INT, v->i = *(const unsigned *)var; break;
    case V_INT64: case V_UINT64: v->kind = UD_INT, v->i = *(const long long *)var; break;
    case V_FLOAT: v->kind = UD_FLOAT, v->v[0] = *(const float *)var; break;
    case V_VEC2: case V_VEC3:
        v->kind = UD_VEC, v->n = v->type == V_VEC2 ? 2 : 3;
        for (int i = 0; i < v->n; i++) v->v[i] = ((const float *)var)[i];
        break;
    case V_STRING:
        v->kind = UD_STRING;
        if (!string_of(*(const char *const *)var, v->text, sizeof(v->text))) v->text[0] = 0;
        break;
    case V_COLOR: v->kind = UD_COLOR, v->v[0] = var[4], v->v[1] = var[5], v->v[2] = var[6]; break;
    case V_OBJECT: v->kind = UD_OBJECT, v->i = *(const void *const *)var != NULL; break;
    case V_EMPTY: case V_EMPTY2: v->kind = UD_NONE; break;
    default: v->kind = UD_RAW, memcpy(v->raw, var, sizeof(v->raw));
    }
}

/* the properties' accessors share slot 0; the class list also holds events and actions (another slot 0) */
static const void *property_marker(const char *cls) {
    const void *seen[4] = {0};
    int count[4] = {0};
    void *const *list = *(void *const *const *)(cls + T_OFF_CLASS_LIST);
    unsigned n = *(const unsigned *)(cls + T_OFF_CLASS_COUNT);
    for (unsigned i = 0; readable(list) && i < n && i < CLASS_PROPS; i++) {
        void **vt = readable(list[i]) ? *(void ***)list[i] : NULL;
        for (int k = 0; vt && k < 4; k++)
            if (!seen[k] || seen[k] == vt[0]) {
                seen[k] = vt[0], count[k]++;
                break;
            }
    }
    int best = 0;
    for (int k = 1; k < 4; k++) best = count[k] > count[best] ? k : best;
    return seen[best];
}

/* One built-in property through its accessor; a fault is contained and the client's lock given back to its depth. */
static int read_property(Ctx *c, const void *rec, void *props, UdValue *v) {
    __declspec(align(16)) unsigned char var[T_PHX_VARIANT_SIZE];
    int status = -1, ok = 0;
    LONG depth = c->cs->RecursionCount;
    void (*free_variant)(void *) = (void (*)(void *))hlib_target(T_PHX_VARIANT_FREE);
    void **vt = *(void ***)rec;
    memset(var, 0, sizeof(var));
    var[T_OFF_VARIANT_TYPE] = V_EMPTY;
    __try {
        ((int *(*)(const void *, int *, void *, void *, char))vt[T_VT_PROP_READ])(rec, &status, props, var, 1);
        if (status == 0) take_variant(v, var), ok = 1;
        free_variant(var);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        while (c->cs->RecursionCount > depth && c->cs->OwningThread == (HANDLE)(uintptr_t)GetCurrentThreadId())
            LeaveCriticalSection(c->cs);
        c->faults++, c->getters = 0;
        hlib_logf("ui_dump: the reader of %s faulted; the built-in properties are not read for the rest of this dump",
                   (const char *)*(void *const *)((const char *)rec + T_OFF_PROP_NAME));
    }
    return ok;
}

/* the class chain's built-in properties, as the engine's readers give them */
static void read_props(Ctx *c, char *rt, UdNode *n) {
    void *props = *(void **)(rt + T_OFF_RT_OBSERVABLE);
    void **pvt = readable(props) ? *(void ***)props : NULL;
    if (!pvt || !hlib_in_image(pvt)) return;
    const char *cls = ((const char *(*)(void *))pvt[T_VT_PROPS_CLASS])(props);
    if (!readable(cls) || ((void *(*)(void *))pvt[T_VT_PROPS_WIDGET])(props) != rt) return;
    const char *cname = *(const char *const *)(cls + T_OFF_CLASS_NAME);
    snprintf(n->cls, sizeof(n->cls), "%s", hlib_in_image(cname) ? cname : "?");
    const char *k = cls;
    for (int d = 0; !c->prop_vt0 && readable(k) && d < CLASS_DEPTH; d++, k = *(const char *const *)(k + T_OFF_CLASS_PARENT)) {
        const char *name = *(const char *const *)(k + T_OFF_CLASS_NAME);
        if (hlib_in_image(name) && 0 == strcmp(name, "Widget")) c->prop_vt0 = property_marker(k);
    }
    for (int d = 0; readable(cls) && d < CLASS_DEPTH && c->getters; d++, cls = *(const char *const *)(cls + T_OFF_CLASS_PARENT)) {
        void *const *list = *(void *const *const *)(cls + T_OFF_CLASS_LIST);
        unsigned count = *(const unsigned *)(cls + T_OFF_CLASS_COUNT);
        if (!readable(list) || count > CLASS_PROPS) continue;
        for (unsigned i = 0; i < count && n->nprops < UD_MAX_PROPS && c->getters && c->prop_vt0; i++) {
            void **vt = readable(list[i]) ? *(void ***)list[i] : NULL;
            if (!vt || vt[0] != c->prop_vt0) continue; /* an event or an action */
            UdValue *v = &n->props[n->nprops];
            memset(v, 0, sizeof(*v));
            v->name = *(const char *const *)((const char *)list[i] + T_OFF_PROP_NAME);
            if (!hlib_in_image(v->name)) continue;
            v->uid = *(const uint32_t *)((const char *)list[i] + T_OFF_PROP_ID), v->index = -1;
            if (read_property(c, list[i], props, v)) n->nprops++;
            else n->props_fault |= c->faults > 0;
        }
    }
    n->props_read = c->getters != 0; /* a reader that faulted on this widget ends the readers: its list is not whole */
}

/* An object's type record: the record getter follows its type hash getter (`lea rax, [rip+d]; ret`); read from the
   code, nothing called. */
static const char *record_of(const char *obj) {
    void **vt = readable(obj) ? *(void ***)obj : NULL;
    const unsigned char *fn = vt && hlib_in_image(vt) ? (const unsigned char *)vt[0] : NULL;
    if (!fn || !hlib_in_image(fn) || fn[0] != T_TYPEHASH_OP || fn[1] != T_TYPEHASH_MODRM) return NULL;
    const unsigned char *g = fn + T_OFF_TYPEREC_GETTER;
    if (g[0] != T_LEA_RAX_0 || g[1] != T_LEA_RAX_1 || g[2] != T_LEA_RAX_2 || g[7] != T_RET_OP) return NULL;
    const char *rec = (const char *)(g + 7) + *(const int32_t *)(g + 3);
    return hlib_in_image(rec) ? rec : NULL;
}

/* the serialised fields of an object whose names the format lacks (uidump_unnamed.h), its type chain walked */
static void unnamed_of(const char *obj, int on, uint32_t uid, UdNode *n) {
    const char *rec = record_of(obj);
    for (int d = 0; g_unnamed && rec && d < CLASS_DEPTH; d++, rec = *(const char *const *)(rec + T_OFF_TYPEREC_PARENT)) {
        if (!hlib_in_image(rec)) break;
        uint32_t owner = *(const uint32_t *)(rec + T_OFF_TYPEREC_HASH);
        int seen = ud_listed(n->unknown_types, n->nunknown, owner);
        if (!seen && n->nunknown < UD_MAX_UNKNOWN && ud_listed(g_unnamed_types, sizeof(g_unnamed_types) / 4, owner))
            n->unknown_types[n->nunknown++] = owner;
        const char *tbl = *(const char *const *)(rec + T_OFF_TYPEREC_FIELDS);
        unsigned count = *(const unsigned *)(rec + T_OFF_TYPEREC_COUNT);
        for (unsigned i = 0; readable(tbl) && i < count && i < CLASS_PROPS && n->nunnamed < UD_MAX_UNNAMED; i++) {
            const char *e = tbl + i * T_TYPEREC_FIELD_SIZE;
            uint32_t crc = *(const uint32_t *)(e + T_OFF_FIELD_CRC);
            if (!ud_listed(g_unnamed_fields, sizeof(g_unnamed_fields) / 4, crc)) continue;
            UdField *f = &n->unnamed[n->nunnamed++];
            uint8_t mem[24];
            memset(f, 0, sizeof(*f));
            f->crc = crc, f->owner = owner, f->on = on, f->uid = uid;
            f->code = *(const uint32_t *)(e + T_OFF_FIELD_CODE), f->elem = *(const uint32_t *)(e + T_OFF_FIELD_ELEM);
            f->offset = *(const uint32_t *)(e + T_OFF_FIELD_OFFSET);
            memcpy(mem, obj + f->offset, sizeof(mem));
            ud_field_value(&f->v, f->code, mem);
            if (f->code == T_PHX_CODE_STRING) /* a string block: its text */
                f->v.kind = UD_STRING, string_of(*(const char *const *)mem, f->v.text, sizeof(f->v.text));
            else if (f->code == T_PHX_CODE_REF && readable(*(const char *const *)mem)) { /* a reference: its GUID */
                char t[37];
                ud_guid_text((const uint8_t *)*(const char *const *)mem + T_OFF_REF_GUID, t);
                f->v.kind = UD_STRING, snprintf(f->v.text, sizeof(f->v.text), "%s", t);
            }
        }
    }
}

/* a template's custom properties in its compact vector: UniqueId and the value index of each one made */
static void custom_uids(const char *tmpl, UdNode *n) {
    uint64_t word = *(const uint64_t *)(tmpl + T_OFF_TMPL_PROPS_N);
    unsigned flags = (unsigned)(word >> T_TMPL_FLAGS_SHIFT), count = (unsigned)(word & T_TMPL_COUNT_MASK);
    const char *at = tmpl + T_OFF_TMPL_PROPS;
    int relative = (flags & T_TMPL_RELATIVE) && !(flags & T_TMPL_ABSOLUTE);
    const uint64_t *items = relative ? (const uint64_t *)(at + *(const int64_t *)at) : *(const uint64_t *const *)at;
    for (unsigned i = 0; readable(items) && i < count && i < UD_MAX_CUSTOM * 4; i++) {
        const char *cpi = (const char *)(uintptr_t)(items[i] & ~(uint64_t)1);
        const char *desc = readable(cpi) ? *(const char *const *)(cpi + T_OFF_CPI_DESC) : NULL;
        if (!readable(desc)) continue; /* not made yet: the template never ran this property */
        int idx = *(const int *)(desc + T_OFF_DESC_INDEX);
        if (idx >= 0 && idx < n->ncustom) n->custom[idx].uid = *(const uint32_t *)(cpi + T_OFF_CPI_UID);
        if (readable(cpi)) unnamed_of(cpi, UD_ON_PROPERTY, *(const uint32_t *)(cpi + T_OFF_CPI_UID), n);
    }
}

static void read_custom(char *rt, const char *file, UdNode *n) {
    unsigned count = *(const unsigned *)(rt + T_OFF_RT_VALUE_COUNT);
    const unsigned char *vals = *(const unsigned char *const *)(rt + T_OFF_RT_VALUES);
    void *(*resolve)(void *) = (void *(*)(void *))hlib_target(T_PHX_RESOLVE);
    if (!readable(vals)) return;
    if (count > UD_MAX_CUSTOM) count = UD_MAX_CUSTOM; /* the rest is not written */
    for (unsigned i = 0; i < count; i++) {
        memset(&n->custom[i], 0, sizeof(n->custom[i]));
        n->custom[i].index = (int)i;
        take_variant(&n->custom[i], vals + i * T_PHX_VARIANT_SIZE);
    }
    n->ncustom = (int)count;
    void *ref = *(void *const *)(file + T_OFF_FILE_TEMPLATE);
    const char *tmpl = ref && resolve ? (const char *)resolve(ref) : NULL;
    if (!readable(tmpl)) return;
    unnamed_of(tmpl, UD_ON_TEMPLATE, 0, n);
    custom_uids(tmpl, n);
}

static void read_layout(char *rt, UdNode *n) {
    float *(*world)(void *) = (float *(*)(void *))hlib_target(T_PHX_WORLD_MATRIX);
    const float *wm = world(rt);
    if (readable(wm)) memcpy(n->world, wm, sizeof(n->world)), n->has_world = 1;
    memcpy(n->local, rt + T_OFF_RT_LOCAL, sizeof(n->local));
    memcpy(n->pos, rt + T_OFF_RT_POSITION, sizeof(n->pos));
    memcpy(n->pivot, rt + T_OFF_RT_PIVOT, sizeof(n->pivot));
    memcpy(n->scale, rt + T_OFF_RT_SCALE, sizeof(n->scale));
    memcpy(n->bb_pos, rt + T_OFF_RT_BBOX, sizeof(n->bb_pos));
    memcpy(n->bb_size, rt + T_OFF_RT_BBOX + sizeof(float) * 4, sizeof(n->bb_size));
    memcpy(n->comp, rt + T_OFF_RT_COMP, sizeof(n->comp));
    memcpy(n->size, rt + T_OFF_RT_SIZE, sizeof(n->size));
    n->rot = *(const float *)(rt + T_OFF_RT_ROTATION);
    n->alpha = *(const float *)(rt + T_OFF_RT_ALPHA);
    n->z = *(const int *)(rt + T_OFF_RT_ZINDEX);
    n->dirty = *(const uint32_t *)(rt + T_OFF_RT_DIRTY);
    n->flags = *(const uint16_t *)(rt + T_OFF_RT_FLAGS);
    n->mask_mode = *(const uint8_t *)(rt + T_OFF_RT_MASK_MODE);
    n->mask_owner = *(void *const *)(rt + T_OFF_RT_MASK_OWNER);
    n->visible = (n->flags & T_RT_FLAG_VISIBLE) != 0, n->drawable = (n->flags & T_RT_FLAG_DRAWABLE) != 0;
    n->receives_mask = (n->flags & T_RT_FLAG_RECEIVES) != 0, n->mask_intersect = n->mask_mode == T_RT_MASK_INTERSECT;
}

static void read_identity(char *rt, const char *file, UdNode *n) {
    n->has_name = string_of(*(const char *const *)(rt + T_OFF_RT_NAME), n->name, sizeof(n->name)) ||
                  string_of(*(const char *const *)(file + T_OFF_FILE_NAME), n->name, sizeof(n->name));
    n->name_crc = *(const uint32_t *)(file + T_OFF_FILE_NAMECRC), n->has_crc = 1;
    memcpy(n->guid, file + T_OFF_FILE_GUID, 16), n->has_guid = 1;
    n->type_hash = type_hash_of(file);
    n->container = keeps_children(file);
    if (n->type_hash == T_PHX_TYPE_CONTAINER || n->type_hash == T_PHX_TYPE_SCENE) {
        const char *ref = *(const char *const *)(file + T_OFF_FILE_TEMPLATE);
        if (readable(ref)) memcpy(n->tmpl, ref + T_OFF_REF_GUID, 16), n->has_tmpl = 1;
    }
}

static int read_widget(void *ctx, const void *w, UdNode *n) {
    Ctx *c = (Ctx *)ctx;
    char *rt = (char *)w;
    __try {
        const char *file = *(const char *const *)(rt + T_OFF_RT_FILE);
        if (!readable(file) || !hlib_in_image(*(void *const *)file)) return 0;
        read_identity(rt, file, n);
        read_layout(rt, n);
        unnamed_of(file, UD_ON_INSTANCE, 0, n);
        if (n->has_tmpl) read_custom(rt, file, n);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "ui_dump: a widget could not be read");
        return 0;
    }
    if (c->getters) {
        __try {
            read_props(c, rt, n);
        } __except (hlib_fault_filter(GetExceptionCode())) {
            n->props_fault = 1, c->faults++, c->getters = 0;
            hlib_log("ui_dump: the built-in properties could not be read; the rest of this dump has none");
        }
    }
    return 1;
}

static const char *getters_state(void *ctx) {
    const Ctx *c = (const Ctx *)ctx;
    return c->faults ? "faulted" : g_getters ? "read" : "off";
}

static BOOL CALLBACK largest_window(HWND w, LPARAM p) {
    DWORD pid = 0;
    RECT r;
    int *best = (int *)p;
    GetWindowThreadProcessId(w, &pid);
    if (pid != GetCurrentProcessId() || !IsWindowVisible(w) || !GetClientRect(w, &r)) return TRUE;
    if ((r.right - r.left) * (r.bottom - r.top) > best[0] * best[1]) best[0] = r.right - r.left, best[1] = r.bottom - r.top;
    return TRUE;
}

static void write_file(const char *name, const UdBuf *b) {
    char dir[MAX_PATH], path[MAX_PATH];
    if (!hlib_user_path(dir, "log") || (!CreateDirectoryA(dir, NULL) && GetLastError() != ERROR_ALREADY_EXISTS)) return;
    if (!hlib_user_path(dir, "log/ui-dump") || (!CreateDirectoryA(dir, NULL) && GetLastError() != ERROR_ALREADY_EXISTS))
        return;
    if (snprintf(path, sizeof(path), "%s/%s", dir, name) >= (int)sizeof(path)) return;
    unsigned long err = 0;
    if (!ud_write_atomic(path, b->p, b->n, &err)) {
        hlib_logf("ui_dump: %s could not be written (error %lu)", path, err);
        return;
    }
    n_written++;
    hlib_logf("ui_dump: %s written (%lu bytes)", path, (unsigned long)b->n);
}

static void header_of(UdHeader *h, char *client, int id, const char *name, const char *scene, const char *stamp,
                      const char *why) {
    int win[2] = {0, 0};
    memset(h, 0, sizeof(*h));
    EnumWindows(largest_window, (LPARAM)win);
    h->scene = scene, h->state_name = name, h->stamp = stamp, h->trigger = why, h->state_id = id;
    h->load_state = client[T_OFF_CLIENT_STATE + *(const unsigned char *)(client + T_OFF_CLIENT_STATE_AT)];
    h->out_w = ((const uint32_t *)(client + T_OFF_CLIENT_OUT_SIZE))[0];
    h->out_h = ((const uint32_t *)(client + T_OFF_CLIENT_OUT_SIZE))[1];
    h->ref_w = ((const uint32_t *)(client + T_OFF_CLIENT_REF_SIZE))[0];
    h->ref_h = ((const uint32_t *)(client + T_OFF_CLIENT_REF_SIZE))[1];
    h->root_x = ((const int *)(client + T_OFF_CLIENT_ROOT_POS))[0];
    h->root_y = ((const int *)(client + T_OFF_CLIENT_ROOT_POS))[1];
    h->win_w = win[0], h->win_h = win[1];
    h->screen_w = GetSystemMetrics(SM_CXSCREEN), h->screen_h = GetSystemMetrics(SM_CYSCREEN);
    h->game_build = T_BUILD_FILESIZE, h->library = hlib_library_build();
    h->max_nodes = g_max, h->max_depth = 64;
}

/* One state's scene, under its client's lock; the file is written after the lock is given back. */
static void dump_state(char *st, int id, const char *name, const char *stamp, const char *why) {
    char *scene = *(char **)(st + T_OFF_UI_SCENE), file_name[160], scene_name[96];
    char *client = readable(scene) ? *(char **)(scene + T_OFF_SCENE_CLIENT) : NULL;
    UdBuf b = {0};
    UdStats stats = {0};
    UdHeader h;
    int ok = 0;
    if (!readable(client) || !readable(*(void **)(client + T_OFF_CLIENT_ROOT))) return;
    Ctx c = {(CRITICAL_SECTION *)(client + T_OFF_CLIENT_LOCK), NULL, g_getters, 0};
    UdReader r = {read_widget, children, &c, getters_state};
    snprintf(scene_name, sizeof(scene_name), "%sscene", name);
    _strlwr_s(scene_name, sizeof(scene_name));
    EnterCriticalSection(c.cs);
    __try {
        __try {
            header_of(&h, client, id, name, scene_name, stamp, why);
            ok = ud_dump(&b, &h, &r, *(void **)(client + T_OFF_CLIENT_ROOT), &stats);
        } __finally {
            LeaveCriticalSection(c.cs);
        }
    } __except (hlib_fault_filter(GetExceptionCode())) {
        ok = 0; /* the lock is back; the buffer is freed below */
        hlib_logf("ui_dump: %s: the walk faulted, nothing written", scene_name);
    }
    ud_file_name(file_name, sizeof(file_name), stamp, scene_name);
    if (ok) write_file(file_name, &b);
    if (ok && g_files && g_files_n < g_files_cap) snprintf(g_files[g_files_n++], 160, "%s", file_name);
    hlib_logf("ui_dump: %s (state %d, %s): %d widget(s)%s, %d unread, %d mask(s) not found, built-in readers %s",
               scene_name, id, why, stats.nodes, stats.truncated ? " (the ui_dump_max limit)" : "", stats.unread,
               stats.masks_unresolved, getters_state(&c));
    ud_buf_free(&b);
}

static void stamp_now(char *out, int cap) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    snprintf(out, (size_t)cap, "%04d%02d%02d-%02d%02d%02d-%03d", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond,
             t.wMilliseconds);
}

/* One state's scene when it is active and has one; 1 when it was dumped. Its reads are guarded here, so a bad state
   costs only itself, not the rest of the frame's loop. */
static int dump_if_active(void *ui, int id, const char *stamp, const char *why) {
    char *(*get)(void *, int) = (char *(*)(void *, int))hlib_target(T_UI_GET_STATE);
    __try {
        char *st = get(ui, id);
        void **vt = readable(st) ? *(void ***)st : NULL;
        if (!vt || !hlib_in_image(vt) || !st[T_OFF_UI_ACTIVE]) return 0;
        const char *name = ((const char *(*)(void *))vt[T_UI_VT_NAME])(st);
        if (!name) return 0;
        dump_state(st, id, name, stamp, why);
        return 1;
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "ui_dump: state %d could not be read; the other states go on", id);
        return 0;
    }
}

/* every open state, or one: its scene dumped when it is active and has one */
static void dump_states(void *ui, int only, const char *why) {
    char stamp[32];
    int n = 0;
    stamp_now(stamp, sizeof(stamp));
    for (int id = 1; id < T_UI_ID_LIMIT; id++)
        if (!only || id == only) n += dump_if_active(ui, id, stamp, why);
    if (!n) hlib_logf("ui_dump: %s, but no open scene was found", why);
}

static int key_down(void) {
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    return pid == GetCurrentProcessId() && (GetAsyncKeyState(VK_CONTROL) & 0x8000) && (GetAsyncKeyState(VK_SHIFT) & 0x8000) &&
           (GetAsyncKeyState(g_key) & 0x8000);
}

/* the named scenes: an open one is dumped once it has been open for the delay (its fade-in done) */
static void watch_state(void *ui, int id, ULONGLONG now) {
    char *(*get)(void *, int) = (char *(*)(void *, int))hlib_target(T_UI_GET_STATE);
    __try {
        char *st = get(ui, id);
        void **vt = readable(st) ? *(void ***)st : NULL;
        int on = vt && hlib_in_image(vt) && st[T_OFF_UI_ACTIVE];
        if (on && !g_was_active[id]) {
            const char *name = ((const char *(*)(void *))vt[T_UI_VT_NAME])(st);
            if (name && ud_scene_listed(g_scenes, name)) g_due[id] = now + (ULONGLONG)g_delay_ms;
        }
        if (!on) g_due[id] = 0;
        g_was_active[id] = (unsigned char)on;
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "ui_dump: state %d could not be read; the other states go on", id);
        g_due[id] = 0, g_was_active[id] = 0;
    }
    if (g_due[id] && now >= g_due[id]) {
        g_due[id] = 0;
        dump_states(ui, id, "the scene opened");
    }
}

static void watch_scenes(void *ui, ULONGLONG now) {
    for (int id = 1; id < T_UI_ID_LIMIT; id++) watch_state(ui, id, now);
}

static void frame_listener(void *ui) {
    static int was;
    static ULONGLONG next_scan;
    ULONGLONG now = GetTickCount64();
    int down = key_down();
    if (down && !was) InterlockedExchange(&g_request, 1);
    was = down;
    if (InterlockedExchange(&g_request, 0)) dump_states(ui, 0, "the key");
    if (g_scenes[0] && now >= next_scan) next_scan = now + SCAN_MS, watch_scenes(ui, now);
}

static const int g_need[] = {T_UI_FRAME, T_UI_GET_STATE, T_PHX_RESOLVE, T_PHX_WORLD_MATRIX, T_PHX_VARIANT_FREE};

const int *hlib_ui_dump_targets(int *n) {
    *n = (int)(sizeof(g_need) / sizeof(g_need[0]));
    return g_need;
}

int hlib_ui_dump_wanted(void) { return hlib_feature_on("ui_dump"); }

static int ud_resolve(void) {
    unsigned char *base = hlib_image_base();
    for (size_t i = 0; i < sizeof(g_need) / sizeof(g_need[0]); i++)
        if (!hlib_target_ok(g_need[i])) return 0;
    if (!base) return 0;
    g_container_update = base + T_PHX_CONTAINER_UPDATE;
    g_group_update = base + T_PHX_GROUP_UPDATE;
    __try {
        if (((void **)(base + T_VT_PHX_CONTAINER_INSTANCE))[T_VT_FILE_UPDATE] == g_container_update &&
            ((void **)(base + T_VT_PHX_GROUP_INSTANCE))[T_VT_FILE_UPDATE] == g_group_update)
            return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    hlib_log("ui_dump: the widget instance classes do not match this build, staying off");
    return 0;
}

static int g_quiet;

int hlib_ui_dump_install(void) {
    char key[8];
    if (InterlockedCompareExchange(&g_on, 1, 0)) return 1;
    if (!ud_resolve()) return 0;
    if (hlib_config_str("ui_dump_key", key, sizeof(key)) && isalnum((unsigned char)key[0])) g_key = toupper((unsigned char)key[0]);
    g_getters = hlib_config_int("ui_dump_getters", 1);
    g_unnamed = hlib_config_int("ui_dump_unnamed", 1);
    g_delay_ms = hlib_config_int("ui_dump_delay_ms", 1500);
    g_max = hlib_config_int("ui_dump_max", 20000);
    if (g_delay_ms < 0) g_delay_ms = 0;
    if (g_max < 1 || g_max > 200000) g_max = 20000;
    if (!hlib_config_str("ui_dump_scenes", g_scenes, sizeof(g_scenes))) g_scenes[0] = 0;
    if (g_quiet) g_key = 0, g_scenes[0] = 0; /* only on request */
    if (!hlib_on_ui_frame("ui_dump", frame_listener)) {
        hlib_log("ui_dump: the UI's frame is not observed, no dump");
        return 0;
    }
    hlib_logf("ui_dump: ready (Ctrl+Shift+%c dumps every open scene into log/ui-dump/; when they open: %s; built-in "
               "readers %s); unverified in game",
               g_key, g_scenes[0] ? g_scenes : "none", g_getters ? "on" : "off");
    return 1;
}

int hlib_ui_dump_install_quiet(void) {
    if (g_on) return 1;
    g_quiet = 1;
    return hlib_ui_dump_install();
}

int hlib_ui_dump_now(void *ui, const int *ids, int n, char (*files)[160], int cap) {
    char stamp[32];
    if (!g_on) return -1;
    stamp_now(stamp, sizeof(stamp));
    g_files = files, g_files_cap = cap, g_files_n = 0;
    if (n <= 0) {
        for (int id = 1; id < T_UI_ID_LIMIT; id++) dump_if_active(ui, id, stamp, "the live link");
    } else {
        for (int i = 0; i < n; i++)
            if (ids[i] > 0 && ids[i] < T_UI_ID_LIMIT) dump_if_active(ui, ids[i], stamp, "the live link");
    }
    g_files = NULL;
    return g_files_n;
}

static void ud_install(void) { hlib_ui_dump_install(); }

/* run-time switch: the key and the scene watcher ride the UI frame only and change nothing in the game; the live link's
   own dumps stay served */
static int ud_toggle(int on) {
    hlib_logf("ui_dump: the key and the scene watcher %s", on ? "back on" : "off (the live link's dumps stay)");
    return 1;
}

static const HlibModule g_mod = {"ui_dump", ud_resolve, ud_install, 0, 0, 0, 0, ud_toggle, HLIB_PRESENT};
void hlib_register_ui_dump(void) { hlib_register_module(&g_mod, HLIB_DEV); }

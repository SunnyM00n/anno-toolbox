/* The live widget tree's shared readers (widgetread.h): the run-time widgets' memory by this build's layouts, and the
   engine's built-in property readers (what a binding's read runs) as the only calls. Nothing is set. */
#include "widgetread.h"
#include "targets.h"
#include <string.h>

enum { CLASS_DEPTH = 8, CLASS_PROPS = 128, STRING_MAX = 4096 };

static void *g_container_update, *g_group_update;
static int g_classes = -1, n_fail;

int wr_readable(const void *p) { return (uintptr_t)p >= 0x10000u && !((uintptr_t)p & 7); }

int wr_classes_ok(void) {
    unsigned char *base = hlib_image_base();
    if (g_classes >= 0) return g_classes;
    g_classes = 0;
    if (!base) return 0;
    g_container_update = base + T_PHX_CONTAINER_UPDATE;
    g_group_update = base + T_PHX_GROUP_UPDATE;
    __try {
        g_classes = ((void **)(base + T_VT_PHX_CONTAINER_INSTANCE))[T_VT_FILE_UPDATE] == g_container_update &&
                    ((void **)(base + T_VT_PHX_GROUP_INSTANCE))[T_VT_FILE_UPDATE] == g_group_update;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        g_classes = 0;
    }
    return g_classes;
}

int wr_string_of(const char *h, char *out, int cap) {
    if (!wr_readable(h)) return 0;
    unsigned len = *(const unsigned *)h;
    if (!len || len > STRING_MAX) return 0;
    int n = (int)len < cap - 1 ? (int)len : cap - 1;
    memcpy(out, h + T_PHX_STRING_TEXT, (size_t)n);
    out[n] = 0;
    return 1;
}

/* a file instance's type hash, read from its getter's code (an immediate store); nothing is called */
static uint32_t type_hash_of(const char *file) {
    void **vt = *(void ***)file;
    const unsigned char *fn = hlib_in_image(vt) ? (const unsigned char *)vt[0] : NULL;
    if (!fn || !hlib_in_image(fn) || fn[0] != T_TYPEHASH_OP || fn[1] != T_TYPEHASH_MODRM) return 0;
    return *(const uint32_t *)(fn + 2);
}

int wr_keeps_children(const char *file) {
    void **vt = wr_readable(file) ? *(void ***)file : NULL;
    if (!vt || !hlib_in_image(vt)) return 0;
    return vt[T_VT_FILE_UPDATE] == g_container_update || vt[T_VT_FILE_UPDATE] == g_group_update;
}

char *wr_client_of(char *state) {
    char *scene = *(char **)(state + T_OFF_UI_SCENE);
    char *client = wr_readable(scene) ? *(char **)(scene + T_OFF_SCENE_CLIENT) : NULL;
    return wr_readable(client) && wr_readable(*(void **)(client + T_OFF_CLIENT_ROOT)) ? client : NULL;
}

int wr_children(const void *w, const void **out, int cap, int *cut, const char *who) {
    const char *rt = (const char *)w;
    int got = 0;
    __try {
        if (!wr_keeps_children(*(char *const *)(rt + T_OFF_RT_FILE))) return 0;
        unsigned n = *(const unsigned *)(rt + T_OFF_RT_NCHILD);
        void *const *kids = *(void *const *const *)(rt + T_OFF_RT_CHILDREN);
        if (!wr_readable(kids)) return 0;
        if (n > (unsigned)cap) *cut = 1, n = (unsigned)cap; /* the first cap are walked, the reader says it is cut */
        for (unsigned i = 0; i < n && got < cap; i++) {
            void **vt = wr_readable(kids[i]) ? *(void ***)kids[i] : NULL;
            if (!vt || !hlib_in_image(vt)) continue;
            void *child = ((void *(*)(void *))vt[T_VT_RT_CHILD])(kids[i]);
            if (wr_readable(child)) out[got++] = child;
        }
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "%s: a widget's children could not be read (after %d)", who, got);
        *cut = 1;
    }
    return got;
}

void wr_identity(const char *rt, const char *file, WrIdentity *out) {
    out->has_name = wr_string_of(*(const char *const *)(rt + T_OFF_RT_NAME), out->name, sizeof out->name) ||
                    wr_string_of(*(const char *const *)(file + T_OFF_FILE_NAME), out->name, sizeof out->name);
    out->name_crc = *(const uint32_t *)(file + T_OFF_FILE_NAMECRC);
    memcpy(out->guid, file + T_OFF_FILE_GUID, 16);
    out->type_hash = type_hash_of(file);
    out->has_tmpl = 0;
    if (out->type_hash == T_PHX_TYPE_CONTAINER || out->type_hash == T_PHX_TYPE_SCENE) {
        const char *ref = *(const char *const *)(file + T_OFF_FILE_TEMPLATE);
        if (wr_readable(ref)) memcpy(out->tmpl, ref + T_OFF_REF_GUID, 16), out->has_tmpl = 1;
    }
}

/* the properties' accessors share slot 0; a class's list also holds events and actions (another slot 0) */
static const void *property_marker(const char *cls) {
    const void *seen[4] = {0};
    int count[4] = {0};
    void *const *list = *(void *const *const *)(cls + T_OFF_CLASS_LIST);
    unsigned n = *(const unsigned *)(cls + T_OFF_CLASS_COUNT);
    for (unsigned i = 0; wr_readable(list) && i < n && i < CLASS_PROPS; i++) {
        void **vt = wr_readable(list[i]) ? *(void ***)list[i] : NULL;
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

void wr_each_property(WrProps *c, char *rt, WrEachProperty each, void *ctx, const char **class_name) {
    void *props = *(void **)(rt + T_OFF_RT_OBSERVABLE);
    void **pvt = wr_readable(props) ? *(void ***)props : NULL;
    if (!pvt || !hlib_in_image(pvt)) return;
    const char *cls = ((const char *(*)(void *))pvt[T_VT_PROPS_CLASS])(props), *k = cls;
    if (!wr_readable(cls) || ((void *(*)(void *))pvt[T_VT_PROPS_WIDGET])(props) != rt) return;
    if (class_name) *class_name = *(const char *const *)(cls + T_OFF_CLASS_NAME);
    for (int d = 0; !c->prop_vt0 && wr_readable(k) && d < CLASS_DEPTH; d++, k = *(const char *const *)(k + T_OFF_CLASS_PARENT)) {
        const char *name = *(const char *const *)(k + T_OFF_CLASS_NAME);
        if (hlib_in_image(name) && !strcmp(name, "Widget")) c->prop_vt0 = property_marker(k);
    }
    for (int d = 0; wr_readable(cls) && d < CLASS_DEPTH && c->getters && c->prop_vt0;
         d++, cls = *(const char *const *)(cls + T_OFF_CLASS_PARENT)) {
        void *const *list = *(void *const *const *)(cls + T_OFF_CLASS_LIST);
        unsigned count = *(const unsigned *)(cls + T_OFF_CLASS_COUNT);
        if (!wr_readable(list) || count > CLASS_PROPS) continue;
        for (unsigned i = 0; i < count && c->getters; i++) {
            void **vt = wr_readable(list[i]) ? *(void ***)list[i] : NULL;
            if (!vt || vt[0] != c->prop_vt0) continue; /* an event or an action */
            const char *name = *(const char *const *)((const char *)list[i] + T_OFF_PROP_NAME);
            if (hlib_in_image(name) && !each(ctx, list[i], props, name)) return;
        }
    }
}

int wr_read_property(WrProps *c, const void *rec, void *props, int (*take)(void *out, const unsigned char *var),
                     void *out) {
    __declspec(align(16)) unsigned char var[T_PHX_VARIANT_SIZE];
    int status = -1, ok = 0;
    LONG depth = c->cs->RecursionCount;
    void (*free_variant)(void *) = (void (*)(void *))hlib_target(T_PHX_VARIANT_FREE);
    void **vt = *(void ***)rec;
    memset(var, 0, sizeof var);
    var[T_OFF_VARIANT_TYPE] = WR_EMPTY;
    __try {
        ((int *(*)(const void *, int *, void *, void *, char))vt[T_VT_PROP_READ])(rec, &status, props, var, 1);
        if (status == 0) ok = take(out, var);
        free_variant(var);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        while (c->cs->RecursionCount > depth && c->cs->OwningThread == (HANDLE)(uintptr_t)GetCurrentThreadId())
            LeaveCriticalSection(c->cs);
        c->faults++, c->getters = 0, ok = 0;
        hlib_logf("%s: the reader of %s faulted; no built-in property is read for the rest of this walk", c->who,
                   (const char *)*(void *const *)((const char *)rec + T_OFF_PROP_NAME));
    }
    return ok;
}

const char *wr_template_of(const char *file) {
    void *(*resolve)(void *) = (void *(*)(void *))hlib_target(T_PHX_RESOLVE);
    void *ref = *(void *const *)(file + T_OFF_FILE_TEMPLATE);
    const char *tmpl = ref && resolve ? (const char *)resolve(ref) : NULL;
    return wr_readable(tmpl) ? tmpl : NULL;
}

/* the widget file keeps its custom properties in a compact vector, in place or behind an offset */
void wr_each_custom(const char *tmpl, WrEachCustom each, void *ctx) {
    uint64_t word = *(const uint64_t *)(tmpl + T_OFF_TMPL_PROPS_N);
    unsigned flags = (unsigned)(word >> T_TMPL_FLAGS_SHIFT), count = (unsigned)(word & T_TMPL_COUNT_MASK);
    const char *at = tmpl + T_OFF_TMPL_PROPS;
    int relative = (flags & T_TMPL_RELATIVE) && !(flags & T_TMPL_ABSOLUTE);
    const uint64_t *items = relative ? (const uint64_t *)(at + *(const int64_t *)at) : *(const uint64_t *const *)at;
    for (unsigned i = 0; wr_readable(items) && i < count && i < WR_MAX_CUSTOM * 4; i++) {
        const char *cpi = (const char *)(uintptr_t)(items[i] & ~(uint64_t)1);
        const char *desc = wr_readable(cpi) ? *(const char *const *)(cpi + T_OFF_CPI_DESC) : NULL;
        if (!wr_readable(desc)) continue; /* not made yet: the widget file never ran this property */
        each(ctx, cpi, *(const int *)(desc + T_OFF_DESC_INDEX), *(const uint32_t *)(cpi + T_OFF_CPI_UID));
    }
}

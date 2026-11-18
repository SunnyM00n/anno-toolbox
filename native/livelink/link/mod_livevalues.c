/* The live widget values (ui.values; docs/live-link.md section 17), the engine side: for the UI states the request
   names, the open scene's live widget tree is walked on the UI frame under the scene client's own lock, and of the
   widgets of the one file the editor has open only what they show is read: a label's text, an image's id, the own
   Visible flag and the widget's own property values. Read only: the engine's built-in property readers (what a binding's
   read runs) are the only calls, and nothing is set. The walk and the answer are livevalues_logic.c, the readers of
   the tree widgetread.c. */
#include "hlib.h"
#include "livelink.h"
#include "livevalues_logic.h"
#include "targets.h"
#include "widgetread.h"
#include <stdio.h>
#include <string.h>

static const int g_need[] = {T_UI_GET_STATE, T_UI_STATE_ID, T_PHX_RESOLVE, T_PHX_VARIANT_FREE};
static int g_ready = -1, n_fail;

const int *hlib_live_link_values_targets(int *n) {
    *n = (int)(sizeof g_need / sizeof g_need[0]);
    return g_need;
}

static int children(void *ctx, const void *w, const void **out, int cap, int *cut) {
    (void)ctx;
    return wr_children(w, out, cap, cut, "live link");
}

static int identify(void *ctx, const void *w, LvIdentity *n) {
    const char *rt = (const char *)w;
    WrIdentity id;
    (void)ctx;
    __try {
        const char *file = *(const char *const *)(rt + T_OFF_RT_FILE);
        if (!wr_readable(file) || !hlib_in_image(*(void *const *)file)) return 0;
        wr_identity(rt, file, &id);
        n->visible = (*(const uint16_t *)(rt + T_OFF_RT_FLAGS) & T_RT_FLAG_VISIBLE) != 0;
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "live link: a widget could not be read");
        return 0;
    }
    n->has_name = id.has_name, n->has_tmpl = id.has_tmpl, n->has_crc = n->has_guid = 1;
    n->name_crc = id.name_crc;
    if (id.has_name) snprintf(n->name, sizeof n->name, "%s", id.name);
    memcpy(n->guid, id.guid, 16);
    if (id.has_tmpl) memcpy(n->tmpl, id.tmpl, 16);
    return 1;
}

/* a variant as an answered value: numbers, switches and texts; 0 for any other kind (left out) */
static int take(void *out, const unsigned char *var) {
    LvValue *v = (LvValue *)out;
    switch (var[T_OFF_VARIANT_TYPE]) {
    case WR_BOOL: v->kind = LV_BOOL, v->num = var[0] != 0; return 1;
    case WR_INT: v->kind = LV_NUMBER, v->num = *(const int *)var; return 1;
    case WR_UINT: v->kind = LV_NUMBER, v->num = *(const unsigned *)var; return 1;
    case WR_INT64: case WR_UINT64: v->kind = LV_NUMBER, v->num = (double)*(const long long *)var; return 1;
    case WR_FLOAT: v->kind = LV_NUMBER, v->num = *(const float *)var; return 1;
    case WR_STRING:
        v->kind = LV_STRING;
        if (!wr_string_of(*(const char *const *)var, v->text, sizeof v->text)) v->text[0] = 0;
        return 1;
    default: return 0;
    }
}

typedef struct {
    WrProps *props;
    LvShown *shown;
} Builtins;

/* a label's Text and an image's DynamicImageId, found by name in the class chain: no other built-in is read */
static int builtin(void *ctx, const void *rec, void *props, const char *name) {
    Builtins *b = (Builtins *)ctx;
    LvShown *s = b->shown;
    int text = !strcmp(name, "Text"), image = !strcmp(name, "DynamicImageId");
    LvValue v;
    if ((!text || s->has_text) && (!image || s->has_image)) return 1;
    memset(&v, 0, sizeof v);
    if (!wr_read_property(b->props, rec, props, take, &v)) return 1;
    if (v.kind != LV_STRING) snprintf(v.text, sizeof v.text, "%.0f", v.num); /* an id held as a number */
    if (!v.text[0]) return 1; /* an empty text: the game has not filled it */
    snprintf(text ? s->text : s->image, LV_TEXT, "%s", v.text);
    *(text ? &s->has_text : &s->has_image) = 1;
    return 1;
}

/* the UniqueId of each value index a container instance's widget file made */
static void value_uid(void *ctx, const char *cpi, int index, uint32_t uid) {
    (void)cpi;
    if (index >= 0 && index < WR_MAX_CUSTOM) ((uint32_t *)ctx)[index] = uid;
}

/* a container instance's own property values, those whose UniqueId is known and whose kind is answered */
static void read_values(char *rt, LvShown *s) {
    uint32_t uid[WR_MAX_CUSTOM] = {0};
    unsigned count = *(const unsigned *)(rt + T_OFF_RT_VALUE_COUNT);
    const unsigned char *vals = *(const unsigned char *const *)(rt + T_OFF_RT_VALUES);
    const char *tmpl = wr_template_of(*(const char *const *)(rt + T_OFF_RT_FILE));
    if (!wr_readable(vals) || !tmpl) return;
    if (count > WR_MAX_CUSTOM) count = WR_MAX_CUSTOM;
    wr_each_custom(tmpl, value_uid, uid);
    for (unsigned i = 0; i < count && s->nvalues < LV_MAX_VALUES; i++) {
        LvValue *v = &s->values[s->nvalues];
        memset(v, 0, sizeof *v);
        v->uid = uid[i];
        if (uid[i] && take(v, vals + i * T_PHX_VARIANT_SIZE)) s->nvalues++;
    }
}

static void shown(void *ctx, const void *w, const LvIdentity *id, LvShown *s) {
    WrProps *c = (WrProps *)ctx;
    Builtins b = {c, s};
    char *rt = (char *)w;
    if (id->has_tmpl) {
        __try {
            read_values(rt, s);
        } __except (hlib_fault_filter(GetExceptionCode())) {
            s->nvalues = 0;
            hlib_log_n(&n_fail, 8, "live link: a widget's property values could not be read");
        }
    }
    if (!c->getters) return;
    __try {
        wr_each_property(c, rt, builtin, &b, NULL);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        c->getters = 0;
        hlib_log("live link: the built-in properties could not be read; the rest of this request has none");
    }
}

int hlib_live_link_values_ready(void) {
    if (g_ready >= 0) return g_ready;
    g_ready = 0;
    for (size_t i = 0; i < sizeof g_need / sizeof g_need[0]; i++)
        if (!hlib_target_ok(g_need[i])) return 0;
    g_ready = wr_classes_ok();
    if (!g_ready) hlib_log("live link: the widget classes do not match this build: no live widget values");
    return g_ready;
}

/* One state's open scene under its client's lock; 1 when its tree was walked (the file found in it or not). */
static int scene_of(LvBuf *b, void *ui, uint32_t id, const char *name, const uint8_t *file) {
    char *(*get)(void *, int) = (char *(*)(void *, int))hlib_target(T_UI_GET_STATE);
    size_t head = b->n;
    int scenes = b->scenes, walked = 0;
    LvStats st;
    __try {
        char *state = get(ui, (int)id);
        void **vt = wr_readable(state) ? *(void ***)state : NULL;
        if (!vt || !hlib_in_image(vt) || !state[T_OFF_UI_ACTIVE]) return 0;
        char *client = wr_client_of(state);
        if (!client) return 0;
        WrProps c = {(CRITICAL_SECTION *)(client + T_OFF_CLIENT_LOCK), NULL, 1, 0, "live link"};
        LvReader r = {identify, shown, children, &c};
        EnterCriticalSection(c.cs);
        __try {
            lv_scene(b, name, file, &r, *(void **)(client + T_OFF_CLIENT_ROOT), &st);
            walked = 1;
        } __finally {
            LeaveCriticalSection(c.cs);
        }
    } __except (hlib_fault_filter(GetExceptionCode())) {
        b->n = head, b->p[head] = 0, b->scenes = scenes; /* the lock is back; nothing of this scene is answered */
        hlib_logf("live link: the scene of %s could not be walked; the other states go on", name);
        return 0;
    }
    if (walked)
        hlib_logf("live link: values of %s: %d widget(s) looked at, %d answered%s", name, st.walked, st.answered,
                   st.cut ? " (cut at the answer's size)" : "");
    return walked;
}

/* Following the open scenes (ui.scene, docs/live-link.md section 18): a state's own name, only from a getter of the
   game's image (a state of ours is not reported), kept per id: a name never changes while the game runs. */
static char g_names[LS_MAX][LS_NAME];
static uint8_t g_named[LS_MAX]; /* 1 named, 2 no plain name */

static const char *state_name(void *st, int id) {
    void **vt = wr_readable(st) ? *(void ***)st : NULL;
    if (id >= 0 && id < LS_MAX && g_named[id]) return g_named[id] == 1 ? g_names[id] : NULL;
    if (!vt || !hlib_in_image(vt) || !hlib_in_image(vt[T_UI_VT_NAME])) return NULL;
    const char *name = ((const char *(*)(void *))vt[T_UI_VT_NAME])(st);
    int plain = (uintptr_t)name >= 0x10000u ? ls_name_ok(name) : 0; /* read under the caller's guard */
    if (id < 0 || id >= LS_MAX) return plain ? name : NULL;
    if (plain) snprintf(g_names[id], LS_NAME, "%s", name);
    g_named[id] = plain ? 1 : 2;
    return plain ? g_names[id] : NULL;
}

int hlib_live_link_scenes_now(void *ui, LsOpen *out, int cap) {
    char *(*get)(void *, int) = (char *(*)(void *, int))hlib_target(T_UI_GET_STATE);
    int n = 0;
    if (!get || !ui) return -1;
    __try {
        for (int id = 0; id < T_UI_ID_LIMIT && id < LS_MAX && n < cap; id++) {
            char *st = get(ui, id);
            void **vt = wr_readable(st) ? *(void ***)st : NULL;
            if (!vt || !hlib_in_image(vt) || !st[T_OFF_UI_ACTIVE] || !*(void **)(st + T_OFF_UI_SCENE)) continue;
            const char *name = state_name(st, id);
            if (!name) continue;
            LsOpen *o = &out[n];
            memset(o, 0, sizeof *o);
            o->id = id;
            snprintf(o->name, LS_NAME, "%s", name);
            char *up = *(char **)(st + T_OFF_UI_PARENT);
            for (int d = 0; wr_readable(up) && d < 12; d++, up = *(char **)(up + T_OFF_UI_PARENT)) {
                if (!d && (name = state_name(up, -1)) != NULL) snprintf(o->parent, LS_NAME, "%s", name);
                o->depth++;
            }
            n++;
        }
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log_n(&n_fail, 8, "live link: the UI states could not be read for following; nothing reported this time");
        return -1; /* not "none open": the client keeps what it was told */
    }
    return n;
}

int hlib_live_link_values_now(void *ui, char (*states)[64], int n, const uint8_t *file, char *data, size_t cap,
                              int *unknown, int *open) {
    uint32_t (*id_of)(const void *) = (uint32_t(*)(const void *))hlib_target(T_UI_STATE_ID);
    LvBuf b;
    *unknown = *open = 0;
    if (!hlib_live_link_values_ready() || !id_of) return -1;
    lv_buf_init(&b, data, cap);
    lv_open(&b, file);
    for (int i = 0; i < n && !b.cut; i++) {
        struct {
            const char *text;
            int64_t length;
        } view = {states[i], (int64_t)strlen(states[i])};
        uint32_t id = id_of(&view);
        if (id == 0xffffffffu || id >= T_UI_ID_LIMIT) (*unknown)++;
        else *open += scene_of(&b, ui, id, states[i], file);
    }
    lv_close(&b);
    return b.scenes;
}

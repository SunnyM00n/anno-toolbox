/* The live widget values, the pure part (livevalues_logic.h): the walk to the open file's widgets and the answer's
   JSON, written into a fixed buffer so that the answer is whole or ends where a widget no longer fits. */
#define _CRT_SECURE_NO_WARNINGS
#include "livevalues_logic.h"
#include "widgetread_logic.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LV_TAIL 64 /* kept free for the closing brackets of a scene and of the answer */

void lv_buf_init(LvBuf *b, char *mem, size_t cap) {
    b->p = mem, b->n = 0, b->cap = cap, b->cut = 0, b->scenes = 0;
    if (cap) mem[0] = 0;
}

/* `s` appended whole when it fits below cap less `keep`; else nothing, and the buffer says it was cut */
static int put_keep(LvBuf *b, const char *s, size_t n, size_t keep) {
    if (b->n + n + 1 + keep > b->cap) {
        b->cut = 1;
        return 0;
    }
    memcpy(b->p + b->n, s, n);
    b->n += n;
    b->p[b->n] = 0;
    return 1;
}

static int put(LvBuf *b, const char *s) { return put_keep(b, s, strlen(s), LV_TAIL); }
static int put_tail(LvBuf *b, const char *s) { return put_keep(b, s, strlen(s), 0); }

void lv_str(LvBuf *b, const char *s) {
    size_t k = strlen(s);
    char *out = (char *)malloc(WT_JSON_ROOM(k));
    if (!out) {
        b->cut = 1;
        return;
    }
    put_keep(b, out, wt_json_string(out, s, k), LV_TAIL);
    free(out);
}

static void num(LvBuf *b, double v) {
    char t[40];
    if (isnan(v) || isinf(v)) snprintf(t, sizeof t, "null");
    else if (v == floor(v) && fabs(v) < 1e15) snprintf(t, sizeof t, "%.0f", v == 0 ? 0.0 : v);
    else snprintf(t, sizeof t, "%.9g", v);
    put(b, t);
}

static int hex(char c) {
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
}

int lv_guid_parse(const char *text, uint8_t out[16]) {
    static const int order[16] = {3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15}; /* text position -> byte */
    uint8_t g[16];
    int k = 0;
    if (strlen(text) != 36) return 0;
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (text[i] != '-') return 0;
            continue;
        }
        int hi = hex(text[i]), lo = hex(text[i + 1]);
        if (hi < 0 || lo < 0) return 0;
        g[order[k++]] = (uint8_t)(hi * 16 + lo);
        i++;
    }
    memcpy(out, g, 16);
    return 1;
}

void lv_open(LvBuf *b, const uint8_t *file) {
    char t[37];
    put(b, "{\"file\":");
    if (file) wt_guid_text(file, t), lv_str(b, t);
    else put(b, "null");
    put(b, ",\"scenes\":[");
}

void lv_close(LvBuf *b) { put_tail(b, b->cut ? "],\"cut\":true}" : "],\"cut\":false}"); }

/* One widget as the answer names it; into `w`, a scratch buffer, so that it goes into the answer whole or not at all */
static void widget(LvBuf *w, const LvIdentity *id, const LvShown *s, int first) {
    char t[64];
    put(w, first ? "{\"guid\":" : ",{\"guid\":");
    if (id->has_guid) wt_guid_text(id->guid, t), lv_str(w, t);
    else put(w, "null");
    if (id->has_crc) snprintf(t, sizeof t, ",\"crc\":\"%08x\"", id->name_crc), put(w, t);
    if (id->has_name) put(w, ",\"name\":"), lv_str(w, id->name);
    put(w, id->visible ? ",\"visible\":true" : ",\"visible\":false");
    if (s->has_text) put(w, ",\"text\":"), lv_str(w, s->text);
    if (s->has_image) put(w, ",\"image\":"), lv_str(w, s->image);
    for (int i = 0; i < s->nvalues && i < LV_MAX_VALUES; i++) {
        const LvValue *v = &s->values[i];
        snprintf(t, sizeof t, "%s\"%u\":", i ? "," : ",\"values\":{", v->uid);
        put(w, t);
        if (v->kind == LV_STRING) lv_str(w, v->text);
        else if (v->kind == LV_BOOL) put(w, v->num != 0 ? "true" : "false");
        else num(w, v->num);
    }
    if (s->nvalues > 0) put(w, "}");
    put(w, "}");
}

typedef struct {
    const void *w;
    int depth, inside; /* inside: placed directly by the anchor's file */
} Pending;

typedef struct {
    Pending *stack;
    const void **kids;
    LvIdentity *id;
    LvShown *shown;
    char *scratch;
} Work;

static void work_free(Work *k) {
    free(k->stack), free((void *)k->kids), free(k->id), free(k->shown), free(k->scratch);
}

/* 1 the widget went into the answer, 0 the answer is full (the walk ends) */
static int answer(LvBuf *b, Work *k, const LvReader *r, const void *w, LvStats *st) {
    LvBuf one;
    if (st->answered >= LV_MAX_ANSWERED) return 0;
    memset(k->shown, 0, sizeof *k->shown);
    r->shown(r->ctx, w, k->id, k->shown);
    lv_buf_init(&one, k->scratch, LV_DATA_MAX);
    widget(&one, k->id, k->shown, st->answered == 0);
    if (one.cut || !put(b, one.p)) return 0;
    st->answered++;
    return 1;
}

static void walk(LvBuf *b, Work *k, const uint8_t *file, const LvReader *r, const void *root, LvStats *st, int *anchor_at) {
    int top = 0, cap = LV_MAX_WALK + LV_MAX_KIDS;
    k->stack[top++] = (Pending){root, 0, 0};
    while (top) {
        Pending p = k->stack[--top];
        if (*anchor_at >= 0 && p.depth <= *anchor_at) break; /* every widget under the anchor was seen */
        if (st->walked >= LV_MAX_WALK) {
            st->cut = 1;
            break;
        }
        st->walked++;
        memset(k->id, 0, sizeof *k->id);
        if (!p.w || !r->identify(r->ctx, p.w, k->id)) continue; /* not readable: nor is what it holds */
        int anchor = *anchor_at < 0 && (file ? k->id->has_tmpl && !memcmp(k->id->tmpl, file, 16) : p.depth == 0);
        if (p.inside) {
            if (!answer(b, k, r, p.w, st)) {
                st->cut = 1;
                break;
            }
            if (k->id->has_tmpl) continue; /* a widget file placed in the file: its widgets are its own */
        }
        if (anchor) *anchor_at = p.depth;
        if (p.depth >= LV_MAX_DEPTH) continue;
        int cut = 0, n = r->children(r->ctx, p.w, k->kids, LV_MAX_KIDS, &cut);
        if (cut) st->cut = 1;
        for (int i = n - 1; i >= 0; i--) {
            if (top >= cap) {
                st->cut = 1;
                break;
            }
            k->stack[top++] = (Pending){k->kids[i], p.depth + 1, p.inside || anchor};
        }
    }
}

int lv_scene(LvBuf *b, const char *state, const uint8_t *file, const LvReader *r, const void *root, LvStats *st) {
    Work k;
    size_t head = b->n;
    int anchor_at = -1;
    memset(st, 0, sizeof *st);
    if (b->cut) return 0; /* the answer is full already */
    k.stack = (Pending *)malloc(sizeof(Pending) * (LV_MAX_WALK + LV_MAX_KIDS));
    k.kids = (const void **)malloc(sizeof(void *) * LV_MAX_KIDS);
    k.id = (LvIdentity *)malloc(sizeof(LvIdentity));
    k.shown = (LvShown *)malloc(sizeof(LvShown));
    k.scratch = (char *)malloc(LV_DATA_MAX);
    if (!k.stack || !k.kids || !k.id || !k.shown || !k.scratch || !root) {
        work_free(&k);
        return 0;
    }
    put(b, b->scenes ? ",{\"state\":" : "{\"state\":");
    lv_str(b, state);
    put(b, ",\"widgets\":[");
    if (!b->cut) walk(b, &k, file, r, root, st, &anchor_at);
    work_free(&k);
    if (anchor_at < 0 || (b->cut && !st->answered)) { /* not placed here, or not even the scene's head fitted */
        b->n = head, b->p[head] = 0;
        st->cut = b->cut;
        return 0;
    }
    if (st->cut) b->cut = 1;
    put_tail(b, st->cut ? "],\"cut\":true}" : "],\"cut\":false}");
    b->scenes++;
    st->found = 1;
    return 1;
}

/* following the open scenes */

void ls_reset(LsSeen *s) { memset(s, 0, sizeof *s); }

int ls_name_ok(const char *name) {
    size_t n = name ? strlen(name) : 0;
    if (!n || n >= LS_NAME) return 0;
    for (size_t i = 0; i < n; i++) {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return 0;
    }
    return 1;
}

int ls_scene_name(const char *state, char *out, int cap) {
    size_t n = ls_name_ok(state) ? strlen(state) : 0;
    if (!n || n + 6 > (size_t)cap) return 0;
    for (size_t i = 0; i < n; i++) out[i] = (char)(state[i] >= 'A' && state[i] <= 'Z' ? state[i] + 32 : state[i]);
    memcpy(out + n, "scene", 6);
    return 1;
}

static int by_depth(const void *a, const void *b) {
    const LsOpen *x = *(const LsOpen *const *)a, *y = *(const LsOpen *const *)b;
    return x->depth != y->depth ? x->depth - y->depth : x->id - y->id;
}

int ls_diff(LsSeen *s, const LsOpen *now, int n, LsChange *out, int cap) {
    const LsOpen *fresh[LS_MAX];
    uint8_t is_open[LS_MAX] = {0};
    int got = 0, nfresh = 0;
    for (int i = 0; i < n; i++)
        if (now[i].id >= 0 && now[i].id < LS_MAX && ls_name_ok(now[i].name)) is_open[now[i].id] = 1;
    while (got < cap) { /* closings, the newest opening first */
        int pick = -1;
        for (int id = 0; id < LS_MAX; id++)
            if (s->open[id] && !is_open[id] && (pick < 0 || s->order[id] > s->order[pick])) pick = id;
        if (pick < 0) break;
        LsChange *c = &out[got++];
        memset(c, 0, sizeof *c);
        c->order = s->order[pick], c->st.id = pick, c->st.depth = -1;
        memcpy(c->st.name, s->name[pick], LS_NAME);
        s->open[pick] = 0;
    }
    for (int i = 0; i < n && nfresh < LS_MAX; i++) {
        int id = now[i].id;
        if (id >= 0 && id < LS_MAX && is_open[id] && !s->open[id]) fresh[nfresh++] = &now[i], is_open[id] = 0;
    }
    qsort((void *)fresh, (size_t)nfresh, sizeof fresh[0], by_depth);
    for (int i = 0; i < nfresh && got < cap; i++) {
        LsChange *c = &out[got++];
        int id = fresh[i]->id;
        c->opened = 1, c->st = *fresh[i], c->order = ++s->next;
        s->open[id] = 1, s->order[id] = c->order;
        memcpy(s->name[id], fresh[i]->name, LS_NAME);
    }
    return got;
}

int ls_event_json(char *out, size_t cap, const LsChange *c, int win_w, int win_h) {
    char scene[LS_NAME + 8], tail[96] = "";
    int n;
    if (!ls_scene_name(c->st.name, scene, sizeof scene)) return -1;
    if (win_w > 0 && win_h > 0) snprintf(tail, sizeof tail, ",\"window\":[%d,%d]", win_w, win_h);
    if (c->opened)
        n = snprintf(out, cap, "{\"change\":\"opened\",\"state\":\"%s\",\"scene\":\"%s\",\"parent\":\"%s\",\"depth\":%d,"
                     "\"order\":%u%s}", c->st.name, scene, ls_name_ok(c->st.parent) ? c->st.parent : "", c->st.depth,
                     c->order, tail);
    else
        n = snprintf(out, cap, "{\"change\":\"closed\",\"state\":\"%s\",\"scene\":\"%s\",\"order\":%u%s}", c->st.name,
                     scene, c->order, tail);
    return n < 0 || (size_t)n >= cap ? -1 : n;
}

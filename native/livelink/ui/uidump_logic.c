/* The UI dump probe, pure (uidump_logic.h): the JSON buffer, the walk in two passes (the widgets' order and ids,
   then each widget read and written with what it inherits), the screen rectangle. uidump_test.c covers it. */
#include "uidump_logic.h"
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

static void grow(UdBuf *b, size_t more) {
    if (b->oom || b->n + more + 1 <= b->cap) return;
    size_t cap = b->cap ? b->cap : 1 << 16;
    while (cap < b->n + more + 1) cap *= 2;
    char *p = (char *)realloc(b->p, cap);
    if (!p) {
        b->oom = 1;
        return;
    }
    b->p = p, b->cap = cap;
}

void ud_buf_free(UdBuf *b) {
    free(b->p);
    memset(b, 0, sizeof(*b));
}

void ud_put(UdBuf *b, const char *s) {
    size_t k = strlen(s);
    grow(b, k);
    if (b->oom) return;
    memcpy(b->p + b->n, s, k + 1);
    b->n += k;
}

void ud_putf(UdBuf *b, const char *fmt, ...) {
    char tmp[512];
    va_list ap;
    va_start(ap, fmt);
    int k = vsnprintf(tmp, sizeof(tmp), fmt, ap);
    va_end(ap);
    if (k < 0) return;
    tmp[sizeof(tmp) - 1] = 0;
    ud_put(b, tmp);
}

/* How many bytes the well-formed UTF-8 sequence at s[i] takes (k bytes in all), 0 for a stray, overlong, surrogate or
   cut one: those are written as U+FFFD so the file reads as UTF-8 whatever the game's string held. */
static size_t utf8_len(const unsigned char *s, size_t i, size_t k) {
    unsigned char c = s[i];
    size_t n = c >= 0xC2 && c <= 0xDF ? 2 : c >= 0xE0 && c <= 0xEF ? 3 : c >= 0xF0 && c <= 0xF4 ? 4 : 0;
    if (!n || i + n > k) return 0;
    for (size_t j = 1; j < n; j++)
        if ((s[i + j] & 0xC0) != 0x80) return 0;
    if ((c == 0xE0 && s[i + 1] < 0xA0) || (c == 0xED && s[i + 1] >= 0xA0)) return 0;
    if ((c == 0xF0 && s[i + 1] < 0x90) || (c == 0xF4 && s[i + 1] >= 0x90)) return 0;
    return n;
}

void ud_str(UdBuf *b, const char *s, int len) {
    char esc[8];
    size_t k = len < 0 ? strlen(s) : (size_t)len;
    grow(b, k * 6 + 2);
    if (b->oom) return;
    b->p[b->n++] = '"';
    for (size_t i = 0; i < k; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            b->p[b->n++] = '\\';
            b->p[b->n++] = (char)c;
        } else if (c < 0x20) { /* the control characters as \u escapes, so the file stays one JSON text */
            snprintf(esc, sizeof(esc), "\\u%04x", c);
            memcpy(b->p + b->n, esc, 6);
            b->n += 6;
        } else if (c >= 0x80) {
            size_t n = utf8_len((const unsigned char *)s, i, k);
            if (n) memcpy(b->p + b->n, s + i, n), b->n += n, i += n - 1;
            else memcpy(b->p + b->n, "\\ufffd", 6), b->n += 6;
        } else {
            b->p[b->n++] = (char)c;
        }
    }
    b->p[b->n++] = '"';
    b->p[b->n] = 0;
}

void ud_num(UdBuf *b, double v) {
    if (isnan(v) || isinf(v)) {
        ud_put(b, "null");
        return;
    }
    if (v == 0) v = 0; /* no -0 */
    ud_putf(b, "%.7g", v);
}

static void nums(UdBuf *b, const float *v, int n) {
    ud_put(b, "[");
    for (int i = 0; i < n; i++) {
        if (i) ud_put(b, ",");
        ud_num(b, v[i]);
    }
    ud_put(b, "]");
}

void ud_guid_text(const uint8_t g[16], char out[37]) {
    snprintf(out, 37, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", g[3], g[2], g[1], g[0],
             g[5], g[4], g[7], g[6], g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
}

static void guid(UdBuf *b, const char *key, const uint8_t g[16], int has) {
    char t[37];
    ud_putf(b, ",\"%s\":", key);
    if (!has) {
        ud_put(b, "null");
        return;
    }
    ud_guid_text(g, t);
    ud_str(b, t, -1);
}

void ud_point(const float m[16], float x, float y, float out[2]) {
    out[0] = x * m[0] + y * m[4] + m[12];
    out[1] = x * m[1] + y * m[5] + m[13];
}

int ud_rect(const UdNode *n, float out[4]) {
    float c[4][2];
    float x = n->bb_pos[0], y = n->bb_pos[1], w = n->bb_size[0], h = n->bb_size[1];
    if (!n->has_world) return 0;
    ud_point(n->world, x, y, c[0]);
    ud_point(n->world, x + w, y, c[1]);
    ud_point(n->world, x, y + h, c[2]);
    ud_point(n->world, x + w, y + h, c[3]);
    out[0] = out[2] = c[0][0], out[1] = out[3] = c[0][1];
    for (int i = 1; i < 4; i++) {
        out[0] = fminf(out[0], c[i][0]), out[2] = fmaxf(out[2], c[i][0]);
        out[1] = fminf(out[1], c[i][1]), out[3] = fmaxf(out[3], c[i][1]);
    }
    return 1;
}

static int same_word(const char *a, size_t an, const char *b) {
    size_t bn = strlen(b);
    if (an == bn) return 0 == _strnicmp(a, b, an);
    return an == bn + 5 && 0 == _strnicmp(a, b, bn) && 0 == _strnicmp(a + bn, "scene", 5); /* "<state>scene" */
}

int ud_scene_listed(const char *list, const char *state_name) {
    const char *p = list;
    while (p && *p) {
        while (*p == ',' || *p == ' ' || *p == ';') p++;
        const char *e = p;
        while (*e && *e != ',' && *e != ';') e++;
        size_t n = (size_t)(e - p);
        while (n && p[n - 1] == ' ') n--;
        if (n && same_word(p, n, state_name)) return 1;
        p = e;
    }
    return 0;
}

void ud_file_name(char *out, int cap, const char *stamp, const char *scene) {
    int k = snprintf(out, (size_t)cap, "%s-", stamp);
    for (const char *s = scene; k > 0 && k < cap - 6 && *s; s++)
        out[k++] = (isalnum((unsigned char)*s) || *s == '-' || *s == '_') ? *s : '_';
    if (k > 0 && k < cap - 5) memcpy(out + k, ".json", 6);
}

/* Pass 1: the widgets in drawing order (depth first, children after their parent), each with its parent and depth,
   and a pointer -> id map for the masks. */
typedef struct {
    const void **w;
    int *parent, *depth, n, cap;
    const void **map; /* open addressing, 2 x cap, pointers */
    int *map_id, map_cap;
} Order;

static unsigned slot_of(const Order *o, const void *p) {
    uintptr_t x = (uintptr_t)p;
    x ^= x >> 17, x *= 0x9e3779b97f4a7c15ull, x ^= x >> 29;
    return (unsigned)(x & (uintptr_t)(o->map_cap - 1));
}

static void map_put(Order *o, const void *p, int id) {
    unsigned s = slot_of(o, p);
    while (o->map[s] && o->map[s] != p) s = (s + 1) & (unsigned)(o->map_cap - 1);
    o->map[s] = p, o->map_id[s] = id;
}

static int map_get(const Order *o, const void *p) {
    if (!p) return -1;
    for (unsigned s = slot_of(o, p); o->map[s]; s = (s + 1) & (unsigned)(o->map_cap - 1))
        if (o->map[s] == p) return o->map_id[s];
    return -1;
}

static int order_init(Order *o, int cap) {
    memset(o, 0, sizeof(*o));
    o->cap = cap;
    for (o->map_cap = 64; o->map_cap < 2 * cap; o->map_cap *= 2) {}
    o->w = (const void **)calloc((size_t)cap, sizeof(void *));
    o->parent = (int *)calloc((size_t)cap, sizeof(int));
    o->depth = (int *)calloc((size_t)cap, sizeof(int));
    o->map = (const void **)calloc((size_t)o->map_cap, sizeof(void *));
    o->map_id = (int *)calloc((size_t)o->map_cap, sizeof(int));
    return o->w && o->parent && o->depth && o->map && o->map_id;
}

static void order_free(Order *o) {
    free((void *)o->w), free(o->parent), free(o->depth), free((void *)o->map), free(o->map_id);
}

typedef struct {
    const void *w;
    int parent, depth;
} Pending;

static void order_walk(Order *o, const UdReader *r, const void *root, int max_depth, UdStats *st) {
    Pending *stack = (Pending *)malloc(sizeof(Pending) * (size_t)(o->cap + UD_MAX_KIDS));
    const void **kids = (const void **)malloc(sizeof(void *) * UD_MAX_KIDS);
    int top = 0;
    if (!stack || !kids) goto out;
    stack[top++] = (Pending){root, -1, 0};
    while (top) {
        Pending p = stack[--top];
        if (!p.w || map_get(o, p.w) >= 0) continue; /* a widget reached twice is written once */
        if (o->n >= o->cap) {
            st->truncated = 1;
            break;
        }
        int id = o->n++;
        o->w[id] = p.w, o->parent[id] = p.parent, o->depth[id] = p.depth;
        map_put(o, p.w, id);
        if (p.depth >= max_depth) {
            st->deep++;
            continue;
        }
        int cut = 0, k = r->children(r->ctx, p.w, kids, UD_MAX_KIDS, &cut);
        for (int i = k - 1; i >= 0; i--) {
            if (top >= o->cap + UD_MAX_KIDS) {
                cut = 1; /* the stack's bound: the rest of this widget's children are not walked */
                break;
            }
            stack[top++] = (Pending){kids[i], id, p.depth + 1};
        }
        if (cut) st->truncated = 1;
    }
out:
    free(stack);
    free((void *)kids);
}

/* What a widget hands to the ones under it. */
typedef struct {
    uint8_t shown, drawable, intersect, has_tmpl;
    float alpha;
    int owner; /* the nearest widget at or above that places a widget file (its template), -1 none */
    uint8_t tmpl[16];
} Inherit;

static void value(UdBuf *b, const UdValue *v) {
    switch (v->kind) {
    case UD_BOOL: ud_put(b, v->i ? "true" : "false"); break;
    case UD_INT: ud_putf(b, "%lld", v->i); break;
    case UD_FLOAT: ud_num(b, v->v[0]); break;
    case UD_STRING: ud_str(b, v->text, -1); break;
    case UD_VEC: {
        float f[4];
        for (int i = 0; i < v->n && i < 4; i++) f[i] = (float)v->v[i];
        nums(b, f, v->n);
        break;
    }
    case UD_COLOR: ud_putf(b, "[%d,%d,%d]", (int)v->v[0], (int)v->v[1], (int)v->v[2]); break;
    case UD_OBJECT: ud_put(b, v->i ? "\"object\"" : "null"); break;
    case UD_RAW: {
        char hex[49];
        for (int i = 0; i < 24; i++) snprintf(hex + 2 * i, 3, "%02x", v->raw[i]);
        ud_str(b, hex, -1);
        break;
    }
    default: ud_put(b, "null");
    }
}

/* the file format's field codes: the kind in the second byte, bit 0 a list */
void ud_field_value(UdValue *v, uint32_t code, const uint8_t mem[24]) {
    uint32_t kind = code >> 8;
    v->type = (int)code;
    if (code & 1) kind = 0; /* a list: its layout in memory is not the file's */
    switch (kind) {
    case 0x01: v->kind = UD_BOOL, v->i = mem[0] != 0; break;
    case 0x04: v->kind = UD_INT, v->i = mem[0]; break;
    case 0x06: v->kind = UD_INT, v->i = *(const uint16_t *)mem; break;
    case 0x07: v->kind = UD_INT, v->i = *(const int32_t *)mem; break;
    case 0x08: case 0x0b: v->kind = UD_INT, v->i = *(const uint32_t *)mem; break;
    case 0x09: case 0x0a: case 0x1a: v->kind = UD_INT, v->i = *(const long long *)mem; break;
    case 0x10: v->kind = UD_FLOAT, v->v[0] = *(const float *)mem; break;
    case 0x11: case 0x12: case 0x13:
        v->kind = UD_VEC, v->n = (int)kind - 0x0f;
        for (int i = 0; i < v->n; i++) v->v[i] = ((const float *)mem)[i];
        break;
    default: v->kind = UD_RAW, memcpy(v->raw, mem, sizeof(v->raw));
    }
}

int ud_listed(const uint32_t *list, int n, uint32_t h) {
    for (int i = 0; i < n; i++)
        if (list[i] == h) return 1;
    return 0;
}

static void unnamed(UdBuf *b, const UdNode *n) {
    static const char *const on[] = {"instance", "template", "property"};
    if (n->nunknown) {
        ud_put(b, ",\"unknown_types\":[");
        for (int i = 0; i < n->nunknown; i++) ud_putf(b, "%s\"%08x\"", i ? "," : "", n->unknown_types[i]);
        ud_put(b, "]");
    }
    if (!n->nunnamed) return;
    ud_put(b, ",\"unnamed\":[");
    for (int i = 0; i < n->nunnamed; i++) {
        const UdField *f = &n->unnamed[i];
        ud_putf(b, "%s{\"crc\":\"%08x\",\"type\":\"%08x\",\"code\":\"0x%x\",\"offset\":%u,\"on\":\"%s\"", i ? "," : "",
                f->crc, f->owner, f->code, f->offset, on[f->on < 3 && f->on >= 0 ? f->on : 0]);
        if (f->elem) ud_putf(b, ",\"elem\":\"%08x\"", f->elem);
        if (f->on == UD_ON_PROPERTY) ud_putf(b, ",\"uid\":%u", f->uid);
        ud_put(b, ",\"value\":");
        value(b, &f->v);
        ud_put(b, "}");
    }
    ud_put(b, "]");
}

static void props(UdBuf *b, const UdNode *n) {
    ud_put(b, ",\"props\":{");
    for (int i = 0, first = 1; i < n->nprops; i++) {
        if (!n->props[i].name) continue;
        if (!first) ud_put(b, ",");
        first = 0;
        ud_str(b, n->props[i].name, -1);
        ud_put(b, ":");
        value(b, &n->props[i]);
    }
    ud_put(b, "}");
    if (!n->ncustom) return;
    ud_put(b, ",\"custom\":[");
    for (int i = 0; i < n->ncustom; i++) {
        const UdValue *v = &n->custom[i];
        ud_putf(b, "%s{\"index\":%d,\"uid\":", i ? "," : "", v->index);
        if (v->uid) ud_putf(b, "%u", v->uid); else ud_put(b, "null");
        ud_putf(b, ",\"type\":%d,\"value\":", v->type);
        value(b, v);
        ud_put(b, "}");
    }
    ud_put(b, "]");
}

static void layout(UdBuf *b, const UdNode *n) {
    ud_put(b, ",\"layout\":{\"position\":");
    nums(b, n->pos, 3);
    ud_put(b, ",\"pivot\":");
    nums(b, n->pivot, 3);
    ud_put(b, ",\"scale\":");
    nums(b, n->scale, 3);
    ud_put(b, ",\"rotation\":");
    ud_num(b, n->rot);
    ud_put(b, ",\"compensation\":");
    nums(b, n->comp, 2);
    ud_put(b, ",\"bbox_pos\":");
    nums(b, n->bb_pos, 3);
    ud_put(b, ",\"bbox_size\":");
    nums(b, n->bb_size, 2);
    ud_put(b, ",\"size\":");
    nums(b, n->size, 2);
    ud_put(b, ",\"alpha\":");
    ud_num(b, n->alpha);
    ud_putf(b, ",\"zindex\":%d,\"flags\":\"0x%04x\",\"dirty\":\"0x%08x\",\"mask_mode\":%d}", n->z, n->flags, n->dirty,
            n->mask_mode);
}

static void geometry(UdBuf *b, const UdNode *n) {
    float r[4], pv[2];
    if (!ud_rect(n, r)) {
        ud_put(b, ",\"screen\":null,\"pivot_screen\":null,\"world\":null");
        return;
    }
    ud_put(b, ",\"screen\":");
    nums(b, r, 4);
    ud_point(n->world, n->pivot[0], n->pivot[1], pv);
    ud_put(b, ",\"pivot_screen\":");
    nums(b, pv, 2);
    ud_put(b, ",\"world\":");
    nums(b, n->world, 16);
    ud_put(b, ",\"local\":");
    nums(b, n->local, 16);
}

/* One widget: who it is, where it is, what it shows; ``in`` is the widget file it is an instance of. */
static void widget(UdBuf *b, const Order *o, int id, const UdNode *n, const Inherit *me, const Inherit *up, int mask_id) {
    ud_putf(b, "%s\n{\"id\":%d,\"parent\":%d,\"depth\":%d,\"owner\":%d", id ? "," : "", id, o->parent[id], o->depth[id],
            up ? up->owner : -1);
    ud_put(b, ",\"name\":");
    if (n->has_name) ud_str(b, n->name, -1); else ud_put(b, "null");
    if (n->has_crc) ud_putf(b, ",\"name_crc\":\"%08x\"", n->name_crc); else ud_put(b, ",\"name_crc\":null");
    guid(b, "guid", n->guid, n->has_guid);
    guid(b, "in", up ? up->tmpl : n->tmpl, up && up->has_tmpl);
    guid(b, "template", n->tmpl, n->has_tmpl);
    ud_putf(b, ",\"type\":\"%08x\",\"class\":", n->type_hash);
    ud_str(b, n->cls, -1);
    ud_putf(b, ",\"container\":%s", n->container ? "true" : "false");
    geometry(b, n);
    layout(b, n);
    ud_putf(b, ",\"visible\":%s,\"drawable\":%s,\"receives_mask\":%s,\"shown\":%s,\"alpha_product\":",
            n->visible ? "true" : "false", n->drawable ? "true" : "false", n->receives_mask ? "true" : "false",
            me->shown ? "true" : "false");
    ud_num(b, me->alpha);
    if (mask_id >= 0) ud_putf(b, ",\"mask\":%d", mask_id); else ud_put(b, ",\"mask\":null");
    ud_put(b, n->props_read ? ",\"props_read\":true" : ",\"props_read\":false"); /* false: its readers were not run */
    if (n->props_fault) ud_put(b, ",\"props_fault\":true");
    props(b, n);
    unnamed(b, n);
    ud_put(b, "}");
}

static void header(UdBuf *b, const UdHeader *h) {
    ud_putf(b, "{\"format\":%d,\"probe\":\"ui_dump\",\"scene\":", UD_FORMAT);
    ud_str(b, h->scene, -1);
    ud_put(b, ",\"state\":");
    ud_str(b, h->state_name ? h->state_name : "", -1);
    ud_putf(b, ",\"state_id\":%d,\"load_state\":%d,\"stamp\":", h->state_id, h->load_state);
    ud_str(b, h->stamp ? h->stamp : "", -1);
    ud_put(b, ",\"trigger\":");
    ud_str(b, h->trigger ? h->trigger : "", -1);
    ud_putf(b, ",\"game_build\":%llu,\"library\":%u", h->game_build, h->library);
    ud_putf(b, ",\"output_size\":[%u,%u],\"reference_size\":[%u,%u],\"root_offset\":[%d,%d]", h->out_w, h->out_h, h->ref_w,
            h->ref_h, h->root_x, h->root_y);
    ud_put(b, ",\"ui_scale\":[");
    ud_num(b, h->ref_w ? (double)h->out_w / h->ref_w : 0);
    ud_put(b, ",");
    ud_num(b, h->ref_h ? (double)h->out_h / h->ref_h : 0);
    ud_putf(b, "],\"window\":[%d,%d],\"screen\":[%d,%d]", h->win_w, h->win_h, h->screen_w, h->screen_h);
}

static void inherit(Inherit *me, const Inherit *up, const UdNode *n, const Inherit *mask, int id) {
    int masked_out = mask && !mask->drawable && mask->intersect; /* an intersect mask not drawn shows nothing */
    me->drawable = (uint8_t)(n->drawable != 0);
    me->intersect = (uint8_t)(n->mask_intersect != 0);
    me->shown = (!up || up->shown) && me->drawable && !masked_out;
    me->alpha = (up ? up->alpha : 1.0f) * n->alpha;
    me->has_tmpl = (uint8_t)n->has_tmpl;
    memcpy(me->tmpl, n->tmpl, 16);
    me->owner = n->has_tmpl ? id : up ? up->owner : -1;
    if (!n->has_tmpl && up) {
        me->has_tmpl = up->has_tmpl; /* the file its children are instances of is the one above */
        memcpy(me->tmpl, up->tmpl, 16);
    }
}

int ud_dump(UdBuf *b, const UdHeader *h, const UdReader *r, const void *root, UdStats *st) {
    Order o;
    memset(st, 0, sizeof(*st));
    UdNode *n = (UdNode *)malloc(sizeof(UdNode));
    Inherit *inh = NULL;
    int ok = n && order_init(&o, h->max_nodes > 0 ? h->max_nodes : 1);
    if (ok) {
        order_walk(&o, r, root, h->max_depth > 0 ? h->max_depth : 64, st);
        inh = (Inherit *)calloc((size_t)(o.n ? o.n : 1), sizeof(Inherit));
        ok = inh != NULL;
    }
    if (ok) {
        header(b, h);
        ud_put(b, ",\"widgets\":[");
        for (int id = 0; id < o.n; id++) {
            memset(n, 0, sizeof(*n));
            n->alpha = 1.0f;
            if (!r->read(r->ctx, o.w[id], n)) st->unread++;
            int up = o.parent[id], mask = map_get(&o, n->mask_owner);
            if (n->mask_owner && mask < 0) st->masks_unresolved++;
            st->props_faults += n->props_fault;
            inherit(&inh[id], up >= 0 ? &inh[up] : NULL, n, mask >= 0 && mask < id ? &inh[mask] : NULL, id);
            widget(b, &o, id, n, &inh[id], up >= 0 ? &inh[up] : NULL, mask);
        }
        st->nodes = o.n;
        ud_putf(b, "\n],\"widget_count\":%d,\"truncated\":%s,\"unread\":%d,\"masks_unresolved\":%d,\"props_faults\":%d", o.n,
                st->truncated ? "true" : "false", st->unread, st->masks_unresolved, st->props_faults);
        if (r->getters) ud_putf(b, ",\"getters\":\"%s\"", r->getters(r->ctx));
        ud_put(b, "}\n");
    }
    if (n) order_free(&o);
    free(inh);
    free(n);
    return ok && !b->oom;
}

int ud_write_atomic(const char *path, const char *data, size_t n, unsigned long *err) {
    char tmp[MAX_PATH + 8];
    DWORD done = 0;
    int ok = 0;
    if (snprintf(tmp, sizeof(tmp), "%s.tmp", path) >= (int)sizeof(tmp)) return *err = ERROR_FILENAME_EXCED_RANGE, 0;
    HANDLE f = CreateFileA(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return *err = GetLastError(), 0;
    ok = WriteFile(f, data, (DWORD)n, &done, NULL) && done == (DWORD)n && FlushFileBuffers(f);
    if (!ok) *err = GetLastError() ? GetLastError() : ERROR_WRITE_FAULT; /* a short write sets none */
    if (!CloseHandle(f) && ok) ok = 0, *err = GetLastError();
    if (ok && !MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) ok = 0, *err = GetLastError();
    if (!ok) DeleteFileA(tmp);
    return ok;
}

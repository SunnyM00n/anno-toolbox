/* The asset registry's table (registry_logic.h). */
#include "registry_logic.h"
#include "util.h"
#include <stdlib.h>
#include <string.h>

void reg_init(RegTable *t) { memset(t, 0, sizeof(*t)); }

void reg_free(RegTable *t) {
    free(t->e);
    memset(t, 0, sizeof(*t));
}

static int reg_add(RegTable *t, uint32_t ours, uint32_t source, int slot_type, int role) {
    if (!ours) return 0;
    if (t->n == t->cap) {
        int cap = t->cap ? t->cap * 2 : 512;
        RegEntry *q = (RegEntry *)realloc(t->e, sizeof(RegEntry) * (size_t)cap);
        if (!q) return 0;
        t->e = q;
        t->cap = cap;
    }
    RegEntry *r = &t->e[t->n++];
    r->ours = ours;
    r->source = source;
    r->slot_type = slot_type;
    r->role = role;
    t->sealed = 0;
    return 1;
}

static uint32_t key_guid(const char *k) {
    char *end;
    unsigned long long v = strtoull(k, &end, 10);
    return *k && !*end && v <= 4294967295ULL ? (uint32_t)v : 0;
}

/* the index of a type name among the block's order, -1 when it is not one */
static int type_index(const RegTable *t, const char *name, size_t n) {
    for (int i = 0; i < t->ntypes; i++)
        if (strlen(t->types[i]) == n && !memcmp(t->types[i], name, n)) return i;
    return -1;
}

static int parse_assets(RegTable *t, const char *obj, const char *e) {
    int added = 0;
    const char *a = hlib_json_value(obj, e, "assets");
    if (!a || *a != '{') return 0;
    const char *p = a, *v;
    char key[24];
    while ((p = hlib_json_next(p, e, key, sizeof(key), &v)) != NULL) {
        uint32_t ours = key_guid(key), source = (uint32_t)hlib_json_int(v, e);
        if (reg_add(t, ours, source, 0, REG_ROLE_NONE)) added++;
    }
    t->pairs += added;
    return added;
}

static int parse_members(RegTable *t, const char *obj, const char *e, const char *member, int role) {
    int added = 0;
    const char *m = hlib_json_value(obj, e, member);
    if (!m || *m != '{') return 0;
    const char *p = m, *v;
    char key[24];
    while ((p = hlib_json_next(p, e, key, sizeof(key), &v)) != NULL) {
        if (*v != '"') continue;
        const char *s = v + 1, *q = strchr(s, '"');
        int idx = q && q < e ? type_index(t, s, (size_t)(q - s)) : -1;
        if (idx >= 0 && reg_add(t, key_guid(key), 0, t->first + idx, role)) added++;
    }
    return added;
}

static void parse_types(RegTable *t, const char *obj, const char *e) {
    const char *end, *arr = hlib_json_arr(obj, e, "types", &end), *v;
    if (!arr) return;
    const char *p = arr;
    t->ntypes = 0;
    while (t->ntypes < REG_MAX_TYPES && (p = hlib_json_elem(p, end, &v)) != NULL) {
        if (*v != '"') continue;
        const char *q = strchr(v + 1, '"');
        size_t n = q ? (size_t)(q - v - 1) : 0;
        if (!n || n >= REG_NAME) continue;
        memcpy(t->types[t->ntypes], v + 1, n);
        t->types[t->ntypes++][n] = 0;
    }
}

int reg_parse(RegTable *t, const char *json, size_t len) {
    const char *e = json + len, *reg = hlib_json_value(json, e, "helper_registry"),
               *sl = hlib_json_value(json, e, "helper_slots");
    int added = 0;
    if (reg && *reg == '{') {
        const char *rend = hlib_json_end(reg, e);
        t->declared += (int)hlib_json_num(reg, rend, "count", 0);
        added += parse_assets(t, reg, rend);
    }
    if (sl && *sl == '{') {
        const char *send = hlib_json_end(sl, e);
        t->first = (int)hlib_json_num(sl, send, "first", 7);
        parse_types(t, sl, send);
        added += parse_members(t, sl, send, "slots", REG_ROLE_SLOT);
        added += parse_members(t, sl, send, "buildings", REG_ROLE_BUILDING);
    }
    return added;
}

static int by_ours(const void *a, const void *b) {
    uint32_t x = ((const RegEntry *)a)->ours, y = ((const RegEntry *)b)->ours;
    return x < y ? -1 : x > y;
}

void reg_seal(RegTable *t) {
    if (t->sealed || !t->n) {
        t->sealed = 1;
        return;
    }
    qsort(t->e, (size_t)t->n, sizeof(RegEntry), by_ours);
    int w = 0;
    for (int i = 0; i < t->n; i++) {
        if (w && t->e[w - 1].ours == t->e[i].ours) {
            RegEntry *d = &t->e[w - 1];
            if (!d->source) d->source = t->e[i].source;
            if (!d->slot_type) d->slot_type = t->e[i].slot_type;
            if (!d->role) d->role = t->e[i].role;
        } else {
            t->e[w++] = t->e[i];
        }
    }
    t->n = w;
    t->sealed = 1;
}

const RegEntry *reg_find(const RegTable *t, uint32_t ours) {
    int lo = 0, hi = t->n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        if (t->e[mid].ours == ours) return &t->e[mid];
        if (t->e[mid].ours < ours) lo = mid + 1;
        else hi = mid - 1;
    }
    return NULL;
}

int reg_consistent(const RegTable *t) { return t->declared == t->pairs; }

const char *reg_type_name(const RegTable *t, int v) {
    int i = v - t->first;
    return i >= 0 && i < t->ntypes ? t->types[i] : "";
}

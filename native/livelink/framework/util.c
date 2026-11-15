#include "util.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int hlib_vformat(char *out, int cap, const char *fmt, va_list ap) {
    if (!out || cap < 1) return 0;
    int n = vsnprintf(out, (size_t)cap, fmt, ap);
    if (n < 0) {
        out[0] = 0;
        return 0;
    }
    return n < cap ? n : cap - 1;
}

int hlib_format(char *out, int cap, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = hlib_vformat(out, cap, fmt, ap);
    va_end(ap);
    return n;
}

/* the ini */
static int blank(char c) { return c == ' ' || c == '\t'; }

static int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }

static int same_word(const char *a, const char *b) {
    for (; *a && *b; a++, b++)
        if (lower((unsigned char)*a) != lower((unsigned char)*b)) return 0;
    return !*a && !*b;
}

/* copy [b, e) trimmed into out; 0 when it does not fit */
static int copy_trimmed(const char *b, const char *e, char *out, int cap) {
    while (b < e && blank(*b)) b++;
    while (e > b && (blank(e[-1]) || e[-1] == '\r')) e--;
    if (e - b >= cap) return 0;
    memcpy(out, b, (size_t)(e - b));
    out[e - b] = 0;
    return 1;
}

int hlib_ini_parse(const char *text, HlibIniPair *out, int max, int *cut) {
    const char *p = text;
    int n = 0;
    if (cut) *cut = 0;
    if ((unsigned char)p[0] == 0xEF && (unsigned char)p[1] == 0xBB && (unsigned char)p[2] == 0xBF) p += 3;
    while (*p) {
        const char *e = p, *eq = NULL;
        while (*e && *e != '\n') {
            if (*e == '=' && !eq) eq = e;
            e++;
        }
        const char *s = p;
        while (s < e && blank(*s)) s++;
        if (eq && *s != '#' && *s != ';') {
            if (n >= max) {
                if (cut) *cut = 1;
            } else {
                memset(&out[n], 0, sizeof(out[n]));
                if (copy_trimmed(s, eq, out[n].key, sizeof(out[n].key)) && out[n].key[0] &&
                    copy_trimmed(eq + 1, e, out[n].value, sizeof(out[n].value)))
                    n++;
            }
        }
        p = *e ? e + 1 : e;
    }
    return n;
}

int hlib_ini_bool(const char *v) {
    static const char *const on[] = {"1", "on", "true", "yes"}, *const off[] = {"0", "off", "false", "no"};
    for (int i = 0; i < 4; i++) {
        if (same_word(v, on[i])) return 1;
        if (same_word(v, off[i])) return 0;
    }
    return -1;
}

int hlib_ini_int(const char *v, int *out) {
    const char *p = v;
    int neg = *p == '-';
    long long n = 0;
    if (neg || *p == '+') p++;
    if (*p >= '0' && *p <= '9') {
        for (; *p >= '0' && *p <= '9' && n < 0x80000000LL; p++) n = n * 10 + (*p - '0');
        if (*p || n >= 0x80000000LL) return 0;
        *out = (int)(neg ? -n : n);
        return 1;
    }
    int b = hlib_ini_bool(v);
    if (b < 0) return 0;
    *out = b;
    return 1;
}

/* per-session records */
int hlib_sess_slot(void *records, size_t stride, int *n, int max, void *key, int create, uint32_t *clock, int *reused) {
    unsigned char *r = (unsigned char *)records;
    int lru = -1;
    if (reused) *reused = 0;
    if (!key) return -1;
    for (int i = 0; i < *n; i++) {
        HlibSessHead *h = (HlibSessHead *)(r + (size_t)i * stride);
        if (h->key == key) {
            h->used = ++*clock;
            return i;
        }
        if (lru < 0 || h->used < ((HlibSessHead *)(r + (size_t)lru * stride))->used) lru = i;
    }
    if (!create || max < 1) return -1;
    if (*n < max) return (*n)++;
    if (reused) *reused = 1;
    return lru;
}

void hlib_keys_note(void **keys, int max, void *key) {
    int free_at = -1;
    for (int i = 0; key && i < max; i++) {
        if (keys[i] == key) return;
        if (!keys[i] && free_at < 0) free_at = i;
    }
    if (key && free_at >= 0) keys[free_at] = key;
}

int hlib_keys_drop(void **keys, int max, void *key) {
    int live = 0;
    for (int i = 0; i < max; i++) {
        if (keys[i] == key) keys[i] = NULL;
        live += keys[i] != NULL;
    }
    return live;
}

int hlib_clamp(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

int hlib_cmp_u32(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

/* per-island turns of a pass */
int hlib_area_due(HlibAreaClocks *k, int area, long long now, long long period, long long *dt) {
    int i = 0, oldest = 0;
    if (dt) *dt = 0;
    while (i < k->n && k->c[i].area != area) {
        if (k->c[i].last_ms < k->c[oldest].last_ms) oldest = i;
        i++;
    }
    if (i == k->n) { /* a new area: its first turn */
        i = k->n < HLIB_AREA_CLOCKS ? k->n++ : oldest;
        k->c[i].area = area;
        k->c[i].last_ms = now;
        return 1;
    }
    HlibAreaClock *c = &k->c[i];
    if (now >= c->last_ms && now - c->last_ms < period) return 0;
    if (dt && now > c->last_ms) *dt = now - c->last_ms;
    c->last_ms = now;
    return 1;
}

int hlib_call_due(HlibCallAreas *c, HlibAreaClocks *k, int area, long long now, long long period) {
    for (int i = 0; i < c->n; i++)
        if (c->area[i] == area) return c->due[i];
    if (c->n >= HLIB_CALL_AREAS) return 0; /* not remembered, it could take its turn twice: a later call has it */
    long long dt = 0;
    int due = hlib_area_due(k, area, now, period, &dt);
    c->area[c->n] = area;
    c->due[c->n] = (signed char)due;
    c->dt[c->n++] = dt;
    return due;
}

int hlib_call_took(const HlibCallAreas *c, int area, long long *dt) {
    for (int i = 0; i < c->n; i++)
        if (c->area[i] == area) {
            if (dt) *dt = c->dt[i];
            return c->due[i];
        }
    return 0;
}

long long hlib_turn_ms(long long dt, long long first, long long max) {
    long long t = dt > 0 ? dt : first;
    return t > max ? max : t;
}

unsigned hlib_hash_ints(const int *v, int n) {
    unsigned h = 2166136261u;
    for (int i = 0; i < n; i++) h = (h ^ (unsigned)v[i]) * 16777619u;
    return h;
}

int hlib_note_changed(HlibNotes *t, int area, const int *v, int nv) {
    unsigned h = hlib_hash_ints(v, nv);
    int i = 0;
    while (i < t->n && t->area[i] != area) i++;
    if (i == t->n) {
        if (t->n >= HLIB_NOTES) return 1;
        t->n++;
        t->area[i] = area;
    } else if (t->hash[i] == h) {
        return 0;
    }
    t->hash[i] = h;
    return 1;
}

/* text scanning */
const char *hlib_find(const char *p, const char *end, const char *s) {
    size_t n = strlen(s);
    for (; p && p + n <= end; p++)
        if (*p == *s && memcmp(p, s, n) == 0) return p;
    return NULL;
}

int hlib_xml_span(const char *p, const char *end, const char *tag, const char **b, const char **e) {
    char open[64], close[64];
    size_t n = strlen(tag);
    if (n > 60) return 0;
    open[0] = '<';
    memcpy(open + 1, tag, n);
    open[n + 1] = '>';
    open[n + 2] = 0;
    close[0] = '<';
    close[1] = '/';
    memcpy(close + 2, tag, n);
    close[n + 2] = '>';
    close[n + 3] = 0;
    const char *o = hlib_find(p, end, open);
    const char *c = o ? hlib_find(o + n + 2, end, close) : NULL;
    if (!c) return 0;
    *b = o + n + 2;
    *e = c;
    return 1;
}

uint32_t hlib_guid_before(const char *start, const char *p, int window) {
    for (const char *q = p; q >= start && p - q < window; q--)
        if (q[0] == 'G' && memcmp(q, "GUID=\"", 6) == 0) return (uint32_t)strtoul(q + 6, NULL, 10);
    return 0;
}

const void *hlib_by_guid(const void *sorted, int n, size_t stride, uint32_t guid) {
    const unsigned char *b = (const unsigned char *)sorted;
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        uint32_t g;
        memcpy(&g, b + (size_t)mid * stride, 4);
        if (g == guid) return b + (size_t)mid * stride;
        if (g < guid)
            lo = mid + 1;
        else
            hi = mid - 1;
    }
    return NULL;
}

static const char *skip_space(const char *p, const char *end);

uint32_t hlib_marker_buff(const char *json, size_t len, const char *state) {
    const char *e = json + len, *m = hlib_find(json, e, "\"markers\"");
    char want[80];
    size_t n = strlen(state);
    if (!m || n > 60) return 0;
    memcpy(want, "\"state\": \"", 10);
    memcpy(want + 10, state, n);
    want[10 + n] = '"';
    want[11 + n] = 0;
    const char *p = hlib_find(m, e, want);
    if (!p) return 0;
    const char *close = hlib_find(p, e, "}"), *b = hlib_find(p, e, "\"buff\":");
    if (!b || !close || b > close) return 0;
    return (uint32_t)strtoul(skip_space(b + 7, e), NULL, 10);
}

const char *hlib_json_value(const char *p, const char *e, const char *key) {
    char k[64];
    int n = hlib_format(k, sizeof(k), "\"%s\"", key);
    const char *q = n > 0 && n < (int)sizeof(k) - 1 ? hlib_find(p, e, k) : NULL;
    if (!q) return NULL;
    q = skip_space(q + n, e);
    return q < e && *q == ':' ? skip_space(q + 1, e) : NULL;
}

const char *hlib_json_value_or(const char *p, const char *e, const char *key, const char *old_key) {
    const char *v = hlib_json_value(p, e, key);
    return v ? v : hlib_json_value(p, e, old_key);
}

int hlib_json_has(const char *text, const char *key, const char *old_key) {
    char k[64];
    int n = hlib_format(k, sizeof(k), "\"%s\"", key), m;
    if (n > 0 && n < (int)sizeof(k) - 1 && strstr(text, k)) return 1;
    m = hlib_format(k, sizeof(k), "\"%s\"", old_key);
    return m > 0 && m < (int)sizeof(k) - 1 && strstr(text, k) != NULL;
}

const char *hlib_json_end(const char *p, const char *e) {
    int depth = 0;
    for (; p < e; p++) {
        if (*p == '"') {
            for (p++; p < e && *p != '"'; p++)
                if (*p == '\\') p++;
            if (!depth) return p < e ? p + 1 : e;
        } else if (*p == '{' || *p == '[') {
            depth++;
        } else if (*p == '}' || *p == ']') {
            if (--depth <= 0) return depth < 0 ? p : p + 1;
        } else if (!depth && (*p == ',' || *p == '\n')) {
            return p;
        }
    }
    return e;
}

const char *hlib_json_next(const char *p, const char *e, char *key, int cap, const char **value) {
    while (p < e && (*p == '{' || *p == ',' || *p == ' ' || *p == '\n' || *p == '\r' || *p == '\t')) p++;
    if (p >= e || *p != '"') return NULL;
    int n = 0;
    for (p++; p < e && *p != '"'; p++)
        if (n < cap - 1) key[n++] = *p;
    key[n] = 0;
    p = skip_space(p + 1, e);
    if (p >= e || *p != ':') return NULL;
    *value = skip_space(p + 1, e);
    return hlib_json_end(*value, e);
}

long long hlib_json_int(const char *p, const char *e) {
    long long v = 0;
    int neg = p && p < e && *p == '-';
    if (neg) p++;
    while (p && p < e && *p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    return neg ? -v : v;
}

const char *hlib_json_ws(const char *p, const char *e) {
    while (p < e && (*p == ' ' || *p == '\n' || *p == '\r' || *p == '\t')) p++;
    return p;
}

const char *hlib_json_elem(const char *p, const char *e, const char **v) {
    p = hlib_json_ws(p, e);
    if (p < e && (*p == '[' || *p == ',')) p = hlib_json_ws(p + 1, e);
    if (p >= e || *p == ']') return NULL;
    *v = p;
    return hlib_json_end(p, e);
}

long long hlib_json_num(const char *obj, const char *e, const char *key, long long dflt) {
    const char *v = hlib_json_value(obj, e, key);
    return v && (*v == '-' || (*v >= '0' && *v <= '9')) ? hlib_json_int(v, e) : dflt;
}

void hlib_json_str(const char *obj, const char *e, const char *key, char *out, int cap) {
    const char *v = hlib_json_value(obj, e, key);
    int n = 0;
    out[0] = 0;
    if (!v || v >= e || *v != '"') return;
    for (v++; v < e && *v != '"'; v++)
        if (n < cap - 1) out[n++] = *v;
    out[n] = 0;
}

const char *hlib_json_arr(const char *obj, const char *e, const char *key, const char **end) {
    const char *v = hlib_json_value(obj, e, key);
    if (!v || *v != '[') return NULL;
    *end = hlib_json_end(v, e);
    return v;
}

int hlib_json_ints(const char *v, const char *e, int *out, int max) {
    const char *x, *p = v;
    int n = 0;
    while (v && *v == '[' && (p = hlib_json_elem(p, e, &x)) != NULL && n < max) out[n++] = (int)hlib_json_int(x, e);
    return n;
}

int hlib_world_of(uint32_t guid, uint32_t first, uint32_t last) {
    if (!guid) return HLIB_WORLD_UNKNOWN;
    return (guid >= first && guid <= last) ? HLIB_WORLD_OURS : HLIB_WORLD_VANILLA;
}

const char *hlib_world_name(int world) {
    return world == HLIB_WORLD_OURS ? "ours" : world == HLIB_WORLD_VANILLA ? "vanilla" : "?";
}

int hlib_u16_to_ascii(const uint16_t *p, size_t n, char *out, int cap) {
    int i = 0;
    for (; i < cap - 1 && (size_t)i < n && p[i]; i++) out[i] = (char)(p[i] < 128 ? p[i] : '?');
    out[i] = 0;
    return i;
}

static uint16_t low(uint16_t c) {
    if (c == '\\') c = '/';
    if (c >= 'A' && c <= 'Z') c += 32;
    return c;
}

int hlib_u16_path_eq(const uint16_t *p, size_t n, const uint16_t *cstr) {
    size_t k = 0;
    for (; k < n && cstr[k]; k++)
        if (low(p[k]) != low(cstr[k])) return 0;
    return k == n && !cstr[k];
}

const char *hlib_tail(const char *p) {
    const char *t = p;
    for (; *p; p++)
        if (*p == '/' || *p == '\\') t = p + 1;
    return t;
}

static const char *skip_space(const char *p, const char *end) {
    while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n')) p++;
    return p;
}

static int starts_at(const char *p, const char *end, const char *lit) {
    for (; *lit; lit++, p++)
        if (p >= end || *p != *lit) return 0;
    return 1;
}

static uint32_t digits_at(const char *p, const char *end, const char **after) {
    uint64_t v = 0;
    const char *q = p;
    while (q < end && *q >= '0' && *q <= '9' && v < 4294967296ULL) v = v * 10 + (uint64_t)(*q++ - '0');
    *after = q;
    return q == p || v > 4294967295ULL ? 0 : (uint32_t)v;
}

uint32_t hlib_name_source(const char *name) {
    const char *end = name + 64, *after;
    if (!starts_at(name, end, "a2070_")) return 0;
    uint32_t v = digits_at(name + 6, end, &after);
    return v && after < end && *after == '_' ? v : 0;
}

int hlib_scan_sources(const char *text, size_t len, HlibSourcePair *out, int max) {
    const char *end = text + len, *p = text, *after;
    int n = 0;
    while (n < max && (p = (const char *)memchr(p, '<', (size_t)(end - p))) != NULL) {
        if (!starts_at(p, end, "<GUID>")) {
            p++;
            continue;
        }
        uint32_t ours = digits_at(p + 6, end, &after);
        p = after;
        if (!ours || !starts_at(p, end, "</GUID>")) continue;
        p = skip_space(p + 7, end);
        if (!starts_at(p, end, "<Name>")) continue;
        uint32_t source = hlib_name_source(p + 6);
        if (source) {
            out[n].ours = ours;
            out[n].source = source;
            n++;
        }
    }
    return n;
}

uint32_t hlib_fnv1a_u32(uint32_t seed, uint32_t value) {
    uint32_t h = 2166136261u ^ seed;
    for (int i = 0; i < 4; i++) {
        h ^= (value >> (i * 8)) & 0xff;
        h *= 16777619u;
    }
    return h;
}

uint32_t hlib_fnv1a_text(const char *s) {
    uint32_t h = 2166136261u;
    for (; s && *s; s++) h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}

int hlib_text_piece(const char *text, int max) {
    int n = 0, cut = 0;
    while (text[n] && n < max) {
        if (text[n] == ' ') cut = n + 1;
        n++;
    }
    return !text[n] || !cut ? n : cut;
}

int hlib_kind_is_ai(int kind) { return kind == HLIB_PK_SECOND || kind == HLIB_PK_THIRD; }

int hlib_human_tally(int list_broken, int humans, int unreadable) { return list_broken ? 2 : humans + unreadable; }

int hlib_reload_holds(int pending, int started, uint64_t since_ms, uint64_t now_ms, uint64_t max_ms) {
    if (!pending) return 0;
    return started || now_ms < since_ms || now_ms - since_ms < max_ms;
}

int hlib_rival_rank(const uint32_t *chain, int n, uint32_t easy, uint32_t medium, uint32_t hard) {
    for (int i = 0; i < n; i++) {
        if (chain[i] == easy) return 0;
        if (chain[i] == medium) return 1;
        if (chain[i] == hard) return 2;
    }
    return -1;
}

/* mod profiles (profile.txt and profiles/<name>.txt): one entry per line, '#' in front disables */

typedef struct {
    const char *p;
    int n; /* line length without the line break */
} Line;

/* the next line of text from *at, 0 at the end */
static int next_line(const char **at, Line *l) {
    const char *s = *at;
    if (!*s) return 0;
    int n = 0;
    while (s[n] && s[n] != '\n') n++;
    l->p = s;
    l->n = n && s[n - 1] == '\r' ? n - 1 : n;
    *at = s[n] ? s + n + 1 : s + n;
    return 1;
}

static void trim(Line *l) {
    while (l->n && (*l->p == ' ' || *l->p == '\t')) l->p++, l->n--;
    while (l->n && (l->p[l->n - 1] == ' ' || l->p[l->n - 1] == '\t')) l->n--;
}

/* 1 when the line starts with word followed by a blank or the end; rest gets the remainder, trimmed */
static int starts_word(Line l, const char *word, Line *rest) {
    int k = 0;
    for (; word[k]; k++)
        if (k >= l.n || l.p[k] != word[k]) return 0;
    if (k < l.n && l.p[k] != ' ' && l.p[k] != '\t') return 0;
    rest->p = l.p + k;
    rest->n = l.n - k;
    trim(rest);
    return 1;
}

static int copy_out(Line v, char *out, int cap) {
    if (cap < 1) return -1;
    int k = 0;
    for (; k < v.n && k < cap - 1; k++) out[k] = v.p[k];
    out[k] = 0;
    return k;
}

static int line_is(Line l, const char *s) {
    int k = 0;
    for (; s[k]; k++)
        if (k >= l.n || l.p[k] != s[k]) return 0;
    return k == l.n;
}

#define PREVIOUS_TAG "## helper-library previous profile:"

int hlib_profile_active(const char *text, char *out, int cap) {
    Line l, v;
    if (cap > 0) out[0] = 0;
    for (const char *at = text; next_line(&at, &l);) {
        trim(&l);
        if (starts_word(l, "ActiveProfile", &v)) return copy_out(v, out, cap);
    }
    return 0;
}

int hlib_profile_previous(const char *text, char *out, int cap) {
    Line l, v;
    if (cap > 0) out[0] = 0;
    for (const char *at = text; next_line(&at, &l);) {
        trim(&l);
        if (starts_word(l, PREVIOUS_TAG, &v)) return copy_out(v, out, cap);
    }
    return -1;
}

int hlib_profile_enables(const char *list, const char *mod_id, int present) {
    Line l, v;
    int listed = 0, enabled = 0, new_mods = 0;
    for (const char *at = list; next_line(&at, &l);) {
        trim(&l);
        if (l.n >= 2 && l.p[0] == '#' && l.p[1] == '#') continue; /* a comment */
        int off = l.n && l.p[0] == '#';
        if (off) {
            l.p++, l.n--;
            trim(&l);
        }
        if (starts_word(l, "EnableNewMods", &v)) {
            new_mods = !off;
        } else if (line_is(l, mod_id)) {
            listed = 1;
            enabled = !off;
        }
    }
    return listed ? enabled : new_mods && present;
}

typedef struct {
    char *p;
    int n, cap, over;
} Buf;

static void put_n(Buf *b, const char *s, int n) {
    for (int i = 0; i < n; i++) {
        if (b->n + 1 >= b->cap) {
            b->over = 1;
            return;
        }
        b->p[b->n++] = s[i];
    }
    b->p[b->n] = 0;
}
static void put_s(Buf *b, const char *s) {
    int n = 0;
    while (s[n]) n++;
    put_n(b, s, n);
}

int hlib_profile_rewrite(const char *text, const char *name, const char *previous, char *out, int cap) {
    Buf b = {out, 0, cap, 0};
    Line l, v;
    const char *eol = "\n";
    for (const char *s = text; *s; s++)
        if (s[0] == '\r' && s[1] == '\n') {
            eol = "\r\n";
            break;
        }
    if (cap < 1) return -1;
    out[0] = 0;
    for (const char *at = text; next_line(&at, &l);) {
        Line t = l;
        trim(&t);
        if (starts_word(t, "ActiveProfile", &v) || starts_word(t, PREVIOUS_TAG, &v)) continue;
        put_n(&b, l.p, l.n);
        put_s(&b, eol);
    }
    if (previous && previous[0]) {
        put_s(&b, PREVIOUS_TAG " ");
        put_s(&b, previous);
        put_s(&b, eol);
    }
    put_s(&b, "ActiveProfile "); /* the last line, without a line break, as the game ships the file */
    put_s(&b, name ? name : "");
    return b.over ? -1 : b.n;
}

/* the mod loader's list (util.h) */

static int line_has(Line l, const char *s) {
    int k = 0;
    while (s[k]) k++;
    for (int i = 0; i + k <= l.n; i++)
        if (0 == memcmp(l.p + i, s, (size_t)k)) return 1;
    return 0;
}

int hlib_loader_log_enables(const char *log, const char *mod_id, int *count) {
    char tag[80];
    Line l;
    int found = 0, complete = 0;
    if (count) *count = -1;
    if (hlib_format(tag, sizeof(tag), "(%s@", mod_id) >= (int)sizeof(tag) - 1) return -1;
    for (const char *at = log; next_line(&at, &l);) {
        if (line_has(l, "Enabled mod: ") && line_has(l, tag)) found = 1;
        for (int i = 0; i + 14 <= l.n; i++)
            if (0 == memcmp(l.p + i, "Enabled mods: ", 14)) {
                complete = 1;
                if (count) *count = atoi(l.p + i + 14);
                break;
            }
    }
    return complete ? found : -1;
}

int hlib_title_buttons_add(const HlibTitleButton *b, int n, uint16_t type, uint64_t text, HlibTitleButton *out,
                            int cap) {
    for (int i = 0; i < n; i++)
        if (b[i].type == type && b[i].text == text) return 0;
    if (n < 0 || n + 1 > cap) return -1;
    if (n) memcpy(out, b, (size_t)n * sizeof(*b));
    memset(&out[n], 0, sizeof(out[n]));
    out[n].type = type;
    out[n].text = text;
    return n + 1;
}

int hlib_status_cfg_path(const uint16_t *s, size_t n) {
    static const char dir[] = "data/anno2070/graphics/ui/status/";
    const size_t m = sizeof(dir) - 1;
    for (size_t at = 0; s && at + m <= n; at++) {
        size_t k = 0;
        for (; k < m; k++) {
            uint16_t c = s[at + k];
            if (c == '\\') c = '/';
            if (c >= 'A' && c <= 'Z') c = (uint16_t)(c + 32);
            if (c != (uint16_t)dir[k]) break;
        }
        if (k == m) return 1;
    }
    return 0;
}

/* the session watch's records (util.h) */
void hlib_life_note(HlibLifeSlot *slots, int sid, void *key, void *ctx, long long now, HlibLifeEvent *ev) {
    ev->ended = NULL;
    ev->moved_from = -1;
    ev->fresh = 0;
    if (sid < 0 || sid >= HLIB_LIFE_SLOTS || !key) return;
    int was = -1;
    for (int i = 0; i < HLIB_LIFE_SLOTS && was < 0; i++)
        if (slots[i].key == key) was = i;
    if (slots[sid].key && slots[sid].key != key) ev->ended = slots[sid].key; /* another session took the slot */
    if (was >= 0 && was != sid) { /* the same session at another slot: its record moves */
        slots[sid] = slots[was];
        slots[was].key = slots[was].ctx = NULL;
        ev->moved_from = was;
    }
    ev->fresh = was < 0;
    slots[sid].key = key;
    if (ctx) slots[sid].ctx = ctx;
    slots[sid].last = now;
}

void *hlib_life_close(HlibLifeSlot *slots, int sid, void *key) {
    if (!key && sid >= 0 && sid < HLIB_LIFE_SLOTS) key = slots[sid].key;
    for (int i = 0; i < HLIB_LIFE_SLOTS; i++)
        if ((i == sid && (!key || slots[i].key == key)) || (key && slots[i].key == key)) slots[i].key = slots[i].ctx = NULL;
    return key;
}

/* the per-area draws (util.h): splitmix64 over the packed inputs, the range by a 64-bit product */
static unsigned long long mix64(unsigned long long z) {
    z += 0x9e3779b97f4a7c15ULL;
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}

/* The game salt (util.h): a game-wide value every draw mixes in; 0 mixes in nothing, the draws of before. */
static volatile uint32_t s_salt;
static volatile int s_salt_state;
static uint32_t s_salt_session; /* the session the forming salt came from */

uint32_t hlib_rng_salt(void) { return s_salt; }
int hlib_rng_salt_state(void) { return s_salt_state; }

uint32_t hlib_rng_salt_of(uint32_t map_seed, uint32_t session) {
    return (uint32_t)(mix64(((unsigned long long)map_seed << 32) | session) >> 32);
}

int hlib_rng_salt_from_map(uint32_t map_seed, uint32_t session, int sessions_alive) {
    if (s_salt_state == HLIB_SALT_RUNNING && !sessions_alive) s_salt_state = HLIB_SALT_NONE; /* a game over nobody saw */
    if (s_salt_state == HLIB_SALT_RUNNING) return 0;
    if (s_salt_state == HLIB_SALT_FORMING && session > s_salt_session) return 0;
    s_salt = hlib_rng_salt_of(map_seed, session);
    s_salt_session = session;
    s_salt_state = HLIB_SALT_FORMING;
    return 1;
}

int hlib_rng_salt_restore(uint32_t salt, int present) {
    if (!present && s_salt_state != HLIB_SALT_NONE) return 0; /* another session of this load had the block */
    int news = s_salt_state == HLIB_SALT_NONE || s_salt != (present ? salt : 0);
    s_salt = present ? salt : 0;
    s_salt_state = HLIB_SALT_RUNNING;
    return news;
}

void hlib_rng_salt_running(void) {
    if (s_salt_state == HLIB_SALT_FORMING) s_salt_state = HLIB_SALT_RUNNING;
}

void hlib_rng_salt_clear(void) {
    s_salt = 0;
    s_salt_session = 0;
    s_salt_state = HLIB_SALT_NONE;
}

uint32_t hlib_rng_mix(uint32_t area, uint32_t stream, long long time, uint32_t idx, uint32_t upper) {
    if (upper <= 1) return 0;
    uint32_t salt = s_salt;
    unsigned long long h = mix64(((unsigned long long)area << 32) | stream);
    if (salt) h = mix64(h ^ mix64(0x2070b1a5ULL << 32 | salt));
    h = mix64(h ^ (unsigned long long)time);
    h = mix64(h ^ idx);
    return (uint32_t)(((h >> 32) * (unsigned long long)upper) >> 32);
}

uint32_t hlib_rng_draw(HlibRngCursor *c, uint32_t area, uint32_t stream, long long time, uint32_t upper) {
    if (c->time != time) c->time = time, c->idx = 0;
    return hlib_rng_mix(area, stream, time, c->idx++, upper);
}

/* Numbers from the mods' text: a decimal point always, whatever locale any thread set. Up to 19 significant
   digits as an integer, then one exact power of ten when it fits (correctly rounded), otherwise a fixed
   product; either way the same bits on every machine. */
static const double k_pow10[23] = {1e0,  1e1,  1e2,  1e3,  1e4,  1e5,  1e6,  1e7,  1e8,  1e9,  1e10, 1e11,
                                   1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18, 1e19, 1e20, 1e21, 1e22};

double hlib_strtod(const char *s, const char **end) {
    const char *p = s;
    unsigned long long m = 0;
    int exp = 0, digits = 0, neg = 0;
    if (end) *end = s;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    if (*p == '-' || *p == '+') neg = *p++ == '-';
    for (; *p >= '0' && *p <= '9'; p++, digits++)
        if (m < 1000000000000000000ULL) m = m * 10 + (unsigned)(*p - '0');
        else exp++;
    if (*p == '.')
        for (p++; *p >= '0' && *p <= '9'; p++, digits++)
            if (m < 1000000000000000000ULL) m = m * 10 + (unsigned)(*p - '0'), exp--;
    if (!digits) return 0.0;
    if (*p == 'e' || *p == 'E') {
        const char *q = p + 1;
        int eneg = 0, e = 0, any = 0;
        if (*q == '-' || *q == '+') eneg = *q++ == '-';
        for (; *q >= '0' && *q <= '9'; q++, any = 1)
            if (e < 10000) e = e * 10 + (*q - '0');
        if (any) exp += eneg ? -e : e, p = q;
    }
    if (end) *end = p;
    double v = (double)m;
    if (m == 0) return neg ? -0.0 : 0.0;
    if (exp > 0) {
        for (; exp > 22 && v < 1e300; exp -= 22) v *= k_pow10[22];
        v *= exp > 22 ? 1e300 * 1e300 : k_pow10[exp];
    } else if (exp < 0) {
        for (; exp < -22 && v > 1e-300; exp += 22) v /= k_pow10[22];
        v /= exp < -22 ? 1e300 : k_pow10[-exp];
    }
    return neg ? -v : v;
}

double hlib_atof(const char *s) { return hlib_strtod(s, NULL); }

/* Trigonometry for values the simulation keeps (tree positions, range overlaps): additions and products only,
   so every peer computes the same bits; the C library's versions may pick a different code path per CPU. */
#define HLIB_PI 3.14159265358979323846
static double sin_core(double x) { /* |x| <= pi/4 */
    double x2 = x * x;
    return x * (1 + x2 * (-1.0 / 6 + x2 * (1.0 / 120 + x2 * (-1.0 / 5040 + x2 * (1.0 / 362880 +
              x2 * (-1.0 / 39916800 + x2 * (1.0 / 6227020800.0 + x2 * (-1.0 / 1307674368000.0))))))));
}
static double cos_core(double x) {
    double x2 = x * x;
    return 1 + x2 * (-0.5 + x2 * (1.0 / 24 + x2 * (-1.0 / 720 + x2 * (1.0 / 40320 + x2 * (-1.0 / 3628800 +
              x2 * (1.0 / 479001600 + x2 * (-1.0 / 87178291200.0 + x2 * (1.0 / 20922789888000.0))))))));
}

/* x as k * pi/2 + r, |r| <= pi/4; pi/2 in three parts keeps r exact enough for the angles we meet */
static double reduce(double x, int *quadrant) {
    const double p1 = 1.5707963267341256, p2 = 6.077100506303966e-11, p3 = 2.0222662487959506e-21;
    double k = x * (2 / HLIB_PI);
    long long n = (long long)(k < 0 ? k - 0.5 : k + 0.5);
    double fn = (double)n;
    *quadrant = (int)(n & 3);
    return ((x - fn * p1) - fn * p2) - fn * p3;
}

double hlib_sin(double x) {
    int q;
    double r = reduce(x, &q);
    return q == 0 ? sin_core(r) : q == 1 ? cos_core(r) : q == 2 ? -sin_core(r) : -cos_core(r);
}

double hlib_cos(double x) {
    int q;
    double r = reduce(x, &q);
    return q == 0 ? cos_core(r) : q == 1 ? -sin_core(r) : q == 2 ? -cos_core(r) : sin_core(r);
}

static double atan_small(double t) { /* |t| <= 2 - sqrt(3) */
    double t2 = t * t, sum = 0;
    for (int k = 27; k >= 1; k -= 2) sum = 1.0 / k - t2 * sum;
    return t * sum;
}

static double atan_pos(double t) { /* t >= 0 */
    const double sqrt3 = 1.7320508075688772, tan15 = 0.2679491924311228;
    int inv = t > 1;
    if (inv) t = 1 / t;
    double a = t > tan15 ? HLIB_PI / 6 + atan_small((sqrt3 * t - 1) / (sqrt3 + t)) : atan_small(t);
    return inv ? HLIB_PI / 2 - a : a;
}

double hlib_acos(double x) {
    if (!(x > -1)) return HLIB_PI; /* also NaN: the far side, never a NaN into the simulation */
    if (x >= 1) return 0;
    return 2 * atan_pos(sqrt((1 - x) / (1 + x))); /* sqrt is exact (IEEE) on every machine */
}

long long hlib_game_time_pick(int has_ctx, int state, long long ctx_ms, long long global_ms) {
    if (!has_ctx || state == 8) return -1;
    if (state == 5 || state == 6) return global_ms >= 0 ? global_ms : -1;
    return ctx_ms;
}

static int perf_cmp(const void *a, const void *b) {
    long long x = ((const HlibPerfRow *)a)->inside_us, y = ((const HlibPerfRow *)b)->inside_us;
    return x < y ? 1 : x > y ? -1 : 0;
}

int hlib_perf_line(HlibPerfRow *rows, int n, int top, long long min_us, int seconds, char *out, int cap) {
    int at = 0, shown = 0;
    if (cap < 1) return 0;
    out[0] = 0;
    qsort(rows, (size_t)n, sizeof(*rows), perf_cmp);
    for (int i = 0; i < n && shown < top; i++) {
        const HlibPerfRow *r = &rows[i];
        if (!r->calls || r->inside_us < min_us) continue;
        if (!shown) at += hlib_format(out + at, cap - at, "perf: last %d s:", seconds);
        at += hlib_format(out + at, cap - at, "%s %s %.1f ms in %lld call(s) (max %.2f ms, waited %.1f ms)", shown ? ";" : "",
                           r->name, r->inside_us / 1000.0, r->calls, r->max_us / 1000.0, r->wait_us / 1000.0);
        shown++;
    }
    return shown;
}

int hlib_switch_step(int state, int event) {
    switch (event) {
    case HLIB_SW_EV_CLICK:
        return state == HLIB_SW_IDLE ? HLIB_SW_ASKING : state;
    case HLIB_SW_EV_CANCEL:
    case HLIB_SW_EV_FAILED:
        return state == HLIB_SW_ASKING ? HLIB_SW_IDLE : state;
    case HLIB_SW_EV_WRITTEN:
        return state == HLIB_SW_ASKING ? HLIB_SW_QUIT : state;
    case HLIB_SW_EV_TITLE:
        return state == HLIB_SW_QUIT ? HLIB_SW_LEAVING : state;
    }
    return state;
}

int hlib_popup_listed(const unsigned char *begin, const unsigned char *end, unsigned stride, int id) {
    if (!begin || !id || end <= begin || stride < sizeof(int32_t)) return 0;
    size_t n = (size_t)(end - begin) / stride;
    for (size_t i = 0; i < n && i < 64; i++) {
        int32_t v;
        memcpy(&v, begin + i * stride, sizeof(v));
        if (v == id) return 1;
    }
    return 0;
}

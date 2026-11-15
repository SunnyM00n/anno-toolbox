/* Live reload, the pure part (reload_logic.h). Host-tested by reload_test.c. */
#include "reload_logic.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char lower(char c) { return c >= 'A' && c <= 'Z' ? (char)(c - 'A' + 'a') : c; }

static int ends_with(const char *s, const char *tail) {
    size_t n = strlen(s), k = strlen(tail);
    if (k > n) return 0;
    for (size_t i = 0; i < k; i++)
        if (lower(s[n - k + i]) != tail[i]) return 0;
    return 1;
}

const char *rl_kind_name(int kind) {
    switch (kind) {
    case RL_MODEL: return "model";
    case RL_CONFIG: return "config";
    case RL_EFFECT: return "effect";
    case RL_INCLUDE: return "include";
    }
    return "file";
}

int rl_kind_of(const char *path) {
    if (!path) return RL_NONE;
    if (ends_with(path, ".rdm")) return RL_MODEL;
    if (ends_with(path, ".cfg")) return RL_CONFIG;
    if (ends_with(path, ".fx")) return RL_EFFECT;
    if (ends_with(path, ".fxh") || ends_with(path, ".fxs")) return RL_INCLUDE;
    return RL_NONE;
}

static int path_char(char c) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '/' || c == '-';
}

int rl_norm_path(const char *in, char *out, int cap) {
    int n = 0;
    if (!in || !out || cap < 2) return 0;
    while (*in == ' ' || *in == '\t') in++;
    while (in[0] == '.' && (in[1] == '/' || in[1] == '\\')) in += 2;
    while (*in == '/' || *in == '\\') in++;
    for (; *in && *in != '\r' && *in != '\n'; in++) {
        char c = *in == '\\' ? '/' : lower(*in);
        if (n + 1 >= cap) return 0;
        out[n++] = c;
    }
    while (n > 0 && (out[n - 1] == ' ' || out[n - 1] == '\t')) n--;
    out[n] = 0;
    if (!n || out[n - 1] == '/') return 0;
    for (int i = 0; i < n; i++) {
        if (!path_char(out[i])) return 0;
        if (out[i] == '/' && out[i + 1] == '/') return 0;
        int start = i == 0 || out[i - 1] == '/';
        if (start && out[i] == '.' && out[i + 1] == '.' && (out[i + 2] == '/' || !out[i + 2])) return 0;
    }
    return 1;
}

void rl_model_key(const char *path, char *out, int cap) {
    int n = 0;
    if (cap < 1) return;
    for (int i = 0; path[i] && n + 1 < cap; i++) {
        out[n++] = path[i];
        if (i >= 3 && 0 == memcmp(path + i - 3, "_lod", 4) && path[i + 1] >= '0' && path[i + 1] <= '9' &&
            path[i + 2] == '.') {
            if (n + 1 < cap) out[n++] = '0';
            i++; /* the digit */
        }
    }
    out[n] = 0;
}

/* the loader's list */

static const char *find_in_line(const char *p, const char *eol, const char *s) {
    size_t k = strlen(s);
    for (; p + k <= eol; p++)
        if (0 == memcmp(p, s, k)) return p;
    return 0;
}

static void copy_folder(char *out, const char *p, const char *e) {
    int n = 0;
    while (p < e && (*p == ' ' || *p == '\t')) p++;
    while (e > p && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '/' || e[-1] == '\\')) e--;
    for (; p < e && n + 1 < RL_PATH_MAX; p++) out[n++] = *p == '\\' ? '/' : *p;
    out[n] = 0;
}

int rl_parse_loader_log(const char *log, RLMods *out) {
    memset(out, 0, sizeof(*out));
    for (const char *line = log; line && *line;) {
        const char *eol = strchr(line, '\n');
        if (!eol) eol = line + strlen(line);
        const char *at = find_in_line(line, eol, "Enabled mod: ");
        if (at && out->n < RL_MODS_MAX) {
            const char *open = 0;
            for (const char *p = eol - 1; p > at; p--)
                if (*p == '(') {
                    open = p;
                    break;
                }
            const char *amp = open ? find_in_line(open, eol, "@") : 0;
            if (open && amp && amp - open - 1 > 0 && amp - open - 1 < (int)sizeof(out->mod[0].id)) {
                RLMod *m = &out->mod[out->n++];
                memcpy(m->id, open + 1, (size_t)(amp - open - 1));
                m->id[amp - open - 1] = 0;
                copy_folder(m->folder, at + 13, open);
            }
        }
        if (find_in_line(line, eol, "Enabled mods: ")) out->complete = 1;
        line = *eol ? eol + 1 : eol;
    }
    return out->n;
}

int rl_mod_by_id(const RLMods *m, const char *id) {
    for (int i = 0; i < m->n; i++)
        if (0 == strcmp(m->mod[i].id, id)) return i;
    return -1;
}

int rl_mod_of_path(const RLMods *m, const char *abs, char *rel, int cap) {
    for (int i = 0; i < m->n; i++) {
        const char *f = m->mod[i].folder;
        size_t k = strlen(f), j = 0;
        if (!k) continue;
        for (; j < k && abs[j]; j++) {
            char a = abs[j] == '\\' ? '/' : lower(abs[j]), b = f[j] == '\\' ? '/' : lower(f[j]);
            if (a != b) break;
        }
        if (j == k && (abs[k] == '/' || abs[k] == '\\') && rl_norm_path(abs + k + 1, rel, cap)) return i;
    }
    return -1;
}

/* the queue */

int rl_queue_push(RLQueue *q, int kind, int mod, const char *path, uint32_t batch) {
    for (int i = 0; i < q->n; i++) {
        const RLRequest *r = &q->item[(q->head + i) % RL_QUEUE_MAX];
        if (r->kind == kind && r->mod == mod && 0 == strcmp(r->path, path)) return 0;
    }
    if (q->n >= RL_QUEUE_MAX || strlen(path) >= RL_PATH_MAX) return -1;
    RLRequest *r = &q->item[(q->head + q->n) % RL_QUEUE_MAX];
    memset(r, 0, sizeof(*r));
    r->kind = kind;
    r->mod = mod;
    r->batch = batch;
    memcpy(r->path, path, strlen(path) + 1);
    q->n++;
    return 1;
}

RLRequest *rl_queue_front(RLQueue *q) { return q->n ? &q->item[q->head] : 0; }

void rl_queue_pop(RLQueue *q) {
    if (!q->n) return;
    q->head = (q->head + 1) % RL_QUEUE_MAX;
    q->n--;
}

int rl_queue_batch(RLQueue *q, const RLRequest *items, int n, uint32_t batch) {
    static const int order[] = {RL_MODEL, RL_CONFIG, RL_EFFECT};
    int queued = 0;
    for (int o = 0; o < 3; o++)
        for (int i = 0; i < n; i++)
            if (items[i].kind == order[o] && rl_queue_push(q, items[i].kind, items[i].mod, items[i].path, batch) == 1)
                queued++;
    return queued;
}

int rl_plan_batch(const RLMods *m, const char *id, const char *const *paths, int n, uint32_t batch, RLHasFileFn has_file,
                  void *ctx, RLRequest *items, RLRefused *refused, int cap, int *nrefused) {
    int k = 0, named = id && *id;
    *nrefused = 0;
    for (int i = 0; i < n; i++) {
        char rel[RL_PATH_MAX];
        int mod = rl_mod_of_path(m, paths[i], rel, sizeof rel), kind;
        if (mod < 0 && rl_norm_path(paths[i], rel, sizeof rel)) {
            mod = named ? rl_mod_by_id(m, id) : -1;
            for (int j = 0; mod < 0 && !named && j < m->n; j++)
                if (has_file(j, rel, ctx)) mod = j;
        }
        kind = mod >= 0 ? rl_kind_of(rel) : RL_NONE;
        if (mod < 0 || kind == RL_NONE || kind == RL_INCLUDE) {
            RLRefused *r = *nrefused < cap ? &refused[(*nrefused)++] : NULL;
            if (!r) continue;
            snprintf(r->path, sizeof r->path, "%s", paths[i]);
            r->why = mod < 0 ? "not in the folder of an enabled mod"
                     : kind == RL_INCLUDE ? "an include: name the effect files that include it"
                                          : "not a model, config or effect file";
            r->batch = batch;
            continue;
        }
        if (k >= cap) continue;
        memset(&items[k], 0, sizeof items[k]);
        items[k].kind = kind, items[k].mod = mod, items[k].batch = batch;
        snprintf(items[k].path, sizeof items[k].path, "%s", rel);
        k++;
    }
    return k;
}

/* the event record */

int rl_widen(const char *s, uint16_t *out, int cap) {
    int n = 0;
    for (; s[n]; n++) {
        if (n + 1 >= cap || (unsigned char)s[n] > 0x7e) return -1;
        out[n] = (uint16_t)(unsigned char)s[n];
    }
    out[n] = 0;
    return n;
}

int rl_event_build(unsigned char *rec, const RLEventLayout *l, const uint16_t *path, int len, uint16_t *heap) {
    if (!rec || !l || !path || len <= 0 || l->path_off + l->cap_off + 8 > l->bytes || l->kind_off + 4 > l->bytes)
        return 0;
    memset(rec, 0, (size_t)l->bytes);
    unsigned char *s = rec + l->path_off;
    uint64_t size = (uint64_t)len, capacity = (uint64_t)(len > l->inline_chars ? len : l->inline_chars);
    if (len > l->inline_chars) {
        if (!heap) return 0;
        memcpy(heap, path, (size_t)len * 2);
        heap[len] = 0;
        memcpy(s, &heap, sizeof(heap));
    } else {
        memcpy(s, path, (size_t)len * 2);
        ((uint16_t *)s)[len] = 0;
    }
    memcpy(s + l->size_off, &size, 8);
    memcpy(s + l->cap_off, &capacity, 8);
    memcpy(rec + l->kind_off, &l->kind_value, 4);
    return 1;
}

/* the scan */

uint32_t rl_hash(const char *s) {
    uint32_t h = 2166136261u;
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 16777619u;
    return h;
}

void rl_snap_init(RLSnap *s) { memset(s, 0, sizeof(*s)); }

void rl_snap_free(RLSnap *s) {
    free(s->file);
    free(s->pool);
    rl_snap_init(s);
}

int rl_snap_add(RLSnap *s, int mod, const char *rel, uint64_t mtime) {
    char p[RL_PATH_MAX];
    int kind = rl_kind_of(rel);
    if (kind == RL_NONE || !rl_norm_path(rel, p, sizeof p)) return 0;
    size_t k = strlen(p) + 1;
    if (s->n == s->cap) {
        int cap = s->cap ? s->cap * 2 : 1024;
        RLFile *f = (RLFile *)realloc(s->file, (size_t)cap * sizeof(RLFile));
        if (!f) return -1;
        s->file = f;
        s->cap = cap;
    }
    if (s->pool_n + k > s->pool_cap) {
        size_t cap = s->pool_cap ? s->pool_cap * 2 : 65536;
        while (cap < s->pool_n + k) cap *= 2;
        char *q = (char *)realloc(s->pool, cap);
        if (!q) return -1;
        s->pool = q;
        s->pool_cap = cap;
    }
    memcpy(s->pool + s->pool_n, p, k);
    RLFile *f = &s->file[s->n++];
    f->mtime = mtime;
    f->hash = rl_hash(p);
    f->path_at = (uint32_t)s->pool_n;
    f->mod = (uint16_t)mod;
    f->kind = (uint8_t)kind;
    s->pool_n += k;
    s->sorted = 0;
    return 1;
}

const char *rl_snap_path(const RLSnap *s, const RLFile *f) { return s->pool + f->path_at; }

static const RLSnap *g_sort_snap;

static int cmp_file(const RLFile *a, const RLFile *b, const RLSnap *sa, const RLSnap *sb) {
    if (a->mod != b->mod) return a->mod < b->mod ? -1 : 1;
    if (a->hash != b->hash) return a->hash < b->hash ? -1 : 1;
    return strcmp(rl_snap_path(sa, a), rl_snap_path(sb, b));
}

static int cmp_sort(const void *a, const void *b) {
    return cmp_file((const RLFile *)a, (const RLFile *)b, g_sort_snap, g_sort_snap);
}

void rl_snap_sort(RLSnap *s) {
    if (s->sorted) return;
    g_sort_snap = s; /* the scan thread is the only caller */
    if (s->n > 1) qsort(s->file, (size_t)s->n, sizeof(RLFile), cmp_sort);
    s->sorted = 1;
}

void rl_snap_move(RLSnap *to, RLSnap *from) {
    rl_snap_free(to);
    *to = *from;
    rl_snap_init(from);
}

static const RLFile *snap_find(const RLSnap *s, const RLFile *key, const RLSnap *key_snap) {
    int lo = 0, hi = s->n - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2, c = cmp_file(&s->file[mid], key, s, key_snap);
        if (!c) return &s->file[mid];
        if (c < 0) lo = mid + 1;
        else hi = mid - 1;
    }
    return 0;
}

static int put_change(RLRequest *out, int n, int cap, int kind, int mod, const char *path) {
    for (int i = 0; i < n; i++)
        if (out[i].kind == kind && out[i].mod == mod && 0 == strcmp(out[i].path, path)) return n;
    if (n >= cap) return n;
    memset(&out[n], 0, sizeof(out[n]));
    out[n].kind = kind;
    out[n].mod = mod;
    memcpy(out[n].path, path, strlen(path) + 1); /* the scan's paths are shorter than RL_PATH_MAX */
    return n + 1;
}

int rl_snap_changes(const RLSnap *last, RLSnap *now, RLRequest *out, int cap, RLIncludesFn includes, void *ctx) {
    int n = 0;
    rl_snap_sort(now);
    for (int i = 0; i < now->n; i++) {
        const RLFile *f = &now->file[i], *was = last ? snap_find(last, f, now) : 0;
        if (was && was->mtime == f->mtime) continue;
        const char *p = rl_snap_path(now, f);
        if (f->kind != RL_INCLUDE) {
            n = put_change(out, n, cap, f->kind, f->mod, p);
            continue;
        }
        for (int j = 0; j < now->n && includes; j++) {
            const RLFile *fx = &now->file[j];
            if (fx->kind == RL_EFFECT && fx->mod == f->mod && includes(f->mod, rl_snap_path(now, fx), p, ctx))
                n = put_change(out, n, cap, RL_EFFECT, fx->mod, rl_snap_path(now, fx));
        }
    }
    return n;
}

/* the include rule */

static const char *base_name(const char *p) {
    const char *b = p;
    for (; *p; p++)
        if (*p == '/' || *p == '\\') b = p + 1;
    return b;
}

int rl_fx_includes(const char *text, size_t len, const char *fxh_path) {
    const char *want = base_name(fxh_path);
    size_t wn = strlen(want);
    if (!text || !wn) return 0;
    for (size_t i = 0; i + 8 < len; i++) {
        if (text[i] != '#' || 0 != memcmp(text + i + 1, "include", 7)) continue;
        size_t j = i + 8, q0 = 0, q1 = 0;
        while (j < len && text[j] != '\n' && !q1) {
            if (text[j] == '"' || text[j] == '<' || text[j] == '>') {
                if (!q0) q0 = j + 1;
                else q1 = j;
            }
            j++;
        }
        if (!q0 || !q1 || q1 - q0 < wn) continue;
        size_t k = 0;
        for (; k < wn && lower(text[q1 - wn + k]) == lower(want[k]); k++) {
        }
        if (k == wn && (q1 - wn == q0 || text[q1 - wn - 1] == '/' || text[q1 - wn - 1] == '\\')) return 1;
    }
    return 0;
}

/* the trigger file */

int rl_trigger_lines(const char *text, RLLineFn fn, void *ctx) {
    int n = 0;
    for (const char *line = text; line && *line;) {
        const char *eol = strchr(line, '\n');
        if (!eol) eol = line + strlen(line);
        char buf[RL_PATH_MAX + 64], id[64] = "";
        size_t k = (size_t)(eol - line);
        if (k < sizeof(buf)) {
            memcpy(buf, line, k);
            buf[k] = 0;
            char *hash = strchr(buf, '#'), *p = buf;
            if (hash) *hash = 0;
            while (*p == ' ' || *p == '\t' || *p == '\r') p++;
            char *colon = strchr(p, ':');
            if (colon && colon - p > 1 && colon - p < (int)sizeof(id)) { /* "x:" alone would be a drive letter */
                memcpy(id, p, (size_t)(colon - p));
                id[colon - p] = 0;
                p = colon + 1;
            }
            char *e = p + strlen(p);
            while (e > p && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r')) *--e = 0;
            if (*p) {
                if (fn) fn(id, p, ctx);
                n++;
            }
        }
        line = *eol ? eol + 1 : eol;
    }
    return n;
}

/* the decision */

int rl_decide(const RLState *s) {
    if (s->busy) return s->tries >= s->max_wait ? RL_REFUSE_TIMEOUT : RL_WAIT; /* the player count is not sure then */
    if (s->multiplayer) return RL_REFUSE_MULTIPLAYER;
    if (!s->file_ok) return RL_REFUSE_FILE;
    if (s->loading || (s->found && s->streaming)) return s->tries >= s->max_wait ? RL_REFUSE_TIMEOUT : RL_WAIT;
    if (!s->found) return RL_SKIP_NOT_LOADED;
    return RL_RUN;
}

const char *rl_decision_name(int d) {
    switch (d) {
    case RL_RUN: return "run";
    case RL_WAIT: return "wait";
    case RL_SKIP_NOT_LOADED: return "not loaded";
    case RL_REFUSE_MULTIPLAYER: return "refused: a multiplayer game runs";
    case RL_REFUSE_FILE: return "refused: the file is missing or empty";
    case RL_REFUSE_TIMEOUT: return "refused: still loading after the wait";
    }
    return "?";
}

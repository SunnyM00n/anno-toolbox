/* The live link's pure part (livelink_logic.h): hashes, frames, JSON, the message set, answers, templates, the log. */
#include "livelink_logic.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* SHA-256 */

static const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
    0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
    0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha_block(LLSha *s, const uint8_t *p) {
    uint32_t w[64], a, b, c, d, e, f, g, h;
    for (int i = 0; i < 16; i++)
        w[i] = (uint32_t)p[i * 4] << 24 | (uint32_t)p[i * 4 + 1] << 16 | (uint32_t)p[i * 4 + 2] << 8 | p[i * 4 + 3];
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4], f = s->h[5], g = s->h[6], h = s->h[7];
    for (int i = 0; i < 64; i++) {
        uint32_t t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g, g = f, f = e, e = d + t1, d = c, c = b, b = a, a = t1 + t2;
    }
    s->h[0] += a, s->h[1] += b, s->h[2] += c, s->h[3] += d, s->h[4] += e, s->h[5] += f, s->h[6] += g, s->h[7] += h;
}

void ll_sha_init(LLSha *s) {
    static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    memcpy(s->h, init, sizeof init);
    s->n = 0;
    s->fill = 0;
}

void ll_sha_update(LLSha *s, const void *data, size_t n) {
    const uint8_t *p = (const uint8_t *)data;
    s->n += n;
    while (n) {
        size_t take = 64 - s->fill < n ? 64 - s->fill : n;
        memcpy(s->buf + s->fill, p, take);
        s->fill += take, p += take, n -= take;
        if (s->fill == 64) sha_block(s, s->buf), s->fill = 0;
    }
}

void ll_sha_final(LLSha *s, uint8_t out[32]) {
    uint64_t bits = s->n * 8;
    uint8_t pad = 0x80, zero = 0, len[8];
    ll_sha_update(s, &pad, 1);
    while (s->fill != 56) ll_sha_update(s, &zero, 1);
    for (int i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56 - 8 * i));
    ll_sha_update(s, len, 8);
    for (int i = 0; i < 8; i++)
        out[i * 4] = (uint8_t)(s->h[i] >> 24), out[i * 4 + 1] = (uint8_t)(s->h[i] >> 16),
        out[i * 4 + 2] = (uint8_t)(s->h[i] >> 8), out[i * 4 + 3] = (uint8_t)s->h[i];
}

void ll_hmac_sha256(const void *key, size_t klen, const void *msg, size_t mlen, uint8_t out[32]) {
    uint8_t k[64] = {0}, ipad[64], opad[64], inner[32];
    LLSha s;
    if (klen > 64) ll_sha_init(&s), ll_sha_update(&s, key, klen), ll_sha_final(&s, k);
    else memcpy(k, key, klen);
    for (int i = 0; i < 64; i++) ipad[i] = k[i] ^ 0x36, opad[i] = k[i] ^ 0x5c;
    ll_sha_init(&s), ll_sha_update(&s, ipad, 64), ll_sha_update(&s, msg, mlen), ll_sha_final(&s, inner);
    ll_sha_init(&s), ll_sha_update(&s, opad, 64), ll_sha_update(&s, inner, 32), ll_sha_final(&s, out);
}

void ll_hex(const uint8_t *in, size_t n, char *out) {
    static const char d[] = "0123456789abcdef";
    for (size_t i = 0; i < n; i++) out[i * 2] = d[in[i] >> 4], out[i * 2 + 1] = d[in[i] & 15];
    out[n * 2] = 0;
}

void ll_proof(const char *token, const char *purpose, const char *value, char out[65]) {
    char msg[256];
    uint8_t mac[32];
    int n = snprintf(msg, sizeof msg, "%s:%s", purpose, value);
    if (n < 0 || n >= (int)sizeof msg) n = (int)sizeof msg - 1;
    ll_hmac_sha256(token, strlen(token), msg, (size_t)n, mac);
    ll_hex(mac, 32, out);
}

int ll_proof_ok(const char *token, const char *purpose, const char *value, const char *given) {
    char want[65];
    unsigned diff = 0;
    ll_proof(token, purpose, value, want);
    if (!given || strlen(given) != 64) return 0;
    for (int i = 0; i < 64; i++) {
        char c = given[i];
        if (c >= 'A' && c <= 'F') c = (char)(c - 'A' + 'a');
        diff |= (unsigned)(c ^ want[i]);
    }
    return diff == 0;
}

/* frames */

void ll_inbuf_init(LLInbuf *b) { b->data = NULL, b->n = b->cap = 0; }
void ll_inbuf_free(LLInbuf *b) {
    free(b->data);
    ll_inbuf_init(b);
}

int ll_inbuf_feed(LLInbuf *b, const void *data, size_t n) {
    if (b->n + n > b->cap) {
        size_t cap = b->cap ? b->cap : 4096;
        uint8_t *grown;
        while (cap < b->n + n) cap *= 2;
        if (cap > (size_t)LL_REQ_MAX * 2 + 8192) return 0; /* a peer that sends no end to a frame */
        grown = (uint8_t *)realloc(b->data, cap);
        if (!grown) return 0;
        b->data = grown, b->cap = cap;
    }
    memcpy(b->data + b->n, data, n);
    b->n += n;
    return 1;
}

int ll_inbuf_next(LLInbuf *b, char *out, size_t cap, size_t *len) {
    uint32_t size;
    if (b->n < 4) return 0;
    size = (uint32_t)b->data[0] | (uint32_t)b->data[1] << 8 | (uint32_t)b->data[2] << 16 | (uint32_t)b->data[3] << 24;
    if (size > LL_REQ_MAX || size + 1 > cap) return -1;
    if (b->n < 4 + (size_t)size) return 0;
    memcpy(out, b->data + 4, size);
    out[size] = 0;
    *len = size;
    memmove(b->data, b->data + 4 + size, b->n - 4 - size);
    b->n -= 4 + size;
    return 1;
}

size_t ll_frame_wrap(const char *json, size_t len, uint8_t *out, size_t cap) {
    if (len > LL_MAX_FRAME || cap < len + 4) return 0;
    out[0] = (uint8_t)len, out[1] = (uint8_t)(len >> 8), out[2] = (uint8_t)(len >> 16), out[3] = (uint8_t)(len >> 24);
    memcpy(out + 4, json, len);
    return len + 4;
}

/* JSON */

typedef struct {
    const char *s;
    size_t len, pos;
    LLTok *tok;
    int max, n, depth;
} JP;

static void ws(JP *p) {
    while (p->pos < p->len && (p->s[p->pos] == ' ' || p->s[p->pos] == '\t' || p->s[p->pos] == '\r' || p->s[p->pos] == '\n'))
        p->pos++;
}

static int add(JP *p, int type, size_t start) {
    if (p->n >= p->max) return -1;
    p->tok[p->n].type = type, p->tok[p->n].start = (int)start, p->tok[p->n].end = (int)start, p->tok[p->n].size = 0;
    return p->n++;
}

static int jstring(JP *p) {
    size_t start = p->pos + 1;
    p->pos++;
    while (p->pos < p->len) {
        unsigned char c = (unsigned char)p->s[p->pos];
        if (c == '"') {
            int t = add(p, LLJ_STR, start);
            if (t < 0) return -1;
            p->tok[t].end = (int)p->pos;
            p->pos++;
            return t;
        }
        if (c < 0x20) return -1;
        if (c == '\\') {
            p->pos++;
            if (p->pos >= p->len) return -1;
            if (p->s[p->pos] == 'u') {
                if (p->pos + 4 >= p->len) return -1;
                for (int i = 1; i <= 4; i++)
                    if (!strchr("0123456789abcdefABCDEF", p->s[p->pos + (size_t)i])) return -1;
                p->pos += 4;
            }
        }
        p->pos++;
    }
    return -1;
}

static int jvalue(JP *p);

static int jcontainer(JP *p, int type, char close) {
    int t = add(p, type, p->pos);
    if (t < 0 || p->depth >= 8) return -1;
    p->depth++;
    p->pos++;
    ws(p);
    if (p->pos < p->len && p->s[p->pos] == close) {
        p->pos++;
    } else {
        for (;;) {
            ws(p);
            if (type == LLJ_OBJ) {
                int k;
                if (p->pos >= p->len || p->s[p->pos] != '"' || (k = jstring(p)) < 0) return -1;
                ws(p);
                if (p->pos >= p->len || p->s[p->pos] != ':') return -1;
                p->pos++, p->tok[k].size = 1;
            }
            if (jvalue(p) < 0) return -1;
            p->tok[t].size++;
            ws(p);
            if (p->pos >= p->len) return -1;
            if (p->s[p->pos] == ',') {
                p->pos++;
                continue;
            }
            if (p->s[p->pos] != close) return -1;
            p->pos++;
            break;
        }
    }
    p->tok[t].end = (int)p->pos;
    p->depth--;
    return t;
}

static int jvalue(JP *p) {
    char c;
    size_t start;
    int t;
    ws(p);
    if (p->pos >= p->len) return -1;
    c = p->s[p->pos], start = p->pos;
    if (c == '{') return jcontainer(p, LLJ_OBJ, '}');
    if (c == '[') return jcontainer(p, LLJ_ARR, ']');
    if (c == '"') return jstring(p);
    if (p->len - start >= 4 && !strncmp(p->s + start, "true", 4)) {
        t = add(p, LLJ_BOOL, start), p->pos += 4;
    } else if (p->len - start >= 5 && !strncmp(p->s + start, "false", 5)) {
        t = add(p, LLJ_BOOL, start), p->pos += 5;
    } else if (p->len - start >= 4 && !strncmp(p->s + start, "null", 4)) {
        t = add(p, LLJ_NULL, start), p->pos += 4;
    } else if (c == '-' || (c >= '0' && c <= '9')) {
        t = add(p, LLJ_NUM, start);
        while (p->pos < p->len && strchr("+-0123456789.eE", p->s[p->pos])) p->pos++;
    } else {
        return -1;
    }
    if (t >= 0) p->tok[t].end = (int)p->pos;
    return t;
}

int ll_json_parse(const char *s, size_t len, LLTok *tok, int max) {
    JP p = {s, len, 0, tok, max, 0, 0};
    if (jvalue(&p) < 0) return -1;
    ws(&p);
    return p.pos == len ? p.n : -1;
}

static int hexval(char c) {
    c = (char)(c | 0x20);
    return c >= 'a' ? c - 'a' + 10 : c - '0';
}

int ll_json_string(const char *s, const LLTok *t, char *out, int cap) {
    int n = 0;
    if (t->type != LLJ_STR) return 0;
    for (int i = t->start; i < t->end; i++) {
        unsigned char c = (unsigned char)s[i];
        char v = (char)c;
        if (c >= 0x80 || c < 0x20) return 0;
        if (c == '\\') {
            c = (unsigned char)s[++i];
            if (c == 'u') {
                unsigned code = 0;
                for (int k = 1; k <= 4; k++) code = code * 16 + (unsigned)hexval(s[i + k]);
                i += 4;
                if (code == 0 || code >= 0x80) return 0;
                v = (char)code;
            } else if (c == 'n') v = '\n';
            else if (c == 't') v = '\t';
            else if (c == 'r') v = '\r';
            else if (c == 'b') v = '\b';
            else if (c == 'f') v = '\f';
            else if (c == '"' || c == '\\' || c == '/') v = (char)c;
            else return 0;
        }
        if (n + 1 >= cap) return 0;
        out[n++] = v;
    }
    out[n] = 0;
    return 1;
}

int ll_json_escape(const char *in, char *out, size_t cap) {
    size_t n = 0;
    for (; *in; in++) {
        unsigned char c = (unsigned char)*in;
        char tmp[8];
        const char *add_s = tmp;
        if (c == '"') add_s = "\\\"";
        else if (c == '\\') add_s = "\\\\";
        else if (c == '\n') add_s = "\\n";
        else if (c == '\r') add_s = "\\r";
        else if (c == '\t') add_s = "\\t";
        else if (c < 0x20) snprintf(tmp, sizeof tmp, "\\u%04x", c);
        else tmp[0] = (char)c, tmp[1] = 0;
        size_t k = strlen(add_s);
        if (n + k + 1 > cap) return -1;
        memcpy(out + n, add_s, k);
        n += k;
    }
    if (n + 1 > cap) return -1;
    out[n] = 0;
    return (int)n;
}

/* the message set */

enum { A_INT, A_NAME, A_STR, A_MODE, A_NUMBERS, A_NAMES, A_FIELDS, A_TOPICS, A_PATHS, A_VALUES, A_BOOL };
typedef struct {
    const char *key;
    int kind, required;
} ArgSpec;
typedef struct {
    const char *name;
    const ArgSpec *args;
    int n;
} ReqSpec;

static const ArgSpec A_HELLO[] = {{"protocol", A_INT, 1}, {"client", A_STR, 1}, {"nonce", A_NAME, 1}, {"proof", A_STR, 1}};
static const ArgSpec A_FILES[] = {{"mod", A_NAME, 0}, {"paths", A_PATHS, 1}};
static const ArgSpec A_ASSETS[] = {{"mode", A_MODE, 0}};
static const ArgSpec A_SCENES[] = {{"mod", A_NAME, 0}, {"states", A_NAMES, 0}, {"paths", A_PATHS, 0}};
static const ArgSpec A_BANKS[] = {{"mod", A_NAME, 0}, {"banks", A_NAMES, 1}};
static const ArgSpec A_SELECT[] = {{"object", A_INT, 0}, {"guid", A_INT, 0}};
static const ArgSpec A_JUMP[] = {{"object", A_INT, 0}, {"session", A_NAME, 0}, {"position", A_NUMBERS, 0}};
static const ArgSpec A_READ[] = {{"guid", A_INT, 0}, {"object", A_INT, 0}, {"fields", A_FIELDS, 1}};
static const ArgSpec A_UIDUMP[] = {{"states", A_NAMES, 0}};
static const ArgSpec A_DBG_RUN[] = {{"command", A_NAME, 1}, {"values", A_VALUES, 0}};
static const ArgSpec A_DBG_CHEAT[] = {{"name", A_NAME, 1}};
static const ArgSpec A_MOD_SET[] = {{"module", A_NAME, 1}, {"on", A_BOOL, 1}};
static const ArgSpec A_TOPICS_ARG[] = {{"topics", A_TOPICS, 1}};
#define N(a) ((int)(sizeof(a) / sizeof((a)[0])))
static const ReqSpec REQS[] = {
    {"hello", A_HELLO, N(A_HELLO)},
    {"files.changed", A_FILES, N(A_FILES)},
    {"reload.assets", A_ASSETS, N(A_ASSETS)},
    {"reload.texts", NULL, 0},
    {"reload.scenes", A_SCENES, N(A_SCENES)},
    {"reload.ambient", NULL, 0},
    {"reload.models", A_FILES, N(A_FILES)},
    {"reload.effects", A_FILES, N(A_FILES)},
    {"reload.banks", A_BANKS, N(A_BANKS)},
    {"reload.session", NULL, 0},
    {"select", A_SELECT, N(A_SELECT)},
    {"jump", A_JUMP, N(A_JUMP)},
    {"read", A_READ, N(A_READ)},
    {"ui.dump", A_UIDUMP, N(A_UIDUMP)},
    {"debug.list", NULL, 0},
    {"debug.run", A_DBG_RUN, N(A_DBG_RUN)},
    {"debug.cheat", A_DBG_CHEAT, N(A_DBG_CHEAT)},
    {"modules.list", NULL, 0},
    {"modules.set", A_MOD_SET, N(A_MOD_SET)},
    {"subscribe", A_TOPICS_ARG, N(A_TOPICS_ARG)},
    {"unsubscribe", A_TOPICS_ARG, N(A_TOPICS_ARG)},
    {"ping", NULL, 0},
    {"bye", NULL, 0},
};

static int name_ok(const char *s, int fields) {
    size_t n = strlen(s);
    if (!n || n > (fields ? LL_FIELD_LEN - 1 : LL_NAME_LEN - 1)) return 0;
    for (; *s; s++) {
        char c = *s;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-' ||
              (fields && c == '/')))
            return 0;
    }
    return 1;
}

int ll_path_ok(const char *path) {
    size_t n = strlen(path);
    const char *part = path;
    if (!n || n >= LL_PATH_LEN || path[0] == '/' || !strncmp(path, "./", 2)) return 0;
    for (const char *c = path;; c++) {
        if (*c == '/' || !*c) {
            if (c == part) return 0; /* an empty part ("a//b", a trailing "/") */
            if (c - part == 2 && part[0] == '.' && part[1] == '.') return 0;
            part = c + 1;
            if (!*c) break;
        } else if (!((*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == '_' ||
                     *c == '.' || *c == '-')) {
            return 0;
        }
    }
    return 1;
}

static int to_int(const char *s, const LLTok *t, int64_t *out) {
    int64_t v = 0;
    if (t->type != LLJ_NUM || t->end == t->start) return 0;
    for (int i = t->start; i < t->end; i++) {
        if (s[i] < '0' || s[i] > '9') return 0;
        v = v * 10 + (s[i] - '0');
        if (v >= ((int64_t)1 << 53)) return 0;
    }
    *out = v;
    return 1;
}

static void copy(char *dst, size_t cap, const char *src) { snprintf(dst, cap, "%s", src); }

typedef struct {
    const char *json;
    const LLTok *tok;
    LLRequest *r;
} Ctx;

static int skip(const LLTok *tok, int i) { /* the index after the token at i and everything below it */
    int n = tok[i].size, k = i + 1;
    if (tok[i].type == LLJ_OBJ) {
        for (int j = 0; j < n; j++) k = skip(tok, k + 1);
    } else if (tok[i].type == LLJ_ARR) {
        for (int j = 0; j < n; j++) k = skip(tok, k);
    }
    return k;
}

static int list(const Ctx *c, int at, int kind, char (*dst)[LL_NAME_LEN], int *count, int most);

static int store(const Ctx *c, const ArgSpec *a, int at) {
    const LLTok *t = &c->tok[at];
    LLRequest *r = c->r;
    char buf[LL_PATH_LEN + 8];
    int64_t v;
    switch (a->kind) {
    case A_INT:
        if (!to_int(c->json, t, &v)) return 0;
        if (!strcmp(a->key, "object")) r->object = v, r->has_object = 1;
        else if (!strcmp(a->key, "guid")) r->guid = v, r->has_guid = 1;
        else r->protocol = (int)v, r->has_protocol = 1;
        return 1;
    case A_BOOL:
        if (t->type != LLJ_BOOL) return 0;
        r->on = c->json[t->start] == 't', r->has_on = 1;
        return 1;
    case A_NAME:
    case A_MODE:
    case A_STR:
        if (a->kind == A_STR && t->type == LLJ_OBJ) return 1; /* "client": the suite's name and version, not used */
        if (!ll_json_string(c->json, t, buf, sizeof buf)) return 0;
        if (a->kind == A_MODE) return (!strcmp(buf, "full") || !strcmp(buf, "rebuild")) && (copy(r->mode, sizeof r->mode, buf), r->has_mode = 1);
        if (a->kind == A_STR && strcmp(a->key, "proof")) return 1; /* "client" as a plain string: not used either */
        if (a->kind == A_STR) return strlen(buf) < sizeof r->proof && (copy(r->proof, sizeof r->proof, buf), 1);
        if (!name_ok(buf, 0)) return 0;
        if (!strcmp(a->key, "mod")) copy(r->mod, sizeof r->mod, buf), r->has_mod = 1;
        else if (!strcmp(a->key, "session")) copy(r->session, sizeof r->session, buf), r->has_session = 1;
        else if (!strcmp(a->key, "command")) copy(r->command, sizeof r->command, buf), r->has_command = 1;
        else if (!strcmp(a->key, "name")) copy(r->cheat, sizeof r->cheat, buf), r->has_cheat = 1;
        else if (!strcmp(a->key, "module")) copy(r->module, sizeof r->module, buf), r->has_module = 1;
        else copy(r->nonce, sizeof r->nonce, buf);
        return 1;
    case A_NUMBERS:
        if (t->type != LLJ_ARR || t->size < 1 || t->size > 3) return 0;
        for (int i = 0; i < t->size; i++) {
            const LLTok *e = &c->tok[at + 1 + i];
            char num[40];
            if (e->type != LLJ_NUM || e->end - e->start >= (int)sizeof num) return 0;
            memcpy(num, c->json + e->start, (size_t)(e->end - e->start)), num[e->end - e->start] = 0;
            r->pos[i] = atof(num);
        }
        r->npos = t->size;
        return 1;
    case A_VALUES:
        if (t->type != LLJ_OBJ || t->size < 1 || t->size > LL_VALUES_MAX) return 0;
        for (int i = 0; i < t->size; i++) { /* scalars only: each entry is two tokens */
            const LLTok *k = &c->tok[at + 1 + 2 * i], *e = k + 1;
            char num[40];
            if (k->type != LLJ_STR || !ll_json_string(c->json, k, r->vname[i], LL_NAME_LEN) || !name_ok(r->vname[i], 0)) return 0;
            if (e->type == LLJ_BOOL) {
                r->vbool[i] = 1, r->vval[i] = c->json[e->start] == 't' ? 1.0 : 0.0;
            } else if (e->type == LLJ_NUM && e->end - e->start < (int)sizeof num) {
                memcpy(num, c->json + e->start, (size_t)(e->end - e->start)), num[e->end - e->start] = 0;
                r->vbool[i] = 0, r->vval[i] = atof(num);
            } else {
                return 0;
            }
        }
        r->nvalues = t->size;
        return 1;
    case A_PATHS:
        if (t->type != LLJ_ARR || t->size < 1 || t->size > LL_PATHS_MAX) return 0;
        for (int i = 0; i < t->size; i++) {
            if (!ll_json_string(c->json, &c->tok[at + 1 + i], r->paths[i], LL_PATH_LEN) || !ll_path_ok(r->paths[i])) return 0;
        }
        r->npaths = t->size;
        return 1;
    case A_FIELDS:
        if (t->type != LLJ_ARR || t->size < 1 || t->size > LL_FIELDS_MAX) return 0;
        for (int i = 0; i < t->size; i++)
            if (!ll_json_string(c->json, &c->tok[at + 1 + i], r->fields[i], LL_FIELD_LEN) || !name_ok(r->fields[i], 1)) return 0;
        r->nfields = t->size;
        return 1;
    default: /* A_NAMES, A_TOPICS */
        return list(c, at, a->kind, r->names, &r->nnames, LL_NAMES_MAX);
    }
}

static int list(const Ctx *c, int at, int kind, char (*dst)[LL_NAME_LEN], int *count, int most) {
    const LLTok *t = &c->tok[at];
    if (t->type != LLJ_ARR || t->size < 1 || t->size > most) return 0;
    for (int i = 0; i < t->size; i++) {
        if (!ll_json_string(c->json, &c->tok[at + 1 + i], dst[i], LL_NAME_LEN)) return 0;
        if (kind == A_TOPICS ? !ll_topic_bit(dst[i]) : !name_ok(dst[i], 0)) return 0;
    }
    *count = t->size;
    return 1;
}

static int key_is(const char *json, const LLTok *t, const char *name) {
    size_t n = strlen(name);
    return t->type == LLJ_STR && (size_t)(t->end - t->start) == n && !strncmp(json + t->start, name, n);
}

static void note(char *err, int cap, const char *fmt, const char *a, const char *b) {
    if (err && cap > 0) snprintf(err, (size_t)cap, fmt, a, b);
}

int ll_request_parse(const char *json, size_t len, LLRequest *out, char *err, int cap) {
    static LLTok tok[LL_TOKENS_MAX];
    Ctx c = {json, tok, out};
    const ReqSpec *spec = NULL;
    int n = ll_json_parse(json, len, tok, LL_TOKENS_MAX), args = -1;
    memset(out, 0, sizeof *out);
    if (n < 1 || tok[0].type != LLJ_OBJ) return note(err, cap, "%s%s", "not a request", ""), 0;
    for (int i = 1, k = 0; k < tok[0].size; k++) { /* the top level: id, type, args */
        const LLTok *key = &tok[i], *val = &tok[i + 1];
        int64_t v;
        if (key_is(json, key, "id") && to_int(json, val, &v)) out->id = (uint32_t)v;
        else if (key_is(json, key, "type")) {
            if (!ll_json_string(json, val, out->type, sizeof out->type)) out->type[0] = '?', out->type[1] = 0;
        } else if (key_is(json, key, "args")) args = i + 1;
        i = skip(tok, i + 1);
    }
    for (size_t i = 0; i < sizeof REQS / sizeof REQS[0]; i++)
        if (!strcmp(REQS[i].name, out->type)) spec = &REQS[i];
    if (!spec) return note(err, cap, "unknown request '%s'%s", out->type, ""), -1;
    if (args >= 0 && tok[args].type != LLJ_OBJ) return note(err, cap, "%s: %s", out->type, "args is not an object"), 0;
    for (int i = args >= 0 ? args + 1 : 0, k = 0; args >= 0 && k < tok[args].size; k++) {
        int known = -1;
        for (int a = 0; a < spec->n; a++)
            if (key_is(json, &tok[i], spec->args[a].key)) known = a;
        if (known < 0) return note(err, cap, "%s: unexpected argument %s", out->type, "in the request"), 0;
        if (!store(&c, &spec->args[known], i + 1)) return note(err, cap, "%s: %s is not valid", out->type, spec->args[known].key), 0;
        i = skip(tok, i + 1);
    }
    for (int a = 0; a < spec->n; a++) {
        int have = 0;
        for (int i = args >= 0 ? args + 1 : 0, k = 0; args >= 0 && k < tok[args].size; k++, i = skip(tok, i + 1))
            have |= key_is(json, &tok[i], spec->args[a].key);
        if (spec->args[a].required && !have) return note(err, cap, "%s: %s is missing", out->type, spec->args[a].key), 0;
    }
    return 1;
}

int ll_request_is(const LLRequest *r, const char *type) { return !strcmp(r->type, type); }

/* what a changed file needs */

static int ends(const char *s, const char *suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && !strcmp(s + n - m, suffix);
}

const char *ll_needs_of(const char *path) {
    char low[LL_PATH_LEN];
    size_t n = strlen(path) < sizeof low - 1 ? strlen(path) : sizeof low - 1;
    const char *name;
    for (size_t i = 0; i < n; i++) low[i] = path[i] >= 'A' && path[i] <= 'Z' ? (char)(path[i] + 32) : path[i];
    low[n] = 0;
    name = strrchr(low, '/') ? strrchr(low, '/') + 1 : low;
    if (ends(low, ".rdm") || ends(low, ".cfg")) return "models";
    if (ends(low, ".fx") || ends(low, ".fxh")) return "effects";
    if (ends(low, ".dds")) return "textures";
    if (ends(low, ".bnk") || ends(low, ".wem")) return "banks";
    if (ends(low, ".a7m") || ends(low, ".a7t") || ends(low, ".a7minfo") || ends(low, ".a7me")) return "session";
    if (!strncmp(low, "data/ui/", 8) || ends(low, ".phx") || ends(low, ".phxl")) return "scenes";
    if (ends(low, ".xml")) {
        if (strstr(low, "/ambient/") || !strncmp(low, "ambient/", 8)) return "ambient";
        return !strncmp(name, "texts", 5) ? "texts" : "assets";
    }
    return "restart";
}

/* text builders */

typedef struct {
    char *p;
    size_t cap, n;
    int bad;
} Buf;

static void put(Buf *b, const char *s) {
    size_t k = strlen(s);
    if (b->bad || b->n + k + 1 > b->cap) b->bad = 1;
    else memcpy(b->p + b->n, s, k + 1), b->n += k;
}

static void put_str(Buf *b, const char *s) { /* a quoted, escaped string */
    char tmp[2048];
    int k = ll_json_escape(s ? s : "", tmp, sizeof tmp);
    if (k < 0) b->bad = 1;
    else put(b, "\""), put(b, tmp), put(b, "\"");
}

static int done(const Buf *b) { return b->bad ? -1 : (int)b->n; }

int ll_needs_json(const LLRequest *r, char *out, size_t cap) {
    Buf b = {out, cap, 0, 0};
    put(&b, "{\"needs\":{");
    for (int i = 0; i < r->npaths; i++) {
        if (i) put(&b, ",");
        put_str(&b, r->paths[i]), put(&b, ":"), put_str(&b, ll_needs_of(r->paths[i]));
    }
    put(&b, "}}");
    return done(&b);
}

int ll_answer_json(char *out, size_t cap, uint32_t id, const char *status, const char *message, const char *data) {
    Buf b = {out, cap, 0, 0};
    char num[16];
    snprintf(num, sizeof num, "%u", id);
    put(&b, "{\"id\":"), put(&b, num), put(&b, ",\"status\":"), put_str(&b, status);
    put(&b, ",\"message\":"), put_str(&b, message), put(&b, ",\"data\":"), put(&b, data && *data ? data : "{}"), put(&b, "}");
    return done(&b);
}

int ll_event_json(char *out, size_t cap, const char *event, const char *data) {
    Buf b = {out, cap, 0, 0};
    put(&b, "{\"event\":"), put_str(&b, event), put(&b, ",\"data\":"), put(&b, data && *data ? data : "{}"), put(&b, "}");
    return done(&b);
}

int ll_greeting_json(char *out, size_t cap, const char *challenge) {
    Buf b = {out, cap, 0, 0};
    put(&b, "{\"challenge\":"), put_str(&b, challenge), put(&b, ",\"protocol\":1}");
    return done(&b);
}

int ll_session_json(char *out, size_t cap, const char *pipe, const char *token, const char *game, const char *build,
                    const char *library, unsigned pid) {
    Buf b = {out, cap, 0, 0};
    char num[16];
    snprintf(num, sizeof num, "%u", pid);
    put(&b, "{\"pipe\":"), put_str(&b, pipe), put(&b, ",\"token\":"), put_str(&b, token), put(&b, ",\"protocol\":1,\"game\":");
    put_str(&b, game), put(&b, ",\"build\":"), put_str(&b, build), put(&b, ",\"library\":"), put_str(&b, library);
    put(&b, ",\"pid\":"), put(&b, num), put(&b, "}");
    return done(&b);
}

int ll_welcome_json(char *out, size_t cap, const char *game, const char *build, const char *library, int kinds_bits,
                    char (*mod_ids)[64], char (*mod_folders)[260], int nmods, const char *proof) {
    Buf b = {out, cap, 0, 0};
    int first = 1;
    put(&b, "{\"game\":"), put_str(&b, game), put(&b, ",\"build\":"), put_str(&b, build);
    put(&b, ",\"library\":"), put_str(&b, library), put(&b, ",\"protocol\":1,\"kinds\":[");
    for (int bit = 1; bit <= LL_KIND_BANKS * 2; bit <<= 1) {
        if (!(kinds_bits & bit) || bit > LL_KIND_SESSION) continue;
        if (!first) put(&b, ",");
        put_str(&b, ll_kind_name(bit)), first = 0;
    }
    if (kinds_bits & LL_KIND_DEBUG) put(&b, first ? "" : ","), put_str(&b, "debug");
    put(&b, "],\"mods\":[");
    for (int i = 0; i < nmods; i++) {
        if (i) put(&b, ",");
        put(&b, "{\"id\":"), put_str(&b, mod_ids[i]), put(&b, ",\"folder\":"), put_str(&b, mod_folders[i]), put(&b, "}");
    }
    put(&b, "],\"proof\":"), put_str(&b, proof), put(&b, "}");
    return done(&b);
}

int ll_selection_json(char *out, size_t cap, int64_t object, int64_t guid, const char *session) {
    Buf b = {out, cap, 0, 0};
    char num[64];
    put(&b, "{\"objects\":[");
    if (object >= 0) {
        snprintf(num, sizeof num, "{\"id\":%lld,\"guid\":%lld,\"session\":", (long long)object, (long long)guid);
        put(&b, num), put_str(&b, session), put(&b, "}");
    }
    put(&b, "]}");
    return done(&b);
}

int ll_values_json(char *out, size_t cap, char fields[][LL_FIELD_LEN], int n, const char *text) {
    Buf b = {out, cap, 0, 0};
    const char *at = text;
    put(&b, "{\"values\":{");
    for (int i = 0; i < n; i++) {
        const char *end = strchr(at, '\n');
        char line[512];
        size_t k = end ? (size_t)(end - at) : strlen(at);
        if (k >= sizeof line) k = sizeof line - 1;
        memcpy(line, at, k), line[k] = 0;
        if (k && line[k - 1] == '\r') line[k - 1] = 0;
        if (i) put(&b, ",");
        put_str(&b, fields[i]), put(&b, ":"), put_str(&b, line);
        at = end ? end + 1 : at + strlen(at);
    }
    put(&b, "}}");
    return done(&b);
}

int ll_pass_json(char *out, size_t cap, const LLPass *p) {
    Buf b = {out, cap, 0, 0};
    char num[160];
    snprintf(num, sizeof num, "{\"seconds\":%.1f,\"warnings\":%d,\"errors\":%d,\"noMatchingNode\":%d,\"missingFile\":%d,\"problem\":",
             p->seconds, p->warnings, p->errors, p->no_matching_node, p->missing_file);
    put(&b, num), put_str(&b, p->first_problem), put(&b, "}");
    return done(&b);
}

static const char *file_word(int code) {
    return code == LL_FILE_DONE ? "reloaded" : code == LL_FILE_HANDED ? "handed" : code == LL_FILE_NOT_LOADED ? "skipped" : "refused";
}

int ll_files_json(char *out, size_t cap, const LLFileResult *f, int n) {
    Buf b = {out, cap, 0, 0};
    char num[40];
    put(&b, "{\"files\":[");
    for (int i = 0; i < n; i++) {
        if (i) put(&b, ",");
        put(&b, "{\"path\":"), put_str(&b, f[i].path), put(&b, ",\"status\":"), put_str(&b, file_word(f[i].code));
        snprintf(num, sizeof num, ",\"ms\":%.1f,\"message\":", f[i].ms);
        put(&b, num), put_str(&b, f[i].why), put(&b, "}");
    }
    put(&b, "]}");
    return done(&b);
}

const char *ll_files_status(const LLFileResult *f, int n, const char **message) {
    int ran = 0, skipped = 0;
    *message = "";
    for (int i = 0; i < n; i++) {
        ran += f[i].code == LL_FILE_DONE || f[i].code == LL_FILE_HANDED;
        skipped += f[i].code == LL_FILE_NOT_LOADED;
        if (!**message && f[i].why[0] && f[i].code != LL_FILE_DONE) *message = f[i].why;
    }
    if (ran) return "done";
    return n && skipped == n ? "skipped" : "refused";
}

/* topics, rate, gate */

int ll_topic_bit(const char *name) {
    if (!strcmp(name, "selection")) return LL_TOPIC_SELECTION;
    if (!strcmp(name, "reload")) return LL_TOPIC_RELOAD;
    if (!strcmp(name, "session")) return LL_TOPIC_SESSION;
    if (!strcmp(name, "log")) return LL_TOPIC_LOG;
    return 0;
}

const char *ll_topic_name(int bit) {
    return bit == LL_TOPIC_SELECTION ? "selection" : bit == LL_TOPIC_RELOAD ? "reload"
           : bit == LL_TOPIC_SESSION ? "session" : bit == LL_TOPIC_LOG ? "log" : "?";
}

void ll_rate_init(LLRate *r, uint64_t now_ms) { r->tokens = LL_RATE_BURST, r->last_ms = now_ms; }

int ll_rate_take(LLRate *r, uint64_t now_ms) {
    if (now_ms > r->last_ms) {
        r->tokens += (double)(now_ms - r->last_ms) * LL_RATE_PER_S / 1000.0;
        if (r->tokens > LL_RATE_BURST) r->tokens = LL_RATE_BURST;
    }
    r->last_ms = now_ms;
    if (r->tokens < 1.0) return 0;
    r->tokens -= 1.0;
    return 1;
}

int ll_kind_of_request(const char *type) {
    static const struct {
        const char *name;
        int kind;
    } t[] = {{"reload.assets", LL_KIND_ASSETS}, {"reload.texts", LL_KIND_TEXTS}, {"reload.scenes", LL_KIND_SCENES},
             {"reload.ambient", LL_KIND_AMBIENT}, {"reload.models", LL_KIND_MODELS}, {"reload.effects", LL_KIND_EFFECTS},
             {"reload.banks", LL_KIND_BANKS}, {"reload.session", LL_KIND_SESSION}, {"select", LL_KIND_SELECT},
             {"jump", LL_KIND_JUMP}, {"read", LL_KIND_READ}, {"ui.dump", LL_KIND_UIDUMP},
             {"debug.list", LL_KIND_DEBUG}, {"debug.run", LL_KIND_DEBUG}, {"debug.cheat", LL_KIND_DEBUG},
             {"modules.list", LL_KIND_DEBUG}, {"modules.set", LL_KIND_DEBUG}};
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++)
        if (!strcmp(t[i].name, type)) return t[i].kind;
    return 0;
}

const char *ll_kind_name(int bit) {
    switch (bit) {
    case LL_KIND_ASSETS: return "assets";
    case LL_KIND_TEXTS: return "texts";
    case LL_KIND_SCENES: return "scenes";
    case LL_KIND_AMBIENT: return "ambient";
    case LL_KIND_MODELS: return "models";
    case LL_KIND_EFFECTS: return "effects";
    case LL_KIND_BANKS: return "banks";
    case LL_KIND_SESSION: return "session";
    case LL_KIND_SELECT: return "select";
    case LL_KIND_JUMP: return "jump";
    case LL_KIND_READ: return "read";
    case LL_KIND_UIDUMP: return "uidump";
    case LL_KIND_DEBUG: return "debug";
    default: return "?";
    }
}

int ll_gate(const LLRequest *r, int multiplayer, int kinds_bits, const char **why) {
    int k = ll_kind_of_request(r->type);
    *why = "";
    if (!k) return 0; /* hello, ping, bye, subscribe, files.changed: answered by the link itself */
    if (multiplayer) {
        *why = "not in a multiplayer game: a change on one computer would split the game";
        return 1;
    }
    if (!(kinds_bits & k)) {
        *why = "this game build cannot serve that request";
        return 2;
    }
    return 0;
}

/* the Lua templates */

static int lua_put(char *cmd, int cap, const char *fmt, ...) {
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(cmd, (size_t)cap, fmt, ap);
    va_end(ap);
    return n >= 0 && n < cap;
}

int ll_lua_reload_assets(char *cmd, int cap, int rebuild_only) {
    return lua_put(cmd, cap, rebuild_only ? "CAssetDataManager:ExecuteFilter()" : "Mods:LoadMods()");
}
int ll_lua_reload_ambient(char *cmd, int cap) { return lua_put(cmd, cap, "CEnvironmentManager:ReloadAmbientSettings()"); }
int ll_lua_select(char *cmd, int cap, int64_t object) { return lua_put(cmd, cap, "Selection:SelectByID(%lld)", (long long)object); }
int ll_lua_jump_object(char *cmd, int cap, int64_t object) { return lua_put(cmd, cap, "Scripts:JumpToObject(%lld)", (long long)object); }

int ll_lua_jump_session(char *cmd, int cap, const char *session, const double *pos) {
    char *end;
    unsigned long long guid = strtoull(session, &end, 10);
    if (end == session || *end) return 0; /* a session is its GUID */
    if (!pos) return lua_put(cmd, cap, "Scripts:JumpToSession(%llu)", guid);
    for (int i = 0; i < 3; i++)
        if (!(pos[i] > -1e7 && pos[i] < 1e7)) return 0; /* a finite world coordinate (this also refuses NaN) */
    /* the game's own debug camera command, in the format its own code writes it */
    return lua_put(cmd, cap, "MetaObjects:CheatLookAtPosition(%llu, %.1f, %.1f, %.1f)", guid, pos[0], pos[1], pos[2]);
}

int ll_lua_select_guid(char *cmd, int cap, int64_t guid) {
    if (guid <= 0) return 0;
    /* the first object of the asset's group in the current session; the text is its id, "" when there is none */
    return lua_put(cmd, cap,
                   "(function() local ok, id = pcall(function() local g = Scripts:GetObjectGroupByGUID(%lld); "
                   "local o = g and g[1]; if o then Selection:SelectByID(o.ID); return tostring(o.ID) end return \"\" end); "
                   "return ok and id or \"\" end)()",
                   (long long)guid);
}

int ll_lua_save_session(char *cmd, int cap) { return lua_put(cmd, cap, "Game:Save(\"" LL_SESSION_SAVE "\", true)"); }
int ll_lua_load_session(char *cmd, int cap) { return lua_put(cmd, cap, "Game:Load(\"" LL_SESSION_SAVE "\", true, true)"); }

int ll_lua_selection(char *cmd, int cap) {
    return lua_put(cmd, cap,
                   "Selection.Object and (tostring(Selection.Object.ID) .. \",\" .. tostring(Selection.Object.GUID) .. \",\" .. "
                   "tostring(Selection.Object.SessionGuid)) or \"\"");
}

/* a value path "A/B" as the member chain ".A.B"; 0 when a part is not an identifier */
static int chain(const char *path, char *out, int cap) {
    int n = 0, start = 1;
    out[n++] = '.';
    for (const char *c = path;; c++) {
        int alpha = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || *c == '_';
        if (*c == '/' || !*c) {
            if (start) return 0; /* an empty part */
            if (!*c) break;
            if (n + 2 > cap) return 0;
            out[n++] = '.', start = 1;
        } else {
            if (!alpha && (start || !(*c >= '0' && *c <= '9'))) return 0;
            if (n + 2 > cap) return 0;
            out[n++] = *c, start = 0;
        }
    }
    out[n] = 0;
    return 1;
}

static int lua_read(char *cmd, int cap, const char *base, char fields[][LL_FIELD_LEN], int n) {
    int used = 0;
    if (n < 1) return 0;
    for (int i = 0; i < n; i++) {
        char member[LL_FIELD_LEN + 2];
        int k;
        if (!chain(fields[i], member, sizeof member)) return 0;
        k = snprintf(cmd + used, (size_t)(cap - used), "%s(select(2, pcall(function() return tostring(%s%s) end)))",
                     i ? " .. \"\\n\" .. " : "", base, member);
        if (k < 0 || k >= cap - used) return 0;
        used += k;
    }
    return 1;
}

int ll_lua_read_asset(char *cmd, int cap, int64_t guid, char fields[][LL_FIELD_LEN], int n) {
    char base[48];
    snprintf(base, sizeof base, "AssetData(%lld)", (long long)guid);
    return lua_read(cmd, cap, base, fields, n);
}

int ll_lua_read_object(char *cmd, int cap, int64_t object, char fields[][LL_FIELD_LEN], int n) {
    static const char *const allowed[] = {"GUID", "ID", "SessionGuid", "Direction", "Visible", "IsBlueprint",
                                          "Health/CurrentHitPoints", "Position/x", "Position/y", "Position/z"};
    char base[64];
    for (int i = 0; i < n; i++) {
        int ok = 0;
        for (size_t a = 0; a < sizeof allowed / sizeof allowed[0]; a++) ok |= !strcmp(fields[i], allowed[a]);
        if (!ok) return 0; /* an object answers from a fixed list only */
    }
    snprintf(base, sizeof base, "Scripts:GetObjectByID(%lld)", (long long)object);
    return lua_read(cmd, cap, base, fields, n);
}

int ll_selection_parse(const char *text, int64_t *object, int64_t *guid, char *session, int cap) {
    char *end;
    long long a, b;
    if (!text || !*text) return 0;
    a = strtoll(text, &end, 10);
    if (end == text || *end != ',') return 0;
    b = strtoll(end + 1, &end, 10);
    if (*end != ',' || strlen(end + 1) >= (size_t)cap) return 0;
    *object = a, *guid = b;
    snprintf(session, (size_t)cap, "%s", end + 1);
    for (char *c = session; *c; c++)
        if (*c < '0' || *c > '9') return 0;
    return 1;
}

/* the loader's log */

void ll_pass_scan(const char *text, size_t len, LLPass *p) {
    size_t i = 0;
    memset(p, 0, sizeof *p);
    while (i < len) {
        size_t j = i;
        char line[512];
        size_t k;
        const char *at;
        while (j < len && text[j] != '\n') j++;
        k = j - i < sizeof line - 1 ? j - i : sizeof line - 1;
        memcpy(line, text + i, k), line[k] = 0;
        if (k && line[k - 1] == '\r') line[k - 1] = 0;
        if ((at = strstr(line, "Patching completed in "))) p->completed = 1, p->seconds = atof(at + 22);
        else {
            int problem = 0;
            if (strstr(line, "[warning]")) p->warnings++, problem = 1;
            if (strstr(line, "[error]")) p->errors++, problem = 1;
            if (strstr(line, "No matching node")) p->no_matching_node++, problem = 1;
            if (strstr(line, "Can't find file to patch")) p->missing_file++, problem = 1;
            if (problem && !p->first_problem[0]) {
                at = strchr(line, ']') && strchr(strchr(line, ']') + 1, ']') ? strchr(strchr(line, ']') + 1, ']') + 2 : line;
                snprintf(p->first_problem, sizeof p->first_problem, "%s", at);
            }
        }
        i = j + 1;
    }
}

size_t ll_log_complete(const char *text, size_t len) {
    while (len && text[len - 1] != '\n') len--;
    return len;
}

int ll_title_mark(const uint16_t *title, int len, uint16_t *out, int cap, int on) {
    static const char suffix[] = " [live link]";
    int sl = (int)sizeof suffix - 1, has = 0, now = len;
    if (len >= sl) {
        has = 1;
        for (int i = 0; i < sl; i++) has &= title[len - sl + i] == (uint16_t)(unsigned char)suffix[i];
    }
    if (on && !has) now = len + sl;
    else if (!on && has) now = len - sl;
    if (now + 1 > cap) return -1;
    memmove(out, title, (size_t)(now < len ? now : len) * sizeof(uint16_t));
    for (int i = len; i < now; i++) out[i] = (uint16_t)(unsigned char)suffix[i - len];
    out[now] = 0;
    return now;
}

int ll_scene_can_start(int countdown, int busy) { return countdown <= 0 && !busy; }

int ll_scene_done(int countdown, int busy, int *seen_busy, uint64_t since_zero_ms) {
    if (countdown > 0) return 0;
    if (busy) {
        *seen_busy = 1;
        return 0;
    }
    return *seen_busy || since_zero_ms >= LL_SCENE_QUIET_MS;
}

int ll_scene_ids(const uint32_t *ids, int n, int32_t *out, int cap, int *unknown) {
    int count = 0;
    *unknown = 0;
    for (int i = 0; i < n; i++) {
        int seen = 0;
        if (ids[i] == 0xffffffffu) {
            (*unknown)++;
            continue;
        }
        for (int j = 0; j < count; j++) seen |= out[j] == (int32_t)ids[i];
        if (!seen && count < cap) out[count++] = (int32_t)ids[i];
    }
    return count;
}

int ll_scene_message(char *out, int cap, char names[][LL_NAME_LEN], const int *known, int n) {
    int at = 0, first = 1;
    at = snprintf(out, (size_t)cap, "reloaded the scenes of ");
    for (int pass = 1; pass >= 0; pass--) {
        first = 1;
        for (int i = 0; i < n && at < cap; i++) {
            if ((known[i] != 0) != pass) continue;
            if (!pass && first) at += snprintf(out + at, (size_t)(cap - at), "; not a state of the game: ");
            else if (!first) at += snprintf(out + at, (size_t)(cap - at), ", ");
            if (at < cap) at += snprintf(out + at, (size_t)(cap - at), "%s", names[i]);
            first = 0;
        }
    }
    return at >= 0 && at < cap;
}

int ll_names_json(char *out, size_t cap, const char *key, char (*names)[160], int n) {
    Buf b = {out, cap, 0, 0};
    put(&b, "{\""), put(&b, key), put(&b, "\":[");
    for (int i = 0; i < n; i++) {
        if (i) put(&b, ",");
        put_str(&b, names[i]);
    }
    put(&b, "]}");
    return done(&b);
}

int ll_waited_out(uint64_t started_ms, uint64_t now_ms, uint64_t limit_ms) { return now_ms > started_ms && now_ms - started_ms >= limit_ms; }

/* Wwise names a bank (and an event) by the FNV-1 hash of its lower-cased name; the suite's formats/fnv.py is the same. */
uint32_t ll_wwise_id(const char *name) {
    uint32_t h = 2166136261u;
    for (const unsigned char *c = (const unsigned char *)name; *c; c++) {
        unsigned char l = *c >= 'A' && *c <= 'Z' ? (unsigned char)(*c + 32) : *c;
        h = (h * 16777619u) ^ l;
    }
    return h;
}

static const char *bank_word(int code) {
    switch (code) {
    case LL_BANK_DONE: return "reloaded";
    case LL_BANK_UNLOAD_FAILED: return "not unloaded";
    case LL_BANK_LOAD_FAILED: return "not loaded again";
    case LL_BANK_PROTECTED: return "refused";
    case LL_BANK_TIMEOUT: return "no answer";
    default: return "?";
    }
}

int ll_banks_json(char *out, size_t cap, const LLBankResult *r, int n) {
    Buf b = {out, cap, 0, 0};
    char num[80];
    put(&b, "{\"banks\":[");
    for (int i = 0; i < n; i++) {
        if (i) put(&b, ",");
        put(&b, "{\"name\":"), put_str(&b, r[i].name);
        snprintf(num, sizeof num, ",\"id\":%u,\"result\":%d,\"prepared\":%s,\"status\":", (unsigned)r[i].id, r[i].ak,
                 r[i].prepared ? "true" : "false");
        put(&b, num), put_str(&b, bank_word(r[i].code)), put(&b, "}");
    }
    put(&b, "]}");
    return done(&b);
}

const char *ll_banks_status(const LLBankResult *r, int n, const char **message) {
    int ran = 0, timeout = 0;
    *message = "";
    for (int i = 0; i < n; i++) {
        ran += r[i].code == LL_BANK_DONE;
        timeout += r[i].code == LL_BANK_TIMEOUT;
        if (!**message && r[i].code != LL_BANK_DONE) {
            static char first[LL_NAME_LEN + 96];
            if (r[i].code == LL_BANK_PROTECTED) snprintf(first, sizeof first, "%s: the Init bank carries every bus and is never reloaded", r[i].name);
            else snprintf(first, sizeof first, "%s: %s (the sound engine's result %d)", r[i].name, bank_word(r[i].code), r[i].ak);
            *message = first;
        }
    }
    if (ran) return "done";
    return timeout ? "skipped" : "refused";
}

int ll_save_watch(LLSaveWatch *w, int newer, int64_t size, uint64_t now_ms) {
    if (!newer || size <= 0) {
        w->size = -1;
        return 0;
    }
    if (size != w->size) {
        w->size = size, w->since_ms = now_ms;
        return 0;
    }
    return now_ms >= w->since_ms && now_ms - w->since_ms >= LL_SAVE_STABLE_MS;
}

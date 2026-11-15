/* Persistence: each module registers a versioned chunk, written as one extra named block into the game's own save
   through the engine's serializer (no side file). The engine looks fields up by name, so a vanilla game never asks
   for the block: an old save loads with defaults, a vanilla game drops the block on its next save. */
#include "hlib.h"
#include "targets.h"
#include "util.h"
#include <string.h>

#define MAX_CHUNKS 64 /* 25 registration sites on 2026-11-06; check_build_numbers.py fails when they outgrow it */
#define BLOCK_MAGIC 0x4B433241u /* "A2CK" */

#define GLOBAL_MAGIC 0x4C473241u /* "A2GL": a game-wide chunk's stamp leads its payload */
#define GLOBAL_HEAD 12

static struct {
    const char *chunk;
    int version, global;
    HlibSaveFn save;
    HlibLoadFn load;
    long long best;  /* a game-wide chunk: the newest stamp delivered in this load */
    LONG seen_gen;   /* the load that stamp belongs to */
} s_chunks[MAX_CHUNKS];
static volatile LONG s_chunk_count;
static volatile LONG s_load_gen = 1; /* a new load starts a new choice of the game-wide blocks */
static SRWLOCK s_global_lock = SRWLOCK_INIT;

#define MAX_ISLAND_FNS 4 /* trees and the harbour reach on 2026-11-14 */
static HlibIslandFn s_island_fn[MAX_ISLAND_FNS];
static volatile LONG s_island_fns;
/* the session whose state is being written or read, per thread: the engine runs session work as scheduler jobs, each
   with its own thread-local session context, so two sessions may serialise at once */
static __declspec(thread) void *s_session;

static int chunk_add(const char *chunk, int version, HlibSaveFn save, HlibLoadFn load, int global) {
    if (s_chunk_count >= MAX_CHUNKS) {
        hlib_logf("persist: chunk '%s' NOT registered: the table holds %d (MAX_CHUNKS), its state is not saved", chunk, MAX_CHUNKS);
        return 0;
    }
    s_chunks[s_chunk_count].chunk = chunk;
    s_chunks[s_chunk_count].version = version;
    s_chunks[s_chunk_count].global = global;
    s_chunks[s_chunk_count].save = save;
    s_chunks[s_chunk_count].load = load;
    InterlockedIncrement(&s_chunk_count); /* a save running meanwhile sees the entry whole */
    hlib_logf("persist: chunk '%s' v%d registered%s", chunk, version, global ? " (game-wide: the newest block wins)" : "");
    return 1;
}

int hlib_persist_register(const char *chunk, int version, HlibSaveFn save, HlibLoadFn load) {
    return chunk_add(chunk, version, save, load, 0);
}
int hlib_persist_register_global(const char *chunk, int version, HlibSaveFn save, HlibLoadFn load) {
    return chunk_add(chunk, version, save, load, 1);
}
void hlib_persist_load_begins(void) { InterlockedIncrement(&s_load_gen); }

void hlib_persist_on_island(HlibIslandFn fn) {
    if (s_island_fns >= MAX_ISLAND_FNS) {
        hlib_logf("persist: an island listener NOT registered: the table holds %d (MAX_ISLAND_FNS)", MAX_ISLAND_FNS);
        return;
    }
    s_island_fn[s_island_fns] = fn;
    InterlockedIncrement(&s_island_fns);
}
void *hlib_persist_session(void) { return s_session; }

/* the engine's name key: FNV-1a hash, padding, text pointer, length */
typedef struct {
    uint32_t hash;
    uint32_t pad;
    const char *text;
    unsigned long long len;
} NameKey;

static void make_key(NameKey *k, const char *text) {
    uint32_t h = 2166136261u;
    unsigned long long n = 0;
    for (; text[n]; n++) h = (h ^ (unsigned char)text[n]) * 16777619u;
    k->hash = h;
    k->pad = 0;
    k->text = text;
    k->len = n;
}

struct HlibSaver {
    void *stream;
    int writing;
    int version;
    int global;
    const char *chunk;
    uint32_t wrote;
    const unsigned char *payload;
    uint32_t payload_len;
};

typedef unsigned long long (*NameFn)(void *table, NameKey *key);
typedef long long (*WriteFn)(void *stream, short id, int size, const void *data);
typedef unsigned int *(*FindFn)(void *stream, short id);

static void block_name(char *out, const char *chunk) {
    int n = 0;
    const char *pre = "A2070.";
    while (*pre) out[n++] = *pre++;
    while (*chunk && n < 62) out[n++] = *chunk++;
    out[n] = 0;
}

int hlib_stream_reading(void *stream) { return *((unsigned char *)stream + T_OFF_STREAM_READ) != 0; }

int hlib_block_put(void *stream, const char *chunk, int version, const void *data, uint32_t len) {
    NameFn add = (NameFn)hlib_target(T_SER_NAME_ADD);
    WriteFn write = (WriteFn)hlib_target(T_SER_WRITE_ATTRIB);
    if (!stream || hlib_stream_reading(stream) || !add || !write) return 0;
    char name[72];
    NameKey key;
    block_name(name, chunk);
    make_key(&key, name);
    void *table = *(void **)((char *)stream + T_OFF_STREAM_NAMES);
    short id = (short)add(table, &key);
    uint32_t total = 12 + len;
    unsigned char *buf = (unsigned char *)HeapAlloc(GetProcessHeap(), 0, total);
    if (!buf) return 0;
    ((uint32_t *)buf)[0] = BLOCK_MAGIC;
    ((uint32_t *)buf)[1] = (uint32_t)version;
    ((uint32_t *)buf)[2] = len;
    if (len) memcpy(buf + 12, data, len);
    write(stream, id, (int)total, buf);
    *(int *)((char *)stream + T_OFF_STREAM_ATTRS) += 1;
    HeapFree(GetProcessHeap(), 0, buf);
    return (int)total;
}

int hlib_saver_put(HlibSaver *s, const void *data, uint32_t len) {
    if (!s || !s->writing || s->wrote) return 0;
    if (!s->global) {
        int total = hlib_block_put(s->stream, s->chunk, s->version, data, len);
        s->wrote = (uint32_t)total;
        return total > 0;
    }
    /* a game-wide chunk: the game time of the save leads it, so a load takes the newest of the sessions' blocks */
    unsigned char *buf = (unsigned char *)HeapAlloc(GetProcessHeap(), 0, GLOBAL_HEAD + len);
    if (!buf) return 0;
    long long stamp = hlib_meta_time_ms();
    uint32_t head[3] = {GLOBAL_MAGIC, (uint32_t)(stamp < 0 ? 0 : stamp), (uint32_t)((stamp < 0 ? 0 : stamp) >> 32)};
    memcpy(buf, head, GLOBAL_HEAD);
    if (len) memcpy(buf + GLOBAL_HEAD, data, len);
    int total = hlib_block_put(s->stream, s->chunk, s->version, buf, GLOBAL_HEAD + len);
    HeapFree(GetProcessHeap(), 0, buf);
    s->wrote = (uint32_t)total;
    return total > 0;
}

/* A game-wide block read: its stamp stripped (an older block has none: stamp 0); 1 when the module should load it, the
   first of this load or a newer one than delivered so far. An absent block (stamp -1) counts only as the first. */
static int global_take(int i, HlibSaver *sv, int found) {
    long long stamp = -1;
    if (found) {
        stamp = 0;
        if (sv->payload_len >= GLOBAL_HEAD && ((const uint32_t *)sv->payload)[0] == GLOBAL_MAGIC) {
            const uint32_t *h = (const uint32_t *)sv->payload;
            stamp = (long long)h[1] | (long long)h[2] << 32;
            sv->payload += GLOBAL_HEAD;
            sv->payload_len -= GLOBAL_HEAD;
        }
    }
    int take;
    AcquireSRWLockExclusive(&s_global_lock);
    if (s_chunks[i].seen_gen != s_load_gen) {
        s_chunks[i].seen_gen = s_load_gen;
        s_chunks[i].best = stamp;
        take = 1;
    } else {
        take = stamp > s_chunks[i].best;
        if (take) s_chunks[i].best = stamp;
    }
    ReleaseSRWLockExclusive(&s_global_lock);
    return take;
}

int hlib_saver_get(HlibSaver *s, const void **data, uint32_t *len) {
    if (!s || s->writing || !s->payload) return 0;
    *data = s->payload;
    *len = s->payload_len;
    return 1;
}

/* The block of one chunk in a save being read: its stored version and payload, 0 if absent or damaged. */
int hlib_block_find(void *stream, const char *chunk, int *version, const unsigned char **payload, uint32_t *len) {
    NameFn find = (NameFn)hlib_target(T_SER_NAME_FIND);
    FindFn attrib = (FindFn)hlib_target(T_SER_FIND_ATTRIB);
    if (!find || !attrib) return 0;
    char name[72];
    NameKey key;
    block_name(name, chunk);
    make_key(&key, name);
    unsigned long long id = find(*(void **)((char *)stream + T_OFF_STREAM_NAMES), &key);
    if ((unsigned)id == 0xffffffffu) return 0;
    unsigned int *a = attrib(stream, (short)id);
    if (!a) return 0;
    uint32_t size = a[0];
    const unsigned int *d = a + 2;
    if (size < 12 || d[0] != BLOCK_MAGIC || d[2] > size - 12) return 0;
    *version = (int)d[1];
    *payload = (const unsigned char *)(d + 3);
    *len = d[2];
    return 1;
}

int hlib_stream_name_id(void *stream, const char *name) {
    NameFn find = (NameFn)hlib_target(T_SER_NAME_FIND);
    NameKey key;
    if (!stream || !find) return -1;
    make_key(&key, name);
    unsigned long long id = find(*(void **)((char *)stream + T_OFF_STREAM_NAMES), &key);
    return (unsigned)id == 0xffffffffu ? -1 : (int)(unsigned short)id;
}

int hlib_stream_get_int(void *stream, const char *name, int *out) {
    FindFn attrib = (FindFn)hlib_target(T_SER_FIND_ATTRIB);
    int id = hlib_stream_name_id(stream, name);
    if (id < 0 || !attrib || !hlib_stream_reading(stream)) return 0;
    unsigned int *a = attrib(stream, (short)id);
    if (!a || a[0] != 4) return 0;
    *out = (int)a[2];
    return 1;
}

uint32_t hlib_stream_offset(void *stream) { return *(uint32_t *)((char *)stream + T_OFF_STREAM_WOFF); }

unsigned char *hlib_stream_bytes(void *stream) {
    unsigned char **v = *(unsigned char ***)((char *)stream + T_OFF_STREAM_BUF);
    return v ? v[0] : NULL;
}

typedef struct {
    void *rm;
    void *stream;
    void *ctx;
} SerCall;

/* one chunk's save or load; a fault in a module's callback is its own, the other chunks go on */
typedef struct {
    void *rm, *stream;
    int i, reading;
} ChunkCall;

static void chunk_work(void *p) {
    ChunkCall *c = (ChunkCall *)p;
    int i = c->i;
    HlibSaver sv = {0};
    sv.stream = c->stream;
    sv.writing = !c->reading;
    sv.chunk = s_chunks[i].chunk;
    sv.version = s_chunks[i].version;
    sv.global = s_chunks[i].global;
    if (!c->reading) {
        if (s_chunks[i].save) s_chunks[i].save(&sv, s_chunks[i].version);
        return;
    }
    int found = hlib_block_find(c->stream, s_chunks[i].chunk, &sv.version, &sv.payload, &sv.payload_len);
    if (!s_chunks[i].load) return;
    if (sv.global && !global_take(i, &sv, found)) { /* passed over for a newer block: handed on as absent */
        found = 0;
        sv.payload = NULL;
        sv.payload_len = 0;
    }
    if (found)
        s_chunks[i].load(&sv, sv.version);
    else
        s_chunks[i].load(&sv, 0); /* no block: an older save, the module keeps its defaults */
}

static void run_chunks(SerCall *c, int reading) {
    for (int i = 0; i < s_chunk_count; i++) {
        ChunkCall cc = {c->rm, c->stream, i, reading};
        char site[64];
        hlib_format(site, sizeof(site), "persist %s %s", reading ? "load" : "save", s_chunks[i].chunk);
        hlib_guarded(site, chunk_work, &cc);
    }
}

static void serialize_work(void *p) {
    SerCall *c = (SerCall *)p;
    int mode = *(int *)((char *)c->ctx + T_OFF_SERCTX_MODE);
    if (mode == T_SER_MODE_PLACE_A || mode == T_SER_MODE_PLACE_B) {
        char *placement = *(char **)((char *)c->ctx + T_OFF_SERCTX_PLACEMENT);
        /* the session watch first: a key back from an earlier game ends before the island reaches a module */
        if (placement) hlib_session_merge(*(short *)(placement + T_OFF_PLACE_SESSION) & T_AREA_SESSION_MASK, c->rm);
        for (int i = 0; i < s_island_fns; i++) s_island_fn[i](c->rm, placement);
        return;
    }
    if (mode != T_SER_MODE_STATE_A && mode != T_SER_MODE_STATE_B) return;
    int reading = *((unsigned char *)c->stream + T_OFF_STREAM_READ) != 0;
    if (reading) hlib_session_loaded(c->rm);
    s_session = c->rm;
    __try {
        run_chunks(c, reading);
    } __finally {
        s_session = NULL;
    }
}

static void (*o_serialize)(void *, void *, void *);

static void serialize_observer(void *rm, void *stream, void *ctx) {
    o_serialize(rm, stream, ctx);
    SerCall c = {rm, stream, ctx};
    hlib_guarded("persist", serialize_work, &c);
}

void hlib_config_persist(void); /* config.c: the simulation settings ride every save that carries a chunk */
void hlib_rngsalt_register(void); /* rngsalt.c: so does the game salt of the draws */

/* placed on the first pass with a chunk or an island listener and the serializer's targets matched; a chunk
   registered later rides it as well */
void hlib_persist_install(int final) {
    static int done, config;
    if (done || (!s_chunk_count && !s_island_fns)) return;
    if (!hlib_target_ok(T_REGROW_SERIALIZE) || !hlib_target_ok(T_SER_NAME_ADD) || !hlib_target_ok(T_SER_NAME_FIND) ||
        !hlib_target_ok(T_SER_FIND_ATTRIB) || !hlib_target_ok(T_SER_WRITE_ATTRIB)) {
        if (final) {
            done = 1;
            hlib_logf("persist: %d chunk(s) registered; the session serializer is not available, save I/O idle",
                       (int)s_chunk_count);
        }
        return;
    }
    done = 1;
    if (s_chunk_count && !config++) {
        hlib_config_persist();
        hlib_rngsalt_register();
    }
    HlibLayer l = {T_REGROW_SERIALIZE, HLIB_AFTER, (void *)serialize_observer, (void **)&o_serialize, 0};
    if (hlib_layers("persist", &l, 1))
        hlib_logf("persist: %d chunk(s) ride the session state serializer", (int)s_chunk_count);
}

/* Live reload (switch live_reload, off): a changed model, model config or effect file of an enabled mod is handed to
   the engine's own file-change handler of its manager (the model, config and shader managers keep them; no retail
   code calls them), as the studio's file watcher would have: a made-up file event, one request a frame, at the
   render-safe point (a layer before the render engine's BeginFrame). Requests come from a key (Ctrl+Shift+R: a
   change scan), the trigger file mods/live-reload.txt (its lines, or a scan) and later the live link (livereload.h).
   The pure part is reload_logic.c. Unverified in game. */
#include "hlib.h"
#include "livelink.h"
#include "livereload.h"
#include "reload_logic.h"
#include "targets.h"
#include "util.h"
#include <stdlib.h>
#include <string.h>

#define TRIGGER_FILE G_TRIGGER_FILE
#define LOADER_LOG G_LOADER_LOG
#define CHANGES_MAX 4096
#define MODS_RETRY_MS 2000
#define RESULTS_MAX 512

typedef void *(*FileEventFn)(void *listener, void *event);
typedef int64_t (*BeginFrameFn)(void *env, int64_t target, float a, float b);

static HlibLock g_lock;
static SRWLOCK s_qlock = SRWLOCK_INIT;
static RLQueue s_q;                     /* under s_qlock; only the render point pops */
static volatile LONG s_queued, s_on, s_mods_ready, s_scan_busy, s_batch;
static RLMods s_mods;                   /* written once before s_mods_ready */
static RLSnap s_last;                   /* the scan thread's */
static int s_have_baseline, s_models = 1, s_effects = 1, s_key = 'R', s_poll_ms = 500, s_wait = 600;
static int n_wait, n_vt;
static BeginFrameFn o_begin;
static volatile LONG s_render_tid, s_ui_tid;
static HlibReloadResult s_res[RESULTS_MAX]; /* under s_qlock: what each request came to, for the live link's answers */
static uint32_t s_res_batch[RESULTS_MAX];
static unsigned s_res_n;

static void result_add(uint32_t batch, int kind, const char *path, int code, double ms, const char *why) {
    AcquireSRWLockExclusive(&s_qlock);
    HlibReloadResult *r = &s_res[s_res_n % RESULTS_MAX];
    r->kind = kind, r->code = code, r->ms = ms;
    hlib_format(r->path, sizeof r->path, "%s", path);
    hlib_format(r->why, sizeof r->why, "%s", why ? why : "");
    s_res_batch[s_res_n % RESULTS_MAX] = batch;
    s_res_n++;
    ReleaseSRWLockExclusive(&s_qlock);
}

static void *held(int data_target) {
    void **h = (void **)hlib_target(data_target);
    return h ? *h : NULL;
}

/* the manager's file listener, when its slot still holds the handler this build was read with */
static void *listener_of(void *mgr, int handler, const char *what) {
    if (!mgr) return NULL;
    void *l = (unsigned char *)mgr + T_OFF_FILELISTENER;
    void **vt = *(void ***)l;
    if (vt && vt[T_FILELISTENER_SLOT] == hlib_target(handler)) return l;
    hlib_log_n(&n_vt, 8, "live reload: the %s manager's file listener does not hold the expected handler; nothing called",
                what);
    return NULL;
}

static void *config_manager(void) {
    unsigned char *engine = (unsigned char *)held(T_DATA_RENDER_ENGINE);
    return engine ? *(void **)(engine + T_OFF_RENDER_SUBSYSTEMS + T_RENDER_SUBSYSTEM_CONFIG * sizeof(void *)) : NULL;
}

static int loading(void) {
    unsigned char *owner = (unsigned char *)held(T_DATA_SESSION_LOADER_OWNER), *job;
    if (!owner) return 0;
    job = *(unsigned char **)(owner + T_OFF_LOADER_JOB);
    return job && *(uint64_t *)(job + T_OFF_LOADER_ACTIVE) != 0;
}

/* an engine std::wstring's characters; 0 when it does not look like a path */
static const uint16_t *wchars(const unsigned char *s, int *len) {
    uint64_t n = *(const uint64_t *)(s + T_STD_STR_SIZE), cap = *(const uint64_t *)(s + T_STD_STR_CAP);
    if (n == 0 || n >= RL_PATH_MAX || cap < n) return NULL;
    *len = (int)n;
    return cap > T_STD_WSTR_INLINE ? *(const uint16_t *const *)s : (const uint16_t *)s;
}

static int weq(const uint16_t *w, int n, const char *s) {
    for (int i = 0; i < n; i++) {
        uint16_t c = w[i] == '\\' ? '/' : w[i];
        if (c >= 'A' && c <= 'Z') c = (uint16_t)(c - 'A' + 'a');
        if (!s[i] || c != (unsigned char)s[i]) return 0;
    }
    return s[n] == 0;
}

/* the model cache's entry for a path: its model object, its key copied for the event; 0 not loaded */
static unsigned char *find_model(const char *path, uint16_t *key, int *klen) {
    char want[RL_PATH_MAX];
    unsigned char *mgr = (unsigned char *)held(T_DATA_MODEL_MANAGER), *head, *node;
    rl_model_key(path, want, sizeof want);
    if (!mgr || !(head = *(unsigned char **)(mgr + T_OFF_MODELMGR_MODELS))) return NULL;
    node = *(unsigned char **)head;
    for (int k = 0; node && node != head && k < T_MODELS_WALK_MAX; k++, node = *(unsigned char **)node) {
        int n;
        const uint16_t *w = wchars(node + T_OFF_MODELNODE_KEY, &n);
        if (w && weq(w, n, want)) {
            memcpy(key, w, (size_t)n * 2);
            key[n] = 0;
            *klen = n;
            return *(unsigned char **)(node + T_OFF_MODELNODE_MODEL);
        }
    }
    return NULL;
}

/* the shader manager's effects of a path: how many, how many in use, the engine's spelling of the first */
static int find_effects(const char *path, int *in_use, uint16_t *spell, int *slen) {
    unsigned char *mgr = (unsigned char *)held(T_DATA_SHADER_MANAGER), *head, *node;
    int found = 0, k = 0;
    *in_use = 0;
    if (!mgr || !(head = *(unsigned char **)(mgr + T_OFF_SHADERMGR_EFFECTS))) return 0;
    for (node = *(unsigned char **)(head + T_OFF_TREE_LEFT); node && node != head && k < T_MODELS_WALK_MAX; k++) {
        unsigned char *fx = *(unsigned char **)(node + T_OFF_EFFECTNODE_EFFECT), *r;
        int n;
        const uint16_t *w = fx ? wchars(fx + T_OFF_EFFECT_PATH, &n) : NULL;
        if (w && weq(w, n, path)) {
            if (!found++) memcpy(spell, w, (size_t)n * 2), spell[n] = 0, *slen = n;
            if (*(int *)(fx + T_OFF_EFFECT_USERS) > 0) (*in_use)++;
        }
        r = *(unsigned char **)(node + T_OFF_TREE_RIGHT); /* the in-order successor */
        if (!*(r + T_OFF_TREE_ISNIL)) {
            node = r;
            while (!*(*(unsigned char **)(node + T_OFF_TREE_LEFT) + T_OFF_TREE_ISNIL))
                node = *(unsigned char **)(node + T_OFF_TREE_LEFT);
        } else {
            unsigned char *p = *(unsigned char **)(node + T_OFF_TREE_PARENT);
            while (!*(p + T_OFF_TREE_ISNIL) && node == *(unsigned char **)(p + T_OFF_TREE_RIGHT)) {
                node = p;
                p = *(unsigned char **)(p + T_OFF_TREE_PARENT);
            }
            node = p;
        }
    }
    return found;
}

static int file_ok(int mod, const char *rel) {
    char full[RL_PATH_MAX * 2];
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (hlib_format(full, sizeof full, "%s/%s", s_mods.mod[mod].folder, rel) >= (int)sizeof full - 1) return 0;
    if (!GetFileAttributesExA(full, GetFileExInfoStandard, &a) || (a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) return 0;
    return a.nFileSizeHigh || a.nFileSizeLow;
}

/* the engine's handler on a made-up event; a C++ throw (the file read's "Can't open the file") is caught here, any
   other fault goes on to the module's guard */
static int call_handler(void *listener, const uint16_t *path, int len, int *threw) {
    static const RLEventLayout layout = {T_FILE_EVENT_BYTES, T_OFF_FILE_EVENT_PATH, T_STD_STR_SIZE, T_STD_STR_CAP,
                                         T_STD_WSTR_INLINE, T_OFF_FILE_EVENT_KIND, T_FILE_EVENT_CHANGED};
    uint64_t rec[T_FILE_EVENT_BYTES / sizeof(uint64_t)];
    uint16_t heap[RL_PATH_MAX];
    FileEventFn fn = *(FileEventFn *)(*(unsigned char **)listener + T_FILELISTENER_SLOT * sizeof(void *));
    *threw = 0;
    if (!rl_event_build((unsigned char *)rec, &layout, path, len, heap)) return 0;
    __try {
        fn(listener, rec);
    } __except (GetExceptionCode() == RL_CXX_EXCEPTION ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) {
        *threw = 1;
        return 0;
    }
    return 1;
}

static double ms_since(LARGE_INTEGER t0) {
    LARGE_INTEGER t1, f;
    QueryPerformanceCounter(&t1);
    QueryPerformanceFrequency(&f);
    return (double)(t1.QuadPart - t0.QuadPart) * 1000.0 / (double)f.QuadPart;
}

static const char *mod_id(int mod) { return mod >= 0 && mod < s_mods.n ? s_mods.mod[mod].id : "?"; }

/* The link's guards for the queue (a save, a session change, an asset reload: the request waits), asked at most every
   BUSY_ASK_MS while requests wait: the save test reads the profiles' folders, and this runs on the render thread. */
#define BUSY_ASK_MS 100
static const char *engine_busy(void) {
    static ULONGLONG next;
    static const char *why;
    ULONGLONG now = GetTickCount64();
    if (now >= next) why = hlib_live_link_busy(0), next = now + BUSY_ASK_MS;
    return why;
}

/* one request: decide, run, log; 1 when it leaves the queue */
static int run_request(RLRequest *r) {
    uint16_t w[RL_PATH_MAX];
    int len = rl_widen(r->path, w, RL_PATH_MAX), threw = 0, in_use = 0, ok = 0;
    unsigned char *model = NULL;
    void *listener = NULL;
    const char *busy = engine_busy();
    RLState st = {hlib_human_count() > 1, loading(), file_ok(r->mod, r->path), 1, 0, r->tries, s_wait, busy != NULL};
    LARGE_INTEGER t0;
    if (r->kind == RL_MODEL) {
        model = find_model(r->path, w, &len);
        st.found = model != NULL;
        st.streaming = model && *(model + T_OFF_MODEL_LOADING);
    }
    int d = rl_decide(&st);
    if (d == RL_WAIT) {
        if (!r->tries) hlib_log_n(&n_wait, 64, "live reload: %s %s of %s waits (%s)", rl_kind_name(r->kind), r->path,
                                   mod_id(r->mod), busy ? busy : st.loading ? "a session loads" : "its levels of detail still load");
        r->tries++;
        return 0;
    }
    if (d != RL_RUN) {
        const char *why = d == RL_SKIP_NOT_LOADED ? "not loaded in this game, nothing to reload (it is read fresh when first used)"
                                                  : rl_decision_name(d);
        hlib_logf("live reload: %s %s of %s (request %u): %s", rl_kind_name(r->kind), r->path, mod_id(r->mod), r->batch, why);
        result_add(r->batch, r->kind, r->path, d == RL_SKIP_NOT_LOADED ? HLIB_RELOAD_NOT_LOADED : HLIB_RELOAD_REFUSED, 0, why);
        return 1;
    }
    if (len <= 0) {
        result_add(r->batch, r->kind, r->path, HLIB_RELOAD_REFUSED, 0, "the path could not be read as a file name");
        return 1;
    }
    int found = 0;
    if (r->kind == RL_MODEL) listener = listener_of(held(T_DATA_MODEL_MANAGER), T_MODEL_FILE_EVENT, "model");
    else if (r->kind == RL_CONFIG) listener = listener_of(config_manager(), T_CONFIG_FILE_EVENT, "config");
    else if (r->kind == RL_EFFECT) {
        listener = listener_of(held(T_DATA_SHADER_MANAGER), T_SHADER_FILE_EVENT, "shader");
        found = find_effects(r->path, &in_use, w, &len);
    }
    if (!listener) {
        result_add(r->batch, r->kind, r->path, HLIB_RELOAD_REFUSED, 0,
                   "this game build's file listener is not the one the library was read with");
        return 1;
    }
    QueryPerformanceCounter(&t0);
    ok = call_handler(listener, w, len, &threw);
    double ms = ms_since(t0);
    result_add(r->batch, r->kind, r->path,
               threw || !ok ? HLIB_RELOAD_REFUSED : r->kind == RL_CONFIG ? HLIB_RELOAD_HANDED : HLIB_RELOAD_DONE, ms,
               threw ? "the engine refused the file (its read failed); the old one stays"
                     : !ok ? "the event could not be made" : "");
    if (threw)
        hlib_logf("live reload: %s %s of %s (request %u): the engine refused the file (its read threw); the old one stays",
                   rl_kind_name(r->kind), r->path, mod_id(r->mod), r->batch);
    else if (!ok) hlib_logf("live reload: %s %s: the event could not be made", rl_kind_name(r->kind), r->path);
    else if (r->kind == RL_MODEL)
        hlib_logf("live reload: model %s of %s (request %u): reloaded in %.1f ms by the engine's model handler, its "
                   "objects rebound%s", r->path, mod_id(r->mod), r->batch, ms,
                   *(model + T_OFF_MODEL_LOADING) ? "; a level of detail still loads" : "");
    else if (r->kind == RL_CONFIG)
        hlib_logf("live reload: config %s of %s (request %u): handed to the engine's config handler, %.1f ms (it re-applies "
                   "a config it has loaded and says nothing otherwise)", r->path, mod_id(r->mod), r->batch, ms);
    else
        hlib_logf("live reload: effect %s of %s (request %u): %.1f ms, its material definitions parsed again; %d effect(s) "
                   "of that file loaded, %d in use (their variants compile again: expect a hitch)", r->path,
                   mod_id(r->mod), r->batch, ms, found, in_use);
    return 1;
}

static void drain_work(void *unused) {
    RLRequest r;
    (void)unused;
    AcquireSRWLockExclusive(&s_qlock);
    RLRequest *front = rl_queue_front(&s_q);
    if (front) r = *front;
    ReleaseSRWLockExclusive(&s_qlock);
    if (!front) return;
    int done = run_request(&r);
    AcquireSRWLockExclusive(&s_qlock);
    if (done) {
        rl_queue_pop(&s_q);
        InterlockedExchange(&s_queued, s_q.n);
    } else if ((front = rl_queue_front(&s_q)) != NULL) {
        front->tries = r.tries;
    }
    ReleaseSRWLockExclusive(&s_qlock);
}

/* the render-safe point: once a frame, before the render engine's frame begins */
static int64_t begin_frame_layer(void *env, int64_t target, float a, float b) {
    if (s_queued && s_on) {
        if (!s_render_tid && !InterlockedExchange(&s_render_tid, (LONG)GetCurrentThreadId()))
            hlib_logf("live reload: the render-safe point runs on thread %lu (the UI frame's: %ld)", GetCurrentThreadId(),
                       s_ui_tid);
        hlib_locked(&g_lock, "live reload", drain_work, NULL);
    }
    return o_begin(env, target, a, b);
}

/* the queue's entry */

static int queue_items(RLRequest *items, int n, const char *source, uint32_t batch) {
    int counts[RL_INCLUDE + 1] = {0}, q;
    for (int i = 0; i < n; i++) {
        int off = ((items[i].kind == RL_MODEL || items[i].kind == RL_CONFIG) && !s_models) || (items[i].kind == RL_EFFECT && !s_effects);
        if (off) {
            result_add(batch, items[i].kind, items[i].path, HLIB_RELOAD_REFUSED, 0,
                       "switched off by live_reload_models / live_reload_effects");
            items[i].kind = RL_NONE;
        }
        counts[items[i].kind]++;
    }
    AcquireSRWLockExclusive(&s_qlock);
    q = rl_queue_batch(&s_q, items, n, batch);
    InterlockedExchange(&s_queued, s_q.n);
    ReleaseSRWLockExclusive(&s_qlock);
    hlib_logf("live reload: request %u (%s): %d model(s), %d config(s), %d effect(s) changed, %d queued%s", batch, source,
               counts[RL_MODEL], counts[RL_CONFIG], counts[RL_EFFECT], q,
               counts[RL_NONE] ? " (some switched off by live_reload_models / live_reload_effects)" : "");
    return q;
}

static int has_file(int mod, const char *rel, void *ctx) {
    (void)ctx;
    return file_ok(mod, rel);
}

/* one number for the whole request: what is queued and what is refused are answered together */
int hlib_live_reload_batch(const char *id, const char *const *paths, int n, const char *source, uint32_t *batch) {
    static RLRequest items[CHANGES_MAX];
    static RLRefused refused[CHANGES_MAX];
    static SRWLOCK lock = SRWLOCK_INIT;
    int k, nr = 0, q;
    uint32_t number;
    if (batch) *batch = 0;
    if (!s_on || !s_mods_ready) return -1;
    number = (uint32_t)InterlockedIncrement(&s_batch);
    if (batch) *batch = number;
    AcquireSRWLockExclusive(&lock);
    k = rl_plan_batch(&s_mods, id, paths, n, number, has_file, NULL, items, refused, CHANGES_MAX, &nr);
    for (int i = 0; i < nr; i++) {
        hlib_logf("live reload: %s (%s) refused: %s", refused[i].path, source, refused[i].why);
        result_add(number, RL_NONE, refused[i].path, HLIB_RELOAD_REFUSED, 0, refused[i].why);
    }
    q = k ? queue_items(items, k, source, number) : 0;
    ReleaseSRWLockExclusive(&lock);
    return q;
}

int hlib_live_reload_mods(char (*id)[64], char (*folder)[260], int cap) {
    int n = 0;
    if (!s_on || !s_mods_ready) return 0;
    for (; n < s_mods.n && n < cap; n++) {
        hlib_format(id[n], 64, "%s", s_mods.mod[n].id);
        hlib_format(folder[n], 260, "%s", s_mods.mod[n].folder);
    }
    return n;
}

int hlib_live_reload_loading(void) { return loading(); }

int hlib_live_reload_files(const char *id, const char *const *paths, int n, const char *source) {
    return hlib_live_reload_batch(id, paths, n, source, NULL);
}

int hlib_live_reload_results(uint32_t batch, HlibReloadResult *out, int cap, int *pending) {
    int n = 0;
    AcquireSRWLockExclusive(&s_qlock);
    if (pending) {
        *pending = 0;
        for (int i = 0; i < s_q.n; i++) *pending += s_q.item[(s_q.head + i) % RL_QUEUE_MAX].batch == batch;
    }
    unsigned first = s_res_n > RESULTS_MAX ? s_res_n - RESULTS_MAX : 0;
    for (unsigned i = first; i < s_res_n && n < cap; i++)
        if (s_res_batch[i % RESULTS_MAX] == batch) out[n++] = s_res[i % RESULTS_MAX];
    ReleaseSRWLockExclusive(&s_qlock);
    return n;
}

/* the scan */

static void walk(RLSnap *s, int mod, const char *dir, const char *rel) {
    char pat[RL_PATH_MAX * 2], sub[RL_PATH_MAX];
    WIN32_FIND_DATAA f;
    if (hlib_format(pat, sizeof pat, "%s/%s*", dir, rel) >= (int)sizeof pat - 1) return;
    HANDLE h = FindFirstFileExA(pat, FindExInfoBasic, &f, FindExSearchNameMatch, NULL, FIND_FIRST_EX_LARGE_FETCH);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (f.cFileName[0] == '.') continue;
        if (hlib_format(sub, sizeof sub, "%s%s", rel, f.cFileName) >= (int)sizeof sub - 1) continue;
        if (f.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (hlib_format(sub, sizeof sub, "%s%s/", rel, f.cFileName) < (int)sizeof sub - 1) walk(s, mod, dir, sub);
        } else {
            uint64_t t = ((uint64_t)f.ftLastWriteTime.dwHighDateTime << 32) | f.ftLastWriteTime.dwLowDateTime;
            rl_snap_add(s, mod, sub, t);
        }
    } while (FindNextFileA(h, &f));
    FindClose(h);
}

static int includes(int mod, const char *fx, const char *fxh, void *ctx) {
    char full[RL_PATH_MAX * 2];
    size_t len = 0;
    (void)ctx;
    if (hlib_format(full, sizeof full, "%s/%s", s_mods.mod[mod].folder, fx) >= (int)sizeof full - 1) return 0;
    char *text = hlib_file_read(full, &len);
    int yes = text && rl_fx_includes(text, len, fxh);
    hlib_file_free(text);
    return yes;
}

static void scan_work(void *source) {
    static RLRequest changes[CHANGES_MAX];
    RLSnap now;
    LARGE_INTEGER t0;
    QueryPerformanceCounter(&t0);
    rl_snap_init(&now);
    for (int m = 0; m < s_mods.n; m++) walk(&now, m, s_mods.mod[m].folder, "");
    rl_snap_sort(&now);
    if (!s_have_baseline) {
        hlib_logf("live reload: %d model, config and effect file(s) of %d enabled mod(s) noted in %.0f ms; a change after "
                   "this is reloaded on Ctrl+Shift+R or when %s is saved", now.n, s_mods.n, ms_since(t0), TRIGGER_FILE);
        rl_snap_move(&s_last, &now);
        s_have_baseline = 1;
        return;
    }
    int n = rl_snap_changes(&s_last, &now, changes, CHANGES_MAX, includes, NULL);
    hlib_logf("live reload: scan (%s): %d file(s) looked at in %.0f ms, %d changed", (const char *)source, now.n,
               ms_since(t0), n);
    rl_snap_move(&s_last, &now);
    if (n) queue_items(changes, n, (const char *)source, (uint32_t)InterlockedIncrement(&s_batch));
}

static DWORD WINAPI scan_thread(void *source) {
    hlib_guarded("live reload scan", scan_work, source);
    InterlockedExchange(&s_scan_busy, 0);
    return 0;
}

int hlib_live_reload_scan(const char *source) {
    if (!s_on || !s_mods_ready) return -1;
    if (InterlockedCompareExchange(&s_scan_busy, 1, 0)) return 0;
    HANDLE h = CreateThread(NULL, 0, scan_thread, (void *)source, 0, NULL);
    if (!h) {
        InterlockedExchange(&s_scan_busy, 0);
        return 0;
    }
    CloseHandle(h);
    return 1;
}

/* the UI frame: the mods once the loader listed them, the key, the trigger file */

static void read_mods(void) {
    char path[MAX_PATH];
    size_t len = 0;
    static RLMods m;
    if (!hlib_user_path(path, LOADER_LOG)) return;
    char *log = hlib_file_read(path, &len);
    if (log && rl_parse_loader_log(log, &m) >= 0 && m.complete) {
        s_mods = m;
        InterlockedExchange(&s_mods_ready, 1);
        for (int i = 0; i < s_mods.n; i++) hlib_logf("live reload: enabled mod %s at %s", s_mods.mod[i].id, s_mods.mod[i].folder);
        hlib_live_reload_scan("the start");
    }
    hlib_file_free(log);
}

typedef struct {
    char id[64][64], path[64][RL_PATH_MAX + 64];
    const char *paths[64];
    int n;
} Lines;

static void take_line(const char *id, const char *path, void *ctx) {
    Lines *l = (Lines *)ctx;
    if (l->n >= 64) return;
    hlib_format(l->id[l->n], sizeof(l->id[0]), "%s", id);
    hlib_format(l->path[l->n], sizeof(l->path[0]), "%s", path);
    l->paths[l->n] = l->path[l->n];
    l->n++;
}

static void trigger_file(void) {
    static uint64_t seen;
    static int first = 1;
    static Lines l;
    char path[MAX_PATH], text[16384];
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!hlib_user_path(path, TRIGGER_FILE) || !GetFileAttributesExA(path, GetFileExInfoStandard, &a)) {
        first = 0; /* a file made later triggers */
        return;
    }
    uint64_t t = ((uint64_t)a.ftLastWriteTime.dwHighDateTime << 32) | a.ftLastWriteTime.dwLowDateTime;
    if (t == seen) return;
    seen = t;
    if (first) { /* there before this start: its time is the baseline */
        first = 0;
        return;
    }
    memset(&l, 0, sizeof l);
    if (hlib_read_text(path, text, sizeof text) < 0) text[0] = 0;
    if (!rl_trigger_lines(text, take_line, &l)) {
        if (!hlib_live_reload_scan(TRIGGER_FILE)) hlib_log("live reload: a scan runs already, the trigger file waits for none");
        return;
    }
    for (int i = 0; i < l.n && i < 64; i++) hlib_live_reload_files(l.id[i], &l.paths[i], 1, TRIGGER_FILE);
}

static void key(void) {
    static SHORT was;
    DWORD pid = 0;
    if (!s_key) return;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    SHORT down = pid == GetCurrentProcessId() && (GetAsyncKeyState(VK_CONTROL) & 0x8000) &&
                 (GetAsyncKeyState(VK_SHIFT) & 0x8000) && (GetAsyncKeyState(s_key) & 0x8000);
    if (down && !was && hlib_live_reload_scan("the key") == 0) hlib_log("live reload: a scan runs already");
    was = down;
}

static void frame_listener(void *ui) {
    static ULONGLONG next_mods, next_poll;
    ULONGLONG now = GetTickCount64();
    (void)ui;
    if (!s_ui_tid) InterlockedExchange(&s_ui_tid, (LONG)GetCurrentThreadId());
    if (!s_mods_ready) {
        if (now >= next_mods) next_mods = now + MODS_RETRY_MS, read_mods();
        return;
    }
    key();
    if (now >= next_poll) next_poll = now + (ULONGLONG)s_poll_ms, trigger_file();
}

static const int g_need[] = {T_RENDER_BEGIN_FRAME, T_DATA_RENDER_ENGINE, T_DATA_MODEL_MANAGER,
                             T_DATA_SESSION_LOADER_OWNER, T_DATA_SHADER_MANAGER, T_MODEL_FILE_EVENT,
                             T_CONFIG_FILE_EVENT, T_SHADER_FILE_EVENT, T_UI_FRAME, T_DATA_LOCAL_PLAYER};

/* the dormant start (no full mode): the live link brings the reload up beside it when both are switched on */
const int *hlib_live_reload_targets(int *n) {
    *n = (int)(sizeof g_need / sizeof g_need[0]);
    return g_need;
}

int hlib_live_reload_wanted(void) { return hlib_feature_on("live_reload"); }

static int lr_resolve(void) {
    for (size_t i = 0; i < sizeof(g_need) / sizeof(g_need[0]); i++)
        if (!hlib_target_ok(g_need[i])) return 0;
    return 1;
}

int hlib_live_reload_install(void) {
    static volatile LONG once;
    if (InterlockedCompareExchange(&once, 1, 0)) return s_on != 0;
    if (!lr_resolve()) return 0;
    hlib_lock_init(&g_lock, "live_reload");
    s_models = hlib_config_int("live_reload_models", 1);
    s_effects = hlib_config_int("live_reload_effects", 1);
    s_key = hlib_config_int("live_reload_key", 'R');
    s_poll_ms = hlib_config_int("live_reload_poll_ms", 500);
    s_wait = hlib_config_int("live_reload_wait_frames", 600);
    if (s_poll_ms < 100) s_poll_ms = 100;
    HlibLayer l = {T_RENDER_BEGIN_FRAME, HLIB_BEFORE, (void *)begin_frame_layer, (void **)&o_begin, 0};
    if (!hlib_layers("live_reload", &l, 1) || !hlib_on_ui_frame("live reload", frame_listener)) {
        hlib_log("live reload: the render frame or the UI frame was not reached, no reload");
        return 0;
    }
    InterlockedExchange(&s_on, 1);
    hlib_logf("live reload: ready (models and configs %s, effects %s; Ctrl+Shift+%c or saving %s: every changed file of "
               "the enabled mods); unverified in game", s_models ? "on" : "off", s_effects ? "on" : "off",
               s_key > ' ' && s_key < 127 ? s_key : '?', TRIGGER_FILE);
    return 1;
}

static void lr_install(void) { hlib_live_reload_install(); }

static const HlibModule g_mod = {"live_reload", lr_resolve, lr_install, 0};
void hlib_register_live_reload(void) { hlib_register_module(&g_mod, HLIB_DEV); }

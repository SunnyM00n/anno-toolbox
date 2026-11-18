/* Core services: logging, the running-build guard and target resolution, the SEH guard and module locks, the
   module/tick registries, and the watcher that brings a session's work up only on the build it was made for. The
   configuration is config.c, the session lifecycle session.c. */
#include "core_int.h"
#include "features_logic.h"
#include "framework.h"
#include "livelink.h"
#include "livereload.h"
#include "targets.h"
#include "util.h"
#include <stdarg.h>
#include <string.h>

/* logging */
/* The Documents folder as the shell has it (OneDrive's folder move, a relocated profile), in the ANSI code page the
   file calls of this library use; set once by the watcher, not in DllMain (loader lock). Until then, and when the shell
   cannot answer or the name has characters the code page lacks and no short name, %USERPROFILE%\Documents. */
static char s_docs[MAX_PATH];
static volatile LONG s_docs_ready;

typedef HRESULT(WINAPI *KnownFolderFn)(const GUID *, DWORD, HANDLE, wchar_t **);
typedef void(WINAPI *CoFreeFn)(void *);
typedef HRESULT(WINAPI *GuidFn)(const wchar_t *, GUID *);

static int docs_ansi(const wchar_t *w, char *out) {
    BOOL lossy = FALSE;
    return WideCharToMultiByte(CP_ACP, WC_NO_BEST_FIT_CHARS, w, -1, out, MAX_PATH, NULL, &lossy) > 0 && !lossy;
}

static void resolve_documents(void) {
    GUID documents; /* FOLDERID_Documents */
    HMODULE shell = LoadLibraryA("shell32.dll"), ole = LoadLibraryA("ole32.dll");
    GuidFn parse = ole ? (GuidFn)(void *)GetProcAddress(ole, "CLSIDFromString") : NULL;
    KnownFolderFn get = shell ? (KnownFolderFn)(void *)GetProcAddress(shell, "SHGetKnownFolderPath") : NULL;
    CoFreeFn release = ole ? (CoFreeFn)(void *)GetProcAddress(ole, "CoTaskMemFree") : NULL;
    wchar_t *w = NULL;
    char path[MAX_PATH];
    if (parse && get && release && 0 == parse(L"{FDD39AD0-238F-46AF-ADB4-6C85480369C7}", &documents) && 0 == get(&documents, 0, NULL, &w) && w) {
        int ok = docs_ansi(w, path);
        if (!ok) { /* a name the code page cannot hold: its 8.3 form, when the volume has one */
            wchar_t shortw[MAX_PATH];
            DWORD n = GetShortPathNameW(w, shortw, MAX_PATH);
            ok = n > 0 && n < MAX_PATH && docs_ansi(shortw, path);
        }
        release(w);
        if (ok && lstrlenA(path) + 40 < MAX_PATH) {
            lstrcpyA(s_docs, path);
            InterlockedExchange(&s_docs_ready, 1);
            hlib_logf("documents folder: %s", s_docs);
            return;
        }
        hlib_log("documents folder: the shell's path cannot be used with the ANSI file calls, using %USERPROFILE%");
    } else {
        hlib_log("documents folder: the shell did not answer, using %USERPROFILE%");
    }
}

/* <Documents>/<the game's folder>/<rel> (game.h); 0 when the path does not fit */
int hlib_user_path(char *out, const char *rel) {
    static const char dir[] = G_USER_FOLDER;
    DWORD n;
    if (s_docs_ready) {
        n = (DWORD)lstrlenA(s_docs);
        lstrcpyA(out, s_docs);
    } else {
        static const char docs[] = "/Documents";
        n = GetEnvironmentVariableA("USERPROFILE", out, MAX_PATH);
        if (n == 0 || n + sizeof(docs) >= MAX_PATH) return 0;
        lstrcatA(out, docs);
        n += sizeof(docs) - 1;
    }
    if (n + sizeof(dir) + lstrlenA(rel) >= MAX_PATH) return 0;
    lstrcatA(out, dir);
    lstrcatA(out, rel);
    return 1;
}

int hlib_read_text(const char *path, char *buf, int cap) {
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    DWORD got = 0;
    if (cap < 1) return -1;
    buf[0] = 0;
    if (h == INVALID_HANDLE_VALUE) return -1;
    BOOL ok = ReadFile(h, buf, (DWORD)cap - 1, &got, NULL);
    CloseHandle(h);
    buf[ok ? got : 0] = 0;
    return ok ? (int)got : -1;
}

/* The log is appended to across starts; past LOG_MAX it becomes helper-library.old.log (one older file kept) and starts
   anew. One thread at a time moves it; the others write on. */
#define LOG_MAX (8ll << 20)
static volatile LONG s_log_moving;

static HANDLE log_open(const char *path) {
    return CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_ALWAYS,
                       FILE_ATTRIBUTE_NORMAL, NULL);
}

static void write_log(const char *line, int len) {
    char path[MAX_PATH], old[MAX_PATH];
    LARGE_INTEGER size;
    if (!hlib_user_path(path, G_LOG_FILE)) return;
    HANDLE h = log_open(path);
    if (h == INVALID_HANDLE_VALUE) return;
    if (GetFileSizeEx(h, &size) && size.QuadPart > LOG_MAX && hlib_user_path(old, G_LOG_OLD_FILE) &&
        !InterlockedCompareExchange(&s_log_moving, 1, 0)) {
        CloseHandle(h);
        MoveFileExA(path, old, MOVEFILE_REPLACE_EXISTING);
        InterlockedExchange(&s_log_moving, 0);
        if ((h = log_open(path)) == INVALID_HANDLE_VALUE) return;
    }
    DWORD w;
    WriteFile(h, line, len, &w, NULL);
    CloseHandle(h);
}

void hlib_log(const char *msg) {
    SYSTEMTIME t;
    GetLocalTime(&t);
    char line[1200];
    int len = hlib_format(line, (int)sizeof(line) - 2, "%04d-%02d-%02d %02d:%02d:%02d %s", t.wYear, t.wMonth, t.wDay,
                           t.wHour, t.wMinute, t.wSecond, msg ? msg : "");
    line[len++] = '\r';
    line[len++] = '\n';
    write_log(line, len);
}

void hlib_logf(const char *fmt, ...) {
    char msg[1100];
    va_list ap;
    va_start(ap, fmt);
    hlib_vformat(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    hlib_log(msg);
}

void hlib_log_n(int *counter, int cap, const char *fmt, ...) {
    char msg[1100];
    va_list ap;
    if (*(volatile int *)counter >= cap || InterlockedIncrement((volatile LONG *)counter) > cap) return;
    va_start(ap, fmt);
    hlib_vformat(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    hlib_log(msg);
}

void hlib_log_once(volatile LONG *done, const char *fmt, ...) {
    char msg[1100];
    va_list ap;
    if (*done || InterlockedIncrement(done) != 1) return;
    va_start(ap, fmt);
    hlib_vformat(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    hlib_log(msg);
}

void hlib_log_every(HlibLogRate *rate, int ms, const char *fmt, ...) {
    char msg[1100];
    va_list ap;
    LONG64 now = (LONG64)GetTickCount64(), next = rate->next_ms;
    if (now < next || InterlockedCompareExchange64(&rate->next_ms, now + ms, next) != next) {
        InterlockedIncrement(&rate->held);
        return;
    }
    int held = (int)InterlockedExchange(&rate->held, 0), len;
    va_start(ap, fmt);
    len = hlib_vformat(msg, sizeof(msg) - 40, fmt, ap);
    va_end(ap);
    if (held) hlib_format(msg + len, 40, " (%d more held back)", held);
    hlib_log(msg);
}

/* a module's switch: its own key, else an older key it was switched by (a module split in two) */
static int module_on(const HlibModule *m) {
    if (m->also_key && !cfg_pair(m->name) && cfg_pair(m->also_key)) return hlib_feature_on(m->also_key);
    return hlib_feature_on(m->name);
}

const HlibModule *s_modules[MAX_MODULES];
int s_module_count;
int s_module_active[MAX_MODULES];
int s_module_settled[MAX_MODULES];
static int s_module_class[MAX_MODULES];
static int s_module_installed[MAX_MODULES]; /* its install ran clean, at start or at a run-time switch */
static volatile LONG s_module_restoring[MAX_MODULES]; /* switched off, its subscriptions run its restore pass */

/* running build guard and target resolution */
static unsigned char *s_base;
static int s_target_ok[T_COUNT];

unsigned char *hlib_image_base(void) { return s_base; }
int hlib_in_image(const void *p) {
    return (const unsigned char *)p >= s_base && (const unsigned char *)p < s_base + T_BUILD_SIZEOFIMAGE;
}
unsigned long long hlib_exe_addr(const void *p) {
    return hlib_in_image(p) ? 0x140000000ULL + (unsigned long long)((const unsigned char *)p - s_base) : (unsigned long long)(uintptr_t)p;
}
int hlib_target_ok(int id) { return id >= 0 && id < T_COUNT && s_target_ok[id]; }
int hlib_targets_ok(const int *ids, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (!hlib_target_ok(ids[i])) return 0;
    return 1;
}
void *hlib_target(int id) {
    if (!hlib_target_ok(id) || !g_targets[id].rva) return NULL;
    return s_base + g_targets[id].rva;
}

static int build_matches(void) {
    char exe[MAX_PATH];
    GetModuleFileNameA(NULL, exe, MAX_PATH);
    HANDLE h = CreateFileA(exe, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    LARGE_INTEGER sz = {0};
    if (h != INVALID_HANDLE_VALUE) {
        GetFileSizeEx(h, &sz);
        CloseHandle(h);
    }
    if ((unsigned long long)sz.QuadPart != T_BUILD_FILESIZE) {
        hlib_logf("guard: file size %lld != expected %llu (%s), library inert", (long long)sz.QuadPart,
                   (unsigned long long)T_BUILD_FILESIZE, G_ID);
        return 0;
    }
    unsigned char *nt = s_base + *(DWORD *)(s_base + 0x3c);
    if (*(DWORD *)nt != 0x00004550u) {
        hlib_log("guard: no PE header, library inert");
        return 0;
    }
    if (*(DWORD *)(nt + 8) != T_BUILD_TIMESTAMP || *(DWORD *)(nt + 0x50) != T_BUILD_SIZEOFIMAGE) {
        hlib_log("guard: build stamp mismatch, library inert");
        return 0;
    }
    return 1;
}

static int target_bytes_present(int id) {
    const HlibTarget *t = &g_targets[id];
    if (!t->rva || !t->verify_len) return t->rva != 0; /* no bytes to check: trust the pinned build */
    for (int i = 0; i < t->verify_len; i++)
        if (s_base[t->rva + i] != t->verify[i]) return 0;
    return 1;
}

/* A target is polled when it has bytes to check; one without an address never will match. */
static int target_pollable(int id) { return g_targets[id].rva && g_targets[id].verify_len; }

static void log_unresolved(int id) {
    const HlibTarget *t = &g_targets[id];
    if (!t->rva) {
        hlib_logf("target %s: unresolved, no address recorded for this build", g_target_names[id]);
        return;
    }
    char found[3 * 24 + 1], want[3 * 24 + 1];
    int n = t->verify_len < 24 ? t->verify_len : 24;
    for (int i = 0; i < n; i++) {
        hlib_format(found + 3 * i, 4, "%02x ", s_base[t->rva + i]);
        hlib_format(want + 3 * i, 4, "%02x ", t->verify[i]);
    }
    found[3 * n] = want[3 * n] = 0;
    hlib_logf("target %s: unresolved, rva 0x%X, found %s| expected %s", g_target_names[id], (unsigned)t->rva, found,
               want);
}

/* SEH guard */
int hlib_fault_filter(unsigned long code) {
    switch (code) {
    case 0xE06D7363u: /* a C++ exception: the script engine's errors travel so, its own handler takes them */
    case 0x40010006u: /* debug output (narrow, wide) and a thread name for the debugger: notices, not faults */
    case 0x4001000Au:
    case 0x406D1388u:
        return EXCEPTION_CONTINUE_SEARCH;
    }
    return EXCEPTION_EXECUTE_HANDLER;
}

/* where a fault happened: the code address (in the game's image or this library) and, for an access violation,
   the access and its address, so the log names the bad read */
typedef struct {
    void *pc;
    ULONG_PTR access, addr;
    int av;
} FaultNote;

static int fault_note(EXCEPTION_POINTERS *ep, FaultNote *n) {
    int r = hlib_fault_filter(ep->ExceptionRecord->ExceptionCode);
    if (r == EXCEPTION_EXECUTE_HANDLER) {
        n->pc = ep->ExceptionRecord->ExceptionAddress;
        n->av = ep->ExceptionRecord->ExceptionCode == 0xC0000005u && ep->ExceptionRecord->NumberParameters >= 2;
        n->access = n->av ? ep->ExceptionRecord->ExceptionInformation[0] : 0;
        n->addr = n->av ? ep->ExceptionRecord->ExceptionInformation[1] : 0;
    }
    return r;
}

static void fault_where(const FaultNote *n, char *out, int cap) {
    HMODULE self = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)(void *)&fault_where, &self);
    HMODULE in = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)n->pc, &in);
    unsigned char *pc = (unsigned char *)n->pc;
    int at;
    if (hlib_in_image(pc)) /* as the file's own address */
        at = hlib_format(out, cap, " at exe 0x%llX", 0x140000000ULL + (unsigned long long)(pc - s_base));
    else if (in && in == self)
        at = hlib_format(out, cap, " at proxy+0x%llX", (unsigned long long)(pc - (unsigned char *)self));
    else
        at = hlib_format(out, cap, " at %p", n->pc);
    if (n->av)
        hlib_format(out + at, cap - at, ", %s 0x%llX", n->access == 1 ? "writing" : n->access == 8 ? "executing" : "reading",
                     (unsigned long long)n->addr);
}

/* one rate per fault site (hashed): a site that faults on every call logs once in a while, with the count held back */
#define FAULT_RATES 32
#define FAULT_LOG_MS 5000
static struct {
    volatile LONG hash;
    HlibLogRate rate;
} s_fault_rate[FAULT_RATES];

static HlibLogRate *fault_rate(const char *site) {
    LONG h = (LONG)(hlib_fnv1a_text(site) | 1);
    for (int j = 0; j < FAULT_RATES; j++) {
        int at = (int)(((unsigned)h + (unsigned)j) % FAULT_RATES);
        LONG cur = s_fault_rate[at].hash;
        if (cur == h || (cur == 0 && 0 == InterlockedCompareExchange(&s_fault_rate[at].hash, h, 0))) return &s_fault_rate[at].rate;
        if (s_fault_rate[at].hash == h) return &s_fault_rate[at].rate;
    }
    return &s_fault_rate[(unsigned)h % FAULT_RATES].rate;
}

int hlib_guarded(const char *site, HlibThunk fn, void *ctx) {
    HlibScope sc;
    FaultNote note = {0};
    int saved = hlib_scope_save(&sc);
    __try {
        __try {
            fn(ctx);
        } __finally {
            if (AbnormalTermination() && saved) hlib_scope_leave(&sc); /* a scope switched inside comes back */
        }
        return 1;
    } __except (fault_note(GetExceptionInformation(), &note)) {
        char where[96];
        fault_where(&note, where, sizeof(where));
        hlib_log_every(fault_rate(site), FAULT_LOG_MS, "fault in %s (exception 0x%08lX%s)", site, GetExceptionCode(), where);
        return 0;
    }
}

/* module locks and their cost meter */
#define MAX_METERED 64
static HlibLock *s_metered[MAX_METERED];
static volatile LONG s_nmetered;
static LONG64 s_qpf;

void hlib_lock_init(HlibLock *k, const char *name) {
    if (k->ready) return;
    InitializeCriticalSection(&k->cs);
    k->name = name;
    k->dead = 0;
    LONG i = InterlockedIncrement(&s_nmetered) - 1;
    if (i < MAX_METERED) s_metered[i] = k;
    InterlockedExchange(&k->ready, 1);
}

int hlib_chunk_newer(HlibLock *k, const char *name, int version, int max) {
    if (version <= max) return 0;
    hlib_logf("%s: the save's block is version %d, newer than this library; the module stays off", name, version);
    InterlockedExchange(&k->dead, 1);
    return 1;
}

static LONG64 qpc(void) {
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return t.QuadPart;
}

static void meter(HlibLock *k, LONG64 t0, LONG64 t1) {
    LONG64 inside = qpc() - t1;
    InterlockedIncrement64(&k->calls);
    InterlockedAdd64(&k->inside, inside);
    InterlockedAdd64(&k->wait, t1 - t0);
    if (inside > k->max) k->max = inside; /* a lost race only loses a maximum */
}

/* One log line per period: the busiest modules since the last one (perf_log=0: none; each client's own). */
static void perf_report(int seconds) {
    HlibPerfRow rows[MAX_METERED];
    int n = 0;
    if (!s_qpf) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        s_qpf = f.QuadPart ? f.QuadPart : 1;
    }
    for (int i = 0; i < s_nmetered && i < MAX_METERED; i++) {
        HlibLock *k = s_metered[i];
        if (!k) continue;
        rows[n].name = k->name;
        rows[n].calls = InterlockedExchange64(&k->calls, 0);
        rows[n].inside_us = InterlockedExchange64(&k->inside, 0) * 1000000 / s_qpf;
        rows[n].wait_us = InterlockedExchange64(&k->wait, 0) * 1000000 / s_qpf;
        rows[n].max_us = InterlockedExchange64(&k->max, 0) * 1000000 / s_qpf;
        n++;
    }
    char line[1000];
    if (hlib_perf_line(rows, n, 8, 1000, seconds, line, sizeof(line))) hlib_log(line);
    hlib_dispatch_budget(seconds);
}

/* the compile time of this file stands for the build: every rebuild is another library for the digest; a build that
   must give the same bytes each time (the link helper, core/build.cmd) passes a fixed HLIB_BUILD_STAMP */
#ifndef HLIB_BUILD_STAMP
#define HLIB_BUILD_STAMP __DATE__ " " __TIME__
#endif
uint32_t hlib_library_build(void) {
    static uint32_t stamp;
    if (!stamp) stamp = hlib_fnv1a_text(HLIB_VERSION " " HLIB_BUILD_STAMP) | 1;
    return stamp;
}

/* The session ends kept for the lock, run by its holder before its own work. A producer stores the key in a free slot
   and only then sets `pending`, so a holder that cleared the flag meanwhile finds the key on the next entry. */
static void run_ends(HlibLock *k) {
    if (!k->pending) return;
    InterlockedExchange(&k->pending, 0);
    for (int i = 0; i < HLIB_LOCK_ENDS; i++) {
        void *key = InterlockedExchangePointer(&k->ends[i], NULL);
        if (key && k->end_fn) hlib_guarded(k->name, (HlibThunk)k->end_fn, key);
    }
}

void hlib_locked_end(HlibLock *k, HlibSessionFn fn, void *key) {
    if (!k->ready || !key) return;
    InterlockedExchangePointer((void *volatile *)(void *)&k->end_fn, (void *)fn);
    if (TryEnterCriticalSection(&k->cs)) {
        __try {
            run_ends(k);
            hlib_guarded(k->name, (HlibThunk)fn, key);
        } __finally {
            LeaveCriticalSection(&k->cs);
        }
        return;
    }
    for (int i = 0; i < HLIB_LOCK_ENDS; i++) { /* another thread holds it: its next entry runs the end */
        if (NULL == InterlockedCompareExchangePointer(&k->ends[i], key, NULL)) {
            InterlockedExchange(&k->pending, 1);
            return;
        }
    }
    hlib_logf("%s: too many session ends at once, one is dropped", k->name);
}

int hlib_locked(HlibLock *k, const char *site, HlibThunk fn, void *ctx) {
    int ok = 0;
    if (!k->ready || k->dead) return 0;
    LONG64 t0 = qpc();
    EnterCriticalSection(&k->cs);
    LONG64 t1 = qpc();
    __try {
        run_ends(k);
        ok = hlib_guarded(site, fn, ctx);
    } __finally {
        meter(k, t0, t1);
        LeaveCriticalSection(&k->cs);
    }
    if (!ok && 0 == InterlockedExchange(&k->dead, 1)) hlib_logf("%s: off for this game after a fault in %s", k->name, site);
    return ok;
}

typedef struct {
    HlibScriptFn fn;
    void *L;
    int n;
} ScriptCall;

static void script_run(void *p) {
    ScriptCall *c = (ScriptCall *)p;
    c->n = c->fn(c->L);
}

int hlib_locked_script(HlibLock *k, HlibScriptFn fn, void *L) {
    ScriptCall c = {fn, L, 0};
    char site[48];
    int ok = 0;
    if (!k->ready || k->dead) return hlib_lua_pushnumber(L, 0);
    hlib_format(site, sizeof(site), "%s script", k->name);
    LONG64 t0 = qpc();
    EnterCriticalSection(&k->cs);
    LONG64 t1 = qpc();
    __try {
        run_ends(k);
        ok = hlib_guarded(site, script_run, &c);
    } __finally {
        meter(k, t0, t1);
        LeaveCriticalSection(&k->cs);
    }
    if (ok) {
        k->faults = 0;
        return c.n;
    }
    if (InterlockedIncrement(&k->faults) >= HLIB_SCRIPT_MUTE_AFTER && 0 == InterlockedExchange(&k->dead, 1))
        hlib_logf("%s: off for this game after %d script faults in a row (last in %s)", k->name, HLIB_SCRIPT_MUTE_AFTER, site);
    return hlib_lua_pushnumber(L, 0);
}

/* Module registry (the bring-up below walks it); a module without a valid class is taken as EXPERIMENTAL (off) */
void hlib_register_module(const HlibModule *m, int feature_class) {
    if (s_module_count >= MAX_MODULES) {
        hlib_logf("module %s NOT registered: the registry holds %d (MAX_MODULES)", m->name, MAX_MODULES);
        return;
    }
    if (!fc_class_ok(feature_class)) {
        hlib_logf("module %s registered without a feature class: taken as experimental (off unless the ini turns it on)",
                   m->name);
        feature_class = HLIB_EXPERIMENTAL;
    }
    s_module_class[s_module_count] = feature_class;
    s_modules[s_module_count++] = m;
}

int core_feature_class(const char *name) {
    for (int i = 0; name && i < s_module_count; i++)
        if (0 == lstrcmpA(s_modules[i]->name, name)) return s_module_class[i];
    return 0;
}

int hlib_module_active(const char *name) {
    for (int i = 0; i < s_module_count; i++)
        if (s_module_active[i] && 0 == lstrcmpA(s_modules[i]->name, name)) return 1;
    return 0;
}

/* luaapi service: observes the script state's creation; returns 1 once done (placed or given up). */
int hlib_luaapi_install(int final);

/* a module's resolve runs on the watcher thread: a fault in it is that module's, it stays off (0) */
typedef struct {
    int (*fn)(void);
    int r;
} ResolveCall;

static void resolve_run(void *p) {
    ResolveCall *c = (ResolveCall *)p;
    c->r = c->fn();
}

static int resolve_guarded(const HlibModule *m) {
    ResolveCall c = {m->resolve, 0};
    char site[64];
    hlib_format(site, sizeof(site), "%s resolve", m->name);
    return hlib_guarded(site, resolve_run, &c) && c.r;
}

/* a module's install, guarded, with the dispatch told whose subscriptions it makes; 1 ran clean */
static int install_one(int i) {
    const HlibModule *m = s_modules[i];
    int ok = 1;
    hlib_dispatch_module(i);
    if (m->install) ok = hlib_guarded(m->name, (HlibThunk)(void *)m->install, NULL);
    hlib_dispatch_module(-1);
    s_module_installed[i] |= ok;
    return ok;
}

static int module_index(const char *name) {
    for (int i = 0; name && i < s_module_count; i++)
        if (0 == lstrcmpA(s_modules[i]->name, name)) return i;
    return -1;
}

static void human_count_of(void *p) { *(int *)p = hlib_human_count(); }

static FcToggle toggle_facts(int i) {
    FcToggle t = {0};
    int humans = 0;
    hlib_guarded("module switch", human_count_of, &humans);
    t.registered = i >= 0, t.humans = humans, t.safe_mode = s_safe_mode, t.dormant = hlib_dormant();
    if (hlib_edition()->link_only) t.dormant = 0; /* the link start is that build's whole start: ordinary rules */
    if (i >= 0) t.toggles = s_modules[i]->toggle != 0, t.lane = s_modules[i]->lane, t.active = s_module_active[i];
    return t;
}

int hlib_module_count(void) { return s_module_count; }

int hlib_module_state(int i, HlibModuleState *out) {
    int noop;
    if (i < 0 || i >= s_module_count) return 0;
    FcToggle t = toggle_facts(i);
    out->name = s_modules[i]->name;
    out->feature_class = s_module_class[i];
    out->on = s_module_active[i];
    out->lane = s_modules[i]->lane;
    out->why = fc_toggle_check(&t, !t.active, &noop);
    out->toggles = !out->why;
    if (!out->why) out->why = "";
    return 1;
}

typedef struct {
    int (*fn)(int);
    int on, r;
} ToggleCall;

static void toggle_run(void *p) {
    ToggleCall *c = (ToggleCall *)p;
    c->r = c->fn(c->on);
}

/* switched on: a module installed before gets its subscriptions back, one that was off at start resolves and installs
   now; then its own toggle puts its session state in place. Off: withdrawn first, then its toggle restores. */
static const char *switch_module(int i, int on) {
    const HlibModule *m = s_modules[i];
    HlibPoint missing;
    ToggleCall c = {m->toggle, on, 0};
    if (on && !s_module_installed[i]) {
        if (m->resolve && !resolve_guarded(m)) return "its targets do not match this build";
        s_module_active[i] = 1;
        if (!install_one(i)) return s_module_active[i] = 0, "its install faulted";
        if (!hlib_dispatch_module_ready(i, &missing)) {
            hlib_dispatch_withdraw(i, 1);
            s_module_active[i] = 0;
            hlib_logf("module %s: the %s is not in place in this start", m->name, hlib_point_name(missing));
            return "a point it listens on is not in place in this start (no module used it at start): set it in the ini";
        }
    } else {
        hlib_dispatch_withdraw(i, !on);
    }
    if (!hlib_guarded(m->name, toggle_run, &c) || !c.r) {
        hlib_dispatch_withdraw(i, on); /* back as it was */
        return on ? "it could not put its state in place" : "it could not put back what it changed: it stays on";
    }
    InterlockedExchange(&s_module_restoring[i], !on && c.r == HLIB_TOGGLE_LATER);
    if (s_module_restoring[i]) { /* its restore is simulation work: its own steps run it, then it says so */
        hlib_dispatch_withdraw(i, 0);
        hlib_logf("module %s: its restore runs on its next steps", m->name);
    }
    s_module_active[i] = on;
    return 0;
}

void hlib_module_restored(const char *name) {
    int i = module_index(name);
    if (i < 0 || !InterlockedExchange(&s_module_restoring[i], 0)) return;
    hlib_dispatch_withdraw(i, 1);
    hlib_logf("module %s: restored, its subscriptions withdrawn", name);
}

int hlib_module_set(const char *name, int on, const char **why) {
    int i = module_index(name), noop;
    FcToggle t = toggle_facts(i);
    *why = fc_toggle_check(&t, on, &noop);
    if (*why || noop) return !*why;
    *why = switch_module(i, on);
    hlib_logf("module %s: %s at run time%s%s", name, on ? "switched on" : "switched off", *why ? ", refused: " : "", *why ? *why : "");
    if (*why) return 0;
    log_digest("after a run-time switch");
    return 1;
}

/* Settle every module whose turn has come: off by switch, active once its targets resolved, and (on
   the final pass) off for good when they did not. A module goes up as soon as its own targets match,
   so a target that never matches delays nobody else. */
static void settle_modules(int final) {
    for (int i = 0; i < s_module_count; i++) {
        const HlibModule *m = s_modules[i];
        if (s_module_settled[i]) continue;
        if (s_safe_mode || !module_on(m)) {
            hlib_logf("module %s: off (%s, class %s)", m->name, s_safe_mode ? "safe mode" : cfg_pair(m->name) ? "the ini" : "its default",
                       fc_class_name(s_module_class[i]));
            s_module_settled[i] = 1;
            continue;
        }
        if (m->resolve && !resolve_guarded(m)) {
            if (final) {
                hlib_logf("module %s: targets unresolved, staying off", m->name);
                s_module_settled[i] = 1;
            }
            continue;
        }
        s_module_active[i] = 1; /* set before the install: an install that asks for its own module sees it on */
        s_module_settled[i] = 1;
        if (!install_one(i)) {
            s_module_active[i] = 0; /* its install faulted: off, as a module whose targets did not resolve */
            hlib_logf("module %s: its install faulted, staying off", m->name);
            continue;
        }
        hlib_logf("module %s: active (class %s%s)", m->name, fc_class_name(s_module_class[i]),
                   m->toggle ? ", switchable at run time" : "");
        if (m->overrides) /* the audit's marker: 117's work this module takes over, and the layer planned instead */
            hlib_logf("module %s: %s 117's own work; planned: %s", m->name,
                       m->overrides == HLIB_SKIPS ? "skips part of" : "replaces part of", m->route ? m->route : "-");
    }
}

/* The shared hooks go in on the pass where their own targets match and a module listens; each installer is
   idempotent and logs a target that never matched on the final pass only. */
static void install_shared(int final) {
    hlib_persist_install(final); /* first: the game salt it registers needs the tick, the events and the lifecycle */
    hlib_tick_install(final);
    hlib_events_install(final);
    hlib_lifecycle_install(final);
    hlib_bus_install(final);
}

/* Resolve every target and bring the modules up. Every target with bytes to check is polled (the
   image can be finalized shortly after load, for call-only targets as much as for hook sites) for up
   to ~2 min; each is independent, so one that never matches disables only the modules that need it. */
static void resolve_and_bring_up(void) {
    int lua_done = s_safe_mode; /* the script observer goes in as soon as its own targets match */
    hlib_edition()->register_modules();
    for (int pass = 0; pass < 60; pass++) {
        int pending = 0;
        for (int id = 0; id < T_COUNT; id++) {
            if (s_target_ok[id]) continue;
            if (target_bytes_present(id))
                s_target_ok[id] = 1;
            else if (target_pollable(id))
                pending++;
        }
        settle_modules(0);
        if (!lua_done) lua_done = hlib_luaapi_install(0);
        if (!s_safe_mode) install_shared(0);
        if (!pending) break;
        Sleep(2000);
    }
    int ok = 0, miss = 0, planned = 0;
    for (int id = 0; id < T_COUNT; id++) {
        if (s_target_ok[id]) {
            ok++;
        } else if (g_target_meta[id].stability == TS_PLANNED && !g_targets[id].rva) {
            planned++;
        } else {
            miss++;
            log_unresolved(id);
        }
    }
    hlib_logf("targets: %d resolved, %d unresolved on this build, %d planned (named, no address yet)", ok, miss, planned);
    settle_modules(1);
    if (!lua_done) hlib_luaapi_install(1);
    if (!s_safe_mode) install_shared(1);
    hlib_targets_report();
    hlib_layers_report();
    hlib_dispatch_report();
}

/* The targets by update stability (targets.h g_target_meta): what an update re-finds by itself and the rows the
   runbook's hand check needs (sites, patched bytes, stubs), named. */
void hlib_targets_report(void) {
    static const char *const kinds[] = {"?", "entry", "call", "vtable slot", "data", "site", "bytes", "stub"};
    int by_kind[8] = {0}, by_class[5] = {0}, len = 0;
    char hand[700];
    hand[0] = 0;
    for (int id = 0; id < T_COUNT; id++) {
        const HlibTargetMeta *m = &g_target_meta[id];
        by_kind[m->kind < 8 ? m->kind : 0]++;
        by_class[m->stability < 5 ? m->stability : 0]++;
        if ((m->kind == TK_SITE || m->kind == TK_BYTES || m->kind == TK_STUB) && len < (int)sizeof(hand) - 40)
            len += hlib_format(hand + len, (int)sizeof(hand) - len, "%s%s", len ? " " : "", g_target_names[id]);
    }
    char line[400];
    int n = hlib_format(line, (int)sizeof(line), "targets by kind:");
    for (int k = 1; k < 8; k++) n += hlib_format(line + n, (int)sizeof(line) - n, " %s %d", kinds[k], by_kind[k]);
    hlib_format(line + n, (int)sizeof(line) - n, "; by update stability: high %d, medium %d, low %d, planned %d",
                 by_class[TS_HIGH], by_class[TS_MEDIUM], by_class[TS_LOW], by_class[TS_PLANNED]);
    hlib_log(line);
    hlib_logf("targets for the hand check on an update (sites, bytes, stubs): %s", hand[0] ? hand : "none");
}

/* Called from the Lua-state capture observer (luaapi.c). */
void hlib_dispatch_lua(void *L) {
    for (int i = 0; i < s_module_count; i++)
        if (s_module_active[i] && s_modules[i]->on_lua)
            hlib_guarded(s_modules[i]->name, (HlibThunk)(void *)s_modules[i]->on_lua, L);
}

/* The dormant start's few targets: polled like the full start's (the image is finalised shortly after load); 1 when
   every one matched, else each one that did not is logged. */
int hlib_resolve_only(const int *ids, int n) {
    int left = n;
    for (int pass = 0; pass < 60 && left; pass++) {
        if (pass) Sleep(2000);
        left = 0;
        for (int i = 0; i < n; i++)
            if (!s_target_ok[ids[i]] && !(s_target_ok[ids[i]] = target_bytes_present(ids[i]))) left++;
    }
    for (int i = 0; i < n; i++)
        if (!s_target_ok[ids[i]]) log_unresolved(ids[i]);
    return !left;
}

/* What a start outside the full mode brings up for any mod, each on its own targets: the modders' live link to the
   suite with its scene, bank, guard and reload reads; who names the start */
static void tools_start(const char *who, int *link) {
    if (!G_HAS_ENGINE) { /* a game with no engine side yet (game.h): the pipe and the handshake, nothing to resolve */
        if (hlib_live_link_wanted()) {
            hlib_logf("%s: live_link=1, the live link comes up for %s with no engine side (handshake and ping only)", who, G_ID);
            *link = hlib_live_link_install();
        }
        return;
    }
    if (hlib_live_link_wanted()) { /* the modders' link to the suite: any mod, so no full mode needed */
        int n = 0, sn = 0, bn = 0, gn = 0, rn = 0;
        const int *ids = hlib_live_link_targets(&n), *sids = hlib_live_link_scene_targets(&sn);
        const int *bids = hlib_live_link_bank_targets(&bn);
        const int *gids = hlib_live_link_guard_targets(&gn), *rids = hlib_live_reload_targets(&rn);
        hlib_logf("%s: live_link=1, the live link comes up (its own targets, then the reload's)", who);
        int vn = 0;
        const int *vids = hlib_live_link_values_targets(&vn);
        if (hlib_resolve_only(ids, n)) {
            hlib_resolve_only(vids, vn); /* the live widget values (ui.values) only when these match */
            hlib_resolve_only(sids, sn); /* the scene reload only when these match */
            hlib_resolve_only(bids, bn); /* the bank reload only when these match */
            hlib_resolve_only(gids, gn); /* the guards: a session switch, a scheduled scenario action, the pause */
            hlib_resolve_only(rids, rn); /* models and effects only when these match too: the link reports what it can serve */
            *link = hlib_live_link_install();
        }
    }
}

/* the link start: the edition's modules settled by hand, so modules.list names what runs */
static void mark_module(const char *name, int up) {
    int i = module_index(name);
    if (i < 0) return;
    s_module_settled[i] = 1, s_module_active[i] = up, s_module_installed[i] = up;
    hlib_logf("module %s: %s (class %s)", name, up ? "active" : "off", fc_class_name(s_module_class[i]));
}

/* The link helper (build.cmd link): never the full mode, no switch button, no script table; the link (on unless
   live_link=0), the reload beside it (or alone with live_reload=1) each on its own targets as the dormant start
   does. */
static void link_start(void) {
    int rn = 0, gn = 0, link = 0, reload = 0;
    const int *rids = hlib_live_reload_targets(&rn), *gids = hlib_live_link_guard_targets(&gn);
    hlib_edition()->register_modules();
    hlib_log("link helper: the live link build (no module of the full library, no switch button, no script table)");
    if (s_safe_mode) {
        hlib_log("link helper: safe mode, nothing comes up");
        return;
    }
    tools_start("link helper", &link);
    if (G_HAS_ENGINE && ((link && hlib_config_int("live_link_models", 1)) || hlib_live_reload_wanted())) {
        if (!link) hlib_resolve_only(gids, gn); /* the reload alone waits on the link's guards too */
        reload = hlib_resolve_only(rids, rn) && hlib_live_reload_install(); /* the link's own call made it already: its answer */
    }
    mark_module("live_link", link);
    mark_module("live_reload", reload);
    if (!link && !reload) hlib_log("link helper: live_link=0 and live_reload off: the game runs as without the library");
    if (cfg_unused_count()) hlib_logf("config: %d key(s) of the ini are not read by the link helper", cfg_unused_count());
}

/* The dormant start (the start mode said so): no module, no script table, nothing written; only what the edition
   adds (the full library's title switch button) and the tools any mod can use; the start mode's late check runs
   until it is done (the full library: the loader's list, for an override the profile check missed), then the thread
   ends. */
static void dormant_start(void) {
    const HlibEdition *ed = hlib_edition();
    int link = 0;
    char why[256];
    if (ed->link_only) {
        link_start();
        return;
    }
    config_dormant_reason(why, sizeof(why));
    hlib_logf("dormant: %s; no module, no script table: the game runs as without the library", why);
    if (ed->dormant) ed->dormant();
    tools_start("dormant", &link);
    int unused = cfg_unused_count();
    if (unused) hlib_logf("config: %d key(s) of the ini are not read in a dormant start", unused);
    for (int s = 0; !config_late_check(s >= 300); s += 2) Sleep(2000);
}

/* The watcher's phases each run guarded: an access violation in the config reader, the mods scanner, a module's
   resolve or a report would otherwise end the game's process (an exception on a thread with no handler). */
typedef void (*Phase)(void);

static void phase_run(void *p) { ((Phase)p)(); }

static int phase(const char *name, Phase fn) { return hlib_guarded(name, phase_run, (void *)fn); }

static int s_perf; /* seconds between cost reports, 0 none */

static void ph_build(void *p) { *(int *)p = build_matches(); }
static void ph_dormant(void) { dormant_start(); }
static void ph_load_config(void) { load_config(); }
static void ph_mods_index(void) { if (!s_safe_mode) hlib_mods_index(); }
static void ph_before_report(void) { if (!s_safe_mode && hlib_edition()->before_report) hlib_edition()->before_report(); } /* the switch button names an obsolete ini key */
static void ph_digest(void) {
    cfg_report_unused();
    log_digest("of this start");
}
static void ph_after_report(void) { if (!s_safe_mode && hlib_edition()->after_report) hlib_edition()->after_report(); } /* the settings mod carries this digest */

typedef struct {
    int i, loader;
} Tick;

static void ph_tick(void *p) {
    Tick *t = (Tick *)p;
    if (!t->loader) t->loader = config_late_check(t->i >= 300);
    if (s_perf && t->i % s_perf == 0) perf_report(s_perf);
}

static DWORD WINAPI watcher(LPVOID p) {
    int matches = 0;
    (void)p;
    s_base = (unsigned char *)GetModuleHandleA(NULL);
    phase("documents folder", resolve_documents);
    phase("config", ph_load_config);
    if (!hlib_guarded("build guard", ph_build, &matches) || !matches) return 0;
    if (hlib_dormant()) {
        phase("dormant start", ph_dormant);
        return 0;
    }
    hlib_log("guard: build matches, polling for the target prologues");
    phase("mods index", ph_mods_index);
    phase("bring-up", resolve_and_bring_up);
    phase("mods release", hlib_mods_release);
    s_perf = s_safe_mode ? 0 : hlib_config_int("perf_log", 60);
    if (s_perf < 0 || s_perf > 3600) s_perf = 60;
    phase("edition before the report", ph_before_report);
    phase("config report", ph_digest);
    phase("edition after the report", ph_after_report);
    Tick t = {0, s_safe_mode}; /* the loader has long written its list by now; a slow one is waited for */
    for (t.i = 1;; t.i++) {   /* stay resident; hooks and ticks log their own work */
        hlib_guarded("watcher", ph_tick, &t);
        Sleep(1000);
    }
}

void hlib_core_start(void) {
    HANDLE h = CreateThread(NULL, 0, watcher, NULL, 0, NULL);
    if (h) CloseHandle(h); /* the watcher runs on (or ends on an unknown build) without anyone waiting on it */
}

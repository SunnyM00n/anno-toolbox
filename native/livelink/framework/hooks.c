/* Hook primitives: entry observers (a redirect at a function's entry, forwarding through a relay that holds the
   original opening instructions), code patches and loader-slot redirects. Ordinary code only, whose opening bytes
   targets.h records and the resolver checks; never guarded code. A batch is all or nothing and goes in as a hot
   patch so no thread ever runs a half-written site. */
#include "framework.h"
#include "targets.h"
#include "relay.h"
#include <tlhelp32.h>

#define REDIRECT_WIDTH HLIB_REDIRECT_WIDTH
#define MAX_WRITES 16
#define MAX_THREADS 2048

typedef struct {
    unsigned char *at;
    const unsigned char *bytes;
    int n;
    DWORD old;
    unsigned char head[2]; /* the original first two bytes, for the rollback */
} Write;

enum { WRITE_CODE, WRITE_VALUES };

/* the held threads: static, nothing is allocated while they are held (one may hold the heap's lock) */
static HANDLE s_held[MAX_THREADS];
static int s_nheld;
static CONTEXT s_ctx;
static SRWLOCK s_write_lock = SRWLOCK_INIT;

static int atomic_ok(const unsigned char *p, int n) {
    ULONG_PTR a = (ULONG_PTR)p;
    return n >= 1 && ((a & 7) + (ULONG_PTR)n <= 8 || (a & 15) + (ULONG_PTR)n <= 16);
}

/* n bytes at p as one locked store, when they lie in one aligned 8- or 16-byte block; 0 otherwise */
static int atomic_put(unsigned char *p, const unsigned char *src, int n) {
    ULONG_PTR a = (ULONG_PTR)p;
    if (!atomic_ok(p, n)) return 0;
    if ((a & 7) + (ULONG_PTR)n <= 8) {
        volatile LONG64 *q = (volatile LONG64 *)(a & ~(ULONG_PTR)7);
        LONG64 was, now;
        do {
            was = *q;
            now = was;
            memcpy((unsigned char *)&now + (a & 7), src, (size_t)n);
        } while (InterlockedCompareExchange64(q, now, was) != was);
        return 1;
    }
    LONG64 *q = (LONG64 *)(a & ~(ULONG_PTR)15);
    __declspec(align(16)) LONG64 cmp[2], now[2];
    cmp[0] = q[0], cmp[1] = q[1];
    do {
        now[0] = cmp[0], now[1] = cmp[1];
        memcpy((unsigned char *)now + (a & 15), src, (size_t)n);
    } while (!InterlockedCompareExchange128(q, now[1], now[0], cmp)); /* a miss puts the current value in cmp */
    return 1;
}

static void release_threads(void) {
    for (int i = 0; i < s_nheld; i++) {
        ResumeThread(s_held[i]);
        CloseHandle(s_held[i]);
    }
    s_nheld = 0;
}

static int rip_inside(DWORD64 rip, const Write *w, int n, int from) {
    for (int k = 0; k < n; k++)
        if (rip >= (DWORD64)(w[k].at + from) && rip < (DWORD64)(w[k].at + w[k].n)) return 1;
    return 0;
}

/* Every other thread, each suspended only long enough to read where it stands (keep 0), or all held until
   release_threads (keep 1). 0 when one stands on a byte from `from` on inside a range. */
static int check_threads(const Write *w, int n, int from, int keep) {
    DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te;
    int clear = 1;
    s_nheld = 0;
    if (snap == INVALID_HANDLE_VALUE) return 0;
    te.dwSize = sizeof(te);
    for (BOOL more = Thread32First(snap, &te); more && s_nheld < MAX_THREADS; more = Thread32Next(snap, &te)) {
        if (te.th32OwnerProcessID != pid || te.th32ThreadID == self) continue;
        HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT, FALSE, te.th32ThreadID);
        if (!h) continue;
        if (SuspendThread(h) == (DWORD)-1) {
            CloseHandle(h);
            continue;
        }
        s_ctx.ContextFlags = CONTEXT_CONTROL;
        if (GetThreadContext(h, &s_ctx) && rip_inside(s_ctx.Rip, w, n, from)) clear = 0;
        if (keep) {
            s_held[s_nheld++] = h;
        } else {
            ResumeThread(h);
            CloseHandle(h);
        }
    }
    CloseHandle(snap);
    return clear;
}

static void flush(const Write *w, int n) {
    for (int k = 0; k < n; k++) FlushInstructionCache(GetCurrentProcess(), w[k].at, (SIZE_T)w[k].n);
}

/* code: each head becomes jmp $ (EB FE) atomically, so a thread that arrives waits on it; once no thread stands
   behind a head, the rest goes in, then the new heads. 0 = nothing changed (the old heads are back). */
static int hot_patch(Write *w, int n) {
    static const unsigned char loop[2] = {0xEB, 0xFE};
    int ok = 0;
    for (int k = 0; k < n; k++) {
        memcpy(w[k].head, w[k].at, 2);
        atomic_put(w[k].at, loop, 2);
    }
    flush(w, n);
    for (int attempt = 0; attempt < 200 && !ok; attempt++) {
        ok = check_threads(w, n, 1, 0); /* a thread on the loop itself (offset 0) is fine */
        if (!ok) Sleep(1);
    }
    if (!ok) {
        for (int k = 0; k < n; k++) atomic_put(w[k].at, w[k].head, 2);
        flush(w, n);
        return 0;
    }
    for (int k = 0; k < n; k++) memcpy(w[k].at + 2, w[k].bytes + 2, (size_t)(w[k].n - 2));
    flush(w, n);
    for (int k = 0; k < n; k++) atomic_put(w[k].at, w[k].bytes, 2);
    flush(w, n);
    return 1;
}

/* the fallback: every other thread held for the write, retried while one stands inside a range */
static int held_patch(Write *w, int n) {
    int ok = 0;
    for (int attempt = 0; attempt < 100 && !ok; attempt++) {
        if (check_threads(w, n, 0, 1)) {
            for (int k = 0; k < n; k++) memcpy(w[k].at, w[k].bytes, (size_t)w[k].n);
            flush(w, n);
            ok = 1;
        }
        release_threads();
        if (!ok) Sleep(1);
    }
    return ok;
}

/* Open every range, write the batch, restore the protections. Values (immediates whose instruction keeps its
   bounds) go in as one atomic store each, code as a hot patch. Nothing is written unless every range opened. */
static int write_all(Write *w, int n, int kind) {
    int opened = 0, ok = 0, hot = 1, values = kind == WRITE_VALUES;
    if (n < 1 || n > MAX_WRITES) return 0;
    for (int k = 0; k < n; k++) {
        if (!atomic_ok(w[k].at, w[k].n)) values = 0;
        if (w[k].n < 2 || !atomic_ok(w[k].at, 2)) hot = 0;
    }
    for (; opened < n; opened++)
        if (!VirtualProtect(w[opened].at, (SIZE_T)w[opened].n, PAGE_EXECUTE_READWRITE, &w[opened].old)) break;
    if (opened == n) {
        AcquireSRWLockExclusive(&s_write_lock);
        if (values) {
            for (int k = 0; k < n; k++) atomic_put(w[k].at, w[k].bytes, w[k].n);
            flush(w, n);
            ok = 1;
        } else if (hot) {
            ok = hot_patch(w, n);
        } else {
            hlib_log("hook: a site's head cannot be stored atomically, the other threads are held for the write");
            ok = held_patch(w, n);
        }
        ReleaseSRWLockExclusive(&s_write_lock);
    }
    /* reverse order: of two sites in one page the later one saw the page already writable, so the first one's record
       (the page's true protection) must be put back last */
    for (int k = opened - 1; k >= 0; k--) VirtualProtect(w[k].at, (SIZE_T)w[k].n, w[k].old, &w[k].old);
    if (opened < n) hlib_log("hook: a site could not be opened, nothing changed");
    else if (!ok) hlib_log("hook: a thread stayed inside a site, nothing changed");
    return ok;
}

/* A relay whose stolen bytes end in a call is a frame while that callee runs; its unwind data comes from the
   function's own (relay.h). Laid out in the upper half of the batch's page. */
#define UNWIND_AT 0x800

static int relay_unwind(unsigned char *out, const unsigned char *site, int call_off) {
    DWORD64 base = 0;
    PRUNTIME_FUNCTION f = RtlLookupFunctionEntry((DWORD64)site, &base, NULL);
    if (!f || base + f->BeginAddress != (DWORD64)site) return 0;
    return hlib_relay_unwind(out, (const unsigned char *)(base + f->UnwindData), call_off);
}

/* registers the relays' unwind data for the entries with a call; *table is what to delete again, 0 none */
static int relay_unwind_all(unsigned char *page, const HlibEntry *entries, int count, PRUNTIME_FUNCTION *table) {
    PRUNTIME_FUNCTION rf = (PRUNTIME_FUNCTION)(page + UNWIND_AT);
    unsigned char *infos = page + UNWIND_AT + 16 * sizeof(RUNTIME_FUNCTION);
    int n = 0, missing = 0;
    *table = NULL;
    for (int i = 0; i < count; i++) {
        int id = entries[i].target, call = g_targets[id].call_off;
        if (!call) continue;
        if (!relay_unwind(infos + n * HLIB_UNWIND_SIZE, (const unsigned char *)hlib_target(id), call)) {
            missing++;
            continue;
        }
        rf[n].BeginAddress = (DWORD)(i * HLIB_RELAY_SIZE);
        rf[n].EndAddress = (DWORD)(i * HLIB_RELAY_SIZE + HLIB_RELAY_CALL_END(call));
        rf[n].UnwindData = (DWORD)(infos + n * HLIB_UNWIND_SIZE - page);
        n++;
    }
    if (n && RtlAddFunctionTable(rf, (DWORD)n, (DWORD64)page)) *table = rf;
    if (missing || (n && !*table))
        hlib_logf("hook: %d relay(s) with a call have no unwind data (an exception thrown through them would not unwind)",
                   missing + (*table ? 0 : n));
    return n;
}

/* the site still holds the bytes the target was resolved on: another hooker (an overlay) may have patched it since
   the resolution pass, and a relay would copy its jump to another address */
static int site_unchanged(int id) {
    const HlibTarget *t = &g_targets[id];
    const unsigned char *site = (const unsigned char *)hlib_target(id);
    return site && t->verify_len <= (int)sizeof(t->verify) && 0 == memcmp(site, t->verify, (size_t)t->verify_len);
}

int hlib_install_entry_observers(const HlibEntry *entries, int count) {
    Write w[MAX_WRITES];
    unsigned char code[MAX_WRITES][32];
    if (count < 1 || count > MAX_WRITES || count * HLIB_RELAY_SIZE > UNWIND_AT) {
        hlib_log("hook: too many entries for one batch, nothing placed");
        return 0;
    }
    for (int i = 0; i < count; i++) {
        int id = entries[i].target;
        if (!hlib_target_ok(id) || !g_targets[id].is_entry_hook || g_targets[id].verify_len < REDIRECT_WIDTH ||
            g_targets[id].verify_len > (int)sizeof(code[0])) {
            hlib_log("hook: a target is not ready, nothing placed");
            return 0;
        }
        if (!hlib_relay_fits(g_targets[id].verify_len, g_targets[id].call_off)) {
            hlib_logf("hook: %s: the stolen prefix and its call do not fit a relay, nothing placed", g_target_names[id]);
            return 0;
        }
        if (!site_unchanged(id)) {
            hlib_logf("hook: %s: the site's bytes changed since it was resolved (another hook?), nothing placed",
                       g_target_names[id]);
            return 0;
        }
    }
    /* one page of relays per batch: a page already in use is never made writable again */
    DWORD old;
    unsigned char *relays = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!relays) {
        hlib_log("hook: relay allocation failed, nothing placed");
        return 0;
    }
    for (int i = 0; i < count; i++) {
        int id = entries[i].target, stolen = g_targets[id].verify_len;
        unsigned char *site = (unsigned char *)hlib_target(id);
        hlib_build_relay(relays + i * HLIB_RELAY_SIZE, site, stolen, g_targets[id].call_off);
        hlib_abs_jump(code[i], entries[i].observer);
        for (int b = REDIRECT_WIDTH; b < stolen; b++) code[i][b] = 0x90;
        w[i].at = site;
        w[i].bytes = code[i];
        w[i].n = stolen;
    }
    PRUNTIME_FUNCTION table;
    relay_unwind_all(relays, entries, count, &table);
    if (!VirtualProtect(relays, 0x1000, PAGE_EXECUTE_READ, &old)) {
        hlib_log("hook: relay protect failed, nothing placed");
        if (table) RtlDeleteFunctionTable(table);
        VirtualFree(relays, 0, MEM_RELEASE);
        return 0;
    }
    FlushInstructionCache(GetCurrentProcess(), relays, 0x1000);
    /* the originals are set before the jumps go in: an observer may run the moment its site is written */
    void *prev[MAX_WRITES];
    for (int i = 0; i < count; i++) {
        prev[i] = *entries[i].original;
        *entries[i].original = relays + i * HLIB_RELAY_SIZE;
    }
    if (!write_all(w, count, WRITE_CODE)) {
        for (int i = 0; i < count; i++) *entries[i].original = prev[i];
        if (table) RtlDeleteFunctionTable(table);
        VirtualFree(relays, 0, MEM_RELEASE);
        return 0;
    }
    hlib_logf("hook: %d entry observer(s) placed", count);
    return 1;
}

int hlib_redirect_slot(int slot_target, void *replacement, void **previous) {
    return hlib_redirect_slot_at((void **)hlib_target(slot_target), replacement, previous);
}

/* the same for a slot found at run time (an entry of a live object's vtable) */
int hlib_redirect_slot_at(void **slot, void *replacement, void **previous) {
    if (!slot) return 0;
    DWORD old;
    if (!VirtualProtect(slot, sizeof(void *), PAGE_READWRITE, &old)) return 0;
    if (previous) *previous = *slot;
    InterlockedExchangePointer(slot, replacement);
    VirtualProtect(slot, sizeof(void *), old, &old);
    return 1;
}

/* Code patches for the few places a module must change a value or a sequence inside a function (ordinary
   code only, checked against the recorded bytes like every target): bytes at an offset into each target, all
   in one batch, or a jump to our stub over a whole-instruction run of the target's width. */
int hlib_patch_bytes(const HlibPatch *patches, int count) {
    Write w[MAX_WRITES];
    if (count < 1 || count > MAX_WRITES) return 0;
    for (int i = 0; i < count; i++) {
        const HlibPatch *p = &patches[i];
        unsigned char *site = (unsigned char *)hlib_target(p->target);
        if (!site || p->offset < 0 || p->n <= 0 || p->offset + p->n > g_targets[p->target].verify_len) {
            hlib_log("hook: a patch site is not ready, nothing changed");
            return 0;
        }
        w[i].at = site + p->offset;
        w[i].bytes = (const unsigned char *)p->bytes;
        w[i].n = p->n;
    }
    return write_all(w, count, WRITE_VALUES);
}

int hlib_patch_jump(int target, void *to, void **back) {
    unsigned char *site = (unsigned char *)hlib_target(target);
    int width = site ? g_targets[target].verify_len : 0;
    unsigned char code[32];
    if (!site || width < REDIRECT_WIDTH || width > (int)sizeof(code)) {
        hlib_log("hook: a jump site is not ready, nothing changed");
        return 0;
    }
    if (!site_unchanged(target)) {
        hlib_logf("hook: %s: the site's bytes changed since it was resolved, nothing changed", g_target_names[target]);
        return 0;
    }
    hlib_abs_jump(code, to);
    for (int b = REDIRECT_WIDTH; b < width; b++) code[b] = 0x90;
    if (back) *back = site + width;
    Write w = {site, code, width, 0, {0, 0}};
    return write_all(&w, 1, WRITE_CODE);
}

/* Jump thunks: a 5-byte `jmp rel32` of ordinary code has no room for a redirect, so its rel32 is re-pointed at a
   relay (`jmp [rip]` to our function) on a page within 2 GB of it; the old target stays untouched and becomes next.
   The rel32 goes in as one locked store, and the old one goes back the same way (hlib_thunks_restore). */
#define THUNK_MAX 16
#define THUNK_REACH 0x7ff00000LL
#define THUNK_RELAY 16 /* a relay's jmp [rip] and address, rounded up */
typedef struct {
    unsigned char *site;
    int32_t old_rel;
} Thunk;
static Thunk s_thunks[THUNK_MAX];
static int s_nthunks, s_thunk_used;
static unsigned char *s_thunk_page;

/* a free page within reach of site, from the lowest free region in range upward; 0 none */
static unsigned char *page_near(const unsigned char *site) {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    ULONG_PTR gran = si.dwAllocationGranularity, lo = (ULONG_PTR)site > THUNK_REACH ? (ULONG_PTR)site - THUNK_REACH : gran;
    ULONG_PTR hi = (ULONG_PTR)site + THUNK_REACH;
    MEMORY_BASIC_INFORMATION mbi;
    for (ULONG_PTR at = (lo + gran - 1) & ~(gran - 1); at < hi && VirtualQuery((void *)at, &mbi, sizeof(mbi));) {
        ULONG_PTR start = ((ULONG_PTR)mbi.BaseAddress + gran - 1) & ~(gran - 1), end = (ULONG_PTR)mbi.BaseAddress + mbi.RegionSize;
        if (mbi.State == MEM_FREE && start + 0x1000 <= end && start + 0x1000 <= hi) {
            void *p = VirtualAlloc((void *)start, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
            if (p) return (unsigned char *)p;
        }
        at = end > at ? end : at + gran;
    }
    return NULL;
}

int hlib_thunk_retarget(int target, void *fn, void **next) {
    unsigned char *site = (unsigned char *)hlib_target(target);
    int32_t rel;
    if (!site || site[0] != 0xE9 || !atomic_ok(site + 1, 4) || s_nthunks >= THUNK_MAX) {
        hlib_log("hook: a thunk site is not a jump this primitive can re-point, nothing changed");
        return 0;
    }
    if (!s_thunk_page || s_thunk_used + THUNK_RELAY > 0x1000 ||
        (LONG64)(s_thunk_page - site) > THUNK_REACH || (LONG64)(site - s_thunk_page) > THUNK_REACH) {
        if ((s_thunk_page = page_near(site)) == NULL) {
            hlib_log("hook: no page within reach of a thunk, nothing changed");
            return 0;
        }
        s_thunk_used = 0;
    }
    DWORD old;
    unsigned char *relay = s_thunk_page + s_thunk_used;
    if (!VirtualProtect(s_thunk_page, 0x1000, PAGE_READWRITE, &old)) return 0;
    hlib_abs_jump(relay, fn);
    VirtualProtect(s_thunk_page, 0x1000, PAGE_EXECUTE_READ, &old);
    FlushInstructionCache(GetCurrentProcess(), relay, HLIB_REDIRECT_WIDTH);
    memcpy(&rel, site + 1, sizeof(rel));
    int32_t now = (int32_t)(relay - (site + 5));
    void *prev = *next;
    *next = site + 5 + rel; /* set before the store: fn may run the moment the thunk points at it */
    Write w = {site + 1, (const unsigned char *)&now, 4, 0, {0, 0}};
    if (!write_all(&w, 1, WRITE_VALUES)) {
        *next = prev;
        return 0;
    }
    s_thunk_used += THUNK_RELAY;
    s_thunks[s_nthunks].site = site;
    s_thunks[s_nthunks++].old_rel = rel;
    return 1;
}

int hlib_thunks_restore(void) {
    int n = 0;
    for (int i = s_nthunks - 1; i >= 0; i--) {
        Write w = {s_thunks[i].site + 1, (const unsigned char *)&s_thunks[i].old_rel, 4, 0, {0, 0}};
        n += write_all(&w, 1, WRITE_VALUES);
    }
    s_nthunks = 0;
    return n; /* the relays stay: a thread may still be on its way through one */
}

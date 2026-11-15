/* The live link's reloads (mod_livelink.c; livelink_module.h): the requests that run over several UI frames - the
   asset and text pass with the engine's own confirmation, model and effect files, the scenes, the sound banks, the
   session - with the two logs they are judged by and the stability guards' engine reads. Start at llr_progress. */
#include "hlib.h"
#include "debug_logic.h"
#include "livelink.h"
#include "livelink_logic.h"
#include "livelink_module.h"
#include "livereload.h"
#include "luacmd.h"
#include "modules_logic.h"
#include "stability_logic.h"
#include "targets.h"
#include "util.h"
#include <sddl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define LOADER_LOG "mods/mod-loader.log"
#define PASS_WAIT_MS 10000
#define FILES_WAIT_MS 30000
#define RESULTS_MAX 512
#define GAME_LOG_DIR "log"
#define GAME_LOG_PREFIX "Anno117_LOGFILE_"
#ifndef RELOAD_START_WAIT_MS /* the host test of the pipe shortens the waits */
#define RELOAD_START_WAIT_MS 10000 /* the engine's start line may be missing this long: it drops a reload without a line */
#endif
#ifndef RELOAD_END_WAIT_MS
#define RELOAD_END_WAIT_MS 60000 /* from its start line to its end line (its file wait has no timeout of its own) */
#endif
#define ENGINE_WATCH_MS 250
#define SCENES_WAIT_MS 10000
#define BANKS_WAIT_MS 10000
#define SAVE_WAIT_MS 30000
#define SCENE_IDS_MAX 64 /* the id list's room, made once (4 bytes each: below the engine's 0x1000 header limit) */

/* the game's own log, where the engine says "--- AssetData Reload Start/End ---" (the UI thread's) */
static char s_glog[MAX_PATH];
static uint64_t s_glog_off;
static ULONGLONG s_engine_next;

uint64_t llr_log_size(void) {
    char path[MAX_PATH];
    WIN32_FILE_ATTRIBUTE_DATA a;
    if (!hlib_user_path(path, LOADER_LOG) || !GetFileAttributesExA(path, GetFileExInfoStandard, &a)) return 0;
    return ((uint64_t)a.nFileSizeHigh << 32) | a.nFileSizeLow;
}

/* a log file from `from` (its start when the file is shorter: it was written anew): the complete lines, in buf */
static size_t text_from(const char *path, uint64_t from, char *buf, size_t cap, uint64_t *size) {
    HANDLE h;
    LARGE_INTEGER end, at;
    DWORD got = 0;
    *size = 0;
    h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    GetFileSizeEx(h, &end);
    *size = (uint64_t)end.QuadPart;
    if (*size < from) from = 0;
    at.QuadPart = (LONGLONG)from;
    if (*size > from && SetFilePointerEx(h, at, NULL, FILE_BEGIN)) {
        DWORD want = (DWORD)(*size - from < cap - 1 ? *size - from : cap - 1);
        if (!ReadFile(h, buf, want, &got, NULL)) got = 0;
    }
    CloseHandle(h);
    buf[got] = 0;
    return ll_log_complete(buf, got);
}

size_t llr_log_from(uint64_t from, char *buf, size_t cap, uint64_t *size) {
    char path[MAX_PATH];
    *size = 0;
    return hlib_user_path(path, LOADER_LOG) ? text_from(path, from, buf, cap, size) : 0;
}

static uint64_t ft64(FILETIME t) { return ((uint64_t)t.dwHighDateTime << 32) | t.dwLowDateTime; }

/* The game's own log (the newest Anno117_LOGFILE_*.log of the user's log folder): the engine writes "--- AssetData
   Reload Start ---" and "--- AssetData Reload End ---" there around its rebuild of the asset database. Only a file made
   by this start counts (by its creation time: a directory's write times lag); the link may come up before the game
   made it, and the previous start's log would answer every reload "skipped". */
static int glog_find(void) {
    char dir[MAX_PATH], pat[MAX_PATH + 40];
    WIN32_FIND_DATAA f;
    FILETIME made, unused1, unused2, unused3;
    uint64_t best = 0, start;
    HANDLE h;
    int found = 0;
    if (!hlib_user_path(dir, GAME_LOG_DIR) || !GetProcessTimes(GetCurrentProcess(), &made, &unused1, &unused2, &unused3)) return 0;
    start = ft64(made);
    snprintf(pat, sizeof pat, "%s\\" GAME_LOG_PREFIX "*.log", dir);
    h = FindFirstFileA(pat, &f);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        uint64_t t = ft64(f.ftCreationTime);
        if (sl_log_of_this_start(t, start) && (!found || t > best)) {
            best = t, found = 1;
            snprintf(s_glog, sizeof s_glog, "%s\\%s", dir, f.cFileName);
        }
    } while (FindNextFileA(h, &f));
    FindClose(h);
    return found;
}

/* from now on: what the engine writes after this call is the next take (0 when there is no game log) */
int llr_glog_sync(void) {
    WIN32_FILE_ATTRIBUTE_DATA a;
    if ((!s_glog[0] && !glog_find()) || !GetFileAttributesExA(s_glog, GetFileExInfoStandard, &a)) return 0;
    s_glog_off = ((uint64_t)a.nFileSizeHigh << 32) | a.nFileSizeLow;
    return 1;
}

/* the new complete lines since the last take, and the engine's reload lines in them */
static void glog_take(SLMarks *marks) {
    static char buf[256 * 1024];
    uint64_t size;
    size_t n;
    memset(marks, 0, sizeof *marks);
    if (!s_glog[0]) { /* found just now: what stands in it is history */
        llr_glog_sync();
        return;
    }
    n = text_from(s_glog, s_glog_off, buf, sizeof buf, &size);
    s_glog_off = (size < s_glog_off ? 0 : s_glog_off) + n;
    sl_scan_marks(buf, n, marks);
}

/* The guard's states (stability_logic.h): a session transition running, the engine's scheduled action word, a save a moment old */
static int transition_active(void) {
    void **h = hlib_target_ok(T_DATA_SES_TRANSITIONS) ? (void **)hlib_target(T_DATA_SES_TRANSITIONS) : NULL;
    int yes = 0;
    __try {
        unsigned char *obj = h ? (unsigned char *)*h : NULL;
        unsigned char **list = obj ? *(unsigned char ***)(obj + T_OFF_TRANS_LIST) : NULL;
        for (int i = 0; list && i < 2; i++)
            if (list[i] && *(uint32_t *)(list[i] + T_OFF_TRANS_ACTIVE)) yes = 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        yes = 0;
    }
    return yes;
}

static int scenario_pending(void) {
    static int n_unknown;
    void **h;
    int yes = 0;
    if (!hlib_target_ok(T_DATA_ASSETDATA_MGR)) {
        hlib_log_n(&n_unknown, 1, "live link: the asset data manager is not located in this game build: a pending scenario action is not checked");
        return 0;
    }
    h = (void **)hlib_target(T_DATA_ASSETDATA_MGR);
    __try {
        unsigned char *m = h ? (unsigned char *)*h : NULL;
        yes = m && *(int *)(m + T_OFF_ASSETDATA_ACTION) != 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        yes = 0;
    }
    return yes;
}

/* The pause manager's applied game speed (stability_logic.h); 0 when it is not there (no game, not located) */
static int game_speed(int *speed) {
    void **h = hlib_target_ok(T_DATA_PAUSE_MANAGER) ? (void **)hlib_target(T_DATA_PAUSE_MANAGER) : NULL;
    int ok = 0;
    __try {
        unsigned char *v = h ? (unsigned char *)*h : NULL;
        if (v) *speed = *(int *)(v - T_OFF_PAUSE_HOLDER + T_OFF_PAUSE_SPEED), ok = 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        ok = 0;
    }
    return ok;
}

/* The end of an asset request the link paused the game for: the speed it found is set again through the game's own
   speed buttons (a game command), unless the player chose another one meanwhile or the rebuild did not end. */
void llr_pause_release(const char *status, const char *message, const char *data, char *msg, size_t mcap, char *dat, size_t dcap) {
    int speed = -1, have = game_speed(&speed), asked = 0;
    const char *note;
    if (sl_resume_wanted(llm_fl.paused_from, have, speed, status)) asked = hlib_lua_run(sl_resume_call(llm_fl.paused_from), NULL, 0);
    note = asked ? "The game runs again at its speed."
           : !strcmp(status, "error") ? "The game stays paused: look at it before you let it run."
           : have && speed != SL_SPEED_PAUSE_FULL ? "" /* the player set a speed meanwhile */
                                                  : "The game stays paused: unpause it yourself.";
    hlib_logf("live link: the pause for the request ends (speed %d before, %d now): %s", llm_fl.paused_from, have ? speed : -1,
               asked ? "set again" : "left as it is");
    snprintf(msg, mcap, "%s%s%s", message, *message && *note ? " " : "", note);
    snprintf(dat, dcap, "%s", data && *data ? data : "{}");
    sl_json_add_bool(dat, dcap, "pausedByLink", 1);
    sl_json_add_bool(dat, dcap, "resumeAsked", asked);
    llm_fl.paused_from = 0;
}

/* A file's write time and size as the file system has them now. A directory listing's copy lags while the file is
   open (a save being written shows its old time), so the file is asked through a handle that blocks nobody. */
static int file_now(const char *path, uint64_t *written, LONGLONG *size) {
    BY_HANDLE_FILE_INFORMATION i;
    HANDLE h = CreateFileA(path, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    int ok;
    if (h == INVALID_HANDLE_VALUE) return 0;
    ok = GetFileInformationByHandle(h, &i);
    CloseHandle(h);
    if (!ok) return 0;
    *written = ft64(i.ftLastWriteTime);
    if (size) *size = ((LONGLONG)i.nFileSizeHigh << 32) | i.nFileSizeLow;
    return 1;
}

/* 1 when nobody holds the file open for writing: it opens with reading shared only. For our own save alone, the one
   file the link waits for (a probe of every save could make the game's own save fail in that moment). */
static int file_closed(const char *path) {
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    CloseHandle(h);
    return 1;
}

/* a save file of any profile written within the last two seconds (or being written: its time moves with each write) */
static int save_just_written(void) {
    char root[MAX_PATH], pat[MAX_PATH + 64], sub[MAX_PATH + 64], file[MAX_PATH + 64], one[MAX_PATH * 2];
    WIN32_FIND_DATAA a, b, f;
    HANDLE ha, hb, hf;
    FILETIME now_ft;
    uint64_t now_u, then;
    int yes = 0;
    if (!hlib_user_path(root, "accounts")) return 0;
    GetSystemTimeAsFileTime(&now_ft);
    now_u = ft64(now_ft);
    snprintf(pat, sizeof pat, "%s\\*", root);
    ha = FindFirstFileA(pat, &a);
    if (ha == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || a.cFileName[0] == '.') continue;
        snprintf(sub, sizeof sub, "%s\\%s\\*", root, a.cFileName);
        hb = FindFirstFileA(sub, &b);
        if (hb == INVALID_HANDLE_VALUE) continue;
        do {
            if (!(b.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || b.cFileName[0] == '.') continue;
            snprintf(file, sizeof file, "%s\\%s\\%s\\*.a8s", root, a.cFileName, b.cFileName);
            hf = FindFirstFileA(file, &f);
            if (hf == INVALID_HANDLE_VALUE) continue;
            do {
                snprintf(one, sizeof one, "%s\\%s\\%s\\%s", root, a.cFileName, b.cFileName, f.cFileName);
                then = ft64(f.ftLastWriteTime); /* kept when the file cannot be asked */
                file_now(one, &then, NULL);
                if (now_u >= then && sl_save_recent((now_u - then) / 10000)) yes = 1;
            } while (!yes && FindNextFileA(hf, &f));
            FindClose(hf);
        } while (!yes && FindNextFileA(hb, &b));
        FindClose(hb);
    } while (!yes && FindNextFileA(ha, &a));
    FindClose(ha);
    return yes;
}

static void engine_state(SLEngine *e) {
    memset(e, 0, sizeof *e);
    e->loading = hlib_live_reload_loading();
    e->reload_pending = hlib_asset_reload_pending();
    e->transition = transition_active();
    e->scenario = scenario_pending();
    e->saving = save_just_written();
}

const char *hlib_live_link_busy(int needs_assets) {
    SLEngine e;
    engine_state(&e);
    return sl_busy_reason(&e, needs_assets);
}

/* The pass is over (the loader's line is in the log): the answer waits for the engine's own end line, which comes a
   frame or more later and is dropped without a line in several states. */
static void confirm_begin(void) {
    sl_confirm_begin(&llm_fl.confirm, GetTickCount64()); /* the frame's own time is old: the Lua call blocked for the pass */
    llm_fl.kind = FL_CONFIRM;
}

static void confirm_progress(ULONGLONG now) {
    SLMarks fresh;
    char msg[320], data[1100];
    int r;
    glog_take(&fresh);
    if (fresh.starts) hlib_asset_reload_started(); /* from here only the end line lifts the guard */
    r = sl_confirm_step(&llm_fl.confirm, &fresh, now, RELOAD_START_WAIT_MS, RELOAD_END_WAIT_MS);
    if (r == SL_CONFIRMED) {
        hlib_asset_reload_end(); /* the registry is read again from here, and the modules look their assets up anew */
        snprintf(msg, sizeof msg, "%s", llm_fl.msg);
        sl_append_stale_note(msg, sizeof msg);
        snprintf(data, sizeof data, "%s", llm_fl.data[0] ? llm_fl.data : "{}");
        sl_json_add_bool(data, sizeof data, "confirmed", 1);
        sl_json_add_bool(data, sizeof data, "staleObjects", 1);
        hlib_logf("live link: the engine's asset rebuild ended; the registry is read again");
        llm_answer("done", msg, data);
    } else if (r == SL_DROPPED) {
        hlib_asset_reload_end();
        llm_answer("skipped", "the engine did not start its asset rebuild within 10 s: it drops a reload without a line in a multiplayer game, "
                          "while a session loads or saves, or when another job holds the game; the mod pass ran, the asset database is the old one", NULL);
    } else if (r == SL_UNFINISHED) {
        llm_answer("error", "the engine began its asset rebuild but has not ended it in 60 s: the game may hang in the asset load (a file of the pass is "
                        "broken or missing); the asset lookups stay off until the engine's end line", NULL);
    }
}

static void pass_progress(ULONGLONG now) {
    static char buf[256 * 1024], data[2048], msg[200];
    uint64_t size;
    size_t n = llr_log_from(llm_fl.off, buf, sizeof buf, &size);
    LLPass p;
    ll_pass_scan(buf, n, &p);
    if (p.completed) {
        ll_pass_json(data, sizeof data, &p);
        snprintf(msg, sizeof msg, "patched in %.0f s, %d warning(s), %d error(s)%s%s", p.seconds, p.warnings, p.errors,
                 p.first_problem[0] ? "; first: " : "", p.first_problem);
        llm_log_off = (size < llm_fl.off ? 0 : llm_fl.off) + n;
        if (!s_glog[0]) { /* no game log to watch: the pass's end is all there is to go by */
            hlib_asset_reload_end();
            llm_answer("done", msg, data);
            return;
        }
        snprintf(llm_fl.msg, sizeof llm_fl.msg, "%s", msg);
        snprintf(llm_fl.data, sizeof llm_fl.data, "%s", data);
        confirm_begin();
        confirm_progress(now);
    } else if (ll_waited_out(llm_fl.started, now, PASS_WAIT_MS)) {
        hlib_asset_reload_end();
        llm_answer("skipped", "the loader's log shows no finished pass: the game did not run it (a loading screen, or no mod loader)", NULL);
    }
}

/* a reload the player started by hand (or any other) is noticed by the engine's lines, so the guard covers it too */
void llr_engine_watch(ULONGLONG now) {
    SLMarks m;
    if (now < s_engine_next) return;
    s_engine_next = now + ENGINE_WATCH_MS;
    glog_take(&m);
    if (m.starts && !hlib_asset_reload_pending()) hlib_log("live link: the engine began an asset rebuild (not asked by the link)");
    if (m.starts) hlib_asset_reload_begin(), hlib_asset_reload_started();
    if (m.ends) hlib_asset_reload_end();
}

static void files_progress(ULONGLONG now) {
    static HlibReloadResult res[RESULTS_MAX];
    static LLFileResult files[RESULTS_MAX];
    static char json[LLM_JSON_MAX];
    const char *why = "";
    int pending = 0, n = hlib_live_reload_results(llm_fl.batch, res, RESULTS_MAX, &pending);
    if (pending) {
        if (ll_waited_out(llm_fl.started, now, FILES_WAIT_MS))
            llm_answer("busy", "still waiting: a session loads or a model's levels still load; the request stays queued", NULL);
        return;
    }
    for (int i = 0; i < n; i++) {
        files[i].code = res[i].code == HLIB_RELOAD_DONE ? LL_FILE_DONE : res[i].code == HLIB_RELOAD_HANDED ? LL_FILE_HANDED
                        : res[i].code == HLIB_RELOAD_NOT_LOADED ? LL_FILE_NOT_LOADED : LL_FILE_REFUSED;
        files[i].ms = res[i].ms;
        snprintf(files[i].path, sizeof files[i].path, "%s", res[i].path);
        snprintf(files[i].why, sizeof files[i].why, "%s", res[i].why);
    }
    ll_files_json(json, sizeof json, files, n);
    llm_answer(ll_files_status(files, n, &why), why, json);
}

/* The engine's scene reload record (docs/live-link.md "The scene reload"): the controller's, read fresh every time */
static unsigned char *scene_record(void) {
    void **holder = (void **)hlib_target(T_DATA_UI_CONTROLLER);
    unsigned char *ctrl = holder ? (unsigned char *)*holder : NULL;
    return ctrl ? *(unsigned char **)(ctrl + T_OFF_UICTRL_SCENE_RELOAD) : NULL;
}

static void scenes_progress(ULONGLONG now) {
    unsigned char *rec = scene_record();
    int countdown, busy;
    char msg[600];
    if (!rec) {
        llm_answer("unavailable", "the UI's scene record is not there", NULL);
        return;
    }
    countdown = *(int *)(rec + T_OFF_SCENE_RELOAD_COUNT), busy = rec[T_OFF_SCENE_RELOAD_BUSY] != 0;
    if (countdown <= 0 && !llm_fl.zero_at) llm_fl.zero_at = now;
    if (ll_scene_done(countdown, busy, &llm_fl.seen_busy, llm_fl.zero_at ? now - llm_fl.zero_at : 0)) {
        hlib_scenes_reloaded();
        ll_scene_message(msg, sizeof msg, llm_fl.names, llm_fl.known, llm_fl.nnames);
        llm_answer("done", msg, NULL);
    } else if (ll_waited_out(llm_fl.started, now, SCENES_WAIT_MS)) {
        llm_answer("skipped", "the UI did not start the reload (no UI frames, or a loading screen holds it)", NULL);
    }
}

/* Sound banks (reload.banks; docs/live-link.md section 9): the language switch's own pattern for one bank - unload it (or
   un-prepare it), then load it again - through Wwise's public calls with our callback, so each step's result is known.
   Whole banks first; a refusal or a failed unload tries the prepared kind. The bank list's record of the id stays. */
typedef void(__cdecl *AkBankCb)(uint32_t id, const void *ptr, int result, void *cookie);
typedef int(__cdecl *AkLoadFn)(uint32_t id, AkBankCb cb, void *cookie, uint32_t type);
typedef int(__cdecl *AkUnloadFn)(uint32_t id, const void *mem, AkBankCb cb, void *cookie, uint32_t type);
typedef int(__cdecl *AkPrepareFn)(int op, uint32_t id, AkBankCb cb, void *cookie, uint32_t flags, uint32_t type);
#define AK_SUCCESS 1

enum { BK_START, BK_UNLOAD_WHOLE, BK_UNLOAD_PREPARED, BK_LOAD, BK_END };
typedef struct {
    LLBankResult res;
    int step;
    volatile LONG seen, code; /* the callback's: set on Wwise's bank thread */
} Bank;
static Bank s_bank[LL_NAMES_MAX];
static int s_nbank;
static volatile LONG s_bank_gen;

static void __cdecl bank_cb(uint32_t id, const void *ptr, int result, void *cookie) {
    uintptr_t c = (uintptr_t)cookie;
    unsigned i = (unsigned)(c & 0xff);
    (void)id, (void)ptr;
    if ((LONG)(c >> 8) != s_bank_gen || i >= LL_NAMES_MAX) return; /* the answer to a request that is over */
    InterlockedExchange(&s_bank[i].code, result);
    InterlockedExchange(&s_bank[i].seen, 1);
}

static void *bank_cookie(int i) { return (void *)(((uintptr_t)s_bank_gen << 8) | (uintptr_t)i); }

static void bank_end(Bank *b, int code) { b->res.code = code, b->step = BK_END; }

/* the unload of one kind of bank is asked for; a refusal at once counts as a failed callback */
static void bank_ask_unload(int i, int prepared) {
    Bank *b = &s_bank[i];
    AkUnloadFn unload = (AkUnloadFn)hlib_target(T_AK_UNLOAD_BANK);
    AkPrepareFn prepare = (AkPrepareFn)hlib_target(T_AK_PREPARE_BANK);
    int r;
    InterlockedExchange(&b->seen, 0);
    b->res.prepared = prepared, b->step = prepared ? BK_UNLOAD_PREPARED : BK_UNLOAD_WHOLE;
    r = prepared ? prepare(1, b->res.id, bank_cb, bank_cookie(i), 1, 0) : unload(b->res.id, NULL, bank_cb, bank_cookie(i), 0);
    if (r != AK_SUCCESS) InterlockedExchange(&b->code, r), InterlockedExchange(&b->seen, 1);
}

static void bank_ask_load(int i) {
    Bank *b = &s_bank[i];
    AkLoadFn load = (AkLoadFn)hlib_target(T_AK_LOAD_BANK);
    AkPrepareFn prepare = (AkPrepareFn)hlib_target(T_AK_PREPARE_BANK);
    int r;
    InterlockedExchange(&b->seen, 0);
    b->step = BK_LOAD;
    r = b->res.prepared ? prepare(0, b->res.id, bank_cb, bank_cookie(i), 0, 0) : load(b->res.id, bank_cb, bank_cookie(i), 0);
    if (r != AK_SUCCESS) b->res.ak = r, bank_end(b, LL_BANK_LOAD_FAILED);
}

/* one step of a bank when its callback has come in (or at the start) */
static void bank_step(int i) {
    Bank *b = &s_bank[i];
    if (b->step == BK_START) {
        bank_ask_unload(i, 0);
    } else if (!b->seen) {
        return; /* still waiting for Wwise's bank thread */
    } else if (b->step == BK_UNLOAD_WHOLE) {
        if (b->code == AK_SUCCESS) bank_ask_load(i);
        else b->res.ak = (int)b->code, bank_ask_unload(i, 1); /* not a whole bank: a prepared one, then */
    } else if (b->step == BK_UNLOAD_PREPARED) {
        if (b->code == AK_SUCCESS) bank_ask_load(i);
        else b->res.ak = (int)b->code, bank_end(b, LL_BANK_UNLOAD_FAILED);
    } else if (b->step == BK_LOAD) {
        b->res.ak = (int)b->code;
        bank_end(b, b->code == AK_SUCCESS ? LL_BANK_DONE : LL_BANK_LOAD_FAILED);
    }
}

static void banks_progress(ULONGLONG now) {
    static char json[LLM_JSON_MAX];
    LLBankResult res[LL_NAMES_MAX];
    const char *why = "";
    int open = 0;
    for (int i = 0; i < s_nbank; i++) {
        if (s_bank[i].step != BK_END) bank_step(i);
        open += s_bank[i].step != BK_END;
    }
    if (open && ll_waited_out(llm_fl.started, now, BANKS_WAIT_MS)) {
        for (int i = 0; i < s_nbank; i++)
            if (s_bank[i].step != BK_END) bank_end(&s_bank[i], LL_BANK_TIMEOUT); /* no callback: Wwise's bank thread is busy */
        open = 0;
    }
    if (open) return;
    for (int i = 0; i < s_nbank; i++) res[i] = s_bank[i].res;
    ll_banks_json(json, sizeof json, res, s_nbank);
    llm_answer(ll_banks_status(res, s_nbank, &why), why, json);
}

void llr_start_banks(const LLRequest *r, ULONGLONG now) {
    void **engine = (void **)hlib_target(T_DATA_SOUND_ENGINE);
    if (!engine || !*engine || !*((unsigned char *)*engine + T_OFF_SOUND_READY)) {
        llm_answer("unavailable", "the sound engine is not up (no audio device, or the game is still starting)", NULL);
        return;
    }
    if (r->nnames <= 0) {
        llm_answer("skipped", "no bank named", NULL);
        return;
    }
    memset(s_bank, 0, sizeof s_bank);
    InterlockedIncrement(&s_bank_gen);
    s_nbank = r->nnames;
    for (int i = 0; i < r->nnames; i++) {
        snprintf(s_bank[i].res.name, LL_NAME_LEN, "%s", r->names[i]);
        s_bank[i].res.id = ll_wwise_id(r->names[i]);
        if (s_bank[i].res.id == LL_BANK_INIT_ID) bank_end(&s_bank[i], LL_BANK_PROTECTED); /* the Init bank carries every bus */
    }
    hlib_logf("live link: banks: %d bank(s) are unloaded and loaded again", s_nbank);
    llm_fl.kind = FL_BANKS, llm_fl.started = now;
    banks_progress(now);
}

/* The session reload (reload.session): the game saves under a fixed name, the file is awaited, and the game loads it
   again - its own flow, which reads the island files anew. The profile's folder is found by the file itself. */
static int newest_save(FILETIME since, uint64_t *at, LONGLONG *size, char *path, size_t cap) {
    char root[MAX_PATH], pat[MAX_PATH + 64], sub[MAX_PATH + 64], file[MAX_PATH + 128];
    WIN32_FIND_DATAA a, b;
    HANDLE ha, hb;
    int found = 0;
    if (!hlib_user_path(root, "accounts")) return 0;
    snprintf(pat, sizeof pat, "%s\\*", root);
    ha = FindFirstFileA(pat, &a);
    if (ha == INVALID_HANDLE_VALUE) return 0;
    do {
        if (!(a.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || a.cFileName[0] == '.') continue;
        snprintf(sub, sizeof sub, "%s\\%s\\*", root, a.cFileName);
        hb = FindFirstFileA(sub, &b);
        if (hb == INVALID_HANDLE_VALUE) continue;
        do {
            uint64_t t;
            LONGLONG n;
            if (!(b.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || b.cFileName[0] == '.') continue;
            snprintf(file, sizeof file, "%s\\%s\\%s\\" LL_SESSION_SAVE ".a8s", root, a.cFileName, b.cFileName);
            if (!file_now(file, &t, &n)) continue; /* the file's own time and size, not the listing's */
            if (t >= ft64(since) && (!found || t > *at)) {
                *at = t, *size = n, found = 1;
                snprintf(path, cap, "%s", file);
            }
        } while (FindNextFileA(hb, &b));
        FindClose(hb);
    } while (FindNextFileA(ha, &a));
    FindClose(ha);
    return found;
}

static void save_progress(ULONGLONG now) {
    char cmd[64], path[MAX_PATH + 128];
    uint64_t at = 0;
    LONGLONG size = 0;
    int newer = newest_save(llm_fl.asked, &at, &size, path, sizeof path);
    /* stood still and closed: a save still held open for writing is not loaded half written */
    if (ll_save_watch(&llm_fl.watch, newer, size, now) && file_closed(path)) {
        if (!ll_lua_load_session(cmd, sizeof cmd) || !hlib_lua_run(cmd, NULL, 0)) {
            llm_answer("unavailable", "the game's command runner did not take the load command (see the log)", NULL);
            return;
        }
        hlib_registry_invalidate();
        hlib_logf("live link: session: saved (%lld bytes), loading it again", (long long)size);
        llm_answer("done", "saved the session as \"" LL_SESSION_SAVE "\" and asked the game to load it: the island files are read anew while it loads", NULL);
    } else if (ll_waited_out(llm_fl.started, now, SAVE_WAIT_MS)) {
        llm_answer("skipped", "the save did not appear in 30 s (the game refused it, or the command names are wrong): nothing was loaded", NULL);
    }
}

void llr_start_session(ULONGLONG now) {
    char cmd[64];
    SYSTEMTIME st;
    ULARGE_INTEGER u;
    if (hlib_live_reload_loading()) {
        llm_answer("busy", "a session loads: ask again when it is in", NULL);
        return;
    }
    if (!ll_lua_save_session(cmd, sizeof cmd)) {
        llm_answer("error", "the command does not fit", NULL);
        return;
    }
    GetSystemTime(&st);
    SystemTimeToFileTime(&st, &llm_fl.asked);
    u.LowPart = llm_fl.asked.dwLowDateTime, u.HighPart = llm_fl.asked.dwHighDateTime;
    u.QuadPart -= 20000000ull; /* two seconds of slack: file times are coarse on some disks */
    llm_fl.asked.dwLowDateTime = u.LowPart, llm_fl.asked.dwHighDateTime = u.HighPart;
    llm_fl.watch.size = -1;
    if (!hlib_lua_run(cmd, NULL, 0)) {
        llm_answer("unavailable", "the game's command runner did not take the save command (see the log)", NULL);
        return;
    }
    hlib_log("live link: session: the save was asked for, waiting for its file");
    llm_fl.kind = FL_SAVE, llm_fl.started = now;
}

static void run_pass(ULONGLONG now);

/* the pause asked before an asset request: the pass runs once the game is paused, or after the wait without it */
static void pause_progress(ULONGLONG now) {
    int speed = -1;
    if (game_speed(&speed) && sl_speed_paused(speed)) {
        hlib_logf("live link: the game is paused (speed %d) for the %s request", speed, llm_fl.what);
    } else if (ll_waited_out(llm_fl.started, now, SL_PAUSE_WAIT_MS)) {
        hlib_logf("live link: the game did not pause within %d ms (speed %d): the %s request runs anyway", SL_PAUSE_WAIT_MS, speed, llm_fl.what);
    } else {
        return;
    }
    run_pass(now);
}

void llr_progress(ULONGLONG now) {
    if (llm_fl.kind == FL_PAUSE) pause_progress(now);
    else if (llm_fl.kind == FL_PASS) pass_progress(now);
    else if (llm_fl.kind == FL_CONFIRM) confirm_progress(now);
    else if (llm_fl.kind == FL_FILES) files_progress(now);
    else if (llm_fl.kind == FL_SCENES) scenes_progress(now);
    else if (llm_fl.kind == FL_BANKS) banks_progress(now);
    else if (llm_fl.kind == FL_SAVE) save_progress(now);
}

/* An asset request: a running single-player session is paused first (the game's own pause, a command that lands on
   the next tick), then the pass runs; the speed goes back in llm_answer() (section 14.2). */
void llr_start_pass(ULONGLONG now, int rebuild_only, const char *what) {
    int speed = -1, have;
    if (hlib_live_reload_loading()) {
        llm_answer("busy", "a session loads: ask again when it is in", NULL);
        return;
    }
    llm_fl.rebuild_only = rebuild_only;
    snprintf(llm_fl.what, sizeof llm_fl.what, "%s", what);
    have = game_speed(&speed);
    llm_fl.paused_from = sl_pause_from(hlib_first_session() >= 0, have, speed);
    if (llm_fl.paused_from && hlib_lua_run(SL_LUA_PAUSE, NULL, 0)) {
        hlib_logf("live link: %s: the game is paused for the request (speed %d)", what, speed);
        llm_fl.kind = FL_PAUSE, llm_fl.started = now;
        pause_progress(now);
        return;
    }
    llm_fl.paused_from = 0; /* not in a session, paused already, or the runner did not take the call */
    run_pass(now);
}

static void run_pass(ULONGLONG now) {
    char cmd[80];
    int rebuild_only = llm_fl.rebuild_only;
    const char *what = llm_fl.what, *why = hlib_live_link_busy(1);
    if (why) { /* the guards again: the wait for the pause took up to 3 s since the request came */
        llm_answer("busy", why, NULL);
        return;
    }
    if (!ll_lua_reload_assets(cmd, sizeof cmd, rebuild_only)) {
        llm_answer("error", "the command does not fit", NULL);
        return;
    }
    llm_fl.off = llr_log_size();
    llr_glog_sync();
    InterlockedExchange64(&llm_asset_since_ms, (LONG64)now); /* the watchdog counts from here */
    hlib_asset_reload_begin(); /* our lookups skip their work until the engine's end line */
    if (!hlib_lua_run(cmd, NULL, 0)) {
        hlib_asset_reload_end();
        llm_answer("unavailable", "the game's command runner did not take the command (see the log)", NULL);
        return;
    }
    if (rebuild_only) {
        if (!s_glog[0]) {
            hlib_asset_reload_end();
            llm_answer("done", "asked: the asset database rebuilds from the last patched data; the game's log was not found, so the end of it is not confirmed", NULL);
            return;
        }
        snprintf(llm_fl.msg, sizeof llm_fl.msg, "the asset database was rebuilt from the last patched data");
        confirm_begin();
        confirm_progress(now);
        return;
    }
    hlib_logf("live link: %s: the mod pass ran (Mods:LoadMods), waiting for the loader's line", what);
    llm_fl.kind = FL_PASS, llm_fl.started = now;
    pass_progress(now);
}

void llr_start_files(const LLRequest *r, ULONGLONG now, const char *want) {
    const char *paths[LL_PATHS_MAX];
    uint32_t batch = 0;
    int queued;
    for (int i = 0; i < r->npaths; i++) {
        if (strcmp(ll_needs_of(r->paths[i]), want)) {
            llm_answer("refused", "a file of another kind than the request's", NULL);
            return;
        }
        paths[i] = r->paths[i];
    }
    queued = hlib_live_reload_batch(r->has_mod ? r->mod : NULL, paths, r->npaths, "the live link", &batch);
    if (queued < 0) {
        llm_answer("unavailable", "the reload of models and effects is not running (see the log)", NULL);
        return;
    }
    llm_fl.kind = FL_FILES, llm_fl.batch = batch, llm_fl.started = now;
    files_progress(now);
}

/* The engine's own scene reload, started the way its (compiled out) file watcher would: the states' ids go into the
   record's vector and its countdown is set to 2; the UI frame's tick then marks the states, runs the reload on the
   engine's lane and clears the busy byte once the scenes are loaded. The buffer is the engine's allocator's (the
   record's destructor frees it with the engine's free): ours is used again only while the record still points at it,
   a record that holds none gets a fresh one (the old may be freed with an earlier record), and one that holds another
   buffer is not written over. */
void llr_start_scenes(const LLRequest *r, ULONGLONG now) {
    static int32_t *vec; /* the buffer the record was last given; valid only while the record points at it */
    uint32_t ids[LL_NAMES_MAX];
    int32_t unique[LL_NAMES_MAX];
    int unknown, n, empty;
    const char *why;
    unsigned char *rec = scene_record();
    void *(*alloc)(size_t) = (void *(*)(size_t))hlib_target(T_UI_NEW);
    uint32_t (*id_of)(const void *) = (uint32_t(*)(const void *))hlib_target(T_UI_STATE_ID);
    int32_t **begin, **end, **cap;
    if (hlib_live_reload_loading()) {
        llm_answer("busy", "a session loads: ask again when it is in", NULL);
        return;
    }
    if (!rec || !alloc || !id_of) {
        llm_answer("unavailable", "the UI's scene record or its name lookup is not there in this game build", NULL);
        return;
    }
    if (r->nnames <= 0) {
        llm_answer("skipped", "no UI state named: nothing to reload (the suite names the states whose scene files changed)", NULL);
        return;
    }
    if (!ll_scene_can_start(*(int *)(rec + T_OFF_SCENE_RELOAD_COUNT), rec[T_OFF_SCENE_RELOAD_BUSY])) {
        llm_answer("busy", "a scene reload is already running", NULL);
        return;
    }
    llm_fl.nnames = r->nnames;
    for (int i = 0; i < r->nnames; i++) {
        struct {
            const char *text;
            int64_t length;
        } view = {r->names[i], (int64_t)strlen(r->names[i])};
        ids[i] = id_of(&view);
        llm_fl.known[i] = ids[i] != 0xffffffffu;
        snprintf(llm_fl.names[i], LL_NAME_LEN, "%s", r->names[i]);
    }
    n = ll_scene_ids(ids, r->nnames, unique, SCENE_IDS_MAX, &unknown);
    if (!n) {
        llm_answer("skipped", "none of the names is a UI state of the game", NULL);
        return;
    }
    begin = (int32_t **)(rec + T_OFF_SCENE_RELOAD_IDS), end = begin + 1, cap = begin + 2;
    empty = !*begin && !*end && !*cap;
    if (!empty && !(vec && *begin == vec)) {
        llm_answer("unavailable", "the record's id list belongs to the engine in this build: not written over", NULL);
        return;
    }
    if ((why = hlib_live_link_busy(0)) != NULL) { /* the guards again, right before the engine is handed the list */
        llm_answer("busy", why, NULL);
        return;
    }
    if (empty && !(vec = (int32_t *)alloc(SCENE_IDS_MAX * sizeof(int32_t)))) {
        llm_answer("error", "the engine gave no memory for the list", NULL);
        return;
    }
    memcpy(vec, unique, (size_t)n * sizeof(int32_t));
    hlib_scenes_reloaded(); /* the widgets die in the reload: forgotten before it, and again after it */
    *begin = vec, *end = vec + n, *cap = vec + SCENE_IDS_MAX;
    *(int *)(rec + T_OFF_SCENE_RELOAD_COUNT) = 2;
    hlib_logf("live link: scenes: %d state(s) handed to the engine's reload (%d name(s) unknown)", n, unknown);
    llm_fl.kind = FL_SCENES, llm_fl.started = now;
}

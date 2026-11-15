/* The live link's engine side run for real, with the engine's calls stubbed: mod_livelink.c, the pipe and its thread,
   the UI frame played by this program's main loop, the game's Lua runner, its log files and its asset reload faked from
   files. The Python client of the suite (pipe_run.py) talks to it over the real named pipe. Not the game: it shows the
   order of things, the refusals and the answers, not that the engine does what the stubs say.
   build.cmd test; the working folder of the "user" is the first argument, the scenario's switches are the files
   there (see pipe_run.py): humans.txt, loading.txt, drop.txt (no engine lines), hang.txt (the Lua call blocks),
   session.txt (a session is in; fast.txt: at speed 3, paused.txt: paused already, nopause.txt: the pause never lands),
   scenario.txt (the asset data manager holds a scheduled scenario load). */
#define _CRT_SECURE_NO_WARNINGS
#include "hlib.h"
#include "debug_logic.h"
#include "livelink.h"
#include "livereload.h"
#include "luacmd.h"
#include "targets.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static char g_user[MAX_PATH];
static HlibUiFrameFn g_frame;
static volatile LONG g_pending, g_gen, g_invalidated;
static volatile LONG g_stop;

/* services of the library the link uses, as small stand-ins */

static void say(const char *kind, const char *text) {
    printf("%s %s\n", kind, text);
    fflush(stdout);
}
void hlib_log(const char *msg) { say("LOG", msg); }
void hlib_logf(const char *fmt, ...) {
    char text[1100];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    say("LOG", text);
}
void hlib_log_n(int *counter, int cap, const char *fmt, ...) {
    char text[1100];
    va_list ap;
    if ((*counter)++ >= cap) return;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    say("LOG", text);
}
int hlib_format(char *out, int cap, const char *fmt, ...) {
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(out, (size_t)cap, fmt, ap);
    va_end(ap);
    return n;
}
int hlib_user_path(char *out, const char *rel) {
    int n = snprintf(out, MAX_PATH, "%s\\%s", g_user, rel);
    for (char *c = out; *c; c++)
        if (*c == '/') *c = '\\';
    return n > 0 && n < MAX_PATH;
}
static int flag(const char *name) {
    char path[MAX_PATH];
    hlib_user_path(path, name);
    return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
}
int hlib_config_int(const char *key, int dflt) {
    if (!strcmp(key, "live_link_stall_s")) return 1;
    if (!strcmp(key, "live_link_commands")) return flag("commands.txt");
    return dflt;
}
int hlib_feature_on(const char *name) { (void)name; return 1; }
int hlib_guarded(const char *site, HlibThunk fn, void *ctx) {
    (void)site;
    fn(ctx);
    return 1;
}
void hlib_register_module(const HlibModule *m, int feature_class) { (void)m, (void)feature_class; }
static const HlibEdition g_edition = {"test", 0, 0, 0, 0, 0};
const HlibEdition *hlib_edition(void) { return &g_edition; }

/* the framework's module registry as three stand-ins: one that cannot be switched, a simulation one, a reader */
static HlibModuleState g_mods[] = {
    {"trees", HLIB_EXPERIMENTAL, 1, 0, HLIB_SIM, "it cannot be switched while the game runs"},
    {"farm_growth", HLIB_EXPERIMENTAL, 0, 1, HLIB_SIM, ""},
    {"ui_dump", HLIB_DEV, 1, 1, HLIB_PRESENT, ""},
};
int hlib_module_count(void) { return 3; }
int hlib_module_state(int i, HlibModuleState *out) {
    if (i < 0 || i >= 3) return 0;
    *out = g_mods[i];
    return 1;
}
int hlib_module_set(const char *name, int on, const char **why) {
    for (int i = 0; i < 3; i++) {
        if (strcmp(g_mods[i].name, name)) continue;
        if (!g_mods[i].toggles) return *why = g_mods[i].why, 0;
        g_mods[i].on = on;
        return *why = NULL, 1;
    }
    return *why = "no module of that name", 0;
}
int hlib_on_ui_frame(const char *owner, HlibUiFrameFn fn) {
    (void)owner;
    g_frame = fn;
    return 1;
}
int hlib_human_count(void) { return flag("humans.txt") ? 2 : 1; }
int hlib_first_session(void) { return flag("session.txt") ? 0 : -1; }

/* the two engine objects the guards read: the pause manager (its holder points 8 bytes in) and the asset data manager */
static unsigned char g_pause_mgr[0x80], g_asset_mgr[0x70];
static void *g_pause_holder = g_pause_mgr + T_OFF_PAUSE_HOLDER, *g_asset_holder = g_asset_mgr;
static void set_speed(int speed) {
    char text[40];
    *(volatile int *)(g_pause_mgr + T_OFF_PAUSE_SPEED) = speed;
    snprintf(text, sizeof text, "speed %d", speed);
    say("GAME", text);
}
void *hlib_target(int id) {
    return id == T_DATA_PAUSE_MANAGER ? (void *)&g_pause_holder : id == T_DATA_ASSETDATA_MGR ? (void *)&g_asset_holder : NULL;
}
int hlib_target_ok(int id) {
    return id == T_UI_FRAME || id == T_SCRIPT_RUN_COMMAND || id == T_SCRIPT_RESULT_FREE || id == T_RECRUIT_FREE_STR ||
           id == T_DATA_SCRIPT_MANAGER || id == T_DATA_LOCAL_PLAYER || id == T_DATA_PAUSE_MANAGER || id == T_DATA_ASSETDATA_MGR;
}
void hlib_registry_invalidate(void) { InterlockedIncrement(&g_invalidated); }

/* the asset reload's guard, as engine.c keeps it: the state is what the test looks at */
void hlib_asset_reload_begin(void) {
    InterlockedExchange(&g_pending, 1);
    say("STATE", "pending on");
}
void hlib_asset_reload_started(void) {
    if (g_pending) say("STATE", "rebuild started");
}
void hlib_asset_reload_end(void) {
    InterlockedIncrement(&g_gen);
    InterlockedExchange(&g_pending, 0);
    hlib_registry_invalidate();
    say("STATE", "pending off");
}
int hlib_asset_reload_pending(void) { return (int)g_pending; }
uint32_t hlib_asset_generation(void) { return (uint32_t)g_gen; }

/* the live reload and the UI's own helpers: not part of this run */
int hlib_live_reload_install(void) { return 0; }
int hlib_live_reload_loading(void) { return flag("loading.txt"); }
int hlib_live_reload_mods(char (*id)[64], char (*folder)[260], int cap) {
    (void)id, (void)folder, (void)cap;
    return 0;
}
int hlib_live_reload_batch(const char *id, const char *const *paths, int n, const char *source, uint32_t *batch) {
    (void)id, (void)paths, (void)n, (void)source;
    if (batch) *batch = 0;
    return -1;
}
int hlib_live_reload_results(uint32_t batch, HlibReloadResult *out, int cap, int *pending) {
    (void)batch, (void)out, (void)cap;
    if (pending) *pending = 0;
    return 0;
}
int hlib_ui_dump_install_quiet(void) { return 0; }
int hlib_ui_dump_now(void *ui, const int *ids, int n, char (*files)[160], int cap) {
    (void)ui, (void)ids, (void)n, (void)files, (void)cap;
    return -1;
}
void hlib_scenes_reloaded(void) {}
int hlib_lua_run_ok(void) { return 1; }

/* The game's Lua runner: Mods:LoadMods() writes the loader's pass line at once and, a moment later, the engine's own
   two lines into the game's log (what the frame sync does a frame or more after the pass); ExecuteFilter() only the
   engine's. drop.txt: the engine drops the rebuild without a line. hang.txt: the call blocks for a while. */
static void append(const char *rel, const char *text) {
    char path[MAX_PATH];
    HANDLE h;
    DWORD put;
    hlib_user_path(path, rel);
    h = CreateFileA(path, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return;
    WriteFile(h, text, (DWORD)strlen(text), &put, NULL);
    CloseHandle(h);
}
static DWORD WINAPI engine_lines(void *unused) {
    (void)unused;
    Sleep(300);
    append("log\\Anno117_LOGFILE_TEST_2026-11-13.log", "--- AssetData Reload Start ---\n");
    Sleep(300);
    append("log\\Anno117_LOGFILE_TEST_2026-11-13.log", "--- AssetData Reload End ---\n");
    return 0;
}
/* the game's pause lands on its next tick, as a command does */
static DWORD WINAPI pause_lands(void *unused) {
    (void)unused;
    Sleep(150);
    set_speed(5);
    return 0;
}
int hlib_lua_run(const char *cmd, char *text, int cap) {
    if (text && cap) text[0] = 0;
    if (!strncmp(cmd, "Pause:", 6) || strstr(cmd, "Mods:LoadMods") || strstr(cmd, "ExecuteFilter")) say("LUA", cmd);
    if (!strcmp(cmd, "Pause:Paused()")) {
        if (!flag("nopause.txt")) CloseHandle(CreateThread(NULL, 0, pause_lands, NULL, 0, NULL));
        return 1;
    }
    if (!strncmp(cmd, "Pause:", 6)) { /* the speed buttons */
        set_speed(strstr(cmd, "Played") ? 2 : strstr(cmd, "TripleFastForward") ? 4 : strstr(cmd, "FastForward") ? 3 : -1);
        return 1;
    }
    if (strstr(cmd, "Cheat")) { /* the debug commands: what the stand-in game answers, and what was run */
        const char *give = "";
        if (strstr(cmd, "table.concat(o, \";\") end)()") && strstr(cmd, "local av, st"))
            give = "ignore_building_costs,1,0;incidents,1,1;heal_selected,0,-;fps_view,1,-";
        else if (strstr(cmd, "Cheat.CheatNames")) give = "Fast_Build;Give-Money";
        else if (strstr(cmd, "return Cheat.GlobalCheats.")) give = "true";
        else if (strstr(cmd, "DestroySelectedObjects")) give = "0"; /* a method this build does not bind */
        else if (strstr(cmd, "pcall(function()")) give = "1";
        if (strstr(cmd, "pcall(function() ")) say("LUA", cmd);
        if (text && cap) snprintf(text, (size_t)cap, "%s", give);
        return !flag("norunner.txt");
    }
    if (strstr(cmd, "Mods:LoadMods")) {
        if (flag("hang.txt")) Sleep(2500);
        append("mods\\mod-loader.log", "[00:00:01] Patching completed in 2s (0 warnings, 0 errors)\n");
    } else if (!strstr(cmd, "ExecuteFilter")) {
        return 1;
    }
    if (!flag("drop.txt")) CloseHandle(CreateThread(NULL, 0, engine_lines, NULL, 0, NULL));
    return 1;
}

#include "mod_livelink.c"
#include "livelink_reload.c"
#include "livelink_requests.c"

int main(int argc, char **argv) {
    char path[MAX_PATH];
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "--table")) { /* the table of debug commands, for pipe_run.py to compare with the suite's */
        for (int i = 0; i < dbg_count(); i++) {
            const DbgCommand *c = dbg_at(i);
            printf("%s|%s|%d|%s|%s|%d\n", c->id, c->tmpl, c->arg, c->arg_name, c->state_field, c->confirm);
        }
        return 0;
    }
    snprintf(g_user, sizeof g_user, "%s", argv[1]);
    hlib_user_path(path, "log");
    CreateDirectoryA(path, NULL);
    hlib_user_path(path, "mods");
    CreateDirectoryA(path, NULL);
    append("log\\Anno117_LOGFILE_TEST_2026-11-13.log", "game started\n");
    append("mods\\mod-loader.log", "loader started\n");
    set_speed(flag("paused.txt") ? 5 : flag("fast.txt") ? 3 : 2);
    if (!hlib_live_link_install()) return 3;
    say("READY", s_pipe_name);
    for (;;) { /* the UI thread: a frame every 10 ms, until the test deletes stop.txt's opposite: quit.txt appears */
        *(volatile int *)(g_asset_mgr + T_OFF_ASSETDATA_ACTION) = flag("scenario.txt") ? 2 : 0;
        if (g_frame) g_frame(NULL);
        if (flag("quit.txt")) break;
        Sleep(10);
    }
    hlib_live_link_shutdown();
    return 0;
}

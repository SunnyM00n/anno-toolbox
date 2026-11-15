/* VERSION.dll shim for the game: Windows loads this in place of the system DLL because the executable imports
   VERSION.dll and its own directory is searched first. The 17 exports are assembly stubs (version_stubs.asm) that
   jump through a table filled here from the real %SystemRoot%\System32\version.dll, so the game's version calls work
   unchanged. DllMain then starts the core on a worker thread (core.c), which brings up the feature modules behind
   the build guard. Build: build.cmd. */
#include "hlib.h"
#include "livelink.h"

static const char *const g_names[] = {
    "GetFileVersionInfoA",        "GetFileVersionInfoByHandle",  "GetFileVersionInfoExA",
    "GetFileVersionInfoExW",      "GetFileVersionInfoSizeA",     "GetFileVersionInfoSizeExA",
    "GetFileVersionInfoSizeExW",  "GetFileVersionInfoSizeW",     "GetFileVersionInfoW",
    "VerFindFileA",               "VerFindFileW",                "VerInstallFileA",
    "VerInstallFileW",            "VerLanguageNameA",            "VerLanguageNameW",
    "VerQueryValueA",             "VerQueryValueW"};
FARPROC g_fn[17];

void hlib_core_start(void);
int hlib_thunks_restore(void); /* hooks.c */

/* What an export does when the system DLL lacks it: fails as Windows' own missing function would (0, the last error
   ERROR_PROC_NOT_FOUND), instead of a jump to address 0. VerInstallFile's 0 means success, so it answers a failure bit. */
static DWORD WINAPI missing_export(void) {
    SetLastError(ERROR_PROC_NOT_FOUND);
    return 0;
}

static DWORD WINAPI missing_install(void) {
    SetLastError(ERROR_PROC_NOT_FOUND);
    return VIF_CANNOTREADSRC;
}

static FARPROC fallback(const char *name) {
    int install = !lstrcmpA(name, "VerInstallFileA") || !lstrcmpA(name, "VerInstallFileW");
    return (FARPROC)(void *)(install ? missing_install : missing_export);
}

static int resolve_real(void) {
    char sys[MAX_PATH];
    UINT n = GetSystemDirectoryA(sys, MAX_PATH);
    HMODULE real = NULL;
    int found = 0;
    if (n > 0 && n < MAX_PATH - 16) {
        lstrcatA(sys, "/version.dll");
        real = LoadLibraryA(sys);
    }
    for (int i = 0; i < (int)(sizeof(g_names) / sizeof(g_names[0])); i++) {
        g_fn[i] = real ? GetProcAddress(real, g_names[i]) : NULL;
        if (g_fn[i]) found++;
        else g_fn[i] = fallback(g_names[i]);
    }
    return found;
}

BOOL WINAPI DllMain(HINSTANCE inst, DWORD reason, LPVOID reserved) {
    if (reason == DLL_PROCESS_ATTACH) {
        HMODULE self;
        int found;
        DisableThreadLibraryCalls(inst);
        /* pinned: a FreeLibrary must not unload an image the game's code is hooked into */
        if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_PIN | GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, (LPCSTR)(void *)DllMain, &self))
            hlib_log("could not pin the library in the process");
        found = resolve_real();
        if (found < (int)(sizeof(g_names) / sizeof(g_names[0])))
            hlib_logf("the system version.dll gave %d of its %d functions; the others fail as missing", found,
                       (int)(sizeof(g_names) / sizeof(g_names[0])));
        char exe[MAX_PATH];
        GetModuleFileNameA(NULL, exe, MAX_PATH);
        hlib_logf("loaded into %s (exe base %p), %s build %08x, save format %d", exe, (void *)GetModuleHandleA(NULL),
                   HLIB_VERSION, hlib_library_build(), HLIB_SAVE_FORMAT);
        hlib_core_start();
    } else if (reason == DLL_PROCESS_DETACH) {
        /* pinned, so this is the process's end; a FreeLibrary (reserved 0) reaches here only when the pin failed: the
           re-pointed thunks are put back */
        hlib_live_link_shutdown(); /* the session file goes with the game, however it ends */
        if (!reserved) hlib_logf("unloading: %d thunk(s) put back", hlib_thunks_restore());
        else hlib_log("unloading");
    }
    return TRUE;
}

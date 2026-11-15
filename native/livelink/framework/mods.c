/* The mods as data: one walk over the mods folder that indexes the files the modules read (every assets.xml and
   .json), the assets.xml texts kept while the modules come up (several modules read them) and whole-file reads. The
   registry (registry.c) is the way to the mods' facts about our assets. Read at install, never written. */
#include "hlib.h"
#include "util.h"
#include <stdlib.h>
#include <string.h>

char *hlib_file_read(const char *path, size_t *len) {
    HANDLE h = CreateFileA(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    LARGE_INTEGER sz;
    char *buf = NULL;
    if (GetFileSizeEx(h, &sz) && sz.QuadPart < (96LL << 20)) {
        buf = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)sz.QuadPart + 1);
        DWORD got = 0;
        if (buf && !ReadFile(h, buf, (DWORD)sz.QuadPart, &got, NULL)) {
            HeapFree(GetProcessHeap(), 0, buf);
            buf = NULL;
        }
        if (buf) {
            buf[got] = 0;
            *len = got;
        }
    }
    CloseHandle(h);
    return buf;
}

void hlib_file_free(char *buf) {
    if (buf) HeapFree(GetProcessHeap(), 0, buf);
}

/* an exact name, or "*tail" for every name ending in tail */
static int name_matches(const char *file, const char *name) {
    if (name[0] != '*') return 0 == lstrcmpiA(file, name);
    int a = lstrlenA(file), b = lstrlenA(name + 1);
    return a >= b && 0 == lstrcmpiA(file + a - b, name + 1);
}

/* the index: the files of interest in walk order, an assets.xml with its text while it is kept */
typedef struct {
    char *path, *name; /* name points into path */
    char *text;
    size_t len;
} Indexed;

static Indexed *g_files;
static int g_nfiles, g_cfiles;
static INIT_ONCE g_once = INIT_ONCE_STATIC_INIT;
static SRWLOCK g_lock = SRWLOCK_INIT;

static void index_add(const char *path, const char *name) {
    if (g_nfiles == g_cfiles) {
        int cap = g_cfiles ? g_cfiles * 2 : 256;
        Indexed *q = (Indexed *)realloc(g_files, sizeof(Indexed) * (size_t)cap);
        if (!q) return;
        g_files = q;
        g_cfiles = cap;
    }
    size_t n = strlen(path);
    char *copy = (char *)malloc(n + 1);
    if (!copy) return;
    memcpy(copy, path, n + 1);
    Indexed *f = &g_files[g_nfiles++];
    f->path = copy;
    f->name = copy + (name - path);
    f->text = NULL;
    f->len = 0;
    if (0 == lstrcmpiA(name, "assets.xml")) f->text = hlib_file_read(copy, &f->len);
}

static void walk(const char *dir, int depth) {
    char pat[MAX_PATH], path[MAX_PATH];
    WIN32_FIND_DATAA fd;
    int dl = lstrlenA(dir);
    if (dl > MAX_PATH - 8) return;
    hlib_format(pat, sizeof(pat), "%s\\*", dir);
    HANDLE h = FindFirstFileA(pat, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (fd.cFileName[0] == '.') continue;
        if (dl + lstrlenA(fd.cFileName) + 2 >= MAX_PATH) continue;
        hlib_format(path, sizeof(path), "%s\\%s", dir, fd.cFileName);
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            if (depth < 12) walk(path, depth + 1);
        } else if (name_matches(fd.cFileName, "assets.xml") || name_matches(fd.cFileName, "*.json")) {
            index_add(path, path + dl + 1);
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
}

static BOOL CALLBACK build(PINIT_ONCE once, PVOID param, PVOID *ctx) {
    char dir[MAX_PATH];
    (void)once;
    (void)param;
    (void)ctx;
    DWORD t0 = GetTickCount();
    if (hlib_user_path(dir, "mods")) walk(dir, 0);
    hlib_logf("mods: %d file(s) indexed in %u ms", g_nfiles, (unsigned)(GetTickCount() - t0));
    return TRUE;
}

void hlib_mods_index(void) { InitOnceExecuteOnce(&g_once, build, NULL, NULL); }

void hlib_mods_release(void) {
    hlib_mods_index();
    AcquireSRWLockExclusive(&g_lock);
    for (int i = 0; i < g_nfiles; i++) {
        hlib_file_free(g_files[i].text);
        g_files[i].text = NULL;
    }
    ReleaseSRWLockExclusive(&g_lock);
}

int hlib_mods_paths(const char *file_name, HlibPathFn fn, void *ctx) {
    int n = 0;
    hlib_mods_index();
    for (int i = 0; i < g_nfiles; i++)
        if (name_matches(g_files[i].name, file_name)) {
            fn(g_files[i].path, ctx);
            n++;
        }
    return n;
}

int hlib_mods_each(const char *file_name, HlibFileFn fn, void *ctx) {
    int n = 0;
    hlib_mods_index();
    AcquireSRWLockShared(&g_lock);
    for (int i = 0; i < g_nfiles; i++) {
        if (!name_matches(g_files[i].name, file_name)) continue;
        size_t len = g_files[i].len;
        char *own = NULL, *text = g_files[i].text;
        if (!text) text = own = hlib_file_read(g_files[i].path, &len);
        if (!text) continue;
        fn(g_files[i].path, text, len, ctx);
        hlib_file_free(own);
        n++;
    }
    ReleaseSRWLockShared(&g_lock);
    return n;
}

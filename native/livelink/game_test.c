/* The game headers (game.h, build<game>.h) of the edition this is compiled for (-DHLIB_GAME_1800, -DHLIB_GAME_2205, none
   for Anno 117): the names agree with each other, the build guard has values and is not the subscription build's, and
   a game without an engine side has no target row at all. build.cmd test builds it once per game. */
#define _CRT_SECURE_NO_WARNINGS
#include "hlib.h"
#include "targets.h"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(c)                                                           \
    do {                                                                   \
        if (!(c)) {                                                        \
            printf("game_test: FAILED %s (line %d)\n", #c, __LINE__);      \
            failures++;                                                    \
        }                                                                  \
    } while (0)

static int starts_with(const char *s, const char *prefix) { return !strncmp(s, prefix, strlen(prefix)); }
static int ends_with(const char *s, const char *suffix) {
    size_t n = strlen(s), m = strlen(suffix);
    return n >= m && !strcmp(s + n - m, suffix);
}

int main(void) {
    int rows = 0;
    /* the id is the Toolbox's game key and every name that carries it agrees */
    CHECK(starts_with(G_ID, "anno") && strlen(G_ID) >= 7);
    CHECK(starts_with(G_PIPE_BASE, "\\\\.\\pipe\\" G_ID "-live-link-"));
    CHECK(starts_with(G_USER_FOLDER, "/") && ends_with(G_USER_FOLDER, "/") && strlen(G_USER_FOLDER) > 2);
    CHECK(strcmp(G_SESSION_FILE, G_SESSION_DIR "/session.json") == 0);
    CHECK(starts_with(G_SETTINGS_FILE, "log/") && starts_with(G_LOG_FILE, "log/") && starts_with(G_LOG_OLD_FILE, "log/"));
    /* the build guard names a real image, and none of the subscription builds' */
    CHECK(T_BUILD_FILESIZE > 100000000ULL && T_BUILD_TIMESTAMP != 0 && T_BUILD_SIZEOFIMAGE != 0);
    CHECK(T_BUILD_FILESIZE != 413154808ULL && T_BUILD_FILESIZE != 462464024ULL && T_BUILD_FILESIZE != 287982936ULL);
    for (int i = 0; i < T_COUNT; i++) rows += g_targets[i].rva != 0;
#if defined(HLIB_GAME_1800)
    CHECK(strcmp(G_ID, "anno1800") == 0 && strcmp(G_USER_FOLDER, "/Anno 1800/") == 0);
    CHECK(T_BUILD_FILESIZE == 430828056ULL && T_BUILD_TIMESTAMP == 0x673210E1u);
    CHECK(G_HAS_ENGINE == 0 && rows == 0);
#elif defined(HLIB_GAME_2205)
    CHECK(strcmp(G_ID, "anno2205") == 0 && strcmp(G_USER_FOLDER, "/Anno 2205/") == 0);
    CHECK(T_BUILD_FILESIZE == 172685744ULL && T_BUILD_TIMESTAMP == 0x5BAA3655u);
    CHECK(G_HAS_ENGINE == 0 && rows == 0);
#else
    CHECK(strcmp(G_ID, "anno117") == 0 && strcmp(G_USER_FOLDER, "/Anno 117 - Pax Romana/") == 0);
    CHECK(T_BUILD_FILESIZE == 388925432ULL && T_BUILD_TIMESTAMP == 0x6AF51859u);
    CHECK(G_HAS_ENGINE == 1 && rows > 100 && g_targets[T_UI_FRAME].rva != 0);
    CHECK(starts_with(G_GAME_LOG_PREFIX, "Anno117_") && starts_with(G_LOADER_LOG, "mods/"));
#endif
    if (failures) {
        printf("game_test: FAILED %d\n", failures);
        return 1;
    }
    printf("game_test: all passed (%s, %d target rows)\n", G_ID, rows);
    return 0;
}

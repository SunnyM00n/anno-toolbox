/* Host test of the live link's module answers (modules_logic.c): the list's fields per class and lane, the escaping of a
   reason, a list that does not fit, the switch's answer. build.cmd test. */
#include "modules_logic.h"

#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                         \
    do {                                                                 \
        if (!(c)) {                                                      \
            printf("linkmodules_test: FAILED %s (line %d)\n", #c, __LINE__); \
            g_fail++;                                                    \
        }                                                                \
    } while (0)

int main(void) {
    char out[1024];
    HlibModuleState m[] = {
        {"trees", HLIB_EXPERIMENTAL, 1, 0, HLIB_SIM, "it places \"layers\""},
        {"probe", HLIB_DEV, 0, 1, HLIB_PRESENT, ""},
        {"anno2070mod", HLIB_GAME, 1, 0, HLIB_SIM, ""},
    };
    int n = ml_list_json(out, sizeof out, m, 3);
    CHECK(n > 0 && (size_t)n == strlen(out));
    CHECK(strstr(out, "{\"modules\":[{\"name\":\"trees\",\"class\":\"experimental\",\"on\":true,\"switchable\":false,"
                      "\"lane\":\"simulation\",\"why\":\"it places \\\"layers\\\"\"}") == out);
    CHECK(strstr(out, "{\"name\":\"probe\",\"class\":\"dev\",\"on\":false,\"switchable\":true,\"lane\":\"presentation\""));
    CHECK(strstr(out, "\"class\":\"game\"") && !strcmp(out + n - 2, "]}"));
    CHECK(ml_list_json(out, sizeof out, m, 0) == 14 && !strcmp(out, "{\"modules\":[]}"));
    CHECK(ml_list_json(out, 40, m, 3) == -1);
    CHECK(ml_set_json(out, sizeof out, "probe", 0) > 0 && !strcmp(out, "{\"module\":\"probe\",\"on\":false}"));
    CHECK(ml_set_json(out, 10, "probe", 1) == -1);
    if (g_fail) {
        printf("linkmodules_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("linkmodules_test: all checks passed\n");
    return 0;
}

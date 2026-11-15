/* The live link's module requests, pure: modules_logic.h. Host-tested by linkmodules_test.c. */
#include "modules_logic.h"
#include "features_logic.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static int append(char *out, size_t cap, size_t *at, const char *fmt, ...) {
    va_list ap;
    int n;
    if (*at >= cap) return 0;
    va_start(ap, fmt);
    n = vsnprintf(out + *at, cap - *at, fmt, ap);
    va_end(ap);
    if (n < 0 || *at + (size_t)n >= cap) return 0;
    *at += (size_t)n;
    return 1;
}

/* a text as a JSON string body: quotes and backslashes escaped, control characters dropped */
static int append_text(char *out, size_t cap, size_t *at, const char *s) {
    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c < 0x20) continue;
        if ((c == '"' || c == '\\') && !append(out, cap, at, "\\")) return 0;
        if (!append(out, cap, at, "%c", c)) return 0;
    }
    return 1;
}

int ml_list_json(char *out, size_t cap, const HlibModuleState *m, int n) {
    size_t at = 0;
    if (!append(out, cap, &at, "{\"modules\":[")) return -1;
    for (int i = 0; i < n; i++) {
        if (!append(out, cap, &at, "%s{\"name\":\"", i ? "," : "") || !append_text(out, cap, &at, m[i].name) ||
            !append(out, cap, &at, "\",\"class\":\"%s\",\"on\":%s,\"switchable\":%s,\"lane\":\"%s\",\"why\":\"",
                    fc_class_name(m[i].feature_class), m[i].on ? "true" : "false", m[i].toggles ? "true" : "false",
                    m[i].lane == HLIB_PRESENT ? "presentation" : "simulation") ||
            !append_text(out, cap, &at, m[i].why) || !append(out, cap, &at, "\"}"))
            return -1;
    }
    return append(out, cap, &at, "]}") ? (int)at : -1;
}

int ml_set_json(char *out, size_t cap, const char *name, int on) {
    size_t at = 0;
    if (!append(out, cap, &at, "{\"module\":\"") || !append_text(out, cap, &at, name) ||
        !append(out, cap, &at, "\",\"on\":%s}", on ? "true" : "false"))
        return -1;
    return (int)at;
}

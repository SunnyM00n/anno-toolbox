/* A text as a JSON string and a file's GUID as text (widgetread_logic.h): what the readers of the live widget tree share. */
#include "widgetread_logic.h"

#include <stdio.h>
#include <string.h>

/* How many bytes the well-formed UTF-8 sequence at s[i] takes (k bytes in all), 0 for a stray, overlong, surrogate or
   cut one: those are written as U+FFFD so the text reads as UTF-8 whatever the game's string held. */
static size_t utf8_len(const unsigned char *s, size_t i, size_t k) {
    unsigned char c = s[i];
    size_t n = c >= 0xC2 && c <= 0xDF ? 2 : c >= 0xE0 && c <= 0xEF ? 3 : c >= 0xF0 && c <= 0xF4 ? 4 : 0;
    if (!n || i + n > k) return 0;
    for (size_t j = 1; j < n; j++)
        if ((s[i + j] & 0xC0) != 0x80) return 0;
    if ((c == 0xE0 && s[i + 1] < 0xA0) || (c == 0xED && s[i + 1] >= 0xA0)) return 0;
    if ((c == 0xF0 && s[i + 1] < 0x90) || (c == 0xF4 && s[i + 1] >= 0x90)) return 0;
    return n;
}

size_t wt_json_string(char *out, const char *s, size_t k) {
    char *o = out;
    *o++ = '"';
    for (size_t i = 0; i < k; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') {
            *o++ = '\\', *o++ = (char)c;
        } else if (c < 0x20) { /* control characters as \u escapes: the text stays one JSON text */
            snprintf(o, 7, "\\u%04x", c), o += 6;
        } else if (c >= 0x80) {
            size_t n = utf8_len((const unsigned char *)s, i, k);
            if (n) memcpy(o, s + i, n), o += n, i += n - 1;
            else memcpy(o, "\\ufffd", 6), o += 6;
        } else {
            *o++ = (char)c;
        }
    }
    *o++ = '"';
    *o = 0;
    return (size_t)(o - out);
}

void wt_guid_text(const uint8_t g[16], char out[37]) {
    snprintf(out, 37, "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", g[3], g[2], g[1], g[0],
             g[5], g[4], g[7], g[6], g[8], g[9], g[10], g[11], g[12], g[13], g[14], g[15]);
}

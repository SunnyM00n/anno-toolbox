/* What every reader of the live widget tree writes alike, pure (no engine call, no Windows call): a text as a JSON
   string and a file's GUID as text. livevalues_logic.c answers with them; livevalues_test.c covers them. */
#ifndef WIDGETREAD_LOGIC_H
#define WIDGETREAD_LOGIC_H

#include <stddef.h>
#include <stdint.h>

/* the room `out` needs for a text of k bytes: every byte as a \u escape, the quotes and the NUL */
#define WT_JSON_ROOM(k) ((k) * 6 + 3)
/* The k bytes at s as a JSON string in out (WT_JSON_ROOM(k) bytes): quoted, '"' and '\' escaped, control characters
   as \u escapes, a stray, overlong, surrogate or cut UTF-8 sequence as U+FFFD. The length written, the NUL not
   counted. */
size_t wt_json_string(char *out, const char *s, size_t k);
/* the files' little-endian UUID layout (uuid bytes_le) */
void wt_guid_text(const uint8_t g[16], char out[37]);

#endif

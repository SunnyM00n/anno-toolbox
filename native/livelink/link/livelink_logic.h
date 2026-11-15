/* The live link, the pure part (mod_livelink.c is the engine side): SHA-256 and the token handshake, the frame
   buffer, a small JSON reader, the closed message set with its typed arguments (the same table as the suite's
   linkprotocol.py), the answers and events as text, what a changed file needs, the request rate, the Lua commands as
   fixed templates and the loader log's pass line. No engine call, no Windows call. */
#ifndef LIVELINK_LOGIC_H
#define LIVELINK_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define LL_PROTOCOL 1
#define LL_MAX_FRAME (1 << 20) /* the protocol's limit: a frame's JSON */
#define LL_REQ_MAX (96 * 1024) /* what a request may be: a larger one ends the connection */
#define LL_PATHS_MAX 256
#define LL_PATH_LEN 200
#define LL_NAMES_MAX 64
#define LL_NAME_LEN 64
#define LL_FIELDS_MAX 64
#define LL_FIELD_LEN 128
#define LL_VALUES_MAX 8 /* the typed values of a debug command */
#define LL_TOKENS_MAX 1100

/* SHA-256 and the keyed hash the handshake uses */
typedef struct {
    uint32_t h[8];
    uint64_t n;
    uint8_t buf[64];
    size_t fill;
} LLSha;
void ll_sha_init(LLSha *s);
void ll_sha_update(LLSha *s, const void *data, size_t n);
void ll_sha_final(LLSha *s, uint8_t out[32]);
void ll_hmac_sha256(const void *key, size_t klen, const void *msg, size_t mlen, uint8_t out[32]);
void ll_hex(const uint8_t *in, size_t n, char *out); /* lowercase, NUL-terminated: out holds 2n+1 */
/* HMAC-SHA256 keyed with the token over "purpose:value", as 64 hex digits (the suite's linkprotocol.proof) */
void ll_proof(const char *token, const char *purpose, const char *value, char out[65]);
/* 1 when given is that proof (hex, either case), compared without stopping at the first difference */
int ll_proof_ok(const char *token, const char *purpose, const char *value, const char *given);

/* the frame buffer: bytes in, whole frames out (a 4-byte little-endian length, then the JSON) */
typedef struct {
    uint8_t *data;
    size_t n, cap;
} LLInbuf;
void ll_inbuf_init(LLInbuf *b);
void ll_inbuf_free(LLInbuf *b);
int ll_inbuf_feed(LLInbuf *b, const void *data, size_t n); /* 0 when memory is short */
/* The next whole frame's JSON, copied into out (NUL-terminated) with its length: 1 given, 0 not complete yet, -1 a frame
   over LL_REQ_MAX (or over cap): the connection ends. */
int ll_inbuf_next(LLInbuf *b, char *out, size_t cap, size_t *len);
size_t ll_frame_wrap(const char *json, size_t len, uint8_t *out, size_t cap); /* the bytes of a frame; 0 if too big */

/* JSON as a flat token list in document order (an object's size is its key count, an array's its items) */
enum { LLJ_NONE, LLJ_OBJ, LLJ_ARR, LLJ_STR, LLJ_NUM, LLJ_BOOL, LLJ_NULL };
typedef struct {
    int type, start, end, size; /* end is one past the last character; a string's start/end exclude its quotes */
} LLTok;
int ll_json_parse(const char *s, size_t len, LLTok *tok, int max); /* the token count, or -1 */
int ll_json_string(const char *s, const LLTok *t, char *out, int cap); /* unescaped ASCII; 0 when it is not plain */
int ll_json_escape(const char *in, char *out, size_t cap);            /* a JSON string body; the length, or -1 */

/* the closed message set: a request as the pipe thread decodes it */
typedef struct {
    uint32_t id;
    char type[24];
    int has_mod, has_mode, has_object, has_guid, has_session, has_protocol, protocol;
    char mod[LL_NAME_LEN], mode[12], session[LL_NAME_LEN];
    char proof[72], nonce[72];
    int64_t object, guid;
    int has_command, has_cheat, nvalues;
    int has_module, has_on, on; /* modules.set: a module's name and its switch */
    char module[LL_NAME_LEN];
    char command[LL_NAME_LEN], cheat[LL_NAME_LEN];
    char vname[LL_VALUES_MAX][LL_NAME_LEN]; /* debug.run's values: a name, a number, whether it was a JSON true or false */
    double vval[LL_VALUES_MAX];
    char vbool[LL_VALUES_MAX];
    int npaths, nnames, nfields, npos;
    char paths[LL_PATHS_MAX][LL_PATH_LEN];
    char names[LL_NAMES_MAX][LL_NAME_LEN]; /* states, banks or topics: the request has one of them */
    char fields[LL_FIELDS_MAX][LL_FIELD_LEN];
    double pos[3];
} LLRequest;
/* Decode and check one request frame (not re-entrant: one pipe thread). 1 and *out set; 0 bad arguments, -1 a name outside
   the set; err says why, and out->id is the request's id when it could be read (the answer goes to it). */
int ll_request_parse(const char *json, size_t len, LLRequest *out, char *err, int cap);
int ll_request_is(const LLRequest *r, const char *type);
/* a path of a request: lower case, '/' only, no "..", no drive; 1 and out set */
int ll_path_ok(const char *path);

/* what a changed mod file needs: a reload kind ("models", "effects", "assets", "texts", "scenes", "ambient", "banks",
   "session"), "textures" (the asset reload, then a new request), or "restart" (the suite's linkprotocol.needs_of) */
const char *ll_needs_of(const char *path);
/* the data of a files.changed answer: {"needs":{"<path>":"<kind>",...}}; the length, or -1 when it does not fit */
int ll_needs_json(const LLRequest *r, char *out, size_t cap);

/* answers, events and the greeting as JSON text; the length, or -1 when it does not fit. data: a JSON object or NULL */
int ll_answer_json(char *out, size_t cap, uint32_t id, const char *status, const char *message, const char *data);
int ll_event_json(char *out, size_t cap, const char *event, const char *data);
int ll_greeting_json(char *out, size_t cap, const char *challenge);
int ll_session_json(char *out, size_t cap, const char *pipe, const char *token, const char *game, const char *build,
                    const char *library, unsigned pid);

/* the mod loader's log after a request: when the pass ended, how long it took, and what it complained about */
typedef struct {
    int completed;
    double seconds;
    int warnings, errors, no_matching_node, missing_file;
    char first_problem[160];
} LLPass;
/* the data of the answers and events */
int ll_welcome_json(char *out, size_t cap, const char *game, const char *build, const char *library, int kinds_bits,
                    char (*mod_ids)[64], char (*mod_folders)[260], int nmods, const char *proof);
int ll_selection_json(char *out, size_t cap, int64_t object, int64_t guid, const char *session); /* object < 0: none */
/* the text a read came back with (one line per field) as {"values":{"<field>":"<line>",...}} */
int ll_values_json(char *out, size_t cap, char fields[][LL_FIELD_LEN], int n, const char *text);
int ll_pass_json(char *out, size_t cap, const LLPass *p);
/* what became of each file of a models or effects reload */
enum { LL_FILE_DONE = 1, LL_FILE_HANDED, LL_FILE_NOT_LOADED, LL_FILE_REFUSED };
typedef struct {
    char path[120], why[100];
    int code;
    double ms;
} LLFileResult;
int ll_files_json(char *out, size_t cap, const LLFileResult *f, int n);
/* done when any file went through, skipped when every one was not loaded, else refused; message names the first reason */
const char *ll_files_status(const LLFileResult *f, int n, const char **message);

/* topics */
enum { LL_TOPIC_SELECTION = 1, LL_TOPIC_RELOAD = 2, LL_TOPIC_SESSION = 4, LL_TOPIC_LOG = 8 };
int ll_topic_bit(const char *name); /* 0 when it is not a topic */
const char *ll_topic_name(int bit);

/* the request rate: a bucket of LL_RATE_BURST requests refilled at LL_RATE_PER_S a second */
#define LL_RATE_BURST 20
#define LL_RATE_PER_S 10
typedef struct {
    double tokens;
    uint64_t last_ms;
} LLRate;
void ll_rate_init(LLRate *r, uint64_t now_ms);
int ll_rate_take(LLRate *r, uint64_t now_ms); /* 1 when the request may go on */

/* The game's rules for one request (section 5 of the public document): 0 to run it, else a refusal sentence.
   multiplayer: more than one human; kinds_bits: what this game build serves (LL_KIND_*). 1 refused, 2 unavailable. */
enum {
    LL_KIND_ASSETS = 1, LL_KIND_TEXTS = 2, LL_KIND_SCENES = 4, LL_KIND_AMBIENT = 8, LL_KIND_MODELS = 16,
    LL_KIND_EFFECTS = 32, LL_KIND_BANKS = 64, LL_KIND_SESSION = 128, LL_KIND_SELECT = 256, LL_KIND_JUMP = 512,
    LL_KIND_READ = 1024, LL_KIND_UIDUMP = 2048, LL_KIND_DEBUG = 4096
};
int ll_kind_of_request(const char *type); /* the LL_KIND_* a request needs; 0 for the control requests */
int ll_gate(const LLRequest *r, int multiplayer, int kinds_bits, const char **why);
const char *ll_kind_name(int bit);

/* the Lua the link runs, from fixed templates; numbers are the only values that go in. 1 and cmd set, 0 when it does not fit */
int ll_lua_reload_assets(char *cmd, int cap, int rebuild_only);
int ll_lua_reload_ambient(char *cmd, int cap);
int ll_lua_select(char *cmd, int cap, int64_t object);
int ll_lua_jump_object(char *cmd, int cap, int64_t object);
int ll_lua_jump_session(char *cmd, int cap, const char *session, const double *pos); /* pos: 3 numbers or NULL */
#define LL_SESSION_SAVE "livelink" /* the save the session reload writes and loads again: <profile>/livelink.a8s */
int ll_lua_select_guid(char *cmd, int cap, int64_t guid);
int ll_lua_save_session(char *cmd, int cap);
int ll_lua_load_session(char *cmd, int cap);
int ll_lua_selection(char *cmd, int cap);                                              /* "id,guid,session" or "" */
/* A value path of an asset ("Building/BuildCost") as the Lua that reads it: AssetData(guid).Building.BuildCost; the
   whole read of several paths joined with a newline. 0 when a path part is not an identifier. */
int ll_lua_read_asset(char *cmd, int cap, int64_t guid, char fields[][LL_FIELD_LEN], int n);
int ll_lua_read_object(char *cmd, int cap, int64_t object, char fields[][LL_FIELD_LEN], int n);
/* the selection's text "id,guid,session" parsed; 1 when there is one */
int ll_selection_parse(const char *text, int64_t *object, int64_t *guid, char *session, int cap);

void ll_pass_scan(const char *text, size_t len, LLPass *p);
/* what the next lines of the log are: complete lines only; the offset after the last one (the rest waits) */
size_t ll_log_complete(const char *text, size_t len);

/* the window title with the link's mark (UTF-16): on adds " [live link]" once, off takes it away; the new length */
int ll_title_mark(const uint16_t *title, int len, uint16_t *out, int cap, int on);

/* The scene reload (docs/live-link.md, "The scene reload"): the engine's own record counts down two frames from 2, then
   reloads the listed states' scenes; it is idle at a countdown of 0 or less and a busy byte of 0. */
#define LL_SCENE_QUIET_MS 600 /* after the countdown ended without a busy byte seen: the reload was too quick to see */
int ll_scene_can_start(int countdown, int busy);
/* 1 when the reload is over: the countdown ended and the busy byte went up and down again (or the quiet time passed
   without it going up); *seen_busy remembers the byte's rise between calls. since_zero_ms: since the countdown ended. */
int ll_scene_done(int countdown, int busy, int *seen_busy, uint64_t since_zero_ms);
/* The ids the names came to (0xffffffff: the engine does not know the name), each once, the known ones in out; the
   count. *unknown: how many names were not known. */
int ll_scene_ids(const uint32_t *ids, int n, int32_t *out, int cap, int *unknown);
/* The answer's sentence: "reloaded the scenes of A, B; not a state of the game: C" (names[i] known[i]) */
int ll_scene_message(char *out, int cap, char names[][LL_NAME_LEN], const int *known, int n);

/* Sound banks: Wwise's id of a name, and what became of each bank of a reload.banks request */
uint32_t ll_wwise_id(const char *name);
#define LL_BANK_INIT_ID 0x50c63a23u /* the Init bank: never unloaded by the link */
enum { LL_BANK_DONE = 1, LL_BANK_UNLOAD_FAILED, LL_BANK_LOAD_FAILED, LL_BANK_PROTECTED, LL_BANK_TIMEOUT };
typedef struct {
    char name[LL_NAME_LEN];
    uint32_t id;
    int code, ak, prepared; /* LL_BANK_*; the last AKRESULT (1 is success); loaded by preparation, not whole */
} LLBankResult;
int ll_banks_json(char *out, size_t cap, const LLBankResult *r, int n);
/* done when any bank went through, skipped when only silence came back, else refused; message names the first failure */
const char *ll_banks_status(const LLBankResult *r, int n, const char **message);

/* The session reload waits for its save: a file newer than the request whose size has stood still for LL_SAVE_STABLE_MS. */
#define LL_SAVE_STABLE_MS 800
typedef struct {
    int64_t size;
    uint64_t since_ms;
} LLSaveWatch;
int ll_save_watch(LLSaveWatch *w, int newer, int64_t size, uint64_t now_ms);

/* {"files": [names]} for the answer to ui.dump: names as the dump wrote them (plain file names) */
int ll_names_json(char *out, size_t cap, const char *key, char (*names)[160], int n);

/* the in-flight request's age against its limit, from the clock: 1 when it has waited out */
int ll_waited_out(uint64_t started_ms, uint64_t now_ms, uint64_t limit_ms);

#endif

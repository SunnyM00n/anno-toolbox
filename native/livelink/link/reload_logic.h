/* Live reload, the pure part (mod_livereload.c is the engine side): the path rules, the enabled mods from the
   loader's list, the queue of reload requests, the file event record the engine's own file handlers read, the
   file-time scan and its changes, the include rule of effect files and the per-request decision. No engine call and
   no build number here: the record's layout comes in from build117.h. */
#ifndef RELOAD_LOGIC_H
#define RELOAD_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define RL_PATH_MAX 260
#define RL_MODS_MAX 64
#define RL_QUEUE_MAX 256
#define RL_CXX_EXCEPTION 0xE06D7363u /* the code a C++ throw raises (the engine's file read throws on a bad file) */

/* what a file is to the reload: the order is the order a batch runs in (a model before its config) */
enum { RL_NONE, RL_MODEL, RL_CONFIG, RL_EFFECT, RL_INCLUDE };
const char *rl_kind_name(int kind);
int rl_kind_of(const char *path); /* by extension: .rdm, .cfg, .fx, .fxh / .fxs; RL_NONE otherwise */

/* A path a request may name, relative to a mod's folder: lower case, '/' separators, no leading "./" or "/"; only
   [a-z0-9_./-]; no "..", no drive, no empty part. 1 and out set, 0 refused. */
int rl_norm_path(const char *in, char *out, int cap);
/* the model cache's key form: "_lod<digit>." made "_lod0." (the engine's handler does the same on its side) */
void rl_model_key(const char *path, char *out, int cap);

/* the mods the loader enabled this start: "Enabled mod: <folder>  (<id>@<version>)" lines of mod-loader.log */
typedef struct {
    char id[64];
    char folder[RL_PATH_MAX]; /* with '/' separators, no trailing one */
} RLMod;
typedef struct {
    RLMod mod[RL_MODS_MAX];
    int n;
    int complete; /* the list's "Enabled mods: N" line was there */
} RLMods;
int rl_parse_loader_log(const char *log, RLMods *out); /* how many */
int rl_mod_by_id(const RLMods *m, const char *id);    /* index or -1 */
/* An absolute path inside one of the folders (case and separators ignored): the mod and the path relative to it,
   normalised as rl_norm_path; -1 when it lies in none (refused). */
int rl_mod_of_path(const RLMods *m, const char *abs, char *rel, int cap);

/* the queue: drained one request per render frame, oldest first; a request already queued is not queued twice */
typedef struct {
    int kind, mod;
    int tries;      /* frames it waited (streaming, a load) */
    uint32_t batch; /* the request or scan it came with, for the log */
    char path[RL_PATH_MAX];
} RLRequest;
typedef struct {
    RLRequest item[RL_QUEUE_MAX];
    int head, n;
} RLQueue;
int rl_queue_push(RLQueue *q, int kind, int mod, const char *path, uint32_t batch); /* 1 queued, 0 there, -1 full */
RLRequest *rl_queue_front(RLQueue *q);                                             /* 0 empty */
void rl_queue_pop(RLQueue *q);
/* a batch in run order: every model, then every config, then every effect (includes are expanded before); n queued */
int rl_queue_batch(RLQueue *q, const RLRequest *items, int n, uint32_t batch);

/* One request's paths as one batch (absolute inside a mod's folder, or relative: in the mod the request names, else
   in the first that has_file finds it in): the taken ones into items, each refused one into refused with its reason,
   both stamped with the one batch number, so the answer finds them all. The number of items; at most cap of each. */
typedef int (*RLHasFileFn)(int mod, const char *rel, void *ctx);
typedef struct {
    char path[RL_PATH_MAX];
    const char *why;
    uint32_t batch;
} RLRefused;
int rl_plan_batch(const RLMods *m, const char *id, const char *const *paths, int n, uint32_t batch, RLHasFileFn has_file,
                  void *ctx, RLRequest *items, RLRefused *refused, int cap, int *nrefused);

/* the file event record the handlers read: a zeroed record, the path as the engine's std::wstring at path_off (inline
   up to inline_chars, else in heap, which must hold the path and its terminator), the kind at kind_off */
typedef struct {
    int bytes, path_off, size_off, cap_off, inline_chars, kind_off, kind_value;
} RLEventLayout;
int rl_event_build(unsigned char *rec, const RLEventLayout *l, const uint16_t *path, int len, uint16_t *heap);
int rl_widen(const char *s, uint16_t *out, int cap); /* ASCII to UTF-16, terminated; the length or -1 */

/* The scan: every reloadable file of the enabled mods with its write time. A change is a file whose time differs from
   the last scan's or that is new; an include's change becomes a change of every effect of the same mod that includes
   it (the includes callback answers 1 for "fx includes fxh"). Deleted files are not changes. */
typedef struct {
    uint64_t mtime;
    uint32_t hash, path_at;
    uint16_t mod;
    uint8_t kind;
} RLFile;
typedef struct {
    RLFile *file;
    int n, cap;
    char *pool;
    size_t pool_n, pool_cap;
    int sorted;
} RLSnap;
void rl_snap_init(RLSnap *s);
void rl_snap_free(RLSnap *s);
int rl_snap_add(RLSnap *s, int mod, const char *rel, uint64_t mtime); /* 1 kept, 0 not reloadable, -1 no memory */
const char *rl_snap_path(const RLSnap *s, const RLFile *f);
void rl_snap_sort(RLSnap *s);
void rl_snap_move(RLSnap *to, RLSnap *from); /* the new scan becomes the last one */
typedef int (*RLIncludesFn)(int mod, const char *fx, const char *fxh, void *ctx);
int rl_snap_changes(const RLSnap *last, RLSnap *now, RLRequest *out, int cap, RLIncludesFn includes, void *ctx);

/* 1 when an effect file's text includes the named include (an #include line whose quoted name ends in its file name,
   case ignored) */
int rl_fx_includes(const char *text, size_t len, const char *fxh_path);

/* The trigger file's lines: a path relative to a mod's folder each, optionally "<mod id>:<path>"; '#' starts a comment.
   fn gets each (mod id "" when none). How many lines named a path; 0: the file asks for a scan. */
typedef void (*RLLineFn)(const char *mod_id, const char *path, void *ctx);
int rl_trigger_lines(const char *text, RLLineFn fn, void *ctx);

/* what to do with the request at the front, from what the engine side found */
typedef struct {
    int multiplayer, loading;
    int file_ok;   /* the mod's file is there and not empty */
    int found;     /* a model: in the model cache; other kinds: 1 */
    int streaming; /* a model whose levels of detail are still loading */
    int tries;
    int max_wait; /* frames a request may wait */
    int busy;     /* the link's guards: a save, a session change, an asset reload (waits, before every other test) */
} RLState;
enum { RL_RUN, RL_WAIT, RL_SKIP_NOT_LOADED, RL_REFUSE_MULTIPLAYER, RL_REFUSE_FILE, RL_REFUSE_TIMEOUT };
int rl_decide(const RLState *s);
const char *rl_decision_name(int d);

uint32_t rl_hash(const char *s); /* FNV-1a 32 of a normalised path */

#endif

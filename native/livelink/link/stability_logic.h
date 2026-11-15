/* The live link's stability guards, the pure part (mod_livelink.c is the engine side): what the engine's own
   reload-end line says, when a request is confirmed or dropped, when the link says busy, and when the pipe thread
   reports a stall. No engine call, no Windows call. */
#ifndef STABILITY_LOGIC_H
#define STABILITY_LOGIC_H

#include <stddef.h>
#include <stdint.h>

/* The game log's two lines around the engine's asset rebuild, counted in the text read since the last look. */
#define SL_LINE_START "--- AssetData Reload Start ---"
#define SL_LINE_END "--- AssetData Reload End ---"
typedef struct {
    int starts, ends;
} SLMarks;
void sl_scan_marks(const char *text, size_t len, SLMarks *m);

/* An asset reload is confirmed by the engine's own end line, not by the end of the mod pass: the swap of the asset
   database comes later and is dropped without a line in several states. start_wait_ms: how long the start line may be
   missing (the silent drop); end_wait_ms: how long the engine may take from its start line to its end line (the file
   wait has no timeout of its own). */
enum { SL_WAIT, SL_CONFIRMED, SL_DROPPED, SL_UNFINISHED };
typedef struct {
    uint64_t since_ms;
    int saw_start;
} SLConfirm;
void sl_confirm_begin(SLConfirm *c, uint64_t now_ms);
int sl_confirm_step(SLConfirm *c, const SLMarks *fresh, uint64_t now_ms, uint64_t start_wait_ms, uint64_t end_wait_ms);

/* What the game is in when a request arrives. */
typedef struct {
    int loading;        /* a session loads */
    int transition;     /* a session transition is running or pending */
    int reload_pending; /* an asset reload is in flight (asked, not yet ended) */
    int scenario;       /* the engine's data manager holds a pending scenario load or revert */
    int saving;         /* a save file was written a moment ago, or our own save runs */
} SLEngine;
/* NULL when the request may go on, else the sentence of the busy answer. needs_assets: the request replaces the asset
   database (the scenario check only matters then: the schedule would overwrite a pending scenario action). */
const char *sl_busy_reason(const SLEngine *e, int needs_assets);

/* 1 when a file written age_ms ago counts as a save in progress */
#define SL_SAVE_RECENT_MS 2000
int sl_save_recent(uint64_t age_ms);

/* 1 when the game's log made at created (file time, 100 ns units) belongs to the process started at process_start: made
   after it, with SL_LOG_SLACK of clock slack (starts lie minutes apart) */
#define SL_LOG_SLACK 50000000ull /* 5 s */
int sl_log_of_this_start(uint64_t created, uint64_t process_start);

/* The pipe thread's watchdog: 1 once per request when no UI frame came for limit_ms after an asset request began
   (asset_since_ms 0: none in flight). reported_for remembers the request it reported (its since time). */
#define SL_STALL_MS 30000
int sl_stalled(uint64_t last_frame_ms, uint64_t asset_since_ms, uint64_t now_ms, uint64_t limit_ms, uint64_t *reported_for);
/* the stalled event's data: how long the game has been silent, and why that may be */
int sl_stalled_json(char *out, size_t cap, uint64_t silent_ms);

/* The pause for an asset request (section 14.2): the pause manager's applied game speed, the game's own Lua calls of
   its speed buttons. The link pauses a running single-player session before the pass and sets the speed it found
   again after the engine's end line, unless the player changed the speed in between or the reload ended in an error. */
enum { SL_SPEED_PAUSE_PLAYABLE = 0, SL_SPEED_NORMAL = 2, SL_SPEED_FAST = 3, SL_SPEED_FASTEST = 4, SL_SPEED_PAUSE_FULL = 5 };
#define SL_LUA_PAUSE "Pause:Paused()"
#define SL_PAUSE_WAIT_MS 3000 /* the pause is a game command: it lands on the next tick */
int sl_speed_paused(int speed);
/* the speed to pause from (2, 3 or 4), 0 when the link leaves the speed alone: no session, no speed, paused already */
int sl_pause_from(int in_session, int have_speed, int speed);
/* the Lua call that sets a running speed again (Played, FastForward, TripleFastForward), NULL for any other */
const char *sl_resume_call(int speed);
/* 1 when the link sets paused_from again: it paused, the game is still in its full pause, the answer is not an error */
int sl_resume_wanted(int paused_from, int have_speed, int speed_now, const char *status);

/* The sentences that go with an asset reload's answer. */
const char *sl_stale_note(void);
const char *sl_pause_note(void);
/* msg + " " + the stale note, cut to fit; the new length */
int sl_append_stale_note(char *msg, size_t cap);
/* Adds "key":true|false to the end of a JSON object text ("{}" included); the new length, or -1 when it does not fit
   or the text is not an object. */
int sl_json_add_bool(char *json, size_t cap, const char *key, int value);

#endif

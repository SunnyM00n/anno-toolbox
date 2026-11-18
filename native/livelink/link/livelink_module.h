/* What the live link's files share (mod_livelink.c, livelink_reload.c, livelink_requests.c): the request in flight on
   the UI frame and its answer, and what the module calls of the other two. UI thread only, but for the watchdog's
   time. */
#ifndef HLIB_LIVELINK_MODULE_H
#define HLIB_LIVELINK_MODULE_H

#include "hlib.h"
#include "livelink_logic.h"
#include "stability_logic.h"

#define LLM_JSON_MAX (128 * 1024)
#define LLM_TEXT_MAX (16 * 1024)

enum { FL_NONE, FL_PASS, FL_FILES, FL_SCENES, FL_BANKS, FL_SAVE, FL_CONFIRM, FL_PAUSE };

/* what the UI frame has in flight */
typedef struct {
    int kind;
    uint32_t id, epoch, batch;
    char type[24];
    ULONGLONG started;
    uint64_t off; /* the loader log's size when a pass started */
    int seen_busy;
    ULONGLONG zero_at; /* a scene reload: when its countdown was first seen ended */
    char names[LL_NAMES_MAX][LL_NAME_LEN];
    int known[LL_NAMES_MAX], nnames;
    FILETIME asked; /* a session reload: when its save was asked for, less two seconds */
    LLSaveWatch watch;
    SLConfirm confirm; /* an asset reload waiting for the engine's own end line */
    int paused_from;   /* an asset request: the speed the link paused the game from (0: it did not) */
    int rebuild_only;  /* an asset request waiting for its pause: what to run then */
    char what[16];
    char msg[240];     /* what the answer says once the engine confirmed */
    char data[1024];
} LLFlight;

/* mod_livelink.c */
extern LLFlight llm_fl;
extern volatile LONG64 llm_asset_since_ms; /* when the asset request in flight began, 0 none: the watchdog reads it */
extern uint64_t llm_log_off;               /* how far the loader's log was read for the log topic */
extern void *llm_ui;                       /* the UI controller the frame listener was called with */
/* the request in flight is answered and over (a paused game gets its speed back first) */
void llm_answer(const char *status, const char *message, const char *data);

/* livelink_reload.c: the requests that take several frames (each starts, llr_progress carries it on) */
void llr_start_pass(ULONGLONG now, int rebuild_only, const char *what);
void llr_start_files(const LLRequest *r, ULONGLONG now, const char *want);
void llr_start_scenes(const LLRequest *r, ULONGLONG now);
void llr_start_banks(const LLRequest *r, ULONGLONG now);
void llr_start_session(ULONGLONG now);
void llr_progress(ULONGLONG now);
void llr_pause_release(const char *status, const char *message, const char *data, char *msg, size_t mcap, char *dat, size_t dcap);
void llr_engine_watch(ULONGLONG now);
uint64_t llr_log_size(void);           /* the loader's log */
size_t llr_log_from(uint64_t from, char *buf, size_t cap, uint64_t *size);
int llr_glog_sync(void);               /* the game's own log found and read to its end; 0 none */

/* livelink_requests.c: the requests answered in their frame */
void llq_run_simple(const char *cmd, const char *ok_message);
void llq_start_values(const LLRequest *r);
void llq_select_guid(int64_t guid);
void llq_start_read(const LLRequest *r);
void llq_start_debug_list(void);
void llq_start_debug_run(const LLRequest *r);
void llq_start_debug_cheat(const LLRequest *r);
void llq_start_modules_list(void);
void llq_start_modules_set(const LLRequest *r);

#endif

/* The live link's stability guards, the pure part (stability_logic.h). Host-tested by stability_test.c. */
#include "stability_logic.h"

#include <stdio.h>
#include <string.h>

static int count_in(const char *text, size_t len, const char *needle) {
    size_t n = strlen(needle);
    int found = 0;
    for (size_t i = 0; i + n <= len; i++)
        if (!memcmp(text + i, needle, n)) found++, i += n - 1;
    return found;
}

void sl_scan_marks(const char *text, size_t len, SLMarks *m) {
    m->starts = text ? count_in(text, len, SL_LINE_START) : 0;
    m->ends = text ? count_in(text, len, SL_LINE_END) : 0;
}

void sl_confirm_begin(SLConfirm *c, uint64_t now_ms) {
    c->since_ms = now_ms;
    c->saw_start = 0;
}

int sl_confirm_step(SLConfirm *c, const SLMarks *fresh, uint64_t now_ms, uint64_t start_wait_ms, uint64_t end_wait_ms) {
    uint64_t waited = now_ms > c->since_ms ? now_ms - c->since_ms : 0;
    if (fresh->starts) c->saw_start = 1;
    if (c->saw_start && fresh->ends) return SL_CONFIRMED; /* an end line with no start line of ours is an older reload's */
    if (!c->saw_start && waited >= start_wait_ms) return SL_DROPPED;
    if (c->saw_start && waited >= end_wait_ms) return SL_UNFINISHED;
    return SL_WAIT;
}

const char *sl_busy_reason(const SLEngine *e, int needs_assets) {
    if (e->loading) return "a session loads: ask again when it is in";
    if (e->reload_pending) return "an asset reload is still running: ask again when the game has finished it";
    if (e->transition) return "a session transition is running: ask again when the game has settled";
    if (e->saving) return "the game is saving: ask again in a moment";
    if (needs_assets && e->scenario) return "the engine holds a pending scenario action that a reload would overwrite: ask again later";
    return NULL;
}

int sl_save_recent(uint64_t age_ms) { return age_ms < SL_SAVE_RECENT_MS; }

int sl_log_of_this_start(uint64_t created, uint64_t process_start) { return created + SL_LOG_SLACK >= process_start; }

int sl_stalled(uint64_t last_frame_ms, uint64_t asset_since_ms, uint64_t now_ms, uint64_t limit_ms, uint64_t *reported_for) {
    uint64_t quiet_from = last_frame_ms > asset_since_ms ? last_frame_ms : asset_since_ms;
    if (!asset_since_ms || now_ms < quiet_from || now_ms - quiet_from < limit_ms) return 0;
    if (*reported_for == asset_since_ms) return 0;
    *reported_for = asset_since_ms;
    return 1;
}

int sl_stalled_json(char *out, size_t cap, uint64_t silent_ms) {
    int n = snprintf(out, cap,
                     "{\"seconds\":%llu,\"message\":\"the game has drawn no UI frame for %llu s since the asset request: it hangs "
                     "in the asset load (a file of the pass is broken or missing), or a large mod pass is still running\"}",
                     (unsigned long long)(silent_ms / 1000), (unsigned long long)(silent_ms / 1000));
    return n > 0 && (size_t)n < cap ? n : -1;
}

const char *sl_stale_note(void) {
    return "Objects already placed keep the data from before the reload; save and load the game (reload.session) for a clean state.";
}

int sl_speed_paused(int speed) { return speed == SL_SPEED_PAUSE_PLAYABLE || speed == SL_SPEED_PAUSE_FULL; }

int sl_pause_from(int in_session, int have_speed, int speed) {
    return in_session && have_speed && sl_resume_call(speed) ? speed : 0;
}

const char *sl_resume_call(int speed) {
    return speed == SL_SPEED_NORMAL ? "Pause:Played()"
           : speed == SL_SPEED_FAST ? "Pause:FastForward()"
           : speed == SL_SPEED_FASTEST ? "Pause:TripleFastForward()" : NULL;
}

int sl_resume_wanted(int paused_from, int have_speed, int speed_now, const char *status) {
    return sl_resume_call(paused_from) && have_speed && speed_now == SL_SPEED_PAUSE_FULL && strcmp(status, "error") != 0;
}

const char *sl_pause_note(void) {
    return "The link pauses a running game for an asset reload and lets it run again after the rebuild; pause it yourself "
           "where it cannot (the mod pass rebuilds the mod file map while the game streams).";
}

int sl_json_add_bool(char *json, size_t cap, const char *key, int value) {
    size_t n = strlen(json);
    int empty;
    char add[96];
    int k;
    while (n && (json[n - 1] == ' ' || json[n - 1] == '\n')) n--;
    if (n < 2 || json[0] != '{' || json[n - 1] != '}') return -1;
    empty = n == 2;
    k = snprintf(add, sizeof add, "%s\"%s\":%s}", empty ? "" : ",", key, value ? "true" : "false");
    if (k < 0 || n - 1 + (size_t)k + 1 > cap) return -1;
    memcpy(json + n - 1, add, (size_t)k + 1);
    return (int)(n - 1 + (size_t)k);
}

int sl_append_stale_note(char *msg, size_t cap) {
    size_t at = strlen(msg);
    int n = snprintf(msg + at, cap - at, "%s%s", at ? " " : "", sl_stale_note());
    if (n < 0 || at + (size_t)n >= cap) {
        msg[cap - 1] = 0;
        return (int)strlen(msg);
    }
    return (int)(at + (size_t)n);
}

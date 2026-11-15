/* Host test of the live link's stability guards (stability_logic.c): the reload lines, the confirmation by the engine's
   end line, the busy rules, the watchdog and the sentences. No game needed. */
#define _CRT_SECURE_NO_WARNINGS
#include "stability_logic.h"

#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                              \
    do {                                                                      \
        if (!(c)) {                                                           \
            printf("stability_test: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            g_fail++;                                                         \
        }                                                                     \
    } while (0)

static void test_marks(void) {
    SLMarks m;
    const char *log = "a\n" SL_LINE_START "\nb\n" SL_LINE_END "\n" SL_LINE_START "\n";
    sl_scan_marks(log, strlen(log), &m);
    CHECK(m.starts == 2 && m.ends == 1);
    sl_scan_marks("--- AssetData Reload Start --", 29, &m);
    CHECK(m.starts == 0 && m.ends == 0);
    sl_scan_marks(NULL, 0, &m);
    CHECK(m.starts == 0 && m.ends == 0);
}

static void test_confirm(void) {
    SLConfirm c;
    SLMarks none = {0, 0}, start = {1, 0}, end = {0, 1}, both = {1, 1};
    sl_confirm_begin(&c, 1000);
    CHECK(sl_confirm_step(&c, &none, 2000, 10000, 60000) == SL_WAIT);
    CHECK(sl_confirm_step(&c, &end, 2500, 10000, 60000) == SL_WAIT); /* an end line of an older reload: not ours */
    CHECK(sl_confirm_step(&c, &start, 3000, 10000, 60000) == SL_WAIT);
    CHECK(sl_confirm_step(&c, &none, 30000, 10000, 60000) == SL_WAIT); /* started: the 10 s no longer count */
    CHECK(sl_confirm_step(&c, &end, 31000, 10000, 60000) == SL_CONFIRMED);
    sl_confirm_begin(&c, 1000);
    CHECK(sl_confirm_step(&c, &both, 1500, 10000, 60000) == SL_CONFIRMED); /* both in one look */
    sl_confirm_begin(&c, 1000);
    CHECK(sl_confirm_step(&c, &none, 11000, 10000, 60000) == SL_DROPPED); /* the silent drop: no start line */
    sl_confirm_begin(&c, 1000);
    CHECK(sl_confirm_step(&c, &start, 1100, 10000, 60000) == SL_WAIT);
    CHECK(sl_confirm_step(&c, &none, 61100, 10000, 60000) == SL_UNFINISHED); /* started, never ended */
}

static void test_busy(void) {
    SLEngine e = {0};
    CHECK(sl_busy_reason(&e, 1) == NULL && sl_busy_reason(&e, 0) == NULL);
    e.scenario = 1;
    CHECK(sl_busy_reason(&e, 1) && strstr(sl_busy_reason(&e, 1), "scenario"));
    CHECK(sl_busy_reason(&e, 0) == NULL); /* a request that does not replace the database leaves the scenario alone */
    e.scenario = 0, e.saving = 1;
    CHECK(sl_busy_reason(&e, 0) && strstr(sl_busy_reason(&e, 0), "saving"));
    e.saving = 0, e.transition = 1;
    CHECK(sl_busy_reason(&e, 0) && strstr(sl_busy_reason(&e, 0), "transition"));
    e.transition = 0, e.reload_pending = 1;
    CHECK(sl_busy_reason(&e, 0) && strstr(sl_busy_reason(&e, 0), "reload"));
    e.reload_pending = 0, e.loading = 1;
    CHECK(sl_busy_reason(&e, 0) && strstr(sl_busy_reason(&e, 0), "loads"));
    CHECK(sl_save_recent(0) && sl_save_recent(1999) && !sl_save_recent(2000) && !sl_save_recent(60000));
    /* the game's log of this start, not the previous start's (file times in 100 ns) */
    {
        const uint64_t start = 133000000000000000ull, minute = 600000000ull;
        CHECK(sl_log_of_this_start(start, start) && sl_log_of_this_start(start + minute, start));
        CHECK(sl_log_of_this_start(start - SL_LOG_SLACK, start) && !sl_log_of_this_start(start - SL_LOG_SLACK - 1, start));
        CHECK(!sl_log_of_this_start(start - 10 * minute, start)); /* the start before */
    }
}

static void test_watchdog(void) {
    uint64_t seen = 0;
    CHECK(!sl_stalled(100, 0, 99999, SL_STALL_MS, &seen));                  /* no asset request in flight */
    CHECK(!sl_stalled(1000, 1000, 20000, SL_STALL_MS, &seen));              /* 19 s of quiet */
    CHECK(sl_stalled(1000, 1000, 31000, SL_STALL_MS, &seen) && seen == 1000); /* 30 s */
    CHECK(!sl_stalled(1000, 1000, 90000, SL_STALL_MS, &seen));              /* once per request */
    CHECK(!sl_stalled(40000, 1000, 60000, SL_STALL_MS, &seen));             /* frames came again */
    CHECK(sl_stalled(40000, 50000, 80000, SL_STALL_MS, &seen) && seen == 50000); /* the next request */
    CHECK(!sl_stalled(5000, 4000, 3000, SL_STALL_MS, &seen));               /* a clock that went back */
    char json[400];
    CHECK(sl_stalled_json(json, sizeof json, 31500) > 0 && strstr(json, "\"seconds\":31"));
    CHECK(sl_stalled_json(json, 20, 31500) == -1);
}

static void test_notes(void) {
    char msg[300] = "patched in 3 s";
    int n = sl_append_stale_note(msg, sizeof msg);
    CHECK(n == (int)strlen(msg) && strstr(msg, "patched in 3 s Objects already placed") && strstr(msg, "reload.session"));
    char tiny[24] = "done";
    sl_append_stale_note(tiny, sizeof tiny);
    CHECK(strlen(tiny) < sizeof tiny && !strncmp(tiny, "done ", 5));
    char empty[200] = "";
    sl_append_stale_note(empty, sizeof empty);
    CHECK(!strncmp(empty, "Objects", 7));
    CHECK(strstr(sl_pause_note(), "pauses a running game"));
}

static void test_pause(void) {
    CHECK(sl_speed_paused(SL_SPEED_PAUSE_PLAYABLE) && sl_speed_paused(SL_SPEED_PAUSE_FULL));
    CHECK(!sl_speed_paused(SL_SPEED_NORMAL) && !sl_speed_paused(SL_SPEED_FASTEST) && !sl_speed_paused(1));
    CHECK(sl_pause_from(1, 1, SL_SPEED_NORMAL) == 2 && sl_pause_from(1, 1, SL_SPEED_FAST) == 3);
    CHECK(sl_pause_from(1, 1, SL_SPEED_FASTEST) == 4);
    CHECK(!sl_pause_from(1, 1, SL_SPEED_PAUSE_FULL) && !sl_pause_from(1, 1, SL_SPEED_PAUSE_PLAYABLE)); /* paused already */
    CHECK(!sl_pause_from(1, 1, 1) && !sl_pause_from(1, 1, 6) && !sl_pause_from(1, 1, -1)); /* the deleted slow speed, nonsense */
    CHECK(!sl_pause_from(0, 1, SL_SPEED_NORMAL) && !sl_pause_from(1, 0, SL_SPEED_NORMAL)); /* no session, no speed */
    CHECK(!strcmp(sl_resume_call(2), "Pause:Played()") && !strcmp(sl_resume_call(3), "Pause:FastForward()"));
    CHECK(!strcmp(sl_resume_call(4), "Pause:TripleFastForward()") && !sl_resume_call(0) && !sl_resume_call(5));
    CHECK(sl_resume_wanted(3, 1, SL_SPEED_PAUSE_FULL, "done") && sl_resume_wanted(2, 1, SL_SPEED_PAUSE_FULL, "skipped"));
    CHECK(sl_resume_wanted(2, 1, SL_SPEED_PAUSE_FULL, "unavailable"));
    CHECK(!sl_resume_wanted(2, 1, SL_SPEED_PAUSE_FULL, "error")); /* a hanging rebuild: the game stays paused */
    CHECK(!sl_resume_wanted(0, 1, SL_SPEED_PAUSE_FULL, "done"));  /* the link did not pause */
    CHECK(!sl_resume_wanted(2, 1, SL_SPEED_FAST, "done") && !sl_resume_wanted(2, 1, SL_SPEED_PAUSE_PLAYABLE, "done")); /* the player chose */
    CHECK(!sl_resume_wanted(2, 0, SL_SPEED_PAUSE_FULL, "done"));
}

static void test_json_bool(void) {
    char a[64] = "{}", b[96] = "{\"seconds\":3.0}", c[20] = "{\"x\":1}", d[32] = "[1]";
    CHECK(sl_json_add_bool(a, sizeof a, "confirmed", 1) > 0 && !strcmp(a, "{\"confirmed\":true}"));
    CHECK(sl_json_add_bool(b, sizeof b, "staleObjects", 1) > 0 && !strcmp(b, "{\"seconds\":3.0,\"staleObjects\":true}"));
    CHECK(sl_json_add_bool(b, sizeof b, "x", 0) > 0 && strstr(b, ",\"x\":false}"));
    CHECK(sl_json_add_bool(c, sizeof c, "staleObjects", 1) == -1 && !strcmp(c, "{\"x\":1}")); /* does not fit: unchanged */
    CHECK(sl_json_add_bool(d, sizeof d, "x", 1) == -1);
}

int main(void) {
    test_json_bool();
    test_marks();
    test_confirm();
    test_busy();
    test_watchdog();
    test_notes();
    test_pause();
    if (g_fail) {
        printf("stability_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("stability_test: ok\n");
    return 0;
}

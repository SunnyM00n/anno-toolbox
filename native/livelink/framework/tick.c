/* The central simulation tick: the source of the dispatch's area step, session step and post-session step. The
   engine steps every area of every session in lockstep (100 ms of game time per step, never wall-clock time, paused
   and scaled with the game), so a subscriber that works here stays in sync with the other peers. The area step's
   layer sits at the entry of the island area's own step (T_AREA_GAME_TICK), inside its job's thread-local session
   context, ahead of the engine's per-area work, and first lets the core's session watch see the stepped session; the
   lode step inside it (T_AREA_TICK) is the fallback. The session and post-session steps wrap the session manager's
   two step slots on the live manager's vtable, placed at the first area step that sees the manager. */
#include "framework.h"
#include "compwalk_logic.h"
#include "vslot_logic.h"
#include "targets.h"

static long long (*o_step)(void *), (*o_area)(void *);
static int s_area_entry; /* 1: the layer is on the area's whole step, 0: on the lode step inside it */

static void watch(void *area) {
    int sid = cw_carea_id(area) & T_AREA_SESSION_MASK; /* the step's construction area */
    hlib_session_note(sid, hlib_session_key(), hlib_session_ctx(), hlib_session_time_ms());
}

static int s_watch_dead;

static int run_tick(void *fn, void *area) {
    ((HlibTickFn)fn)(area);
    return 0;
}

static void slots_check(void);

static void area_dispatch(void *area) {
    if (!s_watch_dead && hlib_session_listeners() && !hlib_guarded("session watch", watch, area)) s_watch_dead = 1;
    slots_check();
    hlib_dispatch(HLIB_AT_AREA_STEP, run_tick, area);
}

/* the lode step's construction area (the fallback) */
static long long step_observer(void *area) {
    area_dispatch(area);
    return o_step(area);
}

/* the area's whole step: the same gates as the engine's own lode step (the step on, a construction area) */
static long long area_observer(void *step) {
    void *area = *(unsigned char *)((char *)step + T_OFF_AREASTEP_ON) ? *(void **)((char *)step + T_OFF_AREASTEP_CAREA) : NULL;
    if (area) area_dispatch(area);
    return o_area(step);
}

void hlib_tick_register(const char *owner, HlibTickFn fn) {
    hlib_subscribe(owner, HLIB_AT_AREA_STEP, HLIB_SIM, (void *)fn);
}
int hlib_tick_ms(void) { return T_SIM_STEP_MS; }

/* The session manager's step slots: [0] its session step (before the areas are queued), [1] its post-session step
   (after them, one thread). Each wrapper finds the original of its manager's class in the table. */
static VSlotTable s_vs;
static SRWLOCK s_vs_lock = SRWLOCK_INIT;
static volatile LONG s_vs_full;
typedef long long (*StepSlotFn)(void *);

static int run_session(void *fn, void *gsm) {
    ((HlibSessionStepFn)fn)(gsm);
    return 0;
}

static long long session_slot(void *gsm) {
    StepSlotFn orig = (StepSlotFn)vs_original(&s_vs, gsm, 0);
    hlib_dispatch(HLIB_AT_SESSION_STEP, run_session, gsm);
    return orig ? orig(gsm) : 0;
}

static int run_game(void *fn, void *ctx) {
    (void)ctx;
    ((HlibMetaFn)fn)();
    return 0;
}

/* After the session's areas: the deferred meta storage changes of that session, its subscribers, and in the first
   session the game step. The point counts as live once the step is seen inside a session's context; a step without
   one leaves the ledger and the game step on their old paths (said once). */
static volatile LONG s_post_blind;
static long long post_slot(void *gsm) {
    StepSlotFn orig = (StepSlotFn)vs_original(&s_vs, gsm, 1);
    long long r = orig ? orig(gsm) : 0;
    int sid = hlib_session_index();
    if (sid < 0) {
        hlib_log_once(&s_post_blind, "tick: the post-session step runs without a session context: the ledger and the "
                                      "game step keep their old paths");
        return r;
    }
    if (!hlib_point_live(HLIB_AT_POST_SESSION)) {
        hlib_point_set_live(HLIB_AT_POST_SESSION);
        hlib_point_set_live(HLIB_AT_GAME_STEP);
        hlib_log("tick: the post-session step runs in its session's context: the ledger pays there, the game step moves there");
    }
    hlib_meta_later_flush(sid);
    hlib_dispatch(HLIB_AT_POST_SESSION, run_session, gsm);
    if (sid == hlib_first_session()) hlib_dispatch(HLIB_AT_GAME_STEP, run_game, NULL);
    return r;
}

void hlib_on_game_step(const char *owner, HlibMetaFn fn) {
    hlib_subscribe(owner, HLIB_AT_GAME_STEP, HLIB_SIM, (void *)fn);
}

/* the post-session step also carries the ledger and the game step */
static int job_wanted(HlibPoint at) {
    return hlib_subscribers(at) ||
           (at == HLIB_AT_POST_SESSION && (hlib_meta_later_users() || hlib_subscribers(HLIB_AT_GAME_STEP)));
}

static int s_slots_wanted;

/* the stepped session's manager: its vtable wrapped once per class, the table filled before the slots go in */
static void slots_check(void) {
    if (!s_slots_wanted || s_vs_full) return;
    char *(*gsm_of)(void) = (char *(*)(void))hlib_target(T_SESSION_GSM);
    void ***gsm = gsm_of ? (void ***)gsm_of() : NULL;
    void **vt = gsm ? *gsm : NULL;
    if (!vt || vs_find(&s_vs, vt) >= 0) return; /* unlocked read: a row, once added, never changes */
    AcquireSRWLockExclusive(&s_vs_lock);
    void *orig[VS_MAX_SLOTS] = {vt[T_VT_GSM_SESSION_STEP / sizeof(void *)], vt[T_VT_GSM_POST_STEP / sizeof(void *)]};
    int row = vs_add(&s_vs, vt, orig);
    if (row >= 0) {
        MemoryBarrier(); /* the originals are in the table before a wrapper can be reached */
        int ok = hlib_redirect_slot_at(&vt[T_VT_GSM_SESSION_STEP / sizeof(void *)], (void *)session_slot, NULL) &&
                 hlib_redirect_slot_at(&vt[T_VT_GSM_POST_STEP / sizeof(void *)], (void *)post_slot, NULL);
        if (ok) hlib_point_set_live(HLIB_AT_SESSION_STEP); /* the post-session step proves itself at its first call */
        hlib_logf("tick: the session manager's step slots %s (class %d)", ok ? "wrapped" : "not writable", row);
    } else if (s_vs.n >= VS_MAX_CLASSES && InterlockedExchange(&s_vs_full, 1) == 0) {
        hlib_log("tick: more session manager classes than the table holds, the rest keep their steps unwrapped");
    }
    ReleaseSRWLockExclusive(&s_vs_lock);
}

/* placed once a module subscribes or keeps per-session state; on the final pass a missing step is logged */
void hlib_tick_install(int final) {
    static int done;
    int jobs = job_wanted(HLIB_AT_SESSION_STEP) || job_wanted(HLIB_AT_POST_SESSION);
    if (done || (!hlib_subscribers(HLIB_AT_AREA_STEP) && !hlib_session_listeners() && !jobs)) return;
    s_area_entry = hlib_target_ok(T_AREA_GAME_TICK);
    if (!s_area_entry && !hlib_target_ok(T_AREA_TICK)) {
        if (final) {
            done = 1;
            hlib_log("tick: the per-area step is not available, subscribers stay idle and sessions are not watched");
        }
        return;
    }
    done = 1;
    s_slots_wanted = jobs && hlib_target_ok(T_SESSION_GSM) && hlib_target_ok(T_SESSION_GAME_TICK) &&
                     hlib_target_ok(T_POST_SESSION_TICK); /* the two jobs pin the slot offsets */
    HlibLayer l = s_area_entry ? (HlibLayer){T_AREA_GAME_TICK, HLIB_BEFORE, (void *)area_observer, (void **)&o_area, 0}
                                : (HlibLayer){T_AREA_TICK, HLIB_BEFORE, (void *)step_observer, (void **)&o_step, 0};
    if (hlib_layers("tick", &l, 1)) {
        hlib_point_set_live(HLIB_AT_AREA_STEP);
        hlib_logf("tick: subscribed to the per-area step (%s)%s", s_area_entry ? "the area's step" : "the lode step",
                   jobs ? (s_slots_wanted ? ", the session steps wait for the first manager" : ", the session steps are not available")
                        : "");
    }
}

/* Dispatch at 117's safe points: one subscriber list per point, each point with its thread and lane, so a simulation
   rule cannot sit on a frame. The sources (tick.c, events.c, session.c, uiframe.c) observe the engine's step and
   call hlib_dispatch; a source places its layer once its point has a subscriber. */
#include "framework.h"
#include "util.h"

#define DP_MAX 32
#define MUTE_AFTER 20 /* consecutive faults that mute a subscriber of a point that otherwise keeps calling it */

typedef struct {
    const char *owner;
    void *fn;
    int lane;
    volatile LONG dead, faults; /* faults: in a row; a point that does not mute outright mutes after MUTE_AFTER */
    volatile LONG64 ticks, calls; /* the budget's meter (performance counter ticks), reset by each report */
    int module;                   /* the registry index whose install subscribed it, -1 none */
    volatile LONG off;            /* withdrawn by a run-time switch: kept in place, not called */
} Sub;

/* the module whose install runs on this thread: its subscriptions are its own, for the run-time switch */
static __declspec(thread) int t_module = -1;
void hlib_dispatch_module(int module) { t_module = module; }

static Sub s_sub[HLIB_AT_COUNT][DP_MAX];
static volatile LONG s_n[HLIB_AT_COUNT];
static volatile LONG s_live[HLIB_AT_COUNT];   /* the point's source is in place (hlib_point_live) */
static volatile LONG64 s_runs[HLIB_AT_COUNT]; /* dispatches since the last report: frames and steps */
static SRWLOCK s_wlock = SRWLOCK_INIT;

/* each point: its name, where it runs, its lane, and whether one fault mutes a subscriber for the game (else MUTE_AFTER in a row do) */
static const struct {
    const char *name, *where;
    int lane, mute;
} s_point[HLIB_AT_COUNT] = {
    [HLIB_AT_AREA_STEP] = {"area step", "each island area's step, in its session's context", HLIB_SIM, 1},
    [HLIB_AT_UPKEEP] = {"upkeep pass", "a session's upkeep pass, in its context", HLIB_SIM, 1},
    [HLIB_AT_META_STEP] = {"meta step", "the meta step's lane, no session", HLIB_SIM, 1},
    [HLIB_AT_SESSION_STEP] = {"session step", "a session's job before its areas", HLIB_SIM, 1},
    [HLIB_AT_POST_SESSION] = {"post-session step", "one thread per session after its areas", HLIB_SIM, 1},
    [HLIB_AT_FACTORY] = {"factory cycle", "the factory's area job", HLIB_SIM, 1},
    [HLIB_AT_MAP_LOADED] = {"map loaded", "the session loader's thread", HLIB_SIM, 1},
    [HLIB_AT_COMMAND] = {"synced command", "the step a lockstep command runs on", HLIB_SIM, 1},
    [HLIB_AT_SESSION_END] = {"session end", "the thread that noticed it", HLIB_SIM, 1},
    [HLIB_AT_UI_FRAME] = {"UI frame", "the UI thread, after the states' update", HLIB_PRESENT, 0},
    [HLIB_AT_GAME_STEP] = {"game step", "once per step for the whole game: the first session's post-session step, "
                            "else the meta step", HLIB_SIM, 1},
};

static int point_ok(HlibPoint at) { return (int)at >= 0 && at < HLIB_AT_COUNT; }
const char *hlib_point_name(HlibPoint at) { return point_ok(at) ? s_point[at].name : "?"; }
int hlib_subscribers(HlibPoint at) { return point_ok(at) ? (int)s_n[at] : 0; }
int hlib_point_live(HlibPoint at) { return point_ok(at) && s_live[at]; }
void hlib_point_set_live(HlibPoint at) {
    if (point_ok(at)) InterlockedExchange(&s_live[at], 1);
}

int hlib_subscribe(const char *owner, HlibPoint at, HlibLane lane, void *fn) {
    int ok = 0;
    if (!point_ok(at) || !fn) return 0;
    if (lane == HLIB_SIM && s_point[at].lane == HLIB_PRESENT) {
        hlib_logf("dispatch: %s refused on the %s: a simulation subscriber on a presentation point", owner,
                   s_point[at].name);
        return 0;
    }
    AcquireSRWLockExclusive(&s_wlock);
    int k = (int)s_n[at];
    for (int i = 0; i < k; i++) /* a module switched on again subscribes what it withdrew: given back in place */
        if (s_sub[at][i].off && s_sub[at][i].fn == fn && 0 == lstrcmpA(s_sub[at][i].owner, owner)) {
            InterlockedExchange(&s_sub[at][i].off, 0);
            ReleaseSRWLockExclusive(&s_wlock);
            return 1;
        }
    if (k < DP_MAX) {
        Sub *s = &s_sub[at][k];
        s->owner = owner;
        s->fn = fn;
        s->lane = lane;
        s->dead = 0;
        s->faults = 0;
        s->module = t_module;
        s->off = 0;
        InterlockedIncrement(&s_n[at]); /* a step running meanwhile sees the entry whole */
        ok = 1;
    }
    ReleaseSRWLockExclusive(&s_wlock);
    if (!ok) hlib_logf("dispatch: no room for %s on the %s", owner, s_point[at].name);
    return ok;
}

int hlib_dispatch_withdraw(int module, int off) {
    int n = 0;
    if (module < 0) return 0;
    for (int at = 0; at < HLIB_AT_COUNT; at++)
        for (int i = 0; i < (int)s_n[at]; i++)
            if (s_sub[at][i].module == module) {
                InterlockedExchange(&s_sub[at][i].off, off ? 1 : 0);
                n++;
            }
    return n;
}

int hlib_dispatch_module_ready(int module, HlibPoint *missing) {
    for (int at = 0; at < HLIB_AT_COUNT; at++)
        for (int i = 0; i < (int)s_n[at]; i++)
            if (s_sub[at][i].module == module && !s_sub[at][i].off && !s_live[at]) {
                if (missing) *missing = (HlibPoint)at;
                return 0;
            }
    return 1;
}

typedef struct {
    HlibRunner run;
    void *fn, *ctx;
    int stop;
} Call;

static void call_one(void *p) {
    Call *c = (Call *)p;
    c->stop = c->run(c->fn, c->ctx);
}

int hlib_dispatch(HlibPoint at, HlibRunner run, void *ctx) {
    int n = hlib_subscribers(at);
    if (point_ok(at)) InterlockedIncrement64(&s_runs[at]);
    for (int i = 0; i < n; i++) {
        Sub *s = &s_sub[at][i];
        if (s->dead || s->off) continue;
        Call c = {run, s->fn, ctx, 0};
        LARGE_INTEGER t0, t1;
        QueryPerformanceCounter(&t0);
        int ok = hlib_guarded(s->owner, call_one, &c);
        QueryPerformanceCounter(&t1);
        InterlockedAdd64(&s->ticks, t1.QuadPart - t0.QuadPart);
        InterlockedIncrement64(&s->calls);
        if (!ok) {
            LONG n = InterlockedIncrement(&s->faults);
            if ((s_point[at].mute || n >= MUTE_AFTER) && 0 == InterlockedExchange(&s->dead, 1))
                hlib_logf("dispatch: %s muted on the %s after %d fault(s) in a row", s->owner, s_point[at].name, (int)n);
            continue;
        }
        if (s->faults) InterlockedExchange(&s->faults, 0);
        if (c.stop) return 1;
    }
    return 0;
}

/* The budget: per presentation module 0.25 ms a frame, all simulation together 1 ms a step. Measured per subscriber
   and logged with the perf line; a module over its line is named, never cut off. */
#define BUDGET_PRESENT_US 250
#define BUDGET_SIM_US 1000
#define BUDGET_OWNERS 64

typedef struct {
    const char *owner;
    long long us;
    int present, over;
} Spend;

static Spend *spend_of(Spend *rows, int *n, const char *owner, int present) {
    for (int i = 0; i < *n; i++)
        if (rows[i].present == present && 0 == lstrcmpA(rows[i].owner, owner)) return &rows[i];
    if (*n >= BUDGET_OWNERS) return NULL;
    Spend *r = &rows[(*n)++];
    r->owner = owner, r->us = 0, r->present = present, r->over = 0;
    return r;
}

/* the subscribers' time since the last report by owner and side; frames and steps counted by their points */
static int budget_collect(Spend *rows, long long qpf, long long *frames, long long *steps) {
    int n = 0;
    *frames = InterlockedExchange64(&s_runs[HLIB_AT_UI_FRAME], 0);
    *steps = InterlockedExchange64(&s_runs[HLIB_AT_GAME_STEP], 0);
    long long meta = InterlockedExchange64(&s_runs[HLIB_AT_META_STEP], 0);
    if (!*steps) *steps = meta; /* no game-step source: the meta step counts the steps */
    for (int at = 0; at < HLIB_AT_COUNT; at++) {
        if (at != HLIB_AT_UI_FRAME && at != HLIB_AT_GAME_STEP && at != HLIB_AT_META_STEP)
            InterlockedExchange64(&s_runs[at], 0);
        for (int i = 0; i < (int)s_n[at]; i++) {
            Sub *s = &s_sub[at][i];
            long long t = InterlockedExchange64(&s->ticks, 0);
            InterlockedExchange64(&s->calls, 0);
            Spend *r = t ? spend_of(rows, &n, s->owner, s_point[at].lane == HLIB_PRESENT) : NULL;
            if (r) r->us += t * 1000000 / qpf;
        }
    }
    return n;
}

void hlib_dispatch_budget(int seconds) {
    static Spend told[BUDGET_OWNERS]; /* the modules already named over their line this start */
    static int ntold;
    Spend rows[BUDGET_OWNERS];
    LARGE_INTEGER f;
    long long frames, steps, sim_us = 0;
    QueryPerformanceFrequency(&f);
    int n = budget_collect(rows, f.QuadPart ? f.QuadPart : 1, &frames, &steps);
    const Spend *worst_p = NULL, *worst_s = NULL;
    for (int i = 0; i < n; i++) {
        if (rows[i].present) {
            if (!worst_p || rows[i].us > worst_p->us) worst_p = &rows[i];
            if (frames > 0 && rows[i].us > BUDGET_PRESENT_US * frames) rows[i].over = 1;
        } else {
            sim_us += rows[i].us;
            if (!worst_s || rows[i].us > worst_s->us) worst_s = &rows[i];
        }
    }
    if (!n) return;
    hlib_logf("budget: last %d s: %lld frame(s), %lld step(s); presentation worst %s %.3f ms a frame (line %.2f); "
               "simulation %.3f ms a step together (line %.2f), worst %s",
               seconds, frames, steps, worst_p ? worst_p->owner : "-",
               worst_p && frames ? worst_p->us / 1000.0 / (double)frames : 0.0, BUDGET_PRESENT_US / 1000.0,
               steps ? sim_us / 1000.0 / (double)steps : 0.0, BUDGET_SIM_US / 1000.0, worst_s ? worst_s->owner : "-");
    for (int i = 0; i < n; i++) {
        if (!rows[i].over || spend_of(told, &ntold, rows[i].owner, 1)->over) continue;
        spend_of(told, &ntold, rows[i].owner, 1)->over = 1;
        hlib_logf("budget: %s spent %.3f ms a frame on the presentation side, over its line of %.2f ms (measured, not cut)",
                   rows[i].owner, rows[i].us / 1000.0 / (double)frames, BUDGET_PRESENT_US / 1000.0);
    }
    static int sim_told;
    if (steps > 0 && sim_us > BUDGET_SIM_US * steps && !sim_told++)
        hlib_logf("budget: the simulation side spent %.3f ms a step together, over its line of %.2f ms; most: %s "
                   "(measured, not cut)",
                   sim_us / 1000.0 / (double)steps, BUDGET_SIM_US / 1000.0, worst_s ? worst_s->owner : "-");
}

/* one line per point that has subscribers: where it runs, its lane, who listens */
void hlib_dispatch_report(void) {
    for (int at = 0; at < HLIB_AT_COUNT; at++) {
        char line[900];
        int n = hlib_subscribers((HlibPoint)at), len;
        if (!n) continue;
        len = hlib_format(line, (int)sizeof(line), "dispatch: %s (%s; %s): %d:", s_point[at].name,
                           s_point[at].lane == HLIB_SIM ? "simulation" : "presentation", s_point[at].where, n);
        for (int i = 0; i < n && len < (int)sizeof(line) - 40; i++)
            len += hlib_format(line + len, (int)sizeof(line) - len, " %s%s", s_sub[at][i].owner,
                                s_sub[at][i].lane == HLIB_PRESENT && s_point[at].lane == HLIB_SIM ? " (reads)" : "");
        hlib_log(line);
    }
}

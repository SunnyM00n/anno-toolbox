/* The engine's event bus as subscriptions (hlib.h hlib_on_engine_event): a module names an engine event and a lane;
   the framework hands the engine one delegate per event and lane through the dispatcher's own listener add, and the
   engine calls it on that lane (8 the simulation's, 10 the UI's) at its dispatch point. The listener add, the
   dispatcher, the invoker and the descriptors are rows of build117.h (placed on 3.0 2026-11-14); where one does not
   resolve the subscriptions are kept and logged, and the modules keep their older paths (hlib_engine_event_live). */
#include "framework.h"
#include "targets.h"

#define BUS_MAX 8 /* subscribers per event and lane */

typedef void (*AddFn)(void *dispatcher, unsigned long long lane, void *descriptor, void *delegate);

static const int s_descriptor[HLIB_EV_COUNT] = {
    [HLIB_EV_META_UNLOADED] = T_EVT_META_UNLOADED,
    [HLIB_EV_SESSION_LOADED] = T_EVT_SESSION_LOADED,
    [HLIB_EV_DESYNC] = T_EVT_DESYNC,
};
static const char *const s_name[HLIB_EV_COUNT] = {
    [HLIB_EV_META_UNLOADED] = "MetaGameUnloaded",
    [HLIB_EV_SESSION_LOADED] = "SessionLoaded",
    [HLIB_EV_DESYNC] = "MPDeSyncDetected",
};

typedef struct {
    const char *owner[BUS_MAX];
    HlibEventFn fn[BUS_MAX];
    volatile LONG dead[BUS_MAX];
    int n, live;
    unsigned char delegate[T_DELEGATE_SIZE]; /* lives as long as the process: the engine keeps a pointer to it */
} Route;

static Route s_route[HLIB_EV_COUNT][2]; /* [event][HLIB_SIM, HLIB_PRESENT] */
static SRWLOCK s_bus_lock = SRWLOCK_INIT;

typedef struct {
    HlibEventFn fn;
    const void *payload;
} BusCall;
static void bus_one(void *p) { ((BusCall *)p)->fn(((BusCall *)p)->payload); }

/* the delegate's function: rcx the route (the delegate's object), rdx the event's payload */
static void bus_deliver(void *ctx, const void *payload) {
    Route *r = (Route *)ctx;
    for (int i = 0; i < r->n; i++) {
        BusCall c = {r->fn[i], payload};
        if (!r->dead[i] && !hlib_guarded(r->owner[i], bus_one, &c)) InterlockedExchange(&r->dead[i], 1);
    }
}

int hlib_on_engine_event(const char *owner, HlibEngineEvent ev, HlibLane lane, HlibEventFn fn) {
    if ((int)ev < 0 || ev >= HLIB_EV_COUNT || !fn || (lane != HLIB_SIM && lane != HLIB_PRESENT)) return 0;
    AcquireSRWLockExclusive(&s_bus_lock);
    Route *r = &s_route[ev][lane];
    int ok = !r->live && r->n < BUS_MAX; /* subscribers come before the route goes in (install time) */
    if (ok) {
        r->owner[r->n] = owner;
        r->fn[r->n++] = fn;
    }
    ReleaseSRWLockExclusive(&s_bus_lock);
    if (!ok) hlib_logf("bus: %s's subscription to %s not taken (after the start or no room)", owner, s_name[ev]);
    return ok;
}

int hlib_engine_event_live(HlibEngineEvent ev, HlibLane lane) {
    return (int)ev >= 0 && ev < HLIB_EV_COUNT && (lane == HLIB_SIM || lane == HLIB_PRESENT) && s_route[ev][lane].live;
}

/* one listener add per event and lane that has subscribers; on the final pass the planned ones say so */
void hlib_bus_install(int final) {
    static int done;
    if (done) return;
    int wanted = 0;
    for (int e = 0; e < HLIB_EV_COUNT; e++) wanted += s_route[e][HLIB_SIM].n + s_route[e][HLIB_PRESENT].n;
    if (!wanted) return;
    AddFn add = (AddFn)hlib_target(T_EVENT_LISTENER_ADD);
    void **dispatcher = (void **)hlib_target(T_DATA_EVENT_DISPATCHER);
    void *invoker = hlib_target(T_DELEGATE_INVOKE_OBJECT);
    if (!add || !dispatcher || !*dispatcher || !invoker) {
        if (!final) return;
        done = 1;
        hlib_logf("bus: %d subscription(s) to engine events wait: the listener add did not resolve on this build, "
                   "their modules keep their older paths",
                   wanted);
        return;
    }
    done = 1;
    for (int e = 0; e < HLIB_EV_COUNT; e++)
        for (int lane = HLIB_SIM; lane <= HLIB_PRESENT; lane++) {
            Route *r = &s_route[e][lane];
            void *descriptor = hlib_target(s_descriptor[e]);
            if (!r->n) continue;
            if (!descriptor) {
                hlib_logf("bus: the %s event's descriptor is not on this build, %d subscriber(s) idle", s_name[e], r->n);
                continue;
            }
            *(void **)(r->delegate + T_OFF_DELEGATE_FN) = (void *)bus_deliver;
            *(void **)(r->delegate + T_OFF_DELEGATE_OBJECT) = r;
            *(void **)(r->delegate + T_OFF_DELEGATE_INVOKER) = invoker;
            unsigned long long id = (unsigned long long)(lane == HLIB_SIM ? T_EVENT_LANE_SIM : T_EVENT_LANE_UI) << 32 | 1;
            add(*dispatcher, id, descriptor, r->delegate); /* queued by the engine when the lane is not ours */
            r->live = 1;
            hlib_logf("bus: %s on the %s lane reaches %d subscriber(s)", s_name[e], lane == HLIB_SIM ? "simulation" : "UI",
                       r->n);
        }
}

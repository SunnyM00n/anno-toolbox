/* The sources of the dispatch's shared points (dispatch.c owns the subscriber lists): a factory's cycle start and
   end, the upkeep pass over a session's buildings, a new session's map loaded, the meta step (the diplomacy
   manager's tick), and the lockstep command the library sends its own synced actions on (it rides the tree brush's
   net command with a slot the brush never uses; the brush itself goes to its one owner). Each is one layer, placed
   once its point has a subscriber. */
#include "framework.h"
#include "compwalk_logic.h"
#include "targets.h"

static HlibBrushFn s_brush;

void hlib_on_factory(const char *owner, HlibFactoryFn fn) {
    hlib_subscribe(owner, HLIB_AT_FACTORY, HLIB_SIM, (void *)fn);
}
void hlib_on_buildings(const char *owner, HlibBuildingsFn fn) {
    hlib_subscribe(owner, HLIB_AT_UPKEEP, HLIB_SIM, (void *)fn);
}
void hlib_on_command(const char *owner, HlibCommandFn fn) {
    hlib_subscribe(owner, HLIB_AT_COMMAND, HLIB_SIM, (void *)fn);
}
void hlib_on_brush(HlibBrushFn fn) { s_brush = fn; }
void hlib_on_map_loaded(const char *owner, HlibMapFn fn) {
    hlib_subscribe(owner, HLIB_AT_MAP_LOADED, HLIB_SIM, (void *)fn);
}
void hlib_on_meta_tick(const char *owner, HlibMetaFn fn) {
    hlib_subscribe(owner, HLIB_AT_META_STEP, HLIB_SIM, (void *)fn);
}

/* the meta step: after the diplomacy manager's own tick; TODO(update): a tickable of our own (T_META_TICKABLE_ADD).
   It carries the game step until the post-session step proves live (tick.c). */
static int meta_run(void *fn, void *ctx) {
    (void)ctx;
    ((HlibMetaFn)fn)();
    return 0;
}
static void *(*o_meta)(void *);
static void *meta_observer(void *mgr) {
    void *r = o_meta(mgr);
    hlib_dispatch(HLIB_AT_META_STEP, meta_run, NULL);
    if (!hlib_point_live(HLIB_AT_POST_SESSION)) hlib_dispatch(HLIB_AT_GAME_STEP, meta_run, NULL);
    return r;
}

/* factory cycles */
typedef struct {
    void *factory;
    int finish;
} FacCall;

static int fac_run(void *fn, void *p) {
    FacCall *c = (FacCall *)p;
    ((HlibFactoryFn)fn)(c->factory, c->finish);
    return 0;
}

static void fac_dispatch(void *factory, int finish) {
    FacCall c = {factory, finish};
    hlib_dispatch(HLIB_AT_FACTORY, fac_run, &c);
}

static void *(*o_fin)(void *);
static void *(*o_start)(void *);
static void *fin_observer(void *f) {
    fac_dispatch(f, 1);
    return o_fin(f);
}
static void *start_observer(void *f) {
    fac_dispatch(f, 0);
    return o_start(f);
}

/* the upkeep pass: the engine walks a session's Maintenance components, one per building */
typedef struct {
    void *const *comps;
    int n;
} BldCall;

static int bld_run(void *fn, void *p) {
    BldCall *c = (BldCall *)p;
    ((HlibBuildingsFn)fn)(c->comps, c->n);
    return 0;
}

static long long (*o_upkeep)(void *, int);
/* the pass works only while the engine's own gate is open (the simulation runs) */
static int upkeep_open(void) {
    void **h = (void **)hlib_target(T_DATA_UPKEEP_GATE);
    return h && *h && *(int *)((char *)*h - 8 + T_OFF_UPKEEP_GATE) != 0;
}

static long long upkeep_observer(void *sys, int a2) {
    void *const *b = *(void *const **)((char *)sys + T_OFF_UPKEEP_BEGIN);
    void *const *e = *(void *const **)((char *)sys + T_OFF_UPKEEP_END);
    if (b && e > b && e - b < 1000000 && upkeep_open()) {
        BldCall c = {b, (int)(e - b)};
        hlib_dispatch(HLIB_AT_UPKEEP, bld_run, &c);
    }
    return o_upkeep(sys, a2);
}

/* A session's map loaded: the loader fills the map (the generator runs inside) and loads every island, for a new
   session and for a save alike; the listeners hear only of a new one. */
typedef struct {
    uint32_t seed, session;
} MapCall;

static int map_run(void *fn, void *p) {
    MapCall *c = (MapCall *)p;
    ((HlibMapFn)fn)(c->seed, c->session);
    return 0;
}

/* the loader's own test: 1 a new session, 0 a save, -1 unreadable */
static int job_new_session(const char *job) {
    __try {
        char *const *map = *(char *const **)(job + T_OFF_JOB_REQ_MAP);
        unsigned long long i = (*(unsigned long long *)(job + T_OFF_JOB_REQ_MAPSIZE) - 1) & *(unsigned long long *)(job + T_OFF_JOB_REQ_OFF);
        const char *req = map ? map[i] : NULL;
        return req ? *(void *const *)(req + T_OFF_REQ_SAVE) == NULL : -1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

static uint32_t map_seed(const char *settings) {
    __try {
        return settings ? *(const uint32_t *)(settings + T_OFF_SETTINGS_SEED) : 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

static unsigned long long (*o_load)(void *, void *, void *, char, uint32_t);
static unsigned long long load_observer(void *job, void *tpl, void *settings, char flag, uint32_t session) {
    unsigned long long r = o_load(job, tpl, settings, flag, session);
    int fresh = (unsigned char)r ? job_new_session((const char *)job) : -2;
    if (fresh == 1) {
        MapCall c = {map_seed((const char *)settings), session};
        hlib_logf("events: the map of new session %u is loaded (map seed %u), %d listener(s)", session, c.seed,
                   hlib_subscribers(HLIB_AT_MAP_LOADED));
        hlib_dispatch(HLIB_AT_MAP_LOADED, map_run, &c);
    } else {
        hlib_logf("events: the map of session %u is loaded for %s, no map event (a save's deposits come from the save)", session,
                   fresh == 0 ? "a save" : fresh == -2 ? "nothing (the load failed)" : "an unreadable loader job");
    }
    return r;
}

/* The island areas of the session in the thread's context, as the engine's own walks take them, each handed on as
   its construction area (owner, economy, fertilities, limited lodes); the ids are copied first, so a listener's
   engine calls cannot move the list under the walk. An id the construction areas do not hold yet is skipped and
   logged: the list's own pointer is the island area, a different object. */
int hlib_each_island_area(HlibAreaFn fn, void *ctx) {
    static int n_skip, n_odd;
    short ids[1024];
    char *(*gsm)(void) = (char *(*)(void))hlib_target(T_SESSION_GSM);
    int n = cw_island_ids(gsm ? gsm() : NULL, ids, (int)(sizeof(ids) / sizeof(ids[0]))), passed = 0;
    for (int i = 0; i < n; i++) {
        void *area = hlib_area(ids[i]);
        if (!area) continue;
        if (cw_carea_id(area) != (unsigned short)ids[i]) /* the id field the central tick's readers rely on */
            hlib_log_n(&n_odd, 2, "events: the construction area of %#x carries id %#x", (unsigned short)ids[i], cw_carea_id(area));
        fn(ids[i], area, ctx);
        passed++;
    }
    if (passed < n)
        hlib_log_n(&n_skip, 4, "events: %d of %d island area(s) have no construction area yet, skipped (their first step "
                    "takes them)", n - passed, n);
    return passed;
}

/* commands */
typedef struct {
    short area;
    int32_t code;
    const int32_t *arg;
} CmdCall;

static int cmd_run(void *fn, void *p) { /* the first listener that knows the code takes it */
    CmdCall *c = (CmdCall *)p;
    return ((HlibCommandFn)fn)(c->area, c->code, c->arg);
}

static void *(*o_brush)(void *, short, int, short, int *);
static void *brush_observer(void *wm, short slot, int count, short area, int *rect) {
    if (slot == HLIB_CMD_SLOT) {
        CmdCall c = {area, count, rect};
        hlib_dispatch(HLIB_AT_COMMAND, cmd_run, &c);
        return NULL;
    }
    return s_brush ? s_brush(o_brush, wm, slot, count, area, rect) : o_brush(wm, slot, count, area, rect);
}

typedef void *(*NetBufFn)(void);
typedef void (*NetBeginFn)(void *buf, int group, int id);
typedef void (*NetHeaderFn)(void *buf);
typedef void (*NetAppendFn)(void *vec, void *pos, const void *src, size_t n);

/* Queue a command on the lockstep buffer, as the tree brush queues its own: every client runs it on the same
   step, each listener gets (area, code, arg[0..3]) and the first that knows the code takes it. */
int hlib_command_send(short area, int32_t code, const int32_t arg[4]) {
    NetBufFn nb = (NetBufFn)hlib_target(T_NET_BUFFER);
    NetBeginFn begin = (NetBeginFn)hlib_target(T_NET_BEGIN);
    NetHeaderFn header = (NetHeaderFn)hlib_target(T_NET_HEADER);
    NetAppendFn app = (NetAppendFn)hlib_target(T_NET_APPEND);
    char *owner = nb ? (char *)nb() : NULL;
    if (!o_brush || !owner || !begin || !header || !app) return 0;
    char *buf = owner + T_OFF_NET_BUFFER;
    begin(buf, (int)T_NET_BRUSH_GROUP, (int)T_NET_BRUSH_ID);
    header(buf);
    void **st = *(void ***)(buf + T_OFF_NETBUF_STREAM);
    short slot = HLIB_CMD_SLOT;
    /* the brush handler reads slot, count, area, then the rect in the order 1 0 3 2 of what it hands on */
    app(st, st[1], &slot, 2);
    app(st, st[1], &code, 4);
    app(st, st[1], &area, 2);
    app(st, st[1], &arg[1], 4);
    app(st, st[1], &arg[0], 4);
    app(st, st[1], &arg[3], 4);
    app(st, st[1], &arg[2], 4);
    char *begin_ptr = (char *)st[0], *end_ptr = (char *)st[1];
    long long start = *(long long *)(buf + T_OFF_NETBUF_RECORD);
    *(unsigned int *)(begin_ptr + start) = (unsigned int)(end_ptr - begin_ptr - start);
    return 1;
}

int hlib_command_ready(void) { return o_brush != 0; }

/* One layer per hook point, placed on the first resolution pass where its point has a subscriber and its targets
   matched; a later subscriber rides the layer already in. On the final pass a point whose targets never matched says
   so. */
static int s_fac_done, s_bld_done, s_cmd_done, s_map_done, s_meta_done;

static int ready(int *done, int listeners, const int *targets, int nt, const char *what, int final) {
    if (*done || !listeners) return 0;
    for (int i = 0; i < nt; i++)
        if (!hlib_target_ok(targets[i])) {
            if (final) {
                *done = 1;
                hlib_logf("events: %s is not available on this build, its %d listener(s) stay idle", what, listeners);
            }
            return 0;
        }
    *done = 1;
    return 1;
}

/* the brush's net command, for our slot only, carries our synced commands instead of the brush's work */
static const char WHY_CARRIER[] = "the library's synced commands ride the tree brush's net command on a slot the brush "
                                  "never uses; planned: a command of our own once a dispatch case can be added "
                                  "without a patch";

void hlib_events_install(int final) {
    static const int fac_t[] = {T_FACTORY_FINISH, T_FACTORY_START}, bld_t[] = {T_UPKEEP_PASS}, cmd_t[] = {T_BUILD_TREES};
    static const int map_t[] = {T_LOAD_TEMPLATE, T_SESSION_GSM, T_AREA_GET}, meta_t[] = {T_DIPLO_META_TICK};
    int nmap = hlib_subscribers(HLIB_AT_MAP_LOADED);
    int nmeta = hlib_subscribers(HLIB_AT_META_STEP) + hlib_subscribers(HLIB_AT_GAME_STEP);
    int nfac = hlib_subscribers(HLIB_AT_FACTORY), nbld = hlib_subscribers(HLIB_AT_UPKEEP);
    int ncmd = hlib_subscribers(HLIB_AT_COMMAND);
    if (ready(&s_map_done, nmap, map_t, 3, "the map-loaded event", final)) {
        HlibLayer l = {T_LOAD_TEMPLATE, HLIB_AFTER, (void *)load_observer, (void **)&o_load, 0};
        if (hlib_layers("events", &l, 1)) {
            hlib_point_set_live(HLIB_AT_MAP_LOADED);
            hlib_logf("events: a new map reaches %d listener(s)", nmap);
        }
    }
    if (ready(&s_meta_done, nmeta, meta_t, 1, "the meta step", final)) {
        HlibLayer l = {T_DIPLO_META_TICK, HLIB_AFTER, (void *)meta_observer, (void **)&o_meta, 0};
        if (hlib_layers("events", &l, 1)) {
            hlib_point_set_live(HLIB_AT_META_STEP);
            hlib_point_set_live(HLIB_AT_GAME_STEP);
            hlib_logf("events: the meta step reaches %d listener(s) (with the game step's)", nmeta);
        }
    }
    if (ready(&s_fac_done, nfac, fac_t, 2, "the factory cycle", final)) {
        HlibLayer l[] = {{T_FACTORY_FINISH, HLIB_BEFORE, (void *)fin_observer, (void **)&o_fin, 0},
                          {T_FACTORY_START, HLIB_BEFORE, (void *)start_observer, (void **)&o_start, 0}};
        if (hlib_layers("events", l, 2)) {
            hlib_point_set_live(HLIB_AT_FACTORY);
            hlib_logf("events: factory cycles reach %d listener(s)", nfac);
        }
    }
    if (ready(&s_bld_done, nbld, bld_t, 1, "the upkeep pass", final)) {
        HlibLayer l = {T_UPKEEP_PASS, HLIB_BEFORE, (void *)upkeep_observer, (void **)&o_upkeep, 0};
        if (hlib_layers("events", &l, 1)) {
            hlib_point_set_live(HLIB_AT_UPKEEP);
            hlib_logf("events: the upkeep pass reaches %d listener(s)", nbld);
        }
    }
    if (ready(&s_cmd_done, ncmd || s_brush, cmd_t, 1, "the synced command", final)) {
        HlibLayer l = {T_BUILD_TREES, HLIB_REPLACES, (void *)brush_observer, (void **)&o_brush, WHY_CARRIER};
        if (hlib_layers("events", &l, 1)) {
            hlib_point_set_live(HLIB_AT_COMMAND);
            hlib_logf("events: synced commands reach %d listener(s)%s", ncmd, s_brush ? ", the brush has its owner" : "");
        }
    }
}

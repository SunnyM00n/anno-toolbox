/* Host test of the framework: the layer registry (layer.c) and the dispatch (dispatch.c) on real calls, with the hook
   primitives and the guard stood in; the feature classes and the run-time switch (features_logic.c); and the target
   table's update-stability rows (targets.h) against the build's rows. build.cmd test. */
#include "layer.c"
#include "dispatch.c"
#include "ledger.c"
#include "features_logic.h"
#include "util.h"
#include "vslot_logic.h"
#include <stdio.h>
#include <string.h>
#include <stdarg.h>

static int g_fail, g_install_ok = 1, g_installs, g_logs;

/* the ledger's engine side stood in: the thread's session and the meta storage's add, recorded in call order */
static int g_sid = -1, g_nadds;
static struct {
    uint32_t p, product;
    int amount;
} g_adds[16];
int hlib_session_index(void) { return g_sid; }
int hlib_meta_add(uint32_t p, uint32_t product, int amount) {
    if (g_nadds < 16) g_adds[g_nadds].p = p, g_adds[g_nadds].product = product, g_adds[g_nadds++].amount = amount;
    return 1;
}
void hlib_log_once(volatile LONG *done, const char *fmt, ...) {
    (void)fmt;
    if (InterlockedIncrement(done) == 1) g_logs++;
}

#define CHECK(c)                                                    \
    do {                                                            \
        if (!(c)) {                                                 \
            printf("framework_test: FAILED %s (line %d)\n", #c, __LINE__); \
            g_fail++;                                               \
        }                                                           \
    } while (0)

void hlib_log(const char *m) {
    (void)m;
    g_logs++;
}
void hlib_logf(const char *f, ...) {
    (void)f;
    g_logs++;
}

/* the stand-in of the engine function and of its patched entry: the site calls whatever the first layer is */
static int g_orig_calls;
static int orig_add(int x) {
    g_orig_calls++;
    return x + 1;
}
static int (*g_entry)(int) = orig_add;

int hlib_install_entry_observers(const HlibEntry *e, int n) {
    if (!g_install_ok) return 0;
    for (int i = 0; i < n; i++) {
        *e[i].original = (void *)orig_add;
        g_entry = (int (*)(int))e[i].observer;
    }
    g_installs++;
    return 1;
}
static int g_thunks;
static int (*g_thunk)(int) = orig_add; /* the thunk's target as the re-pointed jump reaches it */
int hlib_thunk_retarget(int target, void *fn, void **next) {
    (void)target;
    if (!g_install_ok) return 0;
    *next = (void *)orig_add;
    g_thunk = (int (*)(int))fn;
    g_thunks++;
    return 1;
}
int hlib_patch_jump(int target, void *to, void **back) {
    (void)target, (void)to, (void)back;
    return g_install_ok;
}
int hlib_patch_bytes(const HlibPatch *p, int n) {
    (void)p, (void)n;
    return g_install_ok;
}
int hlib_guarded(const char *site, HlibThunk fn, void *ctx) {
    (void)site;
    __try {
        fn(ctx);
        return 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return 0;
    }
}

/* three modules' layers on one target */
static int (*n_a)(int), (*n_b)(int), (*n_c)(int), (*n_x)(int);
static int layer_a(int x) { return n_a(x * 2); }      /* before: doubles the input */
static int layer_b(int x) { return n_b(x) + 100; }    /* after: adds to the result */
static int layer_c(int x) { return n_c(x) * 10; }     /* after, in a later batch */
static int (*n_t1)(int), (*n_t2)(int);
static int layer_t1(int x) { return n_t1(x * 2); } /* the thunk's layers */
static int layer_t2(int x) { return n_t2(x) * 10; }
static int layer_x(int x) { return x < 0 ? 0 : n_x(x); } /* skips the original for a negative input */

static void test_layers(void) {
    HlibLayer ab[2] = {{T_AREA_TICK, HLIB_BEFORE, (void *)layer_a, (void **)&n_a, 0},
                        {T_AREA_TICK, HLIB_AFTER, (void *)layer_b, (void **)&n_b, 0}};
    CHECK(hlib_layers("a", ab, 2));
    CHECK(g_installs == 1); /* one site for the batch's two layers */
    CHECK(g_entry(3) == 107 && g_orig_calls == 1);
    HlibLayer c = {T_AREA_TICK, HLIB_AFTER, (void *)layer_c, (void **)&n_c, 0};
    CHECK(hlib_layers("c", &c, 1));
    CHECK(g_installs == 1); /* spliced in behind b, no second site */
    g_orig_calls = 0;
    CHECK(g_entry(3) == 170 && g_orig_calls == 1); /* a(3) -> b(6) -> c(6) -> orig(6) = 7; *10; +100 */
    CHECK(!hlib_layers("c", &c, 1));             /* the same layer twice */

    /* a skip must say why; without it nothing goes in, with it the declaration is listed */
    HlibLayer x = {T_UPKEEP_PASS, HLIB_SKIPS, (void *)layer_x, (void **)&n_x, 0};
    int rows = s_nrows;
    CHECK(!hlib_layers("x", &x, 1) && s_nrows == rows && !n_x);
    x.why = "a negative input is not the engine's";
    CHECK(hlib_layers("x", &x, 1) && s_nrows == rows + 1);
    HlibLayer bad = {T_FACTORY_START, 0, (void *)layer_a, (void **)&n_a, 0};
    CHECK(!hlib_layers("bad", &bad, 1)); /* no kind */

    /* all or nothing: a refused site leaves the table and the layer's next as they were */
    static int (*n_y)(int);
    HlibLayer y = {T_FACTORY_FINISH, HLIB_AFTER, (void *)layer_c, (void **)&n_y, 0};
    g_install_ok = 0;
    rows = s_nrows;
    CHECK(!hlib_layers("y", &y, 1) && s_nrows == rows && !n_y);
    g_install_ok = 1;

    /* patches: never on a layered target, never two on one; recorded with their kind */
    static const unsigned char nop = 0x90;
    HlibPatch p = {T_AREA_TICK, 0, &nop, 1};
    CHECK(!hlib_site_bytes("p", &p, 1, HLIB_REPLACES, "test"));
    CHECK(hlib_site_jump("j", T_UPRIGHT_SITE, HLIB_SKIPS, (void *)layer_a, NULL, "test"));
    CHECK(!hlib_site_jump("j", T_UPRIGHT_SITE, HLIB_SKIPS, (void *)layer_b, NULL, "test"));
    HlibLayer z = {T_UPRIGHT_SITE, HLIB_AFTER, (void *)layer_b, (void **)&n_b, 0};
    CHECK(!hlib_layers("z", &z, 1));
    /* a thunk layer: the first re-points the thunk, a second is spliced in behind it, a patch stays refused */
    HlibLayer t1 = {T_DISC_TILES_THUNK, HLIB_BEFORE, (void *)layer_t1, (void **)&n_t1, 0};
    HlibLayer t2 = {T_DISC_TILES_THUNK, HLIB_AFTER, (void *)layer_t2, (void **)&n_t2, 0};
    CHECK(hlib_thunk_layer("t", &t1) && g_thunks == 1);
    CHECK(hlib_thunk_layer("t", &t2) && g_thunks == 1);
    g_orig_calls = 0;
    CHECK(g_thunk(3) == 70 && g_orig_calls == 1 && n_t1 == layer_t2); /* t1(3) -> t2(6) -> orig = 7, *10 */
    CHECK(!hlib_thunk_layer("t", &t2));
    HlibPatch tp = {T_DISC_TILES_THUNK, 0, &nop, 1};
    CHECK(!hlib_site_bytes("tp", &tp, 1, HLIB_REPLACES, "test"));
    int logs = g_logs;
    hlib_layers_report();
    CHECK(g_logs > logs);
}

/* dispatch: order, lanes, muting, the first taker */
static int g_order[8], g_norder, g_boom_calls;
static void tick_1(void *a) {
    (void)a;
    g_order[g_norder++] = 1;
}
static void tick_2(void *a) {
    (void)a;
    g_order[g_norder++] = 2;
}
static void tick_boom(void *a) {
    (void)a;
    g_boom_calls++;
    *(volatile int *)0 = 1; /* a fault inside a subscriber */
}
static int run_tick(void *fn, void *ctx) {
    ((HlibTickFn)fn)(ctx);
    return 0;
}
static int take_2(short area, int32_t code, const int32_t arg[4]) {
    (void)area, (void)arg;
    return code == 2;
}
static int g_after_taker;
static int take_any(short area, int32_t code, const int32_t arg[4]) {
    (void)area, (void)code, (void)arg;
    g_after_taker++;
    return 1;
}
static int run_cmd(void *fn, void *ctx) { return ((HlibCommandFn)fn)(0, *(int32_t *)ctx, NULL); }

static void test_dispatch(void) {
    CHECK(hlib_subscribe("one", HLIB_AT_AREA_STEP, HLIB_SIM, (void *)tick_1));
    CHECK(hlib_subscribe("boom", HLIB_AT_AREA_STEP, HLIB_SIM, (void *)tick_boom));
    CHECK(hlib_subscribe("two", HLIB_AT_AREA_STEP, HLIB_PRESENT, (void *)tick_2)); /* a reader on a sim point */
    CHECK(!hlib_subscribe("rule", HLIB_AT_UI_FRAME, HLIB_SIM, (void *)tick_1));   /* a sim rule on a frame */
    CHECK(hlib_subscribers(HLIB_AT_AREA_STEP) == 3 && hlib_subscribers(HLIB_AT_UI_FRAME) == 0);
    hlib_dispatch(HLIB_AT_AREA_STEP, run_tick, NULL);
    hlib_dispatch(HLIB_AT_AREA_STEP, run_tick, NULL);
    CHECK(g_norder == 4 && g_order[0] == 1 && g_order[1] == 2 && g_order[2] == 1 && g_order[3] == 2);
    CHECK(g_boom_calls == 1); /* muted after its fault on a simulation point */
    CHECK(hlib_subscribe("boom", HLIB_AT_UI_FRAME, HLIB_PRESENT, (void *)tick_boom));
    hlib_dispatch(HLIB_AT_UI_FRAME, run_tick, NULL);
    hlib_dispatch(HLIB_AT_UI_FRAME, run_tick, NULL);
    CHECK(g_boom_calls == 3); /* the frame does not mute */
    CHECK(hlib_subscribe("two", HLIB_AT_COMMAND, HLIB_SIM, (void *)take_2));
    CHECK(hlib_subscribe("any", HLIB_AT_COMMAND, HLIB_SIM, (void *)take_any));
    int32_t code = 2;
    CHECK(hlib_dispatch(HLIB_AT_COMMAND, run_cmd, &code) == 1 && g_after_taker == 0);
    code = 5;
    CHECK(hlib_dispatch(HLIB_AT_COMMAND, run_cmd, &code) == 1 && g_after_taker == 1);
    for (int i = 0; i < DP_MAX + 2; i++) hlib_subscribe("many", HLIB_AT_META_STEP, HLIB_SIM, (void *)tick_1);
    CHECK(hlib_subscribers(HLIB_AT_META_STEP) == DP_MAX);
    hlib_dispatch_report();
}

/* the deferred meta storage changes: at once until the post-session step is live, then summed per session, player
   and product and paid by the flush in GUID order whatever order the area jobs came in */
static void test_ledger(void) {
    hlib_meta_later_use("test");
    CHECK(hlib_meta_later_users() == 1);
    g_sid = 0;
    CHECK(hlib_meta_add_later(9, 1, 5) && g_nadds == 1); /* not live: at once */
    g_nadds = 0;
    hlib_point_set_live(HLIB_AT_POST_SESSION);
    CHECK(hlib_point_live(HLIB_AT_POST_SESSION) && !hlib_point_live(HLIB_AT_SESSION_STEP));
    g_sid = -1;
    CHECK(hlib_meta_add_later(9, 1, 5) && g_nadds == 1); /* no session in the thread: at once */
    g_nadds = 0;
    g_sid = 1;
    hlib_meta_add_later(9, 1, 5);
    hlib_meta_add_later(3, 2, -4);
    hlib_meta_add_later(9, 1, 7);
    hlib_meta_add_later(3, 1, 2);
    g_sid = 2;
    hlib_meta_add_later(3, 1, 100);
    hlib_meta_add_later(5, 1, 0); /* nothing to pay */
    CHECK(g_nadds == 0);
    hlib_meta_later_flush(1);
    CHECK(g_nadds == 3);
    CHECK(g_adds[0].p == 3 && g_adds[0].product == 1 && g_adds[0].amount == 2);
    CHECK(g_adds[1].p == 3 && g_adds[1].product == 2 && g_adds[1].amount == -4);
    CHECK(g_adds[2].p == 9 && g_adds[2].product == 1 && g_adds[2].amount == 12);
    hlib_meta_later_flush(1);
    CHECK(g_nadds == 3); /* paid once */
    hlib_meta_later_flush(2);
    CHECK(g_nadds == 4 && g_adds[3].amount == 100);
    hlib_dispatch_budget(60); /* the budget line over what the dispatch measured */
}

/* the stability table against the build's rows: every target classed, planned ones without an address, patched
   ones low, data never hooked */
static int target_is_open(int id) {
#ifdef T_OPEN_TARGETS
    static const int open[] = {T_OPEN_TARGETS};
    for (size_t i = 0; i < sizeof open / sizeof open[0]; i++)
        if (open[i] == id) return 1;
#endif
    (void)id;
    return 0;
}

static void test_target_meta(void) {
    int planned = 0, hand = 0;
    for (int id = 0; id < T_COUNT; id++) {
        const HlibTargetMeta *m = &g_target_meta[id];
        CHECK(m->kind >= TK_ENTRY && m->kind <= TK_STUB);
        CHECK(m->stability >= TS_HIGH && m->stability <= TS_PLANNED);
        if (m->stability == TS_PLANNED) {
            planned++;
            CHECK(g_targets[id].rva == 0);
        } else if (target_is_open(id)) {
            CHECK(g_targets[id].rva == 0); /* open on purpose: it stays unplaced until someone places it */
        } else {
            CHECK(g_targets[id].rva != 0);
        }
        if (m->kind == TK_SITE || m->kind == TK_BYTES || m->kind == TK_STUB) {
            hand++;
            CHECK(m->stability == TS_LOW);
        }
        if (m->kind == TK_DATA) CHECK(!g_targets[id].is_entry_hook); /* a pinned read-only value may carry bytes */
        if (m->kind == TK_ENTRY && m->stability != TS_PLANNED) CHECK(g_targets[id].is_entry_hook);
    }
    CHECK(planned == 1 && hand >= 10); /* T_META_TICKABLE_ADD; the steps and the bus got their rows 2026-11-14 */
}

/* the step slots' table (vslot_logic.h): two manager classes, each wrapper finding its own class's original */
static long long orig_s0(void *o) { (void)o; return 10; }
static long long orig_p0(void *o) { (void)o; return 11; }
static long long orig_s1(void *o) { (void)o; return 20; }
static void test_vslots(void) {
    static void *vt_a[4], *vt_b[4];
    void *obj_a = vt_a, *obj_b = vt_b, *obj_c = vt_b + 1; /* an object is a pointer to its vtable */
    VSlotTable t = {0};
    void *oa[VS_MAX_SLOTS] = {(void *)orig_s0, (void *)orig_p0}, *ob[VS_MAX_SLOTS] = {(void *)orig_s1, NULL};
    CHECK(vs_original(&t, &obj_a, 0) == NULL);
    CHECK(vs_add(&t, (void *const *)vt_a, oa) == 0);
    CHECK(vs_add(&t, (void *const *)vt_a, oa) == -1); /* wrapped once */
    CHECK(vs_add(&t, (void *const *)vt_b, ob) == 1);
    CHECK(((long long (*)(void *))vs_original(&t, &obj_a, 0))(NULL) == 10);
    CHECK(((long long (*)(void *))vs_original(&t, &obj_a, 1))(NULL) == 11);
    CHECK(((long long (*)(void *))vs_original(&t, &obj_b, 0))(NULL) == 20);
    CHECK(vs_original(&t, &obj_b, 1) == NULL && vs_original(&t, &obj_c, 0) == NULL);
    CHECK(vs_original(&t, &obj_a, 2) == NULL && vs_original(&t, NULL, 0) == NULL);
    CHECK(vs_add(&t, NULL, oa) == -1);
    static void *vt_more[VS_MAX_CLASSES + 1][1];
    VSlotTable full = {0};
    for (int i = 0; i < VS_MAX_CLASSES; i++) CHECK(vs_add(&full, (void *const *)vt_more[i], oa) == i);
    CHECK(vs_add(&full, (void *const *)vt_more[VS_MAX_CLASSES], oa) == -1 && full.n == VS_MAX_CLASSES);
}

/* feature classes (features_logic.c): each class's default, the ini over it both ways, a word that is no switch, the
   framework's own switches, an unclassed name off */
static void test_classes(void) {
    CHECK(fc_default(HLIB_GAME) == 1 && fc_default(HLIB_DEV) == 0 && fc_default(HLIB_EXPERIMENTAL) == 0);
    CHECK(fc_default(0) == 0 && !fc_class_ok(0) && !fc_class_ok(4) && fc_class_ok(HLIB_EXPERIMENTAL));
    CHECK(fc_decide(HLIB_GAME, -1) == 1 && fc_decide(HLIB_GAME, hlib_ini_bool("0")) == 0);
    CHECK(fc_decide(HLIB_GAME, hlib_ini_bool("off")) == 0 && fc_decide(HLIB_GAME, hlib_ini_bool("1")) == 1);
    CHECK(fc_decide(HLIB_DEV, -1) == 0 && fc_decide(HLIB_DEV, hlib_ini_bool("yes")) == 1 && fc_decide(HLIB_DEV, 0) == 0);
    CHECK(fc_decide(HLIB_EXPERIMENTAL, -1) == 0 && fc_decide(HLIB_EXPERIMENTAL, hlib_ini_bool("1")) == 1);
    CHECK(fc_decide(HLIB_EXPERIMENTAL, hlib_ini_bool("maybe")) == 0 && fc_decide(HLIB_GAME, hlib_ini_bool("2")) == 1);
    CHECK(fc_switch_class("dormant") == HLIB_GAME && fc_switch_class("Mode_Switch_Button") == HLIB_GAME);
    CHECK(fc_switch_class("safe_mode") == HLIB_DEV && fc_switch_class("trees") == 0 && fc_switch_class(NULL) == 0);
    CHECK(fc_decide(fc_switch_class("unknown_key"), -1) == 0);
    CHECK(!strcmp(fc_class_name(HLIB_GAME), "game") && !strcmp(fc_class_name(HLIB_EXPERIMENTAL), "experimental") &&
          !strcmp(fc_class_name(9), "none"));
}

/* the run-time switch's rules (features_logic.c) and the dispatch's withdrawal by module */
static void test_toggle(void) {
    FcToggle t = {1, 1, HLIB_SIM, 1, 0, 1, 0, 0};
    int noop;
    CHECK(fc_toggle_check(&t, 0, &noop) == NULL && !noop);
    CHECK(fc_toggle_check(&t, 1, &noop) == NULL && noop); /* already on */
    t.humans = 2;
    CHECK(fc_toggle_check(&t, 0, &noop) != NULL); /* a simulation module in a game of several players */
    t.lane = HLIB_PRESENT;
    CHECK(fc_toggle_check(&t, 0, &noop) == NULL && !noop); /* a reader may switch there */
    t.toggles = 0;
    CHECK(fc_toggle_check(&t, 0, &noop) != NULL);
    t.toggles = 1, t.dead = 1;
    CHECK(fc_toggle_check(&t, 0, &noop) != NULL);
    t.dead = 0, t.registered = 0;
    CHECK(fc_toggle_check(&t, 0, &noop) != NULL);
    t.registered = 1, t.safe_mode = 1;
    CHECK(fc_toggle_check(&t, 1, &noop) != NULL);
    t.safe_mode = 0, t.dormant = 1;
    CHECK(fc_toggle_check(&t, 1, &noop) != NULL);

    g_norder = 0;
    hlib_dispatch_module(7);
    CHECK(hlib_subscribe("mod7", HLIB_AT_SESSION_STEP, HLIB_SIM, (void *)tick_1));
    CHECK(hlib_subscribe("mod7", HLIB_AT_POST_SESSION, HLIB_SIM, (void *)tick_2));
    hlib_dispatch_module(-1);
    CHECK(hlib_subscribe("other", HLIB_AT_SESSION_STEP, HLIB_SIM, (void *)tick_2));
    hlib_dispatch(HLIB_AT_SESSION_STEP, run_tick, NULL);
    CHECK(g_norder == 2);
    CHECK(hlib_dispatch_withdraw(7, 1) == 2 && hlib_dispatch_withdraw(-1, 1) == 0);
    hlib_dispatch(HLIB_AT_SESSION_STEP, run_tick, NULL);
    CHECK(g_norder == 3 && g_order[2] == 2); /* the other module still runs */
    int n = hlib_subscribers(HLIB_AT_SESSION_STEP);
    CHECK(hlib_subscribe("mod7", HLIB_AT_SESSION_STEP, HLIB_SIM, (void *)tick_1)); /* given back in place */
    CHECK(hlib_subscribers(HLIB_AT_SESSION_STEP) == n);
    hlib_dispatch(HLIB_AT_SESSION_STEP, run_tick, NULL);
    CHECK(g_norder == 5 && g_order[3] == 1);
    HlibPoint missing = HLIB_AT_COUNT;
    CHECK(!hlib_dispatch_module_ready(7, &missing) && missing == HLIB_AT_SESSION_STEP); /* that source is not live */
    hlib_point_set_live(HLIB_AT_SESSION_STEP);
    CHECK(hlib_dispatch_withdraw(7, 0) == 2 && hlib_dispatch_module_ready(7, &missing)); /* post-session live (ledger) */
}

/* the game's session keys: noted once, the game over when the last is dropped; the shared uint32_t order */
static void test_keys(void) {
    void *keys[2] = {0};
    int a, b, c;
    uint32_t v[3] = {3000000000u, 1, 7};
    hlib_keys_note(keys, 2, &a);
    hlib_keys_note(keys, 2, &a);
    hlib_keys_note(keys, 2, NULL);
    CHECK(keys[0] == &a && !keys[1]);
    hlib_keys_note(keys, 2, &b);
    hlib_keys_note(keys, 2, &c); /* full: not kept */
    CHECK(keys[1] == &b && hlib_keys_drop(keys, 2, &c) == 2);
    CHECK(hlib_keys_drop(keys, 2, &a) == 1 && !keys[0] && hlib_keys_drop(keys, 2, &b) == 0);
    qsort(v, 3, sizeof(v[0]), hlib_cmp_u32);
    CHECK(v[0] == 1 && v[1] == 7 && v[2] == 3000000000u);
}

/* the multiplayer refusals' human count and the asset reload's pending state (engine.c's pure rules) */
static void test_link_guards(void) {
    CHECK(hlib_human_tally(0, 1, 0) == 1 && hlib_human_tally(0, 0, 0) == 0); /* single player, no game */
    CHECK(hlib_human_tally(0, 0, 7) == 7); /* every participant unread (an asset reload holds): not single player */
    CHECK(hlib_human_tally(0, 1, 1) == 2 && hlib_human_tally(0, 2, 0) == 2);
    CHECK(hlib_human_tally(1, 0, 0) == 2); /* the list is there but cannot be read */
    CHECK(!hlib_reload_holds(0, 0, 1000, 1000, 90000) && !hlib_reload_holds(0, 1, 1000, 1000, 90000));
    CHECK(hlib_reload_holds(1, 0, 1000, 1000 + 89999, 90000));
    CHECK(!hlib_reload_holds(1, 0, 1000, 1000 + 90000, 90000)); /* no start line: lapses after the limit */
    CHECK(hlib_reload_holds(1, 1, 1000, 1000 + 900000, 90000));  /* the engine began: only its end line ends it */
    CHECK(hlib_reload_holds(1, 0, 5000, 1000, 90000));           /* a clock read before the begin: still held */
}

int main(void) {
    test_layers();
    test_link_guards();
    test_keys();
    test_dispatch();
    test_ledger();
    test_classes();
    test_toggle();
    test_target_meta();
    test_vslots();
    if (g_fail) {
        printf("framework_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("framework_test: all checks passed\n");
    return 0;
}

/* Host test of the live reload's pure part (reload_logic.c): the path rules, the loader's list, the queue, the file
   event record, the scan and its changes with includes, the trigger file and the decision. No game needed. */
#include "reload_logic.h"

#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                    \
    do {                                                            \
        if (!(c)) {                                                 \
            printf("reload_test: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            g_fail++;                                               \
        }                                                           \
    } while (0)

static void test_paths(void) {
    char p[RL_PATH_MAX];
    CHECK(rl_norm_path("Data\\Graphics\\Buildings\\Ark_LOD1.rdm", p, sizeof p) && !strcmp(p, "data/graphics/buildings/ark_lod1.rdm"));
    CHECK(rl_norm_path("./data/x.cfg  \r\n", p, sizeof p) && !strcmp(p, "data/x.cfg"));
    CHECK(rl_norm_path("/data/x.fx", p, sizeof p) && !strcmp(p, "data/x.fx"));
    CHECK(!rl_norm_path("data/../../evil.rdm", p, sizeof p));
    CHECK(!rl_norm_path("..", p, sizeof p));
    CHECK(!rl_norm_path("C:/x.rdm", p, sizeof p)); /* a drive is no relative path */
    CHECK(!rl_norm_path("data//x.rdm", p, sizeof p));
    CHECK(!rl_norm_path("data/x y.rdm", p, sizeof p));
    CHECK(!rl_norm_path("data/", p, sizeof p));
    CHECK(!rl_norm_path("", p, sizeof p));
    CHECK(rl_norm_path("data/a..b.rdm", p, sizeof p)); /* dots inside a name are fine */
    CHECK(!rl_norm_path("data/averyveryverylongname.rdm", p, 10));
    CHECK(rl_kind_of("a/b.RDM") == RL_MODEL && rl_kind_of("a.cfg") == RL_CONFIG && rl_kind_of("a.fx") == RL_EFFECT);
    CHECK(rl_kind_of("a.fxh") == RL_INCLUDE && rl_kind_of("a.fxs") == RL_INCLUDE && rl_kind_of("a.dds") == RL_NONE);
    CHECK(rl_kind_of("a.cfg.xml") == RL_NONE);
    rl_model_key("data/x/ark_lod2.rdm", p, sizeof p);
    CHECK(!strcmp(p, "data/x/ark_lod0.rdm"));
    rl_model_key("data/x/ark.rdm", p, sizeof p);
    CHECK(!strcmp(p, "data/x/ark.rdm"));
    rl_model_key("data/lod_lod4.rdm", p, sizeof p);
    CHECK(!strcmp(p, "data/lod_lod0.rdm"));
}

static void test_mods(void) {
    const char *log = "[2026-11-04 16:36:58.932] [info] Search mods...\n"
                      "[2026-11-04 16:36:58.932] [info] Enabled mod: C:/Users/x/Documents/Anno 117 - Pax Romana/mods/mymod  (mymod@0.1.0)\r\n"
                      "[2026-11-04 16:36:58.933] [info] Enabled mod: C:\\m\\other/  (other@1.0.0)\n"
                      "[2026-11-04 16:36:58.934] [info] Enabled mods: 2 (plus 0 nested)\n";
    RLMods m;
    char rel[RL_PATH_MAX];
    CHECK(rl_parse_loader_log(log, &m) == 2 && m.complete);
    CHECK(!strcmp(m.mod[0].id, "mymod") && !strcmp(m.mod[0].folder, "C:/Users/x/Documents/Anno 117 - Pax Romana/mods/mymod"));
    CHECK(!strcmp(m.mod[1].id, "other") && !strcmp(m.mod[1].folder, "C:/m/other"));
    CHECK(rl_mod_by_id(&m, "other") == 1 && rl_mod_by_id(&m, "nope") == -1);
    CHECK(rl_mod_of_path(&m, "c:\\users\\X\\documents\\anno 117 - pax romana\\mods\\mymod\\Data\\a.rdm", rel, sizeof rel) == 0);
    CHECK(!strcmp(rel, "data/a.rdm"));
    CHECK(rl_mod_of_path(&m, "C:/m/otherx/data/a.rdm", rel, sizeof rel) == -1); /* a folder that only starts alike */
    CHECK(rl_mod_of_path(&m, "C:/m/other/../mymod/a.rdm", rel, sizeof rel) == -1);
    CHECK(rl_mod_of_path(&m, "D:/elsewhere/a.rdm", rel, sizeof rel) == -1);
    RLMods none;
    CHECK(rl_parse_loader_log("[info] Search mods...\n", &none) == 0 && !none.complete);
}

static void test_queue(void) {
    static RLQueue q;
    RLRequest b[4];
    memset(&q, 0, sizeof q);
    CHECK(rl_queue_push(&q, RL_MODEL, 0, "a.rdm", 1) == 1);
    CHECK(rl_queue_push(&q, RL_MODEL, 0, "a.rdm", 2) == 0); /* already queued */
    CHECK(rl_queue_push(&q, RL_MODEL, 1, "a.rdm", 2) == 1); /* another mod's file */
    CHECK(rl_queue_front(&q) && !strcmp(rl_queue_front(&q)->path, "a.rdm") && rl_queue_front(&q)->batch == 1);
    rl_queue_pop(&q);
    rl_queue_pop(&q);
    CHECK(!rl_queue_front(&q));
    rl_queue_pop(&q); /* empty: nothing */
    memset(b, 0, sizeof b);
    b[0].kind = RL_EFFECT, snprintf(b[0].path, RL_PATH_MAX, "%s", "e.fx");
    b[1].kind = RL_CONFIG, snprintf(b[1].path, RL_PATH_MAX, "%s", "c.cfg");
    b[2].kind = RL_MODEL, snprintf(b[2].path, RL_PATH_MAX, "%s", "m.rdm");
    b[3].kind = RL_INCLUDE, snprintf(b[3].path, RL_PATH_MAX, "%s", "i.fxh"); /* never queued itself */
    CHECK(rl_queue_batch(&q, b, 4, 7) == 3);
    CHECK(rl_queue_front(&q)->kind == RL_MODEL);
    rl_queue_pop(&q);
    CHECK(rl_queue_front(&q)->kind == RL_CONFIG);
    rl_queue_pop(&q);
    CHECK(rl_queue_front(&q)->kind == RL_EFFECT && rl_queue_front(&q)->batch == 7);
    rl_queue_pop(&q);
    int full = 0;
    for (int i = 0; i < RL_QUEUE_MAX + 3; i++) {
        char p[32];
        snprintf(p, sizeof p, "m%d.rdm", i);
        if (rl_queue_push(&q, RL_MODEL, 0, p, 1) < 0) full++;
    }
    CHECK(full == 3 && q.n == RL_QUEUE_MAX);
    for (int i = 0; i < 10; i++) rl_queue_pop(&q); /* the ring wraps */
    CHECK(rl_queue_push(&q, RL_MODEL, 0, "late.rdm", 1) == 1 && q.n == RL_QUEUE_MAX - 9);
}

static void test_event(void) {
    /* the layout of 3.0 as build117.h says: 0x70 bytes, the path at +0x48 (size +0x10, capacity +0x18 of the string),
       seven characters inline, the kind at +0x68 */
    const RLEventLayout l = {0x70, 0x48, 0x10, 0x18, 7, 0x68, 1};
    unsigned char rec[0x70];
    uint16_t w[RL_PATH_MAX], heap[RL_PATH_MAX];
    uint64_t v;
    uint16_t *ptr;
    int len = rl_widen("a.fx", w, RL_PATH_MAX);
    memset(rec, 0xcc, sizeof rec);
    CHECK(len == 4 && rl_event_build(rec, &l, w, len, NULL));
    CHECK(!memcmp(rec + 0x48, w, 10)); /* inline, terminated */
    memcpy(&v, rec + 0x58, 8);
    CHECK(v == 4);
    memcpy(&v, rec + 0x60, 8);
    CHECK(v == 7);
    CHECK(rec[0x68] == 1 && rec[0x69] == 0 && rec[0] == 0 && rec[0x47] == 0);
    len = rl_widen("data/graphics/buildings/ark_lod0.rdm", w, RL_PATH_MAX);
    CHECK(!rl_event_build(rec, &l, w, len, NULL)); /* too long for inline, no heap */
    CHECK(rl_event_build(rec, &l, w, len, heap));
    memcpy(&ptr, rec + 0x48, sizeof ptr);
    CHECK(ptr == heap && !memcmp(heap, w, (size_t)(len + 1) * 2));
    memcpy(&v, rec + 0x58, 8);
    CHECK(v == (uint64_t)len);
    memcpy(&v, rec + 0x60, 8);
    CHECK(v == (uint64_t)len);
    CHECK(rl_widen("caf\xe9", w, RL_PATH_MAX) < 0);
    const RLEventLayout bad = {0x40, 0x48, 0x10, 0x18, 7, 0x68, 1};
    CHECK(!rl_event_build(rec, &bad, w, len, heap));
}

static int fake_includes(int mod, const char *fx, const char *fxh, void *ctx) {
    (void)mod, (void)ctx;
    return !strcmp(fx, "data/shaders/fx/water.fx") && !strcmp(fxh, "data/shaders/fx/common.fxh");
}

static void test_scan(void) {
    RLSnap a, b;
    RLRequest out[16];
    rl_snap_init(&a);
    rl_snap_init(&b);
    CHECK(rl_snap_add(&a, 0, "data/m.rdm", 10) == 1);
    CHECK(rl_snap_add(&a, 0, "data/m.cfg", 10) == 1);
    CHECK(rl_snap_add(&a, 0, "data/t.dds", 10) == 0); /* not reloadable */
    CHECK(rl_snap_add(&a, 0, "data/shaders/fx/water.fx", 10) == 1);
    CHECK(rl_snap_add(&a, 0, "data/shaders/fx/land.fx", 10) == 1);
    CHECK(rl_snap_add(&a, 0, "data/shaders/fx/common.fxh", 10) == 1);
    CHECK(rl_snap_add(&a, 1, "data/m.rdm", 10) == 1);
    CHECK(rl_snap_changes(NULL, &a, out, 16, fake_includes, NULL) == 5); /* no last scan: everything is new */
    rl_snap_sort(&a);
    CHECK(rl_snap_changes(&a, &a, out, 16, fake_includes, NULL) == 0);
    rl_snap_add(&b, 1, "data/m.rdm", 10);
    rl_snap_add(&b, 0, "data/shaders/fx/common.fxh", 11); /* the include changed */
    rl_snap_add(&b, 0, "data/shaders/fx/land.fx", 10);
    rl_snap_add(&b, 0, "data/shaders/fx/water.fx", 10);
    rl_snap_add(&b, 0, "data/m.cfg", 12); /* changed */
    rl_snap_add(&b, 0, "data/new.rdm", 1); /* new; data/m.rdm deleted: no change */
    int n = rl_snap_changes(&a, &b, out, 16, fake_includes, NULL), cfg = 0, fx = 0, rdm = 0;
    for (int i = 0; i < n; i++) {
        cfg += out[i].kind == RL_CONFIG && !strcmp(out[i].path, "data/m.cfg");
        fx += out[i].kind == RL_EFFECT && !strcmp(out[i].path, "data/shaders/fx/water.fx");
        rdm += out[i].kind == RL_MODEL && !strcmp(out[i].path, "data/new.rdm");
    }
    CHECK(n == 3 && cfg == 1 && fx == 1 && rdm == 1);
    CHECK(rl_snap_changes(&a, &b, out, 1, fake_includes, NULL) == 1); /* capped */
    rl_snap_move(&a, &b);
    CHECK(a.n == 6 && b.n == 0 && a.sorted);
    CHECK(rl_snap_changes(&a, &a, out, 16, fake_includes, NULL) == 0);
    rl_snap_free(&a);
    rl_snap_free(&b);
}

static void test_includes(void) {
    const char *fx = "// water\n#include \"shaders/fx/Common.fxh\"\n#include <lighting.fxh>\nfloat4 main() {}\n";
    CHECK(rl_fx_includes(fx, strlen(fx), "data/shaders/fx/common.fxh"));
    CHECK(rl_fx_includes(fx, strlen(fx), "data/shaders/lighting.fxh"));
    CHECK(!rl_fx_includes(fx, strlen(fx), "data/shaders/fx/mmon.fxh")); /* the whole file name, not its end */
    CHECK(!rl_fx_includes(fx, strlen(fx), "data/shaders/fx/other.fxh"));
    const char *bare = "#include \"common.fxh\"";
    CHECK(rl_fx_includes(bare, strlen(bare), "x/common.fxh"));
    CHECK(!rl_fx_includes("#include", 8, "x/common.fxh"));
}

typedef struct {
    int n;
    char id[4][64], path[4][RL_PATH_MAX];
} Lines;

static void take_line(const char *id, const char *path, void *ctx) {
    Lines *l = (Lines *)ctx;
    if (l->n < 4) snprintf(l->id[l->n], 64, "%s", id), snprintf(l->path[l->n], RL_PATH_MAX, "%s", path);
    l->n++;
}

static void test_trigger(void) {
    Lines l;
    memset(&l, 0, sizeof l);
    CHECK(rl_trigger_lines("", take_line, &l) == 0);
    CHECK(rl_trigger_lines("# just a comment\r\n\r\n   \n", take_line, &l) == 0);
    CHECK(rl_trigger_lines("data/a.rdm\r\nmymod:data/b.cfg  # the config\nC:/m/x/data/c.fx\n", take_line, &l) == 3);
    CHECK(!strcmp(l.id[0], "") && !strcmp(l.path[0], "data/a.rdm"));
    CHECK(!strcmp(l.id[1], "mymod") && !strcmp(l.path[1], "data/b.cfg"));
    CHECK(!strcmp(l.id[2], "") && !strcmp(l.path[2], "C:/m/x/data/c.fx")); /* a drive letter is no mod id */
}

static void test_decide(void) {
    RLState s = {0, 0, 1, 1, 0, 0, 5};
    CHECK(rl_decide(&s) == RL_RUN);
    s.streaming = 1;
    CHECK(rl_decide(&s) == RL_WAIT);
    s.tries = 5;
    CHECK(rl_decide(&s) == RL_REFUSE_TIMEOUT);
    s.streaming = 0, s.tries = 0, s.found = 0;
    CHECK(rl_decide(&s) == RL_SKIP_NOT_LOADED);
    s.streaming = 1; /* not in the cache: its streaming state means nothing */
    CHECK(rl_decide(&s) == RL_SKIP_NOT_LOADED);
    s.loading = 1;
    CHECK(rl_decide(&s) == RL_WAIT);
    s.file_ok = 0;
    CHECK(rl_decide(&s) == RL_REFUSE_FILE);
    s.multiplayer = 1;
    CHECK(rl_decide(&s) == RL_REFUSE_MULTIPLAYER);
    CHECK(strcmp(rl_decision_name(RL_REFUSE_FILE), "?") != 0);
    s.busy = 1, s.tries = 0; /* the link's guards: it waits before any other test (the player count is not sure then) */
    CHECK(rl_decide(&s) == RL_WAIT);
    s.tries = 5;
    CHECK(rl_decide(&s) == RL_REFUSE_TIMEOUT);
}

static int has_b(int mod, const char *rel, void *ctx) { /* the second mod holds every relative file asked for */
    (void)ctx;
    return mod == 1 && !strncmp(rel, "data/", 5);
}

/* a mixed request is one batch: what is queued and what is refused carry the same number */
static void test_plan(void) {
    RLMods m = {0};
    RLRequest items[8];
    RLRefused refused[8];
    int nr = -1, k;
    const char *paths[] = {"C:/m/one/Data/a.rdm", "data/b.fx", "data/c.fxh", "D:/x/a.rdm", "data/d.dds", "data/../e.rdm"};
    snprintf(m.mod[0].id, sizeof m.mod[0].id, "one"), snprintf(m.mod[0].folder, sizeof m.mod[0].folder, "C:/m/one");
    snprintf(m.mod[1].id, sizeof m.mod[1].id, "two"), snprintf(m.mod[1].folder, sizeof m.mod[1].folder, "C:/m/two");
    m.n = 2, m.complete = 1;
    k = rl_plan_batch(&m, NULL, paths, 6, 41, has_b, NULL, items, refused, 8, &nr);
    CHECK(k == 2 && nr == 4);
    CHECK(items[0].kind == RL_MODEL && items[0].mod == 0 && !strcmp(items[0].path, "data/a.rdm") && items[0].batch == 41);
    CHECK(items[1].kind == RL_EFFECT && items[1].mod == 1 && items[1].batch == 41);
    for (int i = 0; i < nr; i++) CHECK(refused[i].batch == 41 && refused[i].why);
    CHECK(!strcmp(refused[0].path, "data/c.fxh") && strstr(refused[0].why, "include"));
    CHECK(strstr(refused[1].why, "enabled mod") && strstr(refused[2].why, "not a model"));
    k = rl_plan_batch(&m, "two", paths + 4, 1, 42, has_b, NULL, items, refused, 8, &nr); /* named mod: no search */
    CHECK(k == 0 && nr == 1 && refused[0].batch == 42);
    k = rl_plan_batch(&m, "nope", paths + 1, 1, 43, has_b, NULL, items, refused, 8, &nr);
    CHECK(k == 0 && nr == 1 && strstr(refused[0].why, "enabled mod"));
}

int main(void) {
    test_paths();
    test_mods();
    test_queue();
    test_event();
    test_scan();
    test_includes();
    test_trigger();
    test_decide();
    test_plan();
    if (g_fail) {
        printf("reload_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("reload_test: all checks passed\n");
    return 0;
}

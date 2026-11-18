/* Host test of the live widget values (livevalues_logic.c): a synthetic scene with a widget file placed twice and a
   file nested in it; which widgets each request answers, what of each, the size cap and the GUID text. No game. */
#define _CRT_SECURE_NO_WARNINGS
#include "livevalues_logic.h"
#include "widgetread_logic.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                          \
    do {                                                                  \
        if (!(c)) {                                                       \
            printf("livevalues_test: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            g_fail++;                                                     \
        }                                                                 \
    } while (0)

typedef struct Node {
    LvIdentity id;
    LvShown shown;
    const struct Node *kids[160];
    int nkids, unreadable, shown_reads;
} Node;

static int identify(void *ctx, const void *w, LvIdentity *out) {
    const Node *n = (const Node *)w;
    (void)ctx;
    if (n->unreadable) return 0;
    *out = n->id;
    return 1;
}

static void shown(void *ctx, const void *w, const LvIdentity *id, LvShown *out) {
    Node *n = (Node *)w;
    (void)ctx, (void)id;
    n->shown_reads++;
    *out = n->shown;
}

static int children(void *ctx, const void *w, const void **out, int cap, int *cut) {
    const Node *n = (const Node *)w;
    (void)ctx;
    int k = n->nkids < cap ? n->nkids : cap;
    if (n->nkids > cap) *cut = 1;
    for (int i = 0; i < k; i++) out[i] = n->kids[i];
    return k;
}

static const LvReader R = {identify, shown, children, NULL};

static void guid_of(uint8_t g[16], int seed) {
    for (int i = 0; i < 16; i++) g[i] = (uint8_t)(seed * 16 + i);
}

static void ident(Node *n, int seed, const char *name, int tmpl_seed) {
    memset(n, 0, sizeof *n);
    guid_of(n->id.guid, seed), n->id.has_guid = 1;
    n->id.name_crc = 0x1000u + (uint32_t)seed, n->id.has_crc = 1;
    if (name) snprintf(n->id.name, sizeof n->id.name, "%s", name), n->id.has_name = 1;
    if (tmpl_seed) guid_of(n->id.tmpl, tmpl_seed), n->id.has_tmpl = 1;
    n->id.visible = 1;
}

static void text(Node *n, const char *t) { snprintf(n->shown.text, LV_TEXT, "%s", t), n->shown.has_text = 1; }

static void value(Node *n, uint32_t uid, int kind, double v, const char *t) {
    LvValue *x = &n->shown.values[n->shown.nvalues++];
    x->uid = uid, x->kind = kind, x->num = v;
    if (t) snprintf(x->text, LV_TEXT, "%s", t);
}

/* scene (tmpl 1): Title, C (places file 2: D, E, N places file 3: Z), C2 (file 2 again: D2) */
static Node scene, title, c, d, e, nested, z, c2, d2;
enum { FILE_SCENE = 1, FILE_W = 2, FILE_X = 3 };

static void build(void) {
    ident(&scene, 1, "scene", FILE_SCENE);
    ident(&title, 2, "Title", 0), text(&title, "Hello \"game\"");
    ident(&c, 3, "Panel", FILE_W), value(&c, 7, LV_NUMBER, 2.5, NULL);
    ident(&d, 4, "Amount", 0), text(&d, "1,234 t");
    ident(&e, 5, "Icon", 0), snprintf(e.shown.image, LV_TEXT, "icon_wood"), e.shown.has_image = 1, e.id.visible = 0;
    ident(&nested, 6, NULL, FILE_X), value(&nested, 9, LV_BOOL, 1, NULL), value(&nested, 10, LV_STRING, 0, "x");
    ident(&z, 7, "Deep", 0), text(&z, "not mine");
    ident(&c2, 8, "Panel2", FILE_W);
    ident(&d2, 9, "Amount", 0), text(&d2, "second instance");
    scene.kids[0] = &title, scene.kids[1] = &c, scene.kids[2] = &c2, scene.nkids = 3;
    c.kids[0] = &d, c.kids[1] = &e, c.kids[2] = &nested, c.nkids = 3;
    nested.kids[0] = &z, nested.nkids = 1;
    c2.kids[0] = &d2, c2.nkids = 1;
}

static char g_mem[LV_DATA_MAX];

static const char *ask(const uint8_t *file, LvStats *st, int *found) {
    LvBuf b;
    lv_buf_init(&b, g_mem, sizeof g_mem);
    lv_open(&b, file);
    *found = lv_scene(&b, "HUD", file, &R, &scene, st);
    lv_close(&b);
    return g_mem;
}

static void test_the_scene_answers_its_own_widgets(void) {
    LvStats st;
    int found;
    const char *j = ask(NULL, &st, &found);
    CHECK(found && st.answered == 3 && !st.cut); /* Title and both instances of the widget file */
    CHECK(strstr(j, "{\"file\":null,\"scenes\":[{\"state\":\"HUD\",\"widgets\":[{\"guid\":") == j);
    CHECK(strstr(j, "\"name\":\"Title\",\"visible\":true,\"text\":\"Hello \\\"game\\\"\"}"));
    CHECK(strstr(j, "\"name\":\"Panel\",\"visible\":true,\"values\":{\"7\":2.5}}"));
    CHECK(!strstr(j, "Amount") && !strstr(j, "Deep")); /* the placed file's widgets are its own */
    CHECK(strstr(j, "],\"cut\":false}],\"cut\":false}") != NULL);
}

static void test_a_widget_file_answers_its_first_instance(void) {
    LvStats st;
    uint8_t file[16];
    int found;
    guid_of(file, FILE_W);
    d.shown_reads = d2.shown_reads = title.shown_reads = 0;
    const char *j = ask(file, &st, &found);
    CHECK(found && st.answered == 3);
    CHECK(strstr(j, "\"name\":\"Amount\",\"visible\":true,\"text\":\"1,234 t\"}"));
    CHECK(strstr(j, "\"name\":\"Icon\",\"visible\":false,\"image\":\"icon_wood\"}"));
    CHECK(strstr(j, "\"values\":{\"9\":true,\"10\":\"x\"}}")); /* the nested file's instance with its values */
    CHECK(!strstr(j, "Deep") && !strstr(j, "second instance") && !strstr(j, "Title"));
    CHECK(d.shown_reads == 1 && d2.shown_reads == 0 && title.shown_reads == 0); /* only answered widgets are read */
    char t[37];
    wt_guid_text(file, t);
    CHECK(strstr(j, t) == j + 9); /* {"file":"<guid>" */
}

static void test_a_file_not_in_the_scene_answers_nothing(void) {
    LvStats st;
    uint8_t file[16];
    int found;
    guid_of(file, 12);
    const char *j = ask(file, &st, &found);
    CHECK(!found && st.answered == 0);
    CHECK(strstr(j, "\"scenes\":[],\"cut\":false}") != NULL);
}

static void test_an_unreadable_widget_is_left_out_with_what_it_holds(void) {
    LvStats st;
    int found;
    c.unreadable = 1;
    const char *j = ask(NULL, &st, &found);
    CHECK(found && st.answered == 2 && strstr(j, "Title") && strstr(j, "Panel2") && !strstr(j, "Panel\""));
    CHECK(!strstr(j, "Amount\"")); /* what it holds is not walked */
    c.unreadable = 0;
}

static void test_the_cap_keeps_the_answer_whole(void) {
    static char small[300];
    LvBuf b;
    LvStats st;
    uint8_t file[16];
    guid_of(file, FILE_W);
    lv_buf_init(&b, small, sizeof small);
    lv_open(&b, file);
    lv_scene(&b, "HUD", file, &R, &scene, &st);
    lv_close(&b);
    CHECK(b.cut && st.cut && st.answered >= 1 && st.answered < 3);
    CHECK(b.n < sizeof small && small[b.n - 1] == '}' && strstr(small, "],\"cut\":true}],\"cut\":true}"));
    lv_buf_init(&b, small, 100); /* not even a scene's head: the answer says cut, with no scene */
    lv_open(&b, NULL);
    CHECK(!lv_scene(&b, "a-very-long-state-name-that-does-not-fit-in-the-small-buffer", NULL, &R, &scene, &st));
    lv_close(&b);
    CHECK(!strcmp(small, "{\"file\":null,\"scenes\":[],\"cut\":true}"));
}

/* The shapes of the largest files the game's 136 dumped scenes place (counts and lengths only, the texts made up),
   joined in one file: 146 labels with texts of 159 characters, 40 of them holding 42 property values each, image ids
   as backslash paths and as run-time ids. The answer is whole under the cap. */
static void test_the_largest_dumped_files_fit(void) {
    enum { LABELS = 146, HOLDERS = 40 };
    static Node root, kids[LABELS];
    LvBuf b;
    LvStats st;
    ident(&root, 20, "root", FILE_SCENE);
    for (int i = 0; i < LABELS; i++) {
        ident(&kids[i], 30 + i, "Label", i < HOLDERS ? FILE_W : 0);
        memset(kids[i].shown.text, 'x', 159), kids[i].shown.text[159] = 0, kids[i].shown.has_text = 1;
        for (int k = 0; i < HOLDERS && k < 42; k++) value(&kids[i], 1000u + (uint32_t)k, k % 2 ? LV_BOOL : LV_NUMBER, k, NULL);
        snprintf(kids[i].shown.image, LV_TEXT, i % 3 ? "data\\ui\\fhd\\main\\icons\\icon_%03d.png" : "rtt_%d", 166000 + i);
        kids[i].shown.has_image = 1;
        root.kids[i] = &kids[i];
    }
    root.nkids = LABELS;
    lv_buf_init(&b, g_mem, sizeof g_mem);
    lv_open(&b, NULL);
    CHECK(lv_scene(&b, "Probe", NULL, &R, &root, &st) && st.answered == LABELS && !st.cut);
    lv_close(&b);
    CHECK(!b.cut && b.n < LV_DATA_MAX && strstr(g_mem, "\"image\":\"data\\\\ui\\\\fhd\\\\main") && strstr(g_mem, "\"rtt_166"));
    CHECK(strstr(g_mem, "\"values\":{\"1000\":0,\"1001\":true,") && strstr(g_mem, "\"1041\":true}"));
}

static void test_texts_are_escaped_and_kept_utf8(void) {
    static char mem[200];
    LvBuf b;
    lv_buf_init(&b, mem, sizeof mem);
    lv_str(&b, "a\nb\x01 \xc3\xa9 \xff");
    CHECK(!strcmp(mem, "\"a\\u000ab\\u0001 \xc3\xa9 \\ufffd\""));
}

static void test_guid_text_round_trip(void) {
    uint8_t g[16], back[16];
    char t[37];
    guid_of(g, 5);
    wt_guid_text(g, t);
    CHECK(lv_guid_parse(t, back) && !memcmp(g, back, 16));
    CHECK(!strcmp(t, "53525150-5554-5756-5859-5a5b5c5d5e5f"));
    CHECK(!lv_guid_parse("53525150-5554-5756-5859-5a5b5c5d5e5", back));
    CHECK(!lv_guid_parse("53525150x5554-5756-5859-5a5b5c5d5e5f", back));
    CHECK(!lv_guid_parse("g3525150-5554-5756-5859-5a5b5c5d5e5f", back));
}

static LsOpen open_state(int id, int depth, const char *name, const char *parent) {
    LsOpen o;
    memset(&o, 0, sizeof o);
    o.id = id, o.depth = depth;
    snprintf(o.name, sizeof o.name, "%s", name);
    snprintf(o.parent, sizeof o.parent, "%s", parent);
    return o;
}

static void test_scenes_open_parents_first_then_later_on_top(void) {
    static LsSeen s;
    LsChange c[8];
    LsOpen now[3] = {open_state(0x0a, 5, "ConstructionMenu", "HUD"), open_state(0x06, 2, "Blur", "Game"),
                     open_state(0x33, 5, "HUDGovernorsBar", "HUD")};
    ls_reset(&s);
    CHECK(ls_diff(&s, now, 2, c, 8) == 2); /* the first look: every open scene, by depth, then id */
    CHECK(c[0].opened && !strcmp(c[0].st.name, "Blur") && c[0].order == 1);
    CHECK(c[1].opened && !strcmp(c[1].st.name, "ConstructionMenu") && c[1].order == 2);
    CHECK(ls_diff(&s, now, 2, c, 8) == 0); /* nothing changed */
    CHECK(ls_diff(&s, now, 3, c, 8) == 1 && c[0].opened && c[0].order == 3); /* opened later: a higher order */
    CHECK(ls_diff(&s, now + 1, 2, c, 8) == 1 && !c[0].opened && c[0].order == 2 &&
          !strcmp(c[0].st.name, "ConstructionMenu")); /* closed: its opening's number */
    CHECK(ls_diff(&s, now, 3, c, 8) == 1 && c[0].opened && c[0].order == 4); /* opened again: on top */
    ls_reset(&s);
    CHECK(ls_diff(&s, now, 3, c, 8) == 3 && c[2].order == 3); /* a new subscription is told everything again */
}

static void test_closings_come_first_and_the_cap_holds_the_rest(void) {
    static LsSeen s;
    LsChange c[8];
    LsOpen a[2] = {open_state(1, 3, "Diplomacy", "MetaGame"), open_state(2, 3, "Statistics", "MetaGame")};
    LsOpen b[2] = {open_state(3, 4, "GenericPopup", "Game"), open_state(4, 4, "Notes", "Game")};
    ls_reset(&s);
    ls_diff(&s, a, 2, c, 8);
    CHECK(ls_diff(&s, b, 2, c, 3) == 3); /* two closings, newest first, then one opening */
    CHECK(!c[0].opened && !strcmp(c[0].st.name, "Statistics") && !c[1].opened && !strcmp(c[1].st.name, "Diplomacy"));
    CHECK(c[2].opened && !strcmp(c[2].st.name, "GenericPopup"));
    CHECK(ls_diff(&s, b, 2, c, 3) == 1 && c[0].opened && !strcmp(c[0].st.name, "Notes")); /* the rest, next look */
}

static void test_odd_names_are_not_reported_and_the_event_text(void) {
    static LsSeen s;
    LsChange c[4];
    char json[256], scene[80];
    LsOpen now[2] = {open_state(5, 1, "Bad\"Name", "Game"), open_state(7, 2, "HUD", "GameSession")};
    ls_reset(&s);
    CHECK(ls_diff(&s, now, 2, c, 4) == 1 && c[0].st.id == 7);
    CHECK(ls_scene_name("ConstructionMenu", scene, sizeof scene) && !strcmp(scene, "constructionmenuscene"));
    CHECK(!ls_scene_name("", scene, sizeof scene) && !ls_scene_name("a b", scene, sizeof scene));
    CHECK(ls_event_json(json, sizeof json, &c[0], 2560, 1440) > 0);
    CHECK(!strcmp(json, "{\"change\":\"opened\",\"state\":\"HUD\",\"scene\":\"hudscene\",\"parent\":\"GameSession\","
                        "\"depth\":2,\"order\":1,\"window\":[2560,1440]}"));
    CHECK(ls_diff(&s, now, 0, c, 4) == 1 && ls_event_json(json, sizeof json, &c[0], 0, 0) > 0);
    CHECK(!strcmp(json, "{\"change\":\"closed\",\"state\":\"HUD\",\"scene\":\"hudscene\",\"order\":1}"));
    CHECK(ls_event_json(json, 20, &c[0], 0, 0) == -1);
}

int main(void) {
    build();
    test_scenes_open_parents_first_then_later_on_top();
    test_closings_come_first_and_the_cap_holds_the_rest();
    test_odd_names_are_not_reported_and_the_event_text();
    test_the_scene_answers_its_own_widgets();
    test_a_widget_file_answers_its_first_instance();
    test_a_file_not_in_the_scene_answers_nothing();
    test_an_unreadable_widget_is_left_out_with_what_it_holds();
    test_the_cap_keeps_the_answer_whole();
    test_the_largest_dumped_files_fit();
    test_texts_are_escaped_and_kept_utf8();
    test_guid_text_round_trip();
    printf(g_fail ? "livevalues_test: %d failure(s)\n" : "livevalues_test: ok\n", g_fail);
    return g_fail ? 1 : 0;
}

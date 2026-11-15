/* Host test of the UI dump probe's pure part (uidump_logic.c): a synthetic scene of nine widgets (a scene root scaled
   to the window, a placed widget file, a label, a hidden picture, an intersect mask that is hidden and the picture
   it covers, a group, a widget reached twice, a NaN), walked through a reader as the engine side reads the live tree;
   then the JSON's text, the screen rectangles, what each widget inherits, the truncation, the helpers. */
#include "uidump_logic.h"
#include <math.h>
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                        \
    do {                                                                \
        if (!(c)) {                                                     \
            printf("uidump_test: line %d failed: %s\n", __LINE__, #c); \
            g_fail++;                                                   \
        }                                                               \
    } while (0)

typedef struct {
    const char *name, *cls;
    int kids[6], nkids, tmpl, guid, flags, mask_owner, mask_mode;
    float sx, sy, tx, ty, w, h, alpha;
} Fake;

enum { ROOT, PANEL, LABEL, HIDDEN, MASK, COVERED, GROUP, IN_GROUP, NAN_ONE, NFAKE };
enum { F_VISIBLE = 1, F_RECEIVES = 2, F_DRAWABLE = 4, INTERSECT = 7 };
#define SHOWN (F_VISIBLE | F_DRAWABLE | F_RECEIVES)

static Fake g_tree[NFAKE] = {
    [ROOT] = {"root", "CompositeWidget", {PANEL, GROUP}, 2, 1, 1, SHOWN, -1, 0, 0.5f, 0.5f, 10, 20, 1920, 1080, 1},
    [PANEL] = {"Panel", "CompositeWidget", {LABEL, HIDDEN, MASK, COVERED, LABEL}, 5, 2, 2, SHOWN, -1, 0, 0.5f, 0.5f, 110, 120, 400, 300, 0.5f},
    [LABEL] = {"Title \"one\"\n", "LabelWidget", {0}, 0, 0, 3, SHOWN, -1, 0, 1, 1, 120, 130, 200, 40, 1},
    [HIDDEN] = {"Hidden", "ImageWidget", {0}, 0, 0, 4, F_RECEIVES, -1, 0, 1, 1, 0, 0, 10, 10, 1},
    [MASK] = {"Mask", "ImageWidget", {0}, 0, 0, 5, F_VISIBLE, -1, INTERSECT, 1, 1, 0, 0, 10, 10, 1},
    [COVERED] = {"Covered", "ImageWidget", {0}, 0, 0, 6, SHOWN, MASK, 0, 1, 1, 0, 0, 10, 10, 1},
    [GROUP] = {"Group", "Widget", {IN_GROUP, NAN_ONE}, 2, 0, 7, SHOWN, -1, 0, 1, 1, 0, 0, 0, 0, 1},
    [IN_GROUP] = {"InGroup", "ImageWidget", {0}, 0, 0, 8, SHOWN, -1, 0, 1, 1, 50, 60, 4, 2, 0.25f},
    [NAN_ONE] = {"Nan", "ImageWidget", {0}, 0, 0, 9, SHOWN, -1, 0, 1, 1, 0, 0, 1, 1, 1},
};

static const Fake *g_fault_node;   /* the widget whose built-in readers fault: the readers end there */
static int g_getters_ended, g_cut_group;
static const char *getters_state(void *ctx) {
    (void)ctx;
    return g_getters_ended ? "faulted" : "read";
}

static void fill_guid(uint8_t g[16], int k) {
    for (int i = 0; i < 16; i++) g[i] = (uint8_t)(k * 16 + i);
}

static int fake_read(void *ctx, const void *w, UdNode *out) {
    const Fake *f = (const Fake *)w;
    (void)ctx;
    snprintf(out->name, sizeof(out->name), "%s", f->name);
    snprintf(out->cls, sizeof(out->cls), "%s", f->cls);
    out->has_name = 1, out->has_crc = 1, out->name_crc = 0xabcdef01u;
    out->has_guid = 1, fill_guid(out->guid, f->guid);
    if (f->tmpl) out->has_tmpl = 1, fill_guid(out->tmpl, 100 + f->tmpl);
    out->container = f->nkids > 0;
    out->type_hash = out->container ? 0x0f561333u : 0x41a404e5u;
    out->flags = (uint16_t)f->flags, out->mask_mode = (uint8_t)f->mask_mode;
    out->visible = f->flags & F_VISIBLE, out->drawable = f->flags & F_DRAWABLE, out->receives_mask = f->flags & F_RECEIVES;
    out->mask_intersect = f->mask_mode == INTERSECT;
    out->mask_owner = f->mask_owner >= 0 ? &g_tree[f->mask_owner] : NULL;
    out->alpha = f->alpha, out->scale[0] = f->sx, out->scale[1] = f->sy, out->scale[2] = 1;
    out->bb_size[0] = f->w, out->bb_size[1] = f->h;
    memset(out->world, 0, sizeof(out->world));
    out->world[0] = f->sx, out->world[5] = f->sy, out->world[10] = 1, out->world[15] = 1;
    out->world[12] = f->tx, out->world[13] = f->ty;
    out->has_world = 1;
    if (f == &g_tree[NAN_ONE]) out->rot = NAN;
    out->props_read = !g_getters_ended;
    if (f == g_fault_node) out->props_read = 0, out->props_fault = 1, g_getters_ended = 1;
    out->nprops = 2;
    out->props[0] = (UdValue){.name = "Visible", .index = -1, .type = 0, .kind = UD_BOOL, .i = 1};
    out->props[1] = (UdValue){.name = "Position", .index = -1, .type = 6, .kind = UD_VEC, .n = 3, .v = {1.5, -2, 0}};
    if (f == &g_tree[LABEL]) { /* an unnamed bool of the instance and an unknown type met on it */
        uint8_t mem[24] = {1};
        out->nunnamed = 1, out->unnamed[0].crc = 0x0d2df3efu, out->unnamed[0].owner = 0x41a404e5u;
        out->unnamed[0].code = 0x100, out->unnamed[0].offset = 365, out->unnamed[0].on = UD_ON_INSTANCE;
        ud_field_value(&out->unnamed[0].v, 0x100, mem);
        out->nunknown = 1, out->unknown_types[0] = 0x71e62ea4u;
    }
    if (out->container) {
        out->ncustom = 1;
        out->custom[0] = (UdValue){.uid = 1234u, .index = 0, .type = 4, .kind = UD_STRING};
        snprintf(out->custom[0].text, sizeof(out->custom[0].text), "bound");
    }
    return 1;
}

static int fake_children(void *ctx, const void *w, const void **out, int cap, int *cut) {
    const Fake *f = (const Fake *)w;
    (void)ctx;
    int n = 0;
    for (int i = 0; i < f->nkids && n < cap; i++) out[n++] = &g_tree[f->kids[i]];
    if (g_cut_group && f == &g_tree[GROUP]) *cut = 1; /* a list longer than the walk takes */
    return n;
}

static UdHeader header(int max_nodes) {
    UdHeader h;
    memset(&h, 0, sizeof(h));
    h.scene = "romanizationchoicescene", h.state_name = "RomanizationChoice", h.stamp = "20261109-120000";
    h.trigger = "the key", h.state_id = 7, h.out_w = 960, h.out_h = 540, h.ref_w = 1920, h.ref_h = 1080;
    h.win_w = 960, h.win_h = 540, h.screen_w = 1920, h.screen_h = 1080, h.max_nodes = max_nodes, h.max_depth = 16;
    return h;
}

/* the JSON object of one widget, by its id */
static const char *widget_text(const char *json, int id, char *out, size_t cap) {
    char key[32];
    snprintf(key, sizeof(key), "{\"id\":%d,", id);
    const char *p = strstr(json, key);
    if (!p) return "";
    const char *e = strstr(p, "\n");
    size_t n = e ? (size_t)(e - p) : strlen(p);
    if (n >= cap) n = cap - 1;
    memcpy(out, p, n);
    out[n] = 0;
    return out;
}

static int balanced(const char *s) {
    int depth = 0, in_str = 0;
    for (; *s; s++) {
        if (in_str) {
            if (*s == '\\' && s[1]) s++;
            else if (*s == '"') in_str = 0;
            else if ((unsigned char)*s < 0x20) return 0;
            continue;
        }
        if (*s == '"') in_str = 1;
        else if (*s == '{' || *s == '[') depth++;
        else if (*s == '}' || *s == ']') depth--;
        if (depth < 0) return 0;
    }
    return depth == 0 && !in_str;
}

static void test_scene(void) {
    UdReader r = {fake_read, fake_children, NULL, getters_state};
    UdHeader h = header(64);
    UdBuf b = {0};
    UdStats st;
    char w[8192];
    CHECK(ud_dump(&b, &h, &r, &g_tree[ROOT], &st));
    CHECK(b.p && balanced(b.p));
    CHECK(st.nodes == 9); /* the label reached twice is written once */
    CHECK(st.unread == 0 && !st.truncated && st.masks_unresolved == 0);
    CHECK(strstr(b.p, "\"scene\":\"romanizationchoicescene\""));
    CHECK(strstr(b.p, "\"ui_scale\":[0.5,0.5]"));
    CHECK(strstr(b.p, "\"widget_count\":9"));
    /* drawing order: root 0, panel 1, label 2, hidden 3, mask 4, covered 5, group 6, its two 7 and 8 */
    widget_text(b.p, 2, w, sizeof(w));
    CHECK(strstr(w, "\"name\":\"Title \\\"one\\\"\\u000a\""));
    CHECK(strstr(w, "\"parent\":1") && strstr(w, "\"owner\":1") && strstr(w, "\"depth\":2"));
    CHECK(strstr(w, "\"in\":\"63626160-6564-6766-6869-6a6b6c6d6e6f\"")); /* the panel's file (102) */
    CHECK(strstr(w, "\"screen\":[120,130,320,170]"));
    CHECK(strstr(w, "\"shown\":true") && strstr(w, "\"alpha_product\":0.5"));
    CHECK(strstr(w, "\"props\":{\"Visible\":true,\"Position\":[1.5,-2,0]}"));
    CHECK(strstr(w, "\"unknown_types\":[\"71e62ea4\"]"));
    CHECK(strstr(w, "\"unnamed\":[{\"crc\":\"0d2df3ef\",\"type\":\"41a404e5\",\"code\":\"0x100\",\"offset\":365,"
                    "\"on\":\"instance\",\"value\":true}]"));
    widget_text(b.p, 3, w, sizeof(w));
    CHECK(strstr(w, "\"drawable\":false") && strstr(w, "\"shown\":false"));
    widget_text(b.p, 5, w, sizeof(w));
    CHECK(strstr(w, "\"mask\":4") && strstr(w, "\"shown\":false")); /* an intersect mask that is not drawn hides it */
    widget_text(b.p, 1, w, sizeof(w));
    CHECK(strstr(w, "\"in\":\""));
    CHECK(strstr(w, "\"custom\":[{\"index\":0,\"uid\":1234,\"type\":4,\"value\":\"bound\"}]"));
    CHECK(strstr(w, "\"screen\":[110,120,310,270]")); /* 400 x 300 at scale 0.5 */
    widget_text(b.p, 7, w, sizeof(w));
    CHECK(strstr(w, "\"owner\":0") && strstr(w, "\"alpha_product\":0.25")); /* a group places no file */
    CHECK(strstr(w, "\"in\":\"53525150-5554-5756-5859-5a5b5c5d5e5f\"")); /* the scene's own file (101) */
    widget_text(b.p, 8, w, sizeof(w));
    CHECK(strstr(w, "\"rotation\":null"));
    widget_text(b.p, 0, w, sizeof(w));
    CHECK(strstr(w, "\"parent\":-1") && strstr(w, "\"owner\":-1") && strstr(w, "\"in\":null"));
    ud_buf_free(&b);

    h = header(4); /* a cap below the scene: the first four in order, marked */
    CHECK(ud_dump(&b, &h, &r, &g_tree[ROOT], &st));
    CHECK(st.nodes == 4 && st.truncated && balanced(b.p) && strstr(b.p, "\"truncated\":true"));
    ud_buf_free(&b);
}

/* a reader that faults on one widget: it and every later one say their built-in readers did not run */
static void test_getters_fault(void) {
    UdReader r = {fake_read, fake_children, NULL, getters_state};
    UdHeader h = header(64);
    UdBuf b = {0};
    UdStats st;
    char w[8192];
    g_fault_node = &g_tree[COVERED], g_getters_ended = 0;
    CHECK(ud_dump(&b, &h, &r, &g_tree[ROOT], &st));
    CHECK(balanced(b.p) && st.props_faults == 1);
    for (int id = 0; id < 9; id++) { /* drawing order: the covered picture is the sixth */
        widget_text(b.p, id, w, sizeof(w));
        CHECK(strstr(w, id < 5 ? "\"props_read\":true" : "\"props_read\":false"));
        CHECK(!!strstr(w, "\"props_fault\":true") == (id == 5)); /* only the widget that faulted */
    }
    CHECK(strstr(b.p, "\"props_faults\":1,\"getters\":\"faulted\"}"));
    ud_buf_free(&b);
    g_fault_node = NULL, g_getters_ended = 0;
    CHECK(ud_dump(&b, &h, &r, &g_tree[ROOT], &st));
    CHECK(strstr(b.p, "\"props_faults\":0,\"getters\":\"read\"}") && !strstr(b.p, "\"props_read\":false"));
    ud_buf_free(&b);
}

/* children the walk cannot take (a list past the buffer, a fault reading it) mark the dump cut */
static void test_children_cut(void) {
    UdReader r = {fake_read, fake_children, NULL, NULL};
    UdHeader h = header(64);
    UdBuf b = {0};
    UdStats st;
    g_cut_group = 1;
    CHECK(ud_dump(&b, &h, &r, &g_tree[ROOT], &st));
    CHECK(st.nodes == 9 && st.truncated && strstr(b.p, "\"truncated\":true") && !strstr(b.p, "\"getters\""));
    g_cut_group = 0;
    ud_buf_free(&b);
}

/* strings stay UTF-8 whatever the game held: a cut sequence, a stray byte, an overlong one */
static void test_strings_are_utf8(void) {
    UdBuf b = {0};
    ud_str(&b, "caf\xc3\xa9 \xe2\x82\xac \xf0\x9f\x98\x80", -1);
    CHECK(0 == strcmp(b.p, "\"caf\xc3\xa9 \xe2\x82\xac \xf0\x9f\x98\x80\""));
    ud_buf_free(&b);
    ud_str(&b, "caf\xc3", -1); /* cut at the limit in the middle of the sequence */
    CHECK(0 == strcmp(b.p, "\"caf\\ufffd\""));
    ud_buf_free(&b);
    ud_str(&b, "a\xff" "b\xc0\xaf" "c\xed\xa0\x80" "d\xe2\x82", -1); /* stray, overlong, a surrogate, cut */
    CHECK(0 == strcmp(b.p, "\"a\\ufffdb\\ufffd\\ufffdc\\ufffd\\ufffd\\ufffdd\\ufffd\\ufffd\""));
    ud_buf_free(&b);
}

static int exists(const char *path) { return GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES; }

static void read_back(const char *path, char *out, size_t cap) {
    FILE *f = fopen(path, "rb");
    size_t n = f ? fread(out, 1, cap - 1, f) : 0;
    out[n] = 0;
    if (f) fclose(f);
}

/* the dump file is whole or absent: a refused move leaves the old file and no temporary one, a missing folder fails */
static void test_write_atomic(void) {
    char dir[MAX_PATH], path[MAX_PATH], tmp[MAX_PATH + 8], got[64];
    unsigned long err = 0;
    GetTempPathA(sizeof(dir), dir);
    snprintf(path, sizeof(path), "%suidump_test_%lu.json", dir, GetCurrentProcessId());
    snprintf(tmp, sizeof(tmp), "%s.tmp", path);
    DeleteFileA(path);
    CHECK(ud_write_atomic(path, "{\"a\":1}\n", 8, &err) && !exists(tmp));
    read_back(path, got, sizeof(got));
    CHECK(0 == strcmp(got, "{\"a\":1}\n"));
    CHECK(ud_write_atomic(path, "{\"b\":2}\n", 8, &err)); /* replaces the earlier dump */
    read_back(path, got, sizeof(got));
    CHECK(0 == strcmp(got, "{\"b\":2}\n") && !exists(tmp));
    SetFileAttributesA(path, FILE_ATTRIBUTE_READONLY); /* the move cannot replace it */
    err = 0;
    CHECK(!ud_write_atomic(path, "{\"c\":3}\n", 8, &err) && err != 0);
    CHECK(!exists(tmp)); /* no half file left beside it */
    read_back(path, got, sizeof(got));
    CHECK(0 == strcmp(got, "{\"b\":2}\n")); /* the old dump is whole */
    SetFileAttributesA(path, FILE_ATTRIBUTE_NORMAL);
    DeleteFileA(path);
    snprintf(path, sizeof(path), "%suidump_test_missing_%lu\\x.json", dir, GetCurrentProcessId());
    CHECK(!ud_write_atomic(path, "{}\n", 3, &err) && !exists(path));
}

static void test_helpers(void) {
    char t[37], name[96];
    uint8_t g[16];
    float m[16] = {0}, r[4];
    UdNode n;
    for (int i = 0; i < 16; i++) g[i] = (uint8_t)i;
    ud_guid_text(g, t);
    CHECK(0 == strcmp(t, "03020100-0504-0706-0809-0a0b0c0d0e0f")); /* uuid.UUID(bytes_le=bytes(range(16))) */
    CHECK(ud_scene_listed("maintitlescene, RomanizationChoice", "MainTitle"));
    CHECK(ud_scene_listed("maintitlescene, RomanizationChoice", "romanizationchoice"));
    CHECK(!ud_scene_listed("maintitlescene", "MainTitleX"));
    CHECK(!ud_scene_listed("", "MainTitle") && !ud_scene_listed(NULL, "MainTitle"));
    ud_file_name(name, sizeof(name), "20261109-120000", "main title/scene");
    CHECK(0 == strcmp(name, "20261109-120000-main_title_scene.json"));
    /* a quarter turn: the rectangle's corners are turned, the screen box is their bounds */
    m[1] = 1, m[4] = -1, m[10] = 1, m[15] = 1, m[12] = 100, m[13] = 50;
    memset(&n, 0, sizeof(n));
    memcpy(n.world, m, sizeof(m));
    n.has_world = 1, n.bb_size[0] = 20, n.bb_size[1] = 10;
    CHECK(ud_rect(&n, r) && r[0] == 90 && r[1] == 50 && r[2] == 100 && r[3] == 70);
    n.has_world = 0;
    CHECK(!ud_rect(&n, r));
    UdValue v;
    uint8_t mem[24] = {0};
    float f3[3] = {1.5f, 2, -3};
    memcpy(mem, f3, sizeof(f3));
    memset(&v, 0, sizeof(v)), ud_field_value(&v, 0x1200, mem);
    CHECK(v.kind == UD_VEC && v.n == 3 && v.v[2] == -3);
    memset(&v, 0, sizeof(v)), ud_field_value(&v, 0x801, mem); /* a list stays raw */
    CHECK(v.kind == UD_RAW && v.raw[3] == mem[3]);
    mem[0] = 0xfe, mem[1] = 0xff;
    memset(&v, 0, sizeof(v)), ud_field_value(&v, 0x600, mem);
    CHECK(v.kind == UD_INT && v.i == 0xfffe);
    uint32_t list[2] = {7, 9};
    CHECK(ud_listed(list, 2, 9) && !ud_listed(list, 2, 8));
    UdBuf b = {0};
    ud_num(&b, -0.0), ud_put(&b, " "), ud_num(&b, INFINITY), ud_put(&b, " "), ud_num(&b, 0.1f);
    CHECK(b.p && 0 == strcmp(b.p, "0 null 0.1"));
    ud_buf_free(&b);
}

int main(void) {
    test_scene();
    test_getters_fault();
    test_children_cut();
    test_strings_are_utf8();
    test_write_atomic();
    test_helpers();
    if (g_fail) {
        printf("uidump_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("uidump_test: all checks passed\n");
    return 0;
}

/* Host test of the live link's pure part (livelink_logic.c): the hashes against published vectors, the handshake proof
   against the suite's, frames, the JSON reader, the message set and what a changed file needs, the answers, the rate,
   the gate, the Lua templates and the loader log. No game needed. */
#define _CRT_SECURE_NO_WARNINGS
#include "livelink_logic.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                        \
    do {                                                                \
        if (!(c)) {                                                     \
            printf("livelink_test: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            g_fail++;                                                   \
        }                                                               \
    } while (0)

static void sha_hex(const void *msg, size_t n, char *out) {
    LLSha s;
    uint8_t d[32];
    ll_sha_init(&s), ll_sha_update(&s, msg, n), ll_sha_final(&s, d), ll_hex(d, 32, out);
}

static void hmac_hex(const void *key, size_t k, const void *msg, size_t n, char *out) {
    uint8_t d[32];
    ll_hmac_sha256(key, k, msg, n, d), ll_hex(d, 32, out);
}

static void test_hashes(void) {
    char h[65], big[131];
    uint8_t key20[20];
    sha_hex("abc", 3, h);
    CHECK(!strcmp(h, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    sha_hex("", 0, h);
    CHECK(!strcmp(h, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    sha_hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, h); /* two blocks */
    CHECK(!strcmp(h, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
    memset(key20, 0x0b, sizeof key20);
    hmac_hex(key20, 20, "Hi There", 8, h); /* RFC 4231 case 1 */
    CHECK(!strcmp(h, "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"));
    hmac_hex("Jefe", 4, "what do ya want for nothing?", 28, h); /* case 2 */
    CHECK(!strcmp(h, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"));
    memset(big, 0xaa, sizeof big);
    hmac_hex(big, 131, "Test Using Larger Than Block-Size Key - Hash Key First", 54, h); /* case 6: key over a block */
    CHECK(!strcmp(h, "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"));
}

static void test_proof(void) {
    char p[65];
    ll_proof("0123456789abcdef0123456789abcdef", "client", "abc", p); /* the value the suite's test computes */
    CHECK(!strcmp(p, "6fee175ce19b4c7a354fa5dca90448fe8d96b6ec1b441bdbe310875421182b24"));
    CHECK(ll_proof_ok("0123456789abcdef0123456789abcdef", "client", "abc", p));
    CHECK(!ll_proof_ok("0123456789abcdef0123456789abcdef", "game", "abc", p));
    CHECK(!ll_proof_ok("0123456789abcdef0123456789abcdee", "client", "abc", p));
    CHECK(!ll_proof_ok("0123456789abcdef0123456789abcdef", "client", "abc", "6fee"));
    CHECK(!ll_proof_ok("0123456789abcdef0123456789abcdef", "client", "abc", NULL));
    p[0] = 'X';
    CHECK(!ll_proof_ok("0123456789abcdef0123456789abcdef", "client", "abc", p));
}

static void test_frames(void) {
    static char json[LL_REQ_MAX + 8], one[1024];
    uint8_t wire[1100];
    size_t n, len;
    LLInbuf b;
    n = ll_frame_wrap("{\"id\":3}", 8, wire, sizeof wire);
    CHECK(n == 12 && wire[0] == 8 && wire[1] == 0 && !memcmp(wire + 4, "{\"id\":3}", 8));
    ll_inbuf_init(&b);
    CHECK(ll_inbuf_feed(&b, wire, 3) && ll_inbuf_next(&b, one, sizeof one, &len) == 0); /* the length is not all there */
    CHECK(ll_inbuf_feed(&b, wire + 3, 6) && ll_inbuf_next(&b, one, sizeof one, &len) == 0);
    CHECK(ll_inbuf_feed(&b, wire + 9, 3) && ll_inbuf_next(&b, one, sizeof one, &len) == 1 && len == 8 && !strcmp(one, "{\"id\":3}"));
    CHECK(ll_inbuf_next(&b, one, sizeof one, &len) == 0 && b.n == 0);
    CHECK(ll_inbuf_feed(&b, wire, 12) && ll_inbuf_feed(&b, wire, 12)); /* two at once */
    CHECK(ll_inbuf_next(&b, one, sizeof one, &len) == 1 && ll_inbuf_next(&b, one, sizeof one, &len) == 1 &&
          ll_inbuf_next(&b, one, sizeof one, &len) == 0);
    {
        uint8_t over[4] = {0, 0, 0x20, 0}; /* 2 MB announced */
        ll_inbuf_feed(&b, over, 4);
        CHECK(ll_inbuf_next(&b, one, sizeof one, &len) == -1);
    }
    ll_inbuf_free(&b);
    memset(json, 'a', sizeof json);
    CHECK(ll_frame_wrap(json, LL_MAX_FRAME + 1, (uint8_t *)json, 8) == 0);
    {
        uint8_t big[4] = {0, 0, 2, 0}; /* 128 KB: over what a request may be */
        ll_inbuf_init(&b);
        ll_inbuf_feed(&b, big, 4);
        CHECK(ll_inbuf_next(&b, json, sizeof json, &len) == -1);
        ll_inbuf_free(&b);
    }
}

static void test_json(void) {
    LLTok t[40];
    char s[64];
    const char *doc = " {\"a\": [1, 2.5, -3e2], \"b\": {\"c\": true, \"d\": null}, \"e\": \"x\\ty\\u0041\\\"\"} ";
    int n = ll_json_parse(doc, strlen(doc), t, 40);
    CHECK(n == 14 && t[0].type == LLJ_OBJ && t[0].size == 3);
    CHECK(t[1].type == LLJ_STR && t[1].size == 1 && t[2].type == LLJ_ARR && t[2].size == 3 && t[3].type == LLJ_NUM);
    CHECK(t[n - 1].type == LLJ_STR && ll_json_string(doc, &t[n - 1], s, sizeof s) && !strcmp(s, "x\tyA\""));
    CHECK(ll_json_parse("{\"a\":1} x", 9, t, 40) == -1 && ll_json_parse("{\"a\":", 5, t, 40) == -1);
    CHECK(ll_json_parse("{\"a\":1,}", 8, t, 40) == -1 && ll_json_parse("[1 2]", 5, t, 40) == -1 && ll_json_parse("", 0, t, 40) == -1);
    CHECK(ll_json_parse("[[[[[[[[[[1]]]]]]]]]]", 21, t, 40) == -1); /* nesting is limited */
    CHECK(ll_json_parse("[1,2,3,4,5]", 11, t, 3) == -1);            /* more tokens than the table holds */
    {
        const char *bad = "[\"caf\xc3\xa9\", \"a\\u00e9\", \"\\q\"]";
        n = ll_json_parse(bad, strlen(bad), t, 40);
        CHECK(n == 4 && !ll_json_string(bad, &t[1], s, sizeof s) && !ll_json_string(bad, &t[2], s, sizeof s) &&
              !ll_json_string(bad, &t[3], s, sizeof s)); /* not plain ASCII, an unknown escape */
    }
    CHECK(ll_json_escape("a\"b\\c\n\x01", s, sizeof s) == 15 && !strcmp(s, "a\\\"b\\\\c\\n\\u0001"));
    CHECK(ll_json_escape("abcdef", s, 4) == -1);
}

static int parse(const char *json, LLRequest *r, char *err) { return ll_request_parse(json, strlen(json), r, err, 160); }

static void test_requests(void) {
    static LLRequest r;
    char err[160];
    CHECK(parse("{\"id\":7,\"type\":\"ping\",\"args\":{}}", &r, err) == 1 && r.id == 7 && ll_request_is(&r, "ping"));
    CHECK(parse("{\"id\":8,\"type\":\"ping\"}", &r, err) == 1); /* args may be left out */
    CHECK(parse("{\"id\":9,\"type\":\"reload.assets\",\"args\":{\"mode\":\"rebuild\"}}", &r, err) == 1 && r.has_mode && !strcmp(r.mode, "rebuild"));
    CHECK(parse("{\"id\":9,\"type\":\"reload.assets\",\"args\":{\"mode\":\"rm\"}}", &r, err) == 0 && r.id == 9 && strstr(err, "mode"));
    CHECK(parse("{\"id\":1,\"type\":\"reload.models\",\"args\":{\"mod\":\"m\",\"paths\":[\"data/a.rdm\",\"data/b.cfg\"]}}", &r, err) == 1 &&
          r.npaths == 2 && !strcmp(r.paths[1], "data/b.cfg") && !strcmp(r.mod, "m"));
    CHECK(parse("{\"id\":2,\"type\":\"reload.models\",\"args\":{\"mod\":\"m\"}}", &r, err) == 0 && strstr(err, "paths is missing"));
    CHECK(parse("{\"id\":3,\"type\":\"ping\",\"args\":{\"x\":1}}", &r, err) == 0 && strstr(err, "unexpected"));
    CHECK(parse("{\"id\":4,\"type\":\"shell\",\"args\":{}}", &r, err) == -1 && r.id == 4 && strstr(err, "unknown request"));
    CHECK(parse("{\"id\":5,\"type\":\"reload.models\",\"args\":{\"paths\":[\"../x.rdm\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":5,\"type\":\"reload.models\",\"args\":{\"paths\":[\"a b.rdm\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":5,\"type\":\"reload.models\",\"args\":{\"paths\":[\"/abs.rdm\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":5,\"type\":\"reload.models\",\"args\":{\"paths\":[\"C:/x.rdm\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":5,\"type\":\"reload.models\",\"args\":{\"paths\":[\"a//b.rdm\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":5,\"type\":\"files.changed\",\"args\":{\"mod\":\"a;b\",\"paths\":[\"a.xml\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"subscribe\",\"args\":{\"topics\":[\"selection\",\"log\"]}}", &r, err) == 1 && r.nnames == 2);
    CHECK(parse("{\"id\":6,\"type\":\"subscribe\",\"args\":{\"topics\":[\"secrets\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"select\",\"args\":{\"guid\":-1}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"select\",\"args\":{\"guid\":true}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"select\",\"args\":{\"guid\":1.5}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"select\",\"args\":{\"guid\":9007199254740993}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"select\",\"args\":{\"object\":4294967296}}", &r, err) == 1 && r.object == 4294967296LL);
    CHECK(parse("{\"id\":6,\"type\":\"jump\",\"args\":{\"session\":\"s1\",\"position\":[1,2.5,-3]}}", &r, err) == 1 && r.npos == 3 && r.pos[1] == 2.5);
    CHECK(parse("{\"id\":6,\"type\":\"jump\",\"args\":{\"position\":[1,2,\"x\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":6,\"type\":\"read\",\"args\":{\"guid\":5,\"fields\":[\"Building/BuildCost\"]}}", &r, err) == 1 && r.nfields == 1);
    CHECK(parse("{\"id\":6,\"type\":\"read\",\"args\":{\"guid\":5,\"fields\":[\"a b\"]}}", &r, err) == 0);
    CHECK(parse("{\"id\":10,\"type\":\"hello\",\"args\":{\"protocol\":1,\"client\":{\"name\":\"x\",\"version\":\"1\"},\"nonce\":\"abc\",\"proof\":\"00\"}}", &r, err) == 1 &&
          r.has_protocol && !strcmp(r.nonce, "abc") && !strcmp(r.proof, "00"));
    /* a client named by a plain string after the proof leaves the proof as it came */
    CHECK(parse("{\"id\":11,\"type\":\"hello\",\"args\":{\"protocol\":1,\"nonce\":\"abc\",\"proof\":\"ab\",\"client\":\"suite 1.0\"}}", &r, err) == 1 &&
          !strcmp(r.proof, "ab"));
    CHECK(parse("{\"id\":12,\"type\":\"hello\",\"args\":{\"protocol\":1,\"client\":\"x\",\"nonce\":\"abc\",\"proof\":\"cd\"}}", &r, err) == 1 &&
          !strcmp(r.proof, "cd"));
    CHECK(parse("{\"id\":6,\"type\":\"reload.scenes\",\"args\":{\"mod\":\"m\",\"states\":[\"MainTitle\",\"Game\"],\"paths\":[\"data/ui/a\"]}}", &r, err) == 1 &&
          r.nnames == 2 && !strcmp(r.names[1], "Game") && ll_kind_of_request(r.type) == LL_KIND_SCENES);
    CHECK(parse("{\"id\":6,\"type\":\"ui.dump\",\"args\":{\"states\":[\"HUD\"]}}", &r, err) == 1 && r.nnames == 1 &&
          ll_kind_of_request(r.type) == LL_KIND_UIDUMP);
    CHECK(parse("{\"id\":6,\"type\":\"ui.dump\",\"args\":{}}", &r, err) == 1 && r.nnames == 0);
    CHECK(parse("{\"id\":6,\"type\":\"ui.dump\",\"args\":{\"states\":[\"a b\"]}}", &r, err) == 0);
    CHECK(parse("[1]", &r, err) == 0 && parse("{", &r, err) == 0 && parse("{\"id\":1}", &r, err) == -1);
    CHECK(ll_path_ok("data/a/b_c-d.1.rdm") && !ll_path_ok("./a") && !ll_path_ok("a/") && !ll_path_ok(""));
}

static void test_needs(void) {
    static LLRequest r;
    char out[512], err[160];
    CHECK(!strcmp(ll_needs_of("data/a/house_lod0.rdm"), "models") && !strcmp(ll_needs_of("data/a/house.CFG"), "models"));
    CHECK(!strcmp(ll_needs_of("data/shaders/fx/water.fx"), "effects") && !strcmp(ll_needs_of("x.fxh"), "effects"));
    CHECK(!strcmp(ll_needs_of("data/config/export/main/asset/assets.xml"), "assets"));
    CHECK(!strcmp(ll_needs_of("data/config/gui/texts_english.xml"), "texts"));
    CHECK(!strcmp(ll_needs_of("data/config/ambient/day.xml"), "ambient"));
    CHECK(!strcmp(ll_needs_of("data/ui/studio/generated/hudscene"), "scenes") && !strcmp(ll_needs_of("a.phx"), "scenes"));
    CHECK(!strcmp(ll_needs_of("data/graphics/t_diffuse.dds"), "textures") && !strcmp(ll_needs_of("audio/bank.bnk"), "banks"));
    CHECK(!strcmp(ll_needs_of("maps/island.a7m"), "session") && !strcmp(ll_needs_of("readme.txt"), "restart"));
    CHECK(parse("{\"id\":1,\"type\":\"files.changed\",\"args\":{\"mod\":\"m\",\"paths\":[\"a.rdm\",\"x.txt\"]}}", &r, err) == 1);
    CHECK(ll_needs_json(&r, out, sizeof out) > 0 && !strcmp(out, "{\"needs\":{\"a.rdm\":\"models\",\"x.txt\":\"restart\"}}"));
    CHECK(ll_needs_json(&r, out, 10) == -1);
}

static void test_text(void) {
    char out[512];
    CHECK(ll_answer_json(out, sizeof out, 3, "done", "a\"b", "{}") > 0 &&
          !strcmp(out, "{\"id\":3,\"status\":\"done\",\"message\":\"a\\\"b\",\"data\":{}}")); /* the suite's frame, byte for byte */
    CHECK(ll_answer_json(out, sizeof out, 3, "ok", "", NULL) > 0 && strstr(out, "\"data\":{}"));
    CHECK(ll_answer_json(out, 20, 3, "ok", "", NULL) == -1);
    CHECK(ll_event_json(out, sizeof out, "selection", "{\"objects\":[]}") > 0 && !strcmp(out, "{\"event\":\"selection\",\"data\":{\"objects\":[]}}"));
    CHECK(ll_greeting_json(out, sizeof out, "ab12") > 0 && !strcmp(out, "{\"challenge\":\"ab12\",\"protocol\":1}"));
    CHECK(ll_session_json(out, sizeof out, "\\\\.\\pipe\\anno117-live-link-9", "tok", "anno117", "3.0", "lib", 7) > 0 &&
          strstr(out, "\"pipe\":\"\\\\\\\\.\\\\pipe\\\\anno117-live-link-9\"") && strstr(out, "\"pid\":7"));
    CHECK(ll_topic_bit("log") == LL_TOPIC_LOG && ll_topic_bit("x") == 0 && !strcmp(ll_topic_name(LL_TOPIC_SESSION), "session"));
}

static void test_data(void) {
    char out[1024], fields[2][LL_FIELD_LEN], ids[2][64], folders[2][260];
    LLFileResult f[3] = {{"a.rdm", "", LL_FILE_DONE, 14.2}, {"b.cfg", "", LL_FILE_HANDED, 3.1}, {"c.rdm", "not loaded", LL_FILE_NOT_LOADED, 0}};
    LLPass p = {1, 3.0, 1, 0, 1, 0, "No matching node"};
    const char *why;
    strcpy(ids[0], "m1"), strcpy(folders[0], "C:/mods/m1");
    CHECK(ll_welcome_json(out, sizeof out, "anno117", "392218104", "lib 1", LL_KIND_ASSETS | LL_KIND_MODELS | LL_KIND_SELECT, ids, folders, 1, "ab") > 0);
    CHECK(!strcmp(out, "{\"game\":\"anno117\",\"build\":\"392218104\",\"library\":\"lib 1\",\"protocol\":1,\"kinds\":[\"assets\",\"models\"],"
                       "\"mods\":[{\"id\":\"m1\",\"folder\":\"C:/mods/m1\"}],\"proof\":\"ab\"}")); /* select is no reload kind */
    CHECK(ll_selection_json(out, sizeof out, 4711, 180023, "5000") > 0 && !strcmp(out, "{\"objects\":[{\"id\":4711,\"guid\":180023,\"session\":\"5000\"}]}"));
    CHECK(ll_selection_json(out, sizeof out, -1, 0, "") > 0 && !strcmp(out, "{\"objects\":[]}"));
    strcpy(fields[0], "Building/BuildCost"), strcpy(fields[1], "Text");
    CHECK(ll_values_json(out, sizeof out, fields, 2, "550\r\nan \"x\"") > 0 &&
          !strcmp(out, "{\"values\":{\"Building/BuildCost\":\"550\",\"Text\":\"an \\\"x\\\"\"}}"));
    CHECK(ll_values_json(out, sizeof out, fields, 2, "550") > 0 && strstr(out, "\"Text\":\"\""));
    CHECK(ll_pass_json(out, sizeof out, &p) > 0 && strstr(out, "\"seconds\":3.0") && strstr(out, "\"problem\":\"No matching node\""));
    CHECK(ll_files_json(out, sizeof out, f, 3) > 0 && strstr(out, "\"status\":\"reloaded\"") && strstr(out, "\"status\":\"handed\"") && strstr(out, "\"status\":\"skipped\""));
    CHECK(!strcmp(ll_files_status(f, 3, &why), "done") && !strcmp(why, "not loaded"));
    CHECK(!strcmp(ll_files_status(f + 2, 1, &why), "skipped"));
    f[2].code = LL_FILE_REFUSED;
    CHECK(!strcmp(ll_files_status(f + 2, 1, &why), "refused") && !strcmp(why, "not loaded") && !strcmp(ll_files_status(f, 0, &why), "refused"));
}

static void test_rate_and_gate(void) {
    LLRate rate;
    static LLRequest r;
    const char *why;
    char err[160];
    int taken = 0;
    ll_rate_init(&rate, 1000);
    for (int i = 0; i < 40; i++) taken += ll_rate_take(&rate, 1000);
    CHECK(taken == LL_RATE_BURST);
    CHECK(!ll_rate_take(&rate, 1050) && ll_rate_take(&rate, 1150)); /* 10 a second: one more after 100 ms */
    CHECK(ll_rate_take(&rate, 900000) && rate.tokens <= LL_RATE_BURST); /* the bucket never overfills */
    parse("{\"id\":1,\"type\":\"reload.assets\"}", &r, err);
    CHECK(ll_gate(&r, 0, LL_KIND_ASSETS, &why) == 0);
    CHECK(ll_gate(&r, 1, LL_KIND_ASSETS, &why) == 1 && strstr(why, "multiplayer"));
    CHECK(ll_gate(&r, 0, LL_KIND_TEXTS, &why) == 2);
    parse("{\"id\":1,\"type\":\"ping\"}", &r, err);
    CHECK(ll_gate(&r, 1, 0, &why) == 0); /* ping and the handshake always answer */
    parse("{\"id\":1,\"type\":\"files.changed\",\"args\":{\"paths\":[\"a.xml\"]}}", &r, err);
    CHECK(ll_gate(&r, 1, 0, &why) == 0);
    CHECK(ll_kind_of_request("reload.models") == LL_KIND_MODELS && ll_kind_of_request("select") == LL_KIND_SELECT && ll_kind_of_request("bye") == 0);
}

static void test_lua(void) {
    char cmd[1024], f[2][LL_FIELD_LEN];
    int64_t o, g;
    char session[32];
    CHECK(ll_lua_reload_assets(cmd, sizeof cmd, 0) && !strcmp(cmd, "Mods:LoadMods()"));
    CHECK(ll_lua_reload_assets(cmd, sizeof cmd, 1) && !strcmp(cmd, "CAssetDataManager:ExecuteFilter()"));
    CHECK(ll_lua_reload_ambient(cmd, sizeof cmd) && !strcmp(cmd, "CEnvironmentManager:ReloadAmbientSettings()"));
    CHECK(ll_lua_select(cmd, sizeof cmd, 4294967296LL) && !strcmp(cmd, "Selection:SelectByID(4294967296)"));
    CHECK(ll_lua_jump_object(cmd, sizeof cmd, 12) && !strcmp(cmd, "Scripts:JumpToObject(12)"));
    CHECK(ll_lua_jump_session(cmd, sizeof cmd, "180023", NULL) && !strcmp(cmd, "Scripts:JumpToSession(180023)"));
    CHECK(!ll_lua_jump_session(cmd, sizeof cmd, "s1", NULL) && !ll_lua_jump_session(cmd, sizeof cmd, "180023x", NULL));
    CHECK(ll_lua_jump_session(cmd, sizeof cmd, "180023", (double[]){1, 2.25, -3}) &&
          !strcmp(cmd, "MetaObjects:CheatLookAtPosition(180023, 1.0, 2.2, -3.0)"));
    CHECK(!ll_lua_jump_session(cmd, sizeof cmd, "180023", (double[]){1, 2, 1e300}) &&
          !ll_lua_jump_session(cmd, sizeof cmd, "180023", (double[]){1, 2, NAN}));
    CHECK(ll_lua_select_guid(cmd, sizeof cmd, 1010343) && strstr(cmd, "Scripts:GetObjectGroupByGUID(1010343)") &&
          strstr(cmd, "Selection:SelectByID(o.ID)") && !ll_lua_select_guid(cmd, sizeof cmd, 0) && !ll_lua_select_guid(cmd, 10, 5));
    CHECK(ll_lua_save_session(cmd, sizeof cmd) && !strcmp(cmd, "Game:Save(\"livelink\", true)"));
    CHECK(ll_lua_load_session(cmd, sizeof cmd) && !strcmp(cmd, "Game:Load(\"livelink\", true, true)"));
    CHECK(ll_lua_selection(cmd, sizeof cmd) && strstr(cmd, "Selection.Object and"));
    strcpy(f[0], "Building/BuildCost"), strcpy(f[1], "Text");
    CHECK(ll_lua_read_asset(cmd, sizeof cmd, 5, f, 2) && strstr(cmd, "AssetData(5).Building.BuildCost") && strstr(cmd, " .. \"\\n\" .. ") && strstr(cmd, "AssetData(5).Text"));
    strcpy(f[0], "a/b c"); /* a value path is identifiers only: nothing else goes into the Lua */
    CHECK(!ll_lua_read_asset(cmd, sizeof cmd, 5, f, 1));
    strcpy(f[0], "x());os.exit()--");
    CHECK(!ll_lua_read_asset(cmd, sizeof cmd, 5, f, 1));
    strcpy(f[0], "1abc");
    CHECK(!ll_lua_read_asset(cmd, sizeof cmd, 5, f, 1));
    strcpy(f[0], "a//b");
    CHECK(!ll_lua_read_asset(cmd, sizeof cmd, 5, f, 1));
    strcpy(f[0], "GUID"), strcpy(f[1], "Position/x");
    CHECK(ll_lua_read_object(cmd, sizeof cmd, 77, f, 2) && strstr(cmd, "Scripts:GetObjectByID(77).GUID") && strstr(cmd, ".Position.x"));
    strcpy(f[0], "Owner");
    CHECK(!ll_lua_read_object(cmd, sizeof cmd, 77, f, 1)); /* not on the fixed list */
    CHECK(ll_selection_parse("4711,180023,5000", &o, &g, session, sizeof session) && o == 4711 && g == 180023 && !strcmp(session, "5000"));
    CHECK(!ll_selection_parse("", &o, &g, session, sizeof session) && !ll_selection_parse("1,2", &o, &g, session, sizeof session) &&
          !ll_selection_parse("1,2,x", &o, &g, session, sizeof session));
}

static void test_log(void) {
    LLPass p;
    const char *log = "[2026-11-07 23:07:38.086] [info] Search mods...\n"
                      "[2026-11-07 23:07:38.200] [warning] No matching node: /Values/Foo\r\n"
                      "[2026-11-07 23:07:39.000] [error] Can't find file to patch: data/x.xml\n"
                      "[2026-11-07 23:07:41.500] [info] Patching completed in 3s\n";
    ll_pass_scan(log, strlen(log), &p);
    CHECK(p.completed && p.seconds == 3.0 && p.warnings == 1 && p.errors == 1 && p.no_matching_node == 1 && p.missing_file == 1);
    CHECK(!strcmp(p.first_problem, "No matching node: /Values/Foo"));
    ll_pass_scan("[info] Search mods...\n", 22, &p);
    CHECK(!p.completed && !p.warnings);
    CHECK(ll_log_complete("abc\ndef", 7) == 4 && ll_log_complete("abc\n", 4) == 4 && ll_log_complete("abc", 3) == 0);
}

static void test_title(void) {
    uint16_t title[16] = {'A', 'n', 'n', 'o'}, out[64];
    int n = ll_title_mark(title, 4, out, 64, 1);
    CHECK(n == 16 && out[4] == ' ' && out[5] == '[' && out[15] == ']' && out[16] == 0);
    CHECK(ll_title_mark(out, n, out, 64, 1) == 16); /* the mark is added once */
    n = ll_title_mark(out, n, title, 16, 0);
    CHECK(n == 4 && title[3] == 'o' && title[4] == 0);
    CHECK(ll_title_mark(title, 4, out, 10, 1) == -1);
    CHECK(ll_waited_out(1000, 1500, 400) && !ll_waited_out(1000, 1300, 400) && !ll_waited_out(1000, 900, 400));
}

static void test_scenes(void) {
    uint32_t ids[] = {10, 0xffffffffu, 3, 10, 7};
    int32_t out[8];
    int unknown, seen = 0, known[] = {1, 0, 1};
    char names[3][LL_NAME_LEN] = {"Game", "Nope", "MainTitle"}, msg[200];
    CHECK(ll_scene_ids(ids, 5, out, 8, &unknown) == 3 && unknown == 1 && out[0] == 10 && out[1] == 3 && out[2] == 7);
    CHECK(ll_scene_ids(ids, 5, out, 2, &unknown) == 2); /* the room is the limit */
    CHECK(ll_scene_can_start(-1, 0) && ll_scene_can_start(0, 0) && !ll_scene_can_start(2, 0) && !ll_scene_can_start(0, 1));
    CHECK(!ll_scene_done(2, 0, &seen, 0) && !ll_scene_done(0, 1, &seen, 10) && seen == 1);
    CHECK(ll_scene_done(0, 0, &seen, 10)); /* the busy byte rose and fell */
    seen = 0;
    CHECK(!ll_scene_done(0, 0, &seen, 100) && ll_scene_done(0, 0, &seen, LL_SCENE_QUIET_MS)); /* too quick to see */
    CHECK(ll_scene_message(msg, sizeof msg, names, known, 3));
    CHECK(!strcmp(msg, "reloaded the scenes of Game, MainTitle; not a state of the game: Nope"));
    CHECK(!ll_scene_message(msg, 20, names, known, 3));
    {
        char files[2][160] = {"20261112-1.json", "x\"y.json"}, json[100];
        CHECK(ll_names_json(json, sizeof json, "files", files, 2) > 0 && !strcmp(json, "{\"files\":[\"20261112-1.json\",\"x\\\"y.json\"]}"));
    }
}

static void test_banks_and_save(void) {
    LLBankResult r[2];
    LLSaveWatch w = {-1, 0};
    char json[512];
    const char *why;
    CHECK(ll_wwise_id("Init") == 0x50c63a23u && ll_wwise_id("INIT") == LL_BANK_INIT_ID && ll_wwise_id("") == 2166136261u);
    CHECK(ll_wwise_id("MyBank") == 0x79461f55u);
    memset(r, 0, sizeof r);
    strcpy(r[0].name, "mybank"), r[0].id = 77, r[0].code = LL_BANK_DONE, r[0].ak = 1;
    strcpy(r[1].name, "other"), r[1].id = 78, r[1].code = LL_BANK_UNLOAD_FAILED, r[1].ak = 2;
    CHECK(ll_banks_json(json, sizeof json, r, 2) > 0 &&
          !strcmp(json, "{\"banks\":[{\"name\":\"mybank\",\"id\":77,\"result\":1,\"prepared\":false,\"status\":\"reloaded\"},"
                        "{\"name\":\"other\",\"id\":78,\"result\":2,\"prepared\":false,\"status\":\"not unloaded\"}]}"));
    CHECK(!strcmp(ll_banks_status(r, 2, &why), "done") && strstr(why, "other: not unloaded") && strstr(why, "result 2"));
    CHECK(!strcmp(ll_banks_status(r + 1, 1, &why), "refused"));
    r[1].code = LL_BANK_PROTECTED;
    CHECK(!strcmp(ll_banks_status(r + 1, 1, &why), "refused") && strstr(why, "Init bank"));
    r[1].code = LL_BANK_TIMEOUT;
    CHECK(!strcmp(ll_banks_status(r + 1, 1, &why), "skipped") && !strcmp(ll_banks_status(r, 0, &why), "refused"));
    CHECK(ll_banks_json(json, 40, r, 2) == -1);
    /* the save is done once its file is newer than the request and has stood still */
    CHECK(!ll_save_watch(&w, 0, 0, 1000) && !ll_save_watch(&w, 1, 500, 1100) && !ll_save_watch(&w, 1, 500, 1500));
    CHECK(!ll_save_watch(&w, 1, 900, 1600) && !ll_save_watch(&w, 1, 900, 2000)); /* still growing a moment ago */
    CHECK(ll_save_watch(&w, 1, 900, 1600 + LL_SAVE_STABLE_MS) && !ll_save_watch(&w, 0, 900, 5000) && !ll_save_watch(&w, 1, 0, 5100));
}

int main(void) {
    test_hashes();
    test_proof();
    test_frames();
    test_json();
    test_requests();
    test_needs();
    test_text();
    test_data();
    test_rate_and_gate();
    test_lua();
    test_log();
    test_title();
    test_scenes();
    test_banks_and_save();
    if (g_fail) {
        printf("livelink_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("livelink_test: ok\n");
    return 0;
}

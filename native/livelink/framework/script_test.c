/* Host test of the script binding's pure parts: the declarations (script_api.c), the stack helpers on a
   fake state (luastack.h) and the participant rules (util.c). It also writes the manifest,
   script-api.json, next to this file; run by `build.cmd test`. */
#include <stdio.h>
#include <string.h>
#include "luastack.h"
#include "script_api.h"
#include "util.h"

static int s_fail;
#define CHECK(c)                                                    \
    do {                                                            \
        if (!(c)) {                                                 \
            printf("script_test: FAILED %s (line %d)\n", #c, __LINE__); \
            s_fail = 1;                                             \
        }                                                           \
    } while (0)

/* a state: the fields the helpers use at their offsets, a call info, a stack of 16 values */
typedef struct {
    unsigned char state[0x40];
    unsigned char ci[0x20];
    unsigned char stack[16 * T_LUA_TV_SIZE];
} Fake;

static void fake_init(Fake *f, int used) {
    memset(f, 0, sizeof(*f));
    LS_FIELD(f->state, T_LUA_OFF_BASE) = f->stack;
    LS_FIELD(f->state, T_LUA_OFF_TOP) = f->stack + used * T_LUA_TV_SIZE;
    LS_FIELD(f->state, T_LUA_OFF_STACK_LAST) = f->stack + 15 * T_LUA_TV_SIZE;
    LS_FIELD(f->state, T_LUA_OFF_CI) = f->ci;
    LS_FIELD(f->ci, T_LUA_OFF_CI_TOP) = f->stack;
}

static void test_stack(void) {
    Fake f;
    void *L = f.state;
    double d = 0;
    fake_init(&f, 0);
    CHECK(ls_gettop(L) == 0 && ls_tag(L, 1) == -1);
    ls_push_number(L, 42.5);
    ls_push_bool(L, 1);
    ls_push_nil(L);
    ls_push_lightud(L, &f);
    CHECK(ls_gettop(L) == 4);
    CHECK(ls_tag(L, 1) == T_LUA_TAG_NUMBER && ls_number(L, 1, &d) && d == 42.5);
    CHECK(ls_tag(L, 2) == T_LUA_TAG_BOOLEAN && *(int *)ls_slot(L, 2) == 1);
    CHECK(ls_tag(L, 3) == T_LUA_TAG_NIL && !ls_number(L, 3, &d));
    CHECK(ls_tag(L, 4) == T_LUA_TAG_LIGHTUD && *(void **)ls_slot(L, 4) == (void *)&f);
    CHECK(LS_FIELD(f.ci, T_LUA_OFF_CI_TOP) == f.stack + 4 * T_LUA_TV_SIZE); /* the call info's top follows */
    CHECK(ls_room(L, 10) && !ls_room(L, 11));
    ls_settop(L, 1);
    CHECK(ls_gettop(L) == 1);
    ls_settop(L, 5); /* never raises the top */
    CHECK(ls_gettop(L) == 1);

    /* self skipped: T:F(7) passes the table first, T.F(7) does not */
    fake_init(&f, 0);
    ls_push_raw(L, 0, T_LUA_TAG_TABLE);
    ls_push_number(L, 7);
    CHECK(ls_first_arg(L) == 2 && ls_number(L, ls_first_arg(L), &d) && d == 7);
    fake_init(&f, 0);
    ls_push_number(L, 7);
    CHECK(ls_first_arg(L) == 1 && ls_number(L, ls_first_arg(L), &d) && d == 7);
}

static void test_decls(void) {
    char why[200];
    CHECK(hlib_script_check(why, sizeof(why)));
    if (!hlib_script_check(why, sizeof(why))) printf("script_test: %s\n", why);
    CHECK(g_script_table_count <= HLIB_SCRIPT_TABLES_MAX); /* every declared table gets registered (luaapi.c) */
    CHECK(hlib_script_table_index("HelperLibrary") == 0);
    CHECK(g_script_tables[0].module == 0); /* the core's table is always there */
    CHECK(hlib_script_fn_index("HelperLibrary", "Version") >= 0);
    CHECK(hlib_script_fn_index("HelperLink", "Connected") >= 0);
    CHECK(hlib_script_fn_index("HelperLibrary", "Stock") < 0);
    CHECK(hlib_script_table_index("Anno2070Nope") < 0);
    char small[16];
    CHECK(hlib_script_manifest(small, sizeof(small)) == -1);
}

static void test_participants(void) {
    uint32_t rival[] = {31100, 169, 167, 0};
    uint32_t hard[] = {31105, 32437, 167};
    uint32_t none[] = {41, 0};
    CHECK(hlib_rival_rank(rival, 4, 169, 32436, 32437) == 0);
    CHECK(hlib_rival_rank(hard, 3, 169, 32436, 32437) == 2);
    CHECK(hlib_rival_rank(none, 2, 169, 32436, 32437) == -1);
    CHECK(hlib_rival_rank(rival, 0, 169, 32436, 32437) == -1);
    CHECK(!hlib_kind_is_ai(HLIB_PK_HUMAN) && hlib_kind_is_ai(HLIB_PK_SECOND) && hlib_kind_is_ai(HLIB_PK_THIRD));
    CHECK(!hlib_kind_is_ai(HLIB_PK_VIRTUAL) && !hlib_kind_is_ai(-1));
}

static char s_manifest[196608];

int main(void) {
    test_stack();
    test_decls();
    test_participants();
    int n = hlib_script_manifest(s_manifest, sizeof(s_manifest));
    CHECK(n > 0);
    if (n > 0) {
        FILE *f = fopen("script-api.json", "wb");
        CHECK(f != NULL);
        if (f) {
            fwrite(s_manifest, 1, (size_t)n, f);
            fclose(f);
        }
    }
    puts(s_fail ? "script checks FAILED" : "all script checks passed, script-api.json written");
    return s_fail;
}

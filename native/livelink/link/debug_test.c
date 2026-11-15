/* Host test of the live link's debug commands (debug_logic.c): the table, the typed values, the Lua of each step, the
   probe's reading and the answers. No game needed. */
#define _CRT_SECURE_NO_WARNINGS
#include "debug_logic.h"

#include <stdio.h>
#include <string.h>

static int g_fail;
#define CHECK(c)                                                      \
    do {                                                              \
        if (!(c)) {                                                   \
            printf("debug_test: %s:%d: %s\n", __FILE__, __LINE__, #c); \
            g_fail++;                                                 \
        }                                                             \
    } while (0)

static int call_of(const char *id, const char *name, double v, int is_bool, char *call, const char **why) {
    char names[1][DBG_NAME_LEN] = {{0}};
    double num[1] = {v};
    char isbool[1] = {(char)is_bool};
    const DbgCommand *c = dbg_find(id);
    if (!name) return dbg_call(c, (const char(*)[DBG_NAME_LEN])names, num, isbool, 0, call, 200, why);
    snprintf(names[0], DBG_NAME_LEN, "%s", name);
    return dbg_call(c, (const char(*)[DBG_NAME_LEN])names, num, isbool, 1, call, 200, why);
}

static void test_table(void) {
    CHECK(dbg_count() == 30);
    CHECK(dbg_find("heal_selected") && dbg_find("heal_selected")->arg == DBG_PERCENT);
    CHECK(!dbg_find("os.exit") && !dbg_find("") && !dbg_find("Heal_selected"));
    for (int i = 0; i < dbg_count(); i++) { /* every template is one call of a fixed object, nothing free */
        const DbgCommand *c = dbg_at(i);
        CHECK(!strncmp(c->tmpl, "Cheat.GlobalCheats:", 19) || !strncmp(c->tmpl, "Cheats:", 7));
        CHECK(strchr(c->tmpl, '(') && c->tmpl[strlen(c->tmpl) - 1] == ')' && !strchr(c->tmpl, ';') && !strchr(c->tmpl, '"'));
        CHECK((c->arg == DBG_NO_ARG) == (strchr(c->tmpl, '{') == NULL));
    }
    CHECK(dbg_find("destroy_selected")->confirm && !dbg_find("heal_selected")->confirm);
}

static void test_values(void) {
    char call[200];
    const char *why;
    CHECK(call_of("ignore_building_costs", NULL, 0, 0, call, &why) && !strcmp(call, "Cheat.GlobalCheats:ToggleIgnoreBuildingCosts()"));
    CHECK(call_of("heal_selected", "percentage", 50, 0, call, &why) && !strcmp(call, "Cheats:HealSelectedObjects(50)"));
    CHECK(call_of("heal_selected", "percentage", 100, 0, call, &why) && !call_of("heal_selected", "percentage", 101, 0, call, &why));
    CHECK(!call_of("heal_selected", "percentage", -1, 0, call, &why) && !call_of("heal_selected", "percentage", 1.5, 0, call, &why));
    CHECK(!call_of("heal_selected", "percentage", 1, 1, call, &why) && !strcmp(why, "the value must be a whole number in range"));
    CHECK(!call_of("heal_selected", NULL, 0, 0, call, &why) && !strcmp(why, "a value is missing"));
    CHECK(!call_of("heal_selected", "amount", 5, 0, call, &why) && !strcmp(why, "a value the command does not take"));
    CHECK(!call_of("fps_view", "value", 1, 1, call, &why) && !strcmp(why, "a value the command does not take"));
    CHECK(call_of("invincible_selected", "value", 1, 1, call, &why) && !strcmp(call, "Cheats:SetSelectedObjectsInvincible(true)"));
    CHECK(call_of("invincible_selected", "value", 0, 1, call, &why) && strstr(call, "(false)"));
    CHECK(!call_of("invincible_selected", "value", 1, 0, call, &why) && !strcmp(why, "the value must be true or false"));
    CHECK(call_of("spawn_object", "guid", 1000000001.0, 0, call, &why) && !strcmp(call, "Cheats:SpawnGameObject(1000000001)"));
    CHECK(!call_of("spawn_object", "guid", 9.1e15, 0, call, &why) && !call_of("spawn_object", "guid", -5, 0, call, &why));
    {
        char names[2][DBG_NAME_LEN] = {"guid", "guid"};
        double num[2] = {1, 2};
        char isbool[2] = {0, 0};
        CHECK(!dbg_call(dbg_find("spawn_object"), (const char(*)[DBG_NAME_LEN])names, num, isbool, 2, call, 200, &why) &&
              !strcmp(why, "a value given twice"));
    }
}

static void test_lua(void) {
    char cmd[16384], call[200];
    const char *why;
    CHECK(call_of("heal_selected", "percentage", 50, 0, call, &why));
    CHECK(dbg_run_lua(call, cmd, sizeof cmd) && strstr(cmd, "pcall(function() Cheats:HealSelectedObjects(50) end)") && cmd[0] == '(');
    CHECK(!dbg_run_lua(call, cmd, 20));
    CHECK(dbg_state_lua(dbg_find("incidents"), cmd, sizeof cmd) && strstr(cmd, "return Cheat.GlobalCheats.IncidentsDisabled end"));
    CHECK(!dbg_state_lua(dbg_find("upgrade_check"), cmd, sizeof cmd) && !dbg_state_lua(dbg_find("heal_selected"), cmd, sizeof cmd));
    CHECK(dbg_list_lua(cmd, sizeof cmd) && strlen(cmd) < 12000 && strstr(cmd, "av = Cheat.GlobalCheats.ToggleIgnoreThreats ~= nil") &&
          strstr(cmd, "av = Cheats:") == NULL && strstr(cmd, "av = Cheats.DestroySelectedObjects ~= nil") &&
          strstr(cmd, "\"version_label,\"") && strstr(cmd, "return table.concat(o, \";\") end)()"));
    CHECK(!dbg_list_lua(cmd, 500));
    CHECK(dbg_cheats_lua(cmd, sizeof cmd) && strstr(cmd, "Cheat.CheatNames"));
    CHECK(dbg_cheat_lua(cmd, sizeof cmd, "Show_All-1.0") && strstr(cmd, "Cheat:TriggerCheatByName(\"Show_All-1.0\")"));
    CHECK(!dbg_cheat_lua(cmd, sizeof cmd, "a\"); os.exit() --") && !dbg_cheat_lua(cmd, sizeof cmd, "") && !dbg_cheat_lua(cmd, sizeof cmd, "a b"));
}

static void test_answers(void) {
    char json[8192];
    const char *probe = "ignore_building_costs,1,0;incidents,1,1;heal_selected,0,-;unknown_id,1,1;fps_view,1,-";
    CHECK(dbg_state_parse("true") == 1 && dbg_state_parse("false") == 0 && dbg_state_parse("") == -1 && dbg_state_parse("nil") == -1);
    CHECK(dbg_list_json(json, sizeof json, probe, "Fast_Build;Give-Money;bad name;a\"b;") > 0);
    CHECK(strstr(json, "\"ignore_building_costs\":{\"available\":true,\"state\":false}") &&
          strstr(json, "\"incidents\":{\"available\":true,\"state\":true}") &&
          strstr(json, "\"heal_selected\":{\"available\":false,\"state\":null}") &&
          strstr(json, "\"fps_view\":{\"available\":true,\"state\":null}") && !strstr(json, "unknown_id"));
    CHECK(strstr(json, "\"unlock_all\":{\"available\":false,\"state\":null}")); /* not named by the probe: not available */
    CHECK(strstr(json, "\"cheats\":[\"Fast_Build\",\"Give-Money\"]")); /* an unsafe name is never sent back */
    CHECK(dbg_list_json(json, sizeof json, "", "") > 0 && strstr(json, "\"cheats\":[]"));
    CHECK(dbg_list_json(json, 100, probe, "") == -1);
    CHECK(dbg_cheat_listed("a;b;c", "b") && dbg_cheat_listed("a;b;c", "c") && dbg_cheat_listed("a", "a"));
    CHECK(!dbg_cheat_listed("a;bb;c", "b") && !dbg_cheat_listed("", "a") && !dbg_cheat_listed("ab", "a"));
    CHECK(dbg_state_json(json, sizeof json, 1) > 0 && !strcmp(json, "{\"state\":true}"));
    CHECK(dbg_state_json(json, sizeof json, -1) > 0 && !strcmp(json, "{}"));
}

int main(void) {
    test_table();
    test_values();
    test_lua();
    test_answers();
    if (g_fail) {
        printf("debug_test: %d check(s) failed\n", g_fail);
        return 1;
    }
    printf("debug_test: ok\n");
    return 0;
}

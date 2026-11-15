/* The live link's one-frame requests (mod_livelink.c; livelink_module.h): the widget dump, the selection, read, the
   debug commands and the helper library's modules. Each answers before it returns. */
#include "hlib.h"
#include "debug_logic.h"
#include "livelink.h"
#include "livelink_logic.h"
#include "livelink_module.h"
#include "livereload.h"
#include "luacmd.h"
#include "modules_logic.h"
#include "stability_logic.h"
#include "targets.h"
#include "util.h"
#include <sddl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The live widget values of the open scenes (ui.dump): the UI dump probe's walk, run now for the named states (all open
   ones when none is named); the answer lists the files it wrote under log/ui-dump/, the suite reads them. */
void llq_start_dump(const LLRequest *r) {
    static char files[16][160], json[LLM_JSON_MAX];
    int ids[LL_NAMES_MAX], n = 0, got;
    uint32_t (*id_of)(const void *) = (uint32_t(*)(const void *))hlib_target(T_UI_STATE_ID);
    for (int i = 0; i < r->nnames && id_of; i++) {
        struct {
            const char *text;
            int64_t length;
        } view = {r->names[i], (int64_t)strlen(r->names[i])};
        uint32_t id = id_of(&view);
        if (id != 0xffffffffu) ids[n++] = (int)id;
    }
    if (r->nnames && !n) {
        llm_answer("skipped", "none of the names is a UI state of the game", NULL);
        return;
    }
    got = hlib_ui_dump_now(llm_ui, ids, n, files, 16);
    if (got < 0) {
        llm_answer("unavailable", "the UI dump is not in place in this game build", NULL);
    } else if (!got) {
        llm_answer("skipped", "no open scene with a widget tree: the state is not open (or has no scene)", NULL);
    } else {
        ll_names_json(json, sizeof json, "files", files, got);
        llm_answer("ok", "", json);
    }
}

void llq_run_simple(const char *cmd, const char *ok_message) {
    if (hlib_lua_run(cmd, NULL, 0)) llm_answer("ok", ok_message, NULL);
    else llm_answer("unavailable", "the game's command runner did not take the command (see the log)", NULL);
}

/* select by asset GUID: the first object of the asset's group in the current session (the group's shape is unverified) */
void llq_select_guid(int64_t guid) {
    static char text[LLM_TEXT_MAX], cmd[512];
    if (!ll_lua_select_guid(cmd, sizeof cmd, guid)) {
        llm_answer("refused", "give an asset guid", NULL);
    } else if (!hlib_lua_run(cmd, text, sizeof text)) {
        llm_answer("unavailable", "the game's command runner did not take the command (see the log)", NULL);
    } else if (!text[0]) {
        llm_answer("skipped", "no object of that asset was found in the current session (or the game gave no text back)", NULL);
    } else {
        llm_answer("ok", "selected the asset's first object", NULL);
    }
}

void llq_start_read(const LLRequest *r) {
    static char cmd[4096], text[LLM_TEXT_MAX], json[LLM_JSON_MAX];
    int ok = r->has_object ? ll_lua_read_object(cmd, sizeof cmd, r->object, (char(*)[LL_FIELD_LEN])r->fields, r->nfields)
             : r->has_guid ? ll_lua_read_asset(cmd, sizeof cmd, r->guid, (char(*)[LL_FIELD_LEN])r->fields, r->nfields) : 0;
    if (!ok) {
        llm_answer("refused", "give an asset guid or an object id, and value paths made of names (an object: its fixed list)", NULL);
        return;
    }
    if (!hlib_lua_run(cmd, text, sizeof text) || !text[0]) {
        llm_answer("unavailable", "the game returned no text for the read (the command runner gives no value in this build, or the name is wrong)", NULL);
        return;
    }
    ll_values_json(json, sizeof json, (char(*)[LL_FIELD_LEN])r->fields, r->nfields, text);
    llm_answer("ok", "", json);
}

/* The debug commands (debug.list, debug.run, debug.cheat; switch live_link_commands, a second opt-in): an id of the
   table of debug_logic.c becomes its fixed Lua template with the typed value put in, run inside pcall so that a method the
   build does not bind answers "unavailable". Nothing a client sends is Lua. */
static const char *debug_run(const char *cmd, char *text, int cap) {
    if (!hlib_lua_run(cmd, text, cap)) return "the game's command runner did not take the command (see the log)";
    if (text[0] == '1') return NULL;
    return text[0] == '0' ? "the game does not bind that command (it is not in this build)"
                          : "the game gave no text back: the command may not have run";
}

void llq_start_debug_list(void) {
    static char cmd[16384], probe[8192], cheats[4096], json[LLM_JSON_MAX];
    if (!dbg_list_lua(cmd, sizeof cmd) || !hlib_lua_run(cmd, probe, sizeof probe) || !probe[0]) {
        llm_answer("unavailable", "the game gave no text for the probe of its debug commands (see the log)", NULL);
        return;
    }
    cheats[0] = 0;
    if (dbg_cheats_lua(cmd, sizeof cmd)) hlib_lua_run(cmd, cheats, sizeof cheats);
    if (dbg_list_json(json, sizeof json, probe, cheats) < 0) llm_answer("error", "the list does not fit one answer", NULL);
    else llm_answer("ok", "", json);
}

void llq_start_debug_run(const LLRequest *r) {
    static char cmd[1024], call[256], text[256], json[256];
    const DbgCommand *c = dbg_find(r->command);
    const char *why = "";
    int state = -1;
    if (!c) {
        llm_answer("refused", "not a debug command of the table", NULL);
        return;
    }
    if (!dbg_call(c, (const char(*)[DBG_NAME_LEN])r->vname, r->vval, r->vbool, r->nvalues, call, sizeof call, &why)) {
        llm_answer("refused", why, NULL);
        return;
    }
    hlib_logf("live link: debug command %s: %s", c->id, call);
    if (!dbg_run_lua(call, cmd, sizeof cmd)) {
        llm_answer("error", "the command does not fit", NULL);
        return;
    }
    why = debug_run(cmd, text, sizeof text);
    if (why) {
        llm_answer("unavailable", why, NULL);
        return;
    }
    if (dbg_state_lua(c, cmd, sizeof cmd) && hlib_lua_run(cmd, text, sizeof text)) state = dbg_state_parse(text);
    dbg_state_json(json, sizeof json, state);
    llm_answer("done", "", json);
}

void llq_start_debug_cheat(const LLRequest *r) {
    static char cmd[1024], cheats[4096], text[256];
    const char *why;
    cheats[0] = 0;
    if (!dbg_cheat_name_ok(r->cheat) || !dbg_cheats_lua(cmd, sizeof cmd) || !hlib_lua_run(cmd, cheats, sizeof cheats) ||
        !dbg_cheat_listed(cheats, r->cheat)) {
        llm_answer("refused", "not a cheat the game lists (ask debug.list first)", NULL);
        return;
    }
    hlib_logf("live link: debug cheat %s", r->cheat);
    if (!dbg_cheat_lua(cmd, sizeof cmd, r->cheat)) {
        llm_answer("error", "the command does not fit", NULL);
        return;
    }
    why = debug_run(cmd, text, sizeof text);
    if (why) llm_answer("unavailable", why, NULL);
    else llm_answer("done", "", NULL);
}

/* The helper library's modules (modules.list, modules.set; the debug commands' switch): each with its class and
   whether it runs, and the run-time switch of an event-only module (the framework's rules). The pipe thread asks the
   framework directly: the switch withdraws subscriptions, it runs no engine work. */
void llq_start_modules_list(void) {
    static HlibModuleState m[128];
    static char json[LLM_JSON_MAX];
    int n = 0;
    for (int i = 0; i < hlib_module_count() && n < (int)(sizeof m / sizeof m[0]); i++) n += hlib_module_state(i, &m[n]);
    if (ml_list_json(json, sizeof json, m, n) < 0) llm_answer("error", "the list does not fit one answer", NULL);
    else llm_answer("ok", "", json);
}

void llq_start_modules_set(const LLRequest *r) {
    char json[160];
    const char *why = NULL;
    if (!hlib_module_set(r->module, r->on, &why)) {
        llm_answer("refused", why ? why : "not switched", NULL);
        return;
    }
    ml_set_json(json, sizeof json, r->module, r->on);
    llm_answer("done", "", json);
}

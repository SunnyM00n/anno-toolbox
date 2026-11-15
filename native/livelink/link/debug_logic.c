/* The live link's debug commands, the pure part (debug_logic.h). Host-tested by debug_test.c. */
#include "debug_logic.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define G "Cheat.GlobalCheats:"
#define S "Cheats:"

/* the same ids, templates, arguments and state fields as the suite's debugcommands.py (pipe_run.py compares them) */
static const DbgCommand TABLE[] = {
    {"ignore_building_costs", G "ToggleIgnoreBuildingCosts()", DBG_NO_ARG, "", "IgnoreBuildingCosts", 0},
    {"ignore_fertilities", G "ToggleIgnoreFertilities()", DBG_NO_ARG, "", "IgnoreFertilities", 0},
    {"ignore_threats", G "ToggleIgnoreThreats()", DBG_NO_ARG, "", "IgnoreThreats", 0},
    {"upgrade_check", G "ToggleUpgradeCheck()", DBG_NO_ARG, "", "", 0},
    {"unlock_all", G "ToggleUnlockAllForHumans()", DBG_NO_ARG, "", "", 0},
    {"productivity", G "ToggleProductivity()", DBG_NO_ARG, "", "IsProductivityCheated", 0},
    {"incidents", G "ToggleIncidents()", DBG_NO_ARG, "", "IncidentsDisabled", 0},
    {"happy_days", G "ToggleHappyDaysDisabled()", DBG_NO_ARG, "", "", 0},
    {"super_ship_speed", G "ToggleSuperShipSpeed()", DBG_NO_ARG, "", "SuperShipSpeedEnabled", 0},
    {"undiscovered", G "DisableUndiscovered()", DBG_NO_ARG, "", "IsUndiscoveredDisabled", 0},
    {"win_lose", G "DisableWinLoseConditions()", DBG_NO_ARG, "", "IsWinLoseConditionsDisabled", 0},
    {"construction_ai", G "ToggleConstructionAIEnabled()", DBG_NO_ARG, "", "ConstructionAIEnabled", 0},
    {"attack_view", G "ToggleAttackDebugView()", DBG_NO_ARG, "", "IsAttackDebugView", 0},
    {"los_view", G "ToggleLOSDebugView()", DBG_NO_ARG, "", "IsLOSDebugView", 0},
    {"debug_zoom", G "ToggleUseDebugCameraZoomLevels()", DBG_NO_ARG, "", "", 0},
    {"letterboxing", G "ToggleLetterboxingDisabled()", DBG_NO_ARG, "", "", 0},
    {"banners", G "ToggleBlockBannerNotifications()", DBG_NO_ARG, "", "", 0},
    {"debug_drawings", G "ToggleSuppressDebugDrawings()", DBG_NO_ARG, "", "", 0},
    {"heal_selected", S "HealSelectedObjects({percentage})", DBG_PERCENT, "percentage", "", 0},
    {"damage_selected", S "DamageSelectedObjects({percentage})", DBG_PERCENT, "percentage", "", 0},
    {"invincible_selected", S "SetSelectedObjectsInvincible({value})", DBG_FLAG, "value", "", 0},
    {"destroy_selected", S "DestroySelectedObjects()", DBG_NO_ARG, "", "", 1},
    {"spawn_object", S "SpawnGameObject({guid})", DBG_GUID, "guid", "", 0},
    {"build_mode", S "StartBuildMode({guid})", DBG_GUID, "guid", "", 0},
    {"show_notification", S "ShowNotification({guid})", DBG_GUID, "guid", "", 0},
    {"show_text_popup", S "ShowTextPopup({guid})", DBG_GUID, "guid", "", 0},
    {"show_collectables", S "ShowAllCollectables({value})", DBG_FLAG, "value", "", 0},
    {"fps_view", S "ToggleFpsView()", DBG_NO_ARG, "", "", 0},
    {"debug_ui", S "ToggleDebugUI()", DBG_NO_ARG, "", "", 0},
    {"version_label", S "ToggleVersionLabel()", DBG_NO_ARG, "", "", 0},
};
#define COUNT ((int)(sizeof TABLE / sizeof TABLE[0]))

int dbg_count(void) { return COUNT; }
const DbgCommand *dbg_at(int i) { return i >= 0 && i < COUNT ? &TABLE[i] : NULL; }

const DbgCommand *dbg_find(const char *id) {
    for (int i = 0; i < COUNT; i++)
        if (!strcmp(TABLE[i].id, id)) return &TABLE[i];
    return NULL;
}

int dbg_call(const DbgCommand *c, const char (*names)[DBG_NAME_LEN], const double *num, const char *isbool, int n, char *call,
             int cap, const char **why) {
    char value[40];
    const char *at;
    int have = -1;
    *why = "";
    for (int i = 0; i < n; i++) {
        if (c->arg == DBG_NO_ARG || strcmp(names[i], c->arg_name)) {
            *why = "a value the command does not take";
            return 0;
        }
        have = i;
    }
    if (n > 1) {
        *why = "a value given twice";
        return 0;
    }
    if (c->arg == DBG_NO_ARG) {
        if (snprintf(call, (size_t)cap, "%s", c->tmpl) >= cap) return *why = "the call does not fit", 0;
        return 1;
    }
    if (have < 0) return *why = "a value is missing", 0;
    if (c->arg == DBG_FLAG) {
        if (!isbool[have]) return *why = "the value must be true or false", 0;
        snprintf(value, sizeof value, "%s", num[have] != 0.0 ? "true" : "false");
    } else {
        double v = num[have];
        double limit = c->arg == DBG_PERCENT ? 100.0 : 9007199254740992.0; /* 2^53 */
        if (isbool[have] || v < 0 || v > limit || v != (double)(int64_t)v) return *why = "the value must be a whole number in range", 0;
        snprintf(value, sizeof value, "%lld", (long long)v);
    }
    at = strchr(c->tmpl, '{');
    if (!at || !strchr(at, '}')) return *why = "the template has no place for the value", 0;
    if (snprintf(call, (size_t)cap, "%.*s%s%s", (int)(at - c->tmpl), c->tmpl, value, strchr(at, '}') + 1) >= cap)
        return *why = "the call does not fit", 0;
    return 1;
}

static int put(char *cmd, int cap, const char *fmt, ...) {
    va_list ap;
    int n;
    va_start(ap, fmt);
    n = vsnprintf(cmd, (size_t)cap, fmt, ap);
    va_end(ap);
    return n >= 0 && n < cap;
}

int dbg_run_lua(const char *call, char *cmd, int cap) {
    return put(cmd, cap, "(function() local ok = pcall(function() %s end) return ok and \"1\" or \"0\" end)()", call);
}

int dbg_state_lua(const DbgCommand *c, char *cmd, int cap) {
    if (!c->state_field[0]) return 0;
    {
        char object[48];
        const char *colon = strchr(c->tmpl, ':');
        snprintf(object, sizeof object, "%.*s", (int)(colon - c->tmpl), c->tmpl);
        return put(cmd, cap,
                   "(function() local ok, v = pcall(function() return %s.%s end) if ok and v ~= nil then return tostring(v) end "
                   "return \"\" end)()",
                   object, c->state_field);
    }
}

int dbg_list_lua(char *cmd, int cap) {
    int n = put(cmd, cap, "(function() local o = {} ");
    int len = n ? (int)strlen(cmd) : 0;
    if (!n) return 0;
    for (int i = 0; i < COUNT; i++) {
        const DbgCommand *c = &TABLE[i];
        char object[48], method[64];
        const char *colon = strchr(c->tmpl, ':'), *paren = strchr(c->tmpl, '(');
        snprintf(object, sizeof object, "%.*s", (int)(colon - c->tmpl), c->tmpl);
        snprintf(method, sizeof method, "%.*s", (int)(paren - colon - 1), colon + 1);
        if (!put(cmd + len, cap - len,
                 "do local av, st = false, \"-\" pcall(function() av = %s.%s ~= nil end) ", object, method))
            return 0;
        len += (int)strlen(cmd + len);
        if (c->state_field[0]) {
            if (!put(cmd + len, cap - len, "pcall(function() local v = %s.%s if v ~= nil then st = v and \"1\" or \"0\" end end) ", object,
                     c->state_field))
                return 0;
            len += (int)strlen(cmd + len);
        }
        if (!put(cmd + len, cap - len, "o[#o + 1] = \"%s,\" .. (av and \"1\" or \"0\") .. \",\" .. st end ", c->id)) return 0;
        len += (int)strlen(cmd + len);
    }
    return put(cmd + len, cap - len, "return table.concat(o, \";\") end)()");
}

int dbg_cheats_lua(char *cmd, int cap) {
    return put(cmd, cap,
               "(function() local ok, t = pcall(function() return Cheat.CheatNames end) if not ok or not t then return \"\" end "
               "local o = {} for i = 1, #t do o[#o + 1] = tostring(t[i]) end return table.concat(o, \";\") end)()");
}

int dbg_cheat_lua(char *cmd, int cap, const char *name) {
    if (!dbg_cheat_name_ok(name)) return 0;
    return put(cmd, cap,
               "(function() local ok = pcall(function() Cheat:TriggerCheatByName(\"%s\") end) return ok and \"1\" or \"0\" end)()",
               name);
}

int dbg_state_parse(const char *text) { return !strcmp(text, "true") ? 1 : !strcmp(text, "false") ? 0 : -1; }

int dbg_cheat_name_ok(const char *name) {
    size_t n = strlen(name);
    if (!n || n >= DBG_NAME_LEN) return 0;
    for (; *name; name++) {
        char ch = *name;
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '.' || ch == '-'))
            return 0;
    }
    return 1;
}

int dbg_cheat_listed(const char *cheats, const char *name) {
    size_t n = strlen(name);
    for (const char *p = cheats; *p;) {
        const char *end = strchr(p, ';');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        if (len == n && !strncmp(p, name, n)) return 1;
        p += len + (end ? 1 : 0);
        if (!end) break;
    }
    return 0;
}

static int append(char *out, size_t cap, size_t *at, const char *fmt, ...) {
    va_list ap;
    int n;
    if (*at >= cap) return 0;
    va_start(ap, fmt);
    n = vsnprintf(out + *at, cap - *at, fmt, ap);
    va_end(ap);
    if (n < 0 || *at + (size_t)n >= cap) return 0;
    *at += (size_t)n;
    return 1;
}

/* one "id,a,s" entry of the probe text for an id: 1 and a, s set; 0 when the probe does not name it */
static int probe_of(const char *probe, const char *id, char *a, char *s) {
    size_t n = strlen(id);
    for (const char *p = probe; *p;) {
        const char *end = strchr(p, ';');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        if (len >= n + 4 && !strncmp(p, id, n) && p[n] == ',' && p[n + 2] == ',') {
            *a = p[n + 1], *s = p[n + 3];
            return 1;
        }
        if (!end) break;
        p = end + 1;
    }
    return 0;
}

int dbg_list_json(char *out, size_t cap, const char *probe, const char *cheats) {
    size_t at = 0;
    int first = 1;
    if (!append(out, cap, &at, "{\"commands\":{")) return -1;
    for (int i = 0; i < COUNT; i++) {
        char a = '0', s = '-';
        if (!probe_of(probe, TABLE[i].id, &a, &s)) a = '0';
        if (!append(out, cap, &at, "%s\"%s\":{\"available\":%s,\"state\":%s}", first ? "" : ",", TABLE[i].id, a == '1' ? "true" : "false",
                    s == '1' ? "true" : s == '0' ? "false" : "null"))
            return -1;
        first = 0;
    }
    if (!append(out, cap, &at, "},\"cheats\":[")) return -1;
    first = 1;
    for (const char *p = cheats; *p;) {
        const char *end = strchr(p, ';');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        char name[DBG_NAME_LEN];
        if (len > 0 && len < sizeof name) {
            memcpy(name, p, len), name[len] = 0;
            if (dbg_cheat_name_ok(name) && !append(out, cap, &at, "%s\"%s\"", first ? "" : ",", name)) return -1;
            if (dbg_cheat_name_ok(name)) first = 0;
        }
        if (!end) break;
        p = end + 1;
    }
    return append(out, cap, &at, "]}") ? (int)at : -1;
}

int dbg_state_json(char *out, size_t cap, int state) {
    int n = state < 0 ? snprintf(out, cap, "{}") : snprintf(out, cap, "{\"state\":%s}", state ? "true" : "false");
    return n > 0 && (size_t)n < cap ? n : -1;
}

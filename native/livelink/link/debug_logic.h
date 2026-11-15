/* The live link's debug commands, the pure part (mod_livelink.c is the engine side): the closed table that maps a
   command id to its fixed Lua template (the same ids and templates as the suite's suite/debugcommands.py;
   pipe_run.py compares them), the check of a request's typed values, the Lua the game side runs (every one of them
   inside pcall, so a method the build does not bind answers "unavailable" and never crashes), and the answers' data.
   No engine call, no Windows call. */
#ifndef DEBUG_LOGIC_H
#define DEBUG_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define DBG_NAME_LEN 64
#define DBG_VALUES_MAX 8

enum { DBG_NO_ARG, DBG_PERCENT, DBG_GUID, DBG_FLAG };

typedef struct {
    const char *id;
    const char *tmpl;        /* "Object:Method(...)": {percentage}, {guid} or {value} is where the typed value goes */
    int arg;                 /* DBG_* of the one argument, DBG_NO_ARG for none */
    const char *arg_name;    /* "percentage", "guid" or "value" */
    const char *state_field; /* the game's field that holds a toggle's state ("" none) */
    int confirm;             /* destructive: the suite asks first (the game does not) */
} DbgCommand;

int dbg_count(void);
const DbgCommand *dbg_at(int i);
const DbgCommand *dbg_find(const char *id); /* NULL when it is not in the table */

/* The typed values of a request: names[i], num[i] and isbool[i] (a JSON true or false). 1 and the call text (the
   template with the value put in) in call; else 0 and the sentence why. Every argument present, of its kind and in
   range, nothing else. */
int dbg_call(const DbgCommand *c, const char (*names)[DBG_NAME_LEN], const double *num, const char *isbool, int n, char *call,
             int cap, const char **why);

/* the Lua of each step; 1 when it fits */
int dbg_run_lua(const char *call, char *cmd, int cap);       /* runs the call inside pcall: the text "1" (it ran) or "0" */
int dbg_state_lua(const DbgCommand *c, char *cmd, int cap);   /* the toggle's state as text; 0 when it has no field */
int dbg_list_lua(char *cmd, int cap);                         /* every command's availability and state: "id,a,s;..." */
int dbg_cheats_lua(char *cmd, int cap);                       /* the game's registered cheat names: "a;b;c" */
int dbg_cheat_lua(char *cmd, int cap, const char *name);      /* TriggerCheatByName inside pcall; name already checked */

/* "true" -> 1, "false" -> 0, anything else -> -1 */
int dbg_state_parse(const char *text);
/* A cheat name the link may send back: a letter, digit, '_', '.', '-' only, at most 63 characters. */
int dbg_cheat_name_ok(const char *name);
/* 1 when name is one of the ';' separated names of the game's own list */
int dbg_cheat_listed(const char *cheats, const char *name);

/* {"commands":{"<id>":{"available":bool,"state":bool|null},...},"cheats":["name",...]}; every id of the table, those
   the probe did not name as unavailable. The length, or -1 when it does not fit. */
int dbg_list_json(char *out, size_t cap, const char *probe, const char *cheats);
/* {"state":true|false} or {} */
int dbg_state_json(char *out, size_t cap, int state);

#endif

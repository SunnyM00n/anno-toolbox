/* The helper library's script surface, declared once: the global table of each module and every function in it, with
   its kind, arguments, results and meaning. The binding layer exposes only what is declared here and the host test
   writes the manifest from the same list, so panels and the library cannot drift apart. No game dependency. */
#ifndef HLIB_SCRIPT_API_H
#define HLIB_SCRIPT_API_H

#define HLIB_SCRIPT_VERSION 2 /* what HelperLibrary.Version() answers; bump when a declaration changes */

enum { HLIB_SCRIPT_READOUT = 0, HLIB_SCRIPT_ACTION = 1 };

typedef struct {
    const char *table;  /* the global table: Helper<Name> of the core, Anno2070<Module> of a module */
    const char *module; /* feature key whose module must be running, 0 = always there (the core's own) */
    const char *doc;
} HlibScriptTableDecl;

typedef struct {
    const char *table;
    const char *name;
    int kind;
    const char *args;    /* comma-separated names, "" for none; engine ids and GUIDs as numbers */
    const char *returns; /* comma-separated types: number or boolean */
    const char *doc;
} HlibScriptFnDecl;

extern const HlibScriptTableDecl g_script_tables[];
extern const int g_script_table_count;
/* the tables the binding layer keeps a revision for and registers (luaapi.c); hlib_script_check fails above it */
#define HLIB_SCRIPT_TABLES_MAX 64
extern const HlibScriptFnDecl g_script_fns[];
extern const int g_script_fn_count;

/* Every table gets this readout from the binding layer itself, so no module declares it. */
#define HLIB_SCRIPT_REVISION "Revision"

int hlib_script_table_index(const char *table);                 /* -1 if not declared */
int hlib_script_fn_index(const char *table, const char *name); /* -1 if not declared */
/* 1 when the declarations are well formed; otherwise 0 and the reason in why. */
int hlib_script_check(char *why, int cap);
/* The manifest as JSON; its length, or -1 when cap is too small. */
int hlib_script_manifest(char *out, int cap);

#endif

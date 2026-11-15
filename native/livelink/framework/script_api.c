/* The declarations of the script surface and the manifest writer (script_api.h). A module adds its table and
   functions here, then binds its C functions to the names in install() (recipe). */
#include "script_api.h"

const HlibScriptTableDecl g_script_tables[] = {
    {"HelperLibrary", 0, "the library itself: present whenever it runs, whatever is switched on"},
    {"HelperLink", 0, "the live link to the tool suite (the companion mod's key binding and label): whether it is connected, sending the selected object to it"},
};
const int g_script_table_count = (int)(sizeof(g_script_tables) / sizeof(g_script_tables[0]));

const HlibScriptFnDecl g_script_fns[] = {
    {"HelperLink", "Connected", HLIB_SCRIPT_READOUT, "", "boolean", "true: the tool suite is connected to the live link"},
    {"HelperLink", "EditSelected", HLIB_SCRIPT_ACTION, "", "boolean",
     "sends the selected object to the suite as an edit request (as Ctrl+Shift+E does); true when it was queued"},
    {"HelperLibrary", "Version", HLIB_SCRIPT_READOUT, "", "number", "the version of this script surface"},
    {"HelperLibrary", "Available", HLIB_SCRIPT_READOUT, "", "boolean", "true: the library runs"},
    {"HelperLibrary", "Enabled", HLIB_SCRIPT_READOUT, "module", "boolean",
     "whether the feature module of that config key runs; a panel's root visibility binds to it"},
    {"HelperLibrary", "LocalOwner", HLIB_SCRIPT_READOUT, "", "number",
     "GUID of the participant this client plays, 0 outside a game"},
    {"HelperLibrary", "ParticipantCount", HLIB_SCRIPT_READOUT, "", "number", "how many participants the game has"},
    {"HelperLibrary", "ParticipantAt", HLIB_SCRIPT_READOUT, "index", "number, number, boolean, number",
     "participant 1..count: GUID, kind (0 human, 1 rival, 2 third party, 3 virtual), AI, difficulty "
     "(0 easy, 1 medium, 2 hard, -1 none)"},
};
const int g_script_fn_count = (int)(sizeof(g_script_fns) / sizeof(g_script_fns[0]));

static int str_eq(const char *a, const char *b) {
    for (; *a && *a == *b; a++, b++) {
    }
    return *a == *b;
}

int hlib_script_table_index(const char *table) {
    for (int i = 0; i < g_script_table_count; i++)
        if (str_eq(g_script_tables[i].table, table)) return i;
    return -1;
}

int hlib_script_fn_index(const char *table, const char *name) {
    for (int i = 0; i < g_script_fn_count; i++)
        if (str_eq(g_script_fns[i].table, table) && str_eq(g_script_fns[i].name, name)) return i;
    return -1;
}

typedef struct {
    char *p;
    int n, cap, over;
} Out;

static void put(Out *o, const char *s) {
    for (; *s; s++) {
        if (o->n + 1 >= o->cap) {
            o->over = 1;
            return;
        }
        o->p[o->n++] = *s;
    }
    o->p[o->n] = 0;
}

static void put_str(Out *o, const char *s) {
    char c[3] = {0, 0, 0};
    put(o, "\"");
    for (; *s; s++) {
        c[0] = *s == '"' || *s == '\\' ? '\\' : *s;
        c[1] = c[0] == '\\' ? *s : 0;
        put(o, c);
    }
    put(o, "\"");
}

/* a comma-separated list as a JSON array of strings */
static void put_list(Out *o, const char *s) {
    char item[64];
    int k = 0, first = 1;
    put(o, "[");
    for (;; s++) {
        if (*s == ',' || !*s) {
            while (k && item[k - 1] == ' ') k--;
            item[k] = 0;
            if (k) {
                put(o, first ? "" : ", ");
                put_str(o, item);
                first = 0;
            }
            k = 0;
            if (!*s) break;
        } else if (k < (int)sizeof(item) - 1 && !(k == 0 && *s == ' ')) {
            item[k++] = *s;
        }
    }
    put(o, "]");
}

static void put_fn(Out *o, const char *name, int kind, const char *args, const char *rets, const char *doc, int last) {
    put(o, "        {\"name\": ");
    put_str(o, name);
    put(o, kind == HLIB_SCRIPT_ACTION ? ", \"kind\": \"action\", \"args\": " : ", \"kind\": \"readout\", \"args\": ");
    put_list(o, args);
    put(o, ", \"returns\": ");
    put_list(o, rets);
    put(o, ", \"doc\": ");
    put_str(o, doc);
    put(o, last ? "}\n" : "},\n");
}

int hlib_script_manifest(char *out, int cap) {
    Out o = {out, 0, cap, 0};
    if (cap < 1) return -1;
    out[0] = 0;
    put(&o, "{\n  \"about\": \"The helper library's script functions: one global table per module, called with a colon "
            "(the table as self) or a dot. Written by the library's host test from its declarations; do not edit.\",\n");
    put(&o, "  \"version\": ");
    char v[12];
    int n = 0, x = HLIB_SCRIPT_VERSION;
    do v[n++] = (char)('0' + x % 10); while ((x /= 10) && n < 11);
    for (int i = 0; i < n / 2; i++) {
        char t = v[i];
        v[i] = v[n - 1 - i];
        v[n - 1 - i] = t;
    }
    v[n] = 0;
    put(&o, v);
    put(&o, ",\n  \"tables\": [\n");
    for (int t = 0; t < g_script_table_count; t++) {
        const HlibScriptTableDecl *d = &g_script_tables[t];
        put(&o, "    {\"table\": ");
        put_str(&o, d->table);
        put(&o, ", \"module\": ");
        if (d->module)
            put_str(&o, d->module);
        else
            put(&o, "null");
        put(&o, ", \"doc\": ");
        put_str(&o, d->doc);
        put(&o, ",\n      \"functions\": [\n");
        int last = -1;
        for (int i = 0; i < g_script_fn_count; i++)
            if (str_eq(g_script_fns[i].table, d->table)) last = i;
        put_fn(&o, HLIB_SCRIPT_REVISION, HLIB_SCRIPT_READOUT, "", "number",
               "bumped whenever anything this table shows changed; a binding refreshes on a change", last < 0);
        for (int i = 0; i < g_script_fn_count; i++) {
            const HlibScriptFnDecl *f = &g_script_fns[i];
            if (str_eq(f->table, d->table)) put_fn(&o, f->name, f->kind, f->args, f->returns, f->doc, i == last);
        }
        put(&o, t + 1 < g_script_table_count ? "      ]},\n" : "      ]}\n");
    }
    put(&o, "  ]\n}\n");
    return o.over ? -1 : o.n;
}

static int fail(char *why, int cap, const char *a, const char *b) {
    Out o = {why, 0, cap, 0};
    if (cap > 0) why[0] = 0;
    put(&o, a);
    put(&o, b);
    return 0;
}

/* number, boolean, or string: a text the game itself made (the island's name), never words of the library's own */
static int is_type(const char *s, int n) {
    return (n == 6 && s[0] == 'n' && s[1] == 'u' && s[2] == 'm' && s[3] == 'b' && s[4] == 'e' && s[5] == 'r') ||
           (n == 7 && s[0] == 'b' && s[1] == 'o' && s[2] == 'o' && s[3] == 'l' && s[4] == 'e' && s[5] == 'a' &&
            s[6] == 'n') ||
           (n == 6 && s[0] == 's' && s[1] == 't' && s[2] == 'r' && s[3] == 'i' && s[4] == 'n' && s[5] == 'g');
}

int hlib_script_check(char *why, int cap) {
    if (g_script_table_count > HLIB_SCRIPT_TABLES_MAX) return fail(why, cap, "more tables than the binding keeps: ", "raise HLIB_SCRIPT_TABLES_MAX");
    for (int t = 0; t < g_script_table_count; t++) {
        /* the library's own tables are Helper<Name>, a module's carry its family's prefix */
        const char *name = g_script_tables[t].table, *pre = g_script_tables[t].module ? "Anno2070" : "Helper";
        for (int k = 0; pre[k]; k++)
            if (name[k] != pre[k]) return fail(why, cap, "table name without its prefix (Helper, Anno2070): ", name);
        if (hlib_script_table_index(name) != t) return fail(why, cap, "table declared twice: ", name);
    }
    for (int i = 0; i < g_script_fn_count; i++) {
        const HlibScriptFnDecl *f = &g_script_fns[i];
        if (hlib_script_table_index(f->table) < 0) return fail(why, cap, "function of an undeclared table: ", f->name);
        if (hlib_script_fn_index(f->table, f->name) != i) return fail(why, cap, "function declared twice: ", f->name);
        if (str_eq(f->name, HLIB_SCRIPT_REVISION)) return fail(why, cap, "Revision is added by the binding: ", f->table);
        if (f->name[0] < 'A' || f->name[0] > 'Z') return fail(why, cap, "function names start upper case: ", f->name);
        if (f->kind != HLIB_SCRIPT_READOUT && f->kind != HLIB_SCRIPT_ACTION) return fail(why, cap, "bad kind: ", f->name);
        if (!f->doc[0]) return fail(why, cap, "function without a meaning: ", f->name);
        if (!f->returns[0]) return fail(why, cap, "function without a result: ", f->name);
        const char *s = f->returns;
        while (*s) {
            while (*s == ' ' || *s == ',') s++;
            int n = 0;
            while (s[n] && s[n] != ',' && s[n] != ' ') n++;
            if (n && !is_type(s, n)) return fail(why, cap, "results are numbers, booleans or game texts: ", f->name);
            s += n;
        }
    }
    return 1;
}

/* Script binding layer. When the engine creates its script state, it registers its own globals and every class
   binding and then returns; an observer on that function's exit adds the library's tables to the fresh state, on the
   engine's thread and under the engine's own script lock, the same moment the game's own bindings appear. A new
   state (the engine creates it again) gets the tables again. Should the state exist before the observer is placed,
   the first script call of one of four common bindings hands it over instead. */
#include "hlib.h"
#include "targets.h"
#include "luastack.h"
#include "script_api.h"
#include "livelink.h"

typedef void *(*MakeFn)(void *L, void *fn, const char *name, int nupvals, long long extra);
typedef void (*PushStr)(void *L, const char *s, unsigned long long len);
typedef void (*SetEntry)(void *L, int table_index);
typedef void *(*NewTable)(void *L, int narr, int nrec);
typedef void *(*PushValue)(void *L, int idx);
typedef int (*CheckStack)(void *L, int n);
typedef void (*SetTop)(void *L, int idx);
typedef void *(*Index2Adr)(void *L, int idx);
typedef const char *(*ToLString)(void *L, int idx, unsigned long long *len);
typedef int (*MtxFn)(void *mtx);

static int str_len(const char *s) {
    int n = 0;
    while (s[n]) n++;
    return n;
}

/* the stack helpers (the module side in hlib.h) */

static int new_table(void *L, int nrec) {
    NewTable nt = (NewTable)hlib_target(T_LUA_CREATETABLE);
    if (!L || !nt) return 0;
    nt(L, 0, nrec);
    return 1;
}

/* The globals table of a state, 0 when the pseudo index gives none. The engine refuses a store into a read-only
   table with a script error, which outside a protected call would end the game. */
static unsigned char *globals_table(void *L) {
    Index2Adr adr = (Index2Adr)hlib_target(T_LUA_INDEX2ADR);
    unsigned char *g = adr ? (unsigned char *)adr(L, T_LUA_GLOBALS_INDEX) : NULL;
    if (!g || *(int *)(g + T_LUA_OFF_TAG) != T_LUA_TAG_TABLE) return NULL;
    return *(unsigned char **)g;
}

static int globals_writable(void *L) {
    unsigned char *t = globals_table(L);
    return t && !t[T_LUA_OFF_TABLE_READONLY];
}

/* _G[name] = the table on top, which stays on the stack's top as it was */
static int set_global(void *L, const char *name) {
    PushStr push = (PushStr)hlib_target(T_LUA_PUSHLSTRING);
    PushValue pv = (PushValue)hlib_target(T_LUA_PUSHVALUE);
    SetEntry set = (SetEntry)hlib_target(T_LUA_SETTABLE_TOP);
    if (!L || !push || !pv || !set || !globals_writable(L)) return 0;
    push(L, name, (unsigned long long)str_len(name));
    pv(L, -2);
    set(L, T_LUA_GLOBALS_INDEX);
    return 1;
}

static int room(void *L, int n) {
    CheckStack cs = (CheckStack)hlib_target(T_LUA_CHECKSTACK);
    return ls_room(L, n) || (cs && cs(L, n));
}

int hlib_lua_pushnumber(void *L, double v) {
    if (!room(L, 1)) return 0;
    ls_push_number(L, v);
    return 1;
}

int hlib_lua_pushbool(void *L, int b) {
    if (!room(L, 1)) return 0;
    ls_push_bool(L, b);
    return 1;
}

int hlib_lua_pushstring(void *L, const char *s, int len) {
    PushStr push = (PushStr)hlib_target(T_LUA_PUSHLSTRING);
    if (!push || !s || len < 0 || !room(L, 1)) return 0;
    push(L, s, (unsigned long long)len);
    return 1;
}

int hlib_lua_arg(void *L, int n, double *out) { return ls_number(L, ls_first_arg(L) + n - 1, out); }

double hlib_lua_num(void *L, int n) {
    double v = 0;
    hlib_lua_arg(L, n, &v);
    return v;
}

int hlib_lua_argstring(void *L, int n, char *out, int cap) {
    ToLString tls = (ToLString)hlib_target(T_LUA_TOLSTRING);
    int i = ls_first_arg(L) + n - 1;
    if (cap < 1) return 0;
    out[0] = 0;
    if (!tls || ls_tag(L, i) != T_LUA_TAG_STRING) return 0; /* a string only: no type change, no collection step */
    unsigned long long len = 0;
    const char *s = tls(L, i, &len);
    int k = 0;
    for (; s && k < cap - 1 && (unsigned long long)k < len; k++) out[k] = s[k];
    out[k] = 0;
    return 1;
}

/* the declared functions: bound C functions behind one guarded entry */

#define MAX_BOUND 768
typedef struct {
    int decl;
    HlibScriptFn fn;
    volatile LONG dead;
} Bound;
static Bound s_bound[MAX_BOUND];
static int s_nbound;
#define MAX_TABLES HLIB_SCRIPT_TABLES_MAX /* script_api.c declares the tables; its check fails above this */
static volatile LONG s_revision[MAX_TABLES];

int hlib_script_bind(const char *table, const char *name, HlibScriptFn fn) {
    int d = hlib_script_fn_index(table, name);
    if (d < 0) {
        hlib_logf("script: %s.%s is not declared in script_api.c, not bound", table, name);
        return 0;
    }
    for (int i = 0; i < s_nbound; i++)
        if (s_bound[i].decl == d) {
            s_bound[i].fn = fn;
            return 1;
        }
    if (s_nbound >= MAX_BOUND) return 0;
    s_bound[s_nbound].decl = d;
    s_bound[s_nbound].fn = fn;
    s_nbound++;
    return 1;
}

void hlib_script_changed(const char *table) {
    int t = hlib_script_table_index(table);
    if (t >= 0 && t < MAX_TABLES) InterlockedIncrement(&s_revision[t]);
}

/* as a script sees it: the function only while its table's module runs, none once it was muted */
HlibScriptFn hlib_script_find(const char *table, const char *name) {
    int t = hlib_script_table_index(table), d = hlib_script_fn_index(table, name);
    const char *m = t >= 0 ? g_script_tables[t].module : NULL;
    if (d < 0 || (m && !hlib_module_active(m))) return NULL;
    for (int i = 0; i < s_nbound; i++)
        if (s_bound[i].decl == d) return s_bound[i].dead ? NULL : s_bound[i].fn;
    return NULL;
}

long hlib_script_revision(const char *table) {
    int t = hlib_script_table_index(table);
    return t >= 0 && t < MAX_TABLES ? s_revision[t] : 0;
}

static void *upvalue_1(void *L) {
    Index2Adr adr = (Index2Adr)hlib_target(T_LUA_INDEX2ADR);
    unsigned char *v = adr ? (unsigned char *)adr(L, T_LUA_UPVALUE_1) : NULL;
    return v && *(int *)(v + T_LUA_OFF_TAG) == T_LUA_TAG_LIGHTUD ? *(void **)v : NULL;
}

/* A script error travels as a C++ exception; it passes through (the core's filter), every other fault stops here. */
static int call_bound(void *L) {
    Bound *b = (Bound *)upvalue_1(L);
    int top = ls_gettop(L), n = 0;
    if (!b || !b->fn || b->dead) return 0;
    __try {
        n = b->fn(L);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        if (0 == InterlockedExchange(&b->dead, 1))
            hlib_logf("script: fault in %s.%s, it answers nothing from now on", g_script_fns[b->decl].table,
                       g_script_fns[b->decl].name);
        ls_settop(L, top);
        return 0;
    }
    return n < 0 || n > ls_gettop(L) - top ? 0 : n;
}

static int call_revision(void *L) {
    volatile LONG *r = (volatile LONG *)upvalue_1(L);
    return r ? hlib_lua_pushnumber(L, (double)*r) : 0;
}

/* t[name] = closure(fn, upvalue p), for the table on top */
static int add_closure(void *L, const char *name, void *fn, void *p) {
    PushStr push = (PushStr)hlib_target(T_LUA_PUSHLSTRING);
    MakeFn make = (MakeFn)hlib_target(T_LUA_REGISTER_CFUNCTION);
    SetEntry set = (SetEntry)hlib_target(T_LUA_SETTABLE_TOP);
    if (!push || !make || !set || !room(L, 3)) return 0;
    push(L, name, (unsigned long long)str_len(name));
    ls_push_lightud(L, p);
    make(L, fn, name, 1, 0);
    set(L, -3);
    return 1;
}

/* the core's own table */

static int nat_version(void *L) { return hlib_lua_pushnumber(L, HLIB_SCRIPT_VERSION); }
static int nat_available(void *L) { return hlib_lua_pushbool(L, 1); }
static int nat_enabled(void *L) {
    char key[64];
    return hlib_lua_pushbool(L, hlib_lua_argstring(L, 1, key, sizeof(key)) && hlib_module_active(key));
}
static int nat_local_owner(void *L) { return hlib_lua_pushnumber(L, hlib_local_participant()); }
static int nat_participant_count(void *L) { return hlib_lua_pushnumber(L, hlib_participant_count(NULL)); }
static int nat_participant_at(void *L) {
    double i = 0;
    HlibParticipant p;
    if (!hlib_lua_arg(L, 1, &i) || !hlib_participant_info(NULL, (int)i - 1, &p)) return 0;
    hlib_lua_pushnumber(L, p.guid);
    hlib_lua_pushnumber(L, p.kind);
    hlib_lua_pushbool(L, p.is_ai);
    hlib_lua_pushnumber(L, p.difficulty);
    return 4;
}

/* the live link's two calls: always bound, they answer "not connected" while the link is off */
static int link_connected(void *L) { return hlib_lua_pushbool(L, hlib_live_link_connected()); }
static int link_edit_selected(void *L) { return hlib_lua_pushbool(L, hlib_live_link_edit_selected()); }

static void bind_native(void) {
    hlib_script_bind("HelperLink", "Connected", link_connected);
    hlib_script_bind("HelperLink", "EditSelected", link_edit_selected);
    hlib_script_bind("HelperLibrary", "Version", nat_version);
    hlib_script_bind("HelperLibrary", "Available", nat_available);
    hlib_script_bind("HelperLibrary", "Enabled", nat_enabled);
    hlib_script_bind("HelperLibrary", "LocalOwner", nat_local_owner);
    hlib_script_bind("HelperLibrary", "ParticipantCount", nat_participant_count);
    hlib_script_bind("HelperLibrary", "ParticipantAt", nat_participant_at);
}

/* registration on a state */

void hlib_dispatch_lua(void *L);

static int register_table(void *L, int t) {
    const HlibScriptTableDecl *d = &g_script_tables[t];
    int nfn = 0, missing = 0;
    if (t >= MAX_TABLES) {
        hlib_logf("script: table %s is past the %d the binding keeps, not registered", d->table, MAX_TABLES);
        return 0;
    }
    if (!new_table(L, 8)) return 0;
    add_closure(L, HLIB_SCRIPT_REVISION, (void *)call_revision, (void *)&s_revision[t]);
    for (int i = 0; i < g_script_fn_count; i++) {
        if (lstrcmpA(g_script_fns[i].table, d->table) != 0) continue;
        Bound *b = NULL;
        for (int k = 0; k < s_nbound; k++)
            if (s_bound[k].decl == i) b = &s_bound[k];
        if (!b || !b->fn) {
            missing++;
            continue;
        }
        nfn += add_closure(L, g_script_fns[i].name, (void *)call_bound, b);
    }
    int ok = set_global(L, d->table);
    ls_settop(L, ls_gettop(L) - 1);
    if (ok)
        hlib_logf("script: %s registered, Revision and %d function(s)%s", d->table, nfn,
                   missing ? " (some declared ones are not bound)" : "");
    return ok;
}

static void *s_state;     /* the state the tables went into last */
static volatile LONG s_generation;

typedef struct {
    void *L;
    int top, may_open;
} RegCall;

/* The engine's sandbox makes the main state's globals read-only once its own globals are in (a script's thread reads
   them through its own environment); at the state's creation, under the script lock, the library opens them for its
   tables and closes them again. A state met late, inside a script call, is left as it is. */
static void register_work(void *p) {
    RegCall *c = (RegCall *)p;
    void *L = c->L;
    int tables = 0;
    c->top = ls_gettop(L);
    unsigned char *g = globals_table(L);
    if (!room(L, 8) || !g) {
        hlib_logf("script: the state has %s, no tables added", g ? "no room on its stack" : "no globals table");
        return;
    }
    unsigned char closed = g[T_LUA_OFF_TABLE_READONLY];
    if (closed && !c->may_open) {
        hlib_log("script: the state's globals are read-only (the engine's sandbox), no tables added");
        return;
    }
    if (closed) g[T_LUA_OFF_TABLE_READONLY] = 0;
    __try {
        for (int t = 0; t < g_script_table_count; t++) {
            const char *m = g_script_tables[t].module;
            if (!m || hlib_module_active(m)) tables += register_table(L, t);
        }
    } __finally {
        if (closed) g[T_LUA_OFF_TABLE_READONLY] = closed;
    }
    hlib_logf("script: %d table(s) on state %d%s", tables, (int)s_generation,
               closed ? " (its read-only globals opened for them and closed again)" : "");
    hlib_dispatch_lua(L);
}

static void register_all(void *L, const char *how, int may_open) {
    RegCall c = {L, -1, may_open};
    SetTop settop = (SetTop)hlib_target(T_LUA_SETTOP);
    LONG gen = InterlockedIncrement(&s_generation);
    hlib_logf("script: state %d %s", (int)gen, how);
    s_state = L;
    hlib_guarded("script", register_work, &c);
    if (settop && c.top >= 0 && ls_gettop(L) != c.top) settop(L, c.top); /* leave the stack as the engine had it */
}

/* the capture */

static long long (*o_init)(void *engine);

static void *engine_state(void *engine) { return engine ? *(void **)((char *)engine + T_OFF_ENGINE_STATE) : NULL; }

static long long init_observer(void *engine) {
    long long r = o_init(engine);
    void *L = NULL;
    __try {
        L = engine_state(engine);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        L = NULL;
    }
    if (!L) {
        hlib_log("script: the engine created no state, nothing added");
        return r;
    }
    void *lock = hlib_target(T_DATA_SCRIPT_LOCK);
    MtxFn mlock = (MtxFn)hlib_target(T_MTX_LOCK), munlock = (MtxFn)hlib_target(T_MTX_UNLOCK);
    int locked = lock && mlock && munlock;
    if (locked) mlock(lock);
    __try {
        register_all(L, "created by the engine, adding the tables", 1);
    } __finally {
        if (locked) munlock(lock); /* an engine exception travelling out must not leave the script mutex held */
    }
    return r;
}

/* The late path: a binding's validity check runs inside a script call, so its argument is the running
   state; it hands over the state once, and only when the engine's creation was missed. */
static volatile LONG s_late_claimed;
static void late_claim(void *L) {
    if (L && !s_state && 0 == InterlockedCompareExchange(&s_late_claimed, 1, 0))
        register_all(L, "met in a script call (it existed before the library was up), adding the tables", 0);
}

#define CAPTURE(n)                        \
    static long long (*o_cap##n)(void *); \
    static long long cap##n(void *L) {    \
        late_claim(L);                    \
        return o_cap##n(L);               \
    }
CAPTURE(0)
CAPTURE(1)
CAPTURE(2)
CAPTURE(3)

/* the script engine as it is now: 0 none yet, 1 constructed, 2 its state exists */
static int engine_now(void) {
    void **g = (void **)hlib_target(T_DATA_SCRIPT_MANAGER);
    int st = 0;
    __try {
        char *mgr = g && *g ? (char *)*g - T_OFF_SCRIPTMGR_HOLDER : NULL;
        void *engine = mgr ? *(void **)(mgr + T_OFF_SCRIPTMGR_ENGINE) : NULL;
        st = !engine ? 0 : engine_state(engine) ? 2 : 1;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        st = 0;
    }
    return st;
}

static const int s_need[] = {T_LUA_REGISTER_CFUNCTION, T_LUA_PUSHLSTRING, T_LUA_SETTABLE_TOP, T_LUA_CREATETABLE,
                             T_LUA_PUSHVALUE, T_LUA_CHECKSTACK, T_LUA_SETTOP, T_LUA_INDEX2ADR,
                             T_LUA_TOLSTRING, T_LUA_ENGINE_INIT};

/* Called on every pass of the target resolution until it returns 1, so the observer is in place as
   early as its own targets allow. final: the last pass, log what is missing. */
int hlib_luaapi_install(int final) {
    for (int i = 0; i < (int)(sizeof(s_need) / sizeof(s_need[0])); i++)
        if (!hlib_target_ok(s_need[i])) {
            if (final) hlib_log("script: binding helpers not resolved on this build, no script functions added");
            return final;
        }
    bind_native();
    HlibLayer e = {T_LUA_ENGINE_INIT, HLIB_AFTER, (void *)init_observer, (void **)&o_init, 0};
    if (!hlib_layers("script", &e, 1)) {
        hlib_log("script: the state's creation could not be observed, no script functions added");
        return 1;
    }
    int now = engine_now();
    if (!hlib_target_ok(T_MTX_LOCK) || !hlib_target_ok(T_MTX_UNLOCK) || !hlib_target_ok(T_DATA_SCRIPT_LOCK))
        hlib_log("script: the engine's script lock is not resolved, tables are added without it");
    if (now == 0) {
        hlib_log("script: observing the state's creation (no script engine yet)");
        return 1;
    }
    const HlibLayer late[4] = {{T_LUA_CAPTURE_0, HLIB_BEFORE, (void *)cap0, (void **)&o_cap0, 0},
                                {T_LUA_CAPTURE_1, HLIB_BEFORE, (void *)cap1, (void **)&o_cap1, 0},
                                {T_LUA_CAPTURE_2, HLIB_BEFORE, (void *)cap2, (void **)&o_cap2, 0},
                                {T_LUA_CAPTURE_3, HLIB_BEFORE, (void *)cap3, (void **)&o_cap3, 0}};
    int ok = 1;
    for (int i = 0; i < 4; i++) ok &= hlib_target_ok(late[i].target);
    if (ok && hlib_layers("script", late, 4))
        hlib_logf("script: the script engine was up before the library (%s); the next script call hands over the state",
                   now == 2 ? "its state exists" : "constructed");
    else
        hlib_log("script: the script engine was up before the library and the late capture is not available; "
                  "the tables come with the next state");
    return 1;
}

/* Shared contract for the helper library: the core services every feature module uses and the
   module interface itself. This header
   names nothing from the game; concrete locations live only in targets.h. */
#ifndef HLIB_H
#define HLIB_H

#include <windows.h>
#include <stdint.h>

#define HLIB_VERSION "helper-library 0.1"
/* The save format of the full mode's saves, carried in the meta part's marker: raised when a release breaks a rule
   of save compatibility on purpose. The guard refuses a save of a higher format (a newer build) before any module
   reads it; a lower one is migrated by the modules' chunk versions. */
#define HLIB_SAVE_FORMAT 1
/* this build of the library, a stamp every peer of a game must share (it joins the settings digest) */
uint32_t hlib_library_build(void);

/* logging: one line each, formatted with the C library's rules (floats and %lld work), cut at ~1 KB */
void hlib_log(const char *msg);
void hlib_logf(const char *fmt, ...);
/* the same, but only the first cap lines counted by *counter (a few lines of a kind, not thousands) */
void hlib_log_n(int *counter, int cap, const char *fmt, ...);
/* the first line of a kind only (*done counts the calls) */
void hlib_log_once(volatile LONG *done, const char *fmt, ...);
/* at most one line per ms of wall time; the next line that passes says how many were held back */
typedef struct {
    volatile LONG64 next_ms;
    volatile LONG held;
} HlibLogRate;
void hlib_log_every(HlibLogRate *rate, int ms, const char *fmt, ...);

/* configuration and feature switches */
/* Read once at start from the log folder's ini, and from a key held at start (safe mode).
   A feature is on only when enabled here AND its module resolved its targets. Without its key a feature takes its
   class's default (a module's class at its register, the framework's own switches in features_logic.c; an unknown
   name is off). Switch values: 1/0, on/off, true/false, yes/no; anything else keeps the default and is logged. */
int hlib_feature_on(const char *name); /* 1 if the named feature is enabled */
int hlib_config_int(const char *key, int dflt); /* "key=value" of the ini, decimal or a switch word */
int hlib_config_str(const char *key, char *out, int cap); /* its text, trimmed; 0 when absent */
/* A setting that changes the simulation: every peer of a game must use the same value. Read like
   hlib_config_int, and it goes into the settings digest the log, the save and the load compare; off is the
   module's dead flag, which the core sets when a game of several players loads a save made with another
   value (0: log only). */
int hlib_config_sim(const char *key, int dflt, volatile LONG *off);
uint32_t hlib_config_digest(char *text, int cap); /* the simulation settings as "key=value ..." and their hash */
/* The UI dump probe (ui/mod_uidump.c, switch ui_dump): a debug read of the live widget trees, in a dormant start too. */
int hlib_ui_dump_wanted(void);
int hlib_ui_dump_install(void); /* 1 when in place */
int hlib_ui_dump_install_quiet(void); /* the same without the key and the scene watcher: for the live link */
/* The open scenes of the UI states ids (n of them; none: every open scene) written now, on the UI frame (ui: the
   frame listener's argument); the file names in files (cap of them). The count, -1 when the dump is not in place. */
int hlib_ui_dump_now(void *ui, const int *ids, int n, char (*files)[160], int cap);
const int *hlib_ui_dump_targets(int *n);

/* files under <Documents>/Anno 117 - Pax Romana */
int hlib_user_path(char *out, const char *rel);          /* out holds MAX_PATH; 0 if it does not fit */
int hlib_read_text(const char *path, char *buf, int cap); /* NUL-terminated; the length or -1 */

/* The mods as data (mods.c). The mods folder is indexed once (assets.xml and every .json, the assets.xml texts kept
   while the modules come up); every file of that name ("*tail": ending so) is handed on whole, or by its path only. */
typedef void (*HlibFileFn)(const char *path, const char *text, size_t len, void *ctx);
typedef void (*HlibPathFn)(const char *path, void *ctx);
int hlib_mods_each(const char *file_name, HlibFileFn fn, void *ctx); /* files handed on */
int hlib_mods_paths(const char *file_name, HlibPathFn fn, void *ctx);
void hlib_mods_index(void);   /* builds the index now (the core does, on its own thread) */
void hlib_mods_release(void); /* drops the kept texts once the modules are up */
char *hlib_file_read(const char *path, size_t *len);                  /* NUL-terminated, hlib_file_free */
void hlib_file_free(char *buf);
/* The asset registry (registry.c): one record per GUID of ours, built lazily from the engine's asset (the asset
   manager's lookup) and the library's table (the "helper_registry" and "helper_slots" blocks of the mod's
   anno2070-conversion.json, read once and indexed by GUID). A field both have is the engine's. Modules ask here;
   they do not read the mods' assets.xml for these facts. */
typedef struct {
    uint32_t guid;
    uint32_t source;   /* the source GUID it was made from, 0 none */
    int slot_type;     /* the slot type value its Slot/SlotType must carry (7.. = the mod's own kinds), 0 none */
    int role;          /* 1 a slot asset of a kind of the mod's own, 2 a building that stands on one, 0 other */
    int known;         /* 1 when the library's table has the GUID */
    void *asset;       /* the engine's asset now (a reload replaces it), 0 when the engine has none */
} HlibRules;
int hlib_rules(uint32_t guid, HlibRules *out); /* 1 when either source knows the GUID */
typedef void (*HlibRulesFn)(const HlibRules *r, void *ctx);
int hlib_rules_each_slot(HlibRulesFn fn, void *ctx); /* every record with a slot kind of ours (asset 0); how many */
int hlib_rules_slot_types(int *first);                /* how many own slot types the table names, the first's value */
void hlib_registry_invalidate(void);                  /* the asset reload: the table is read again at the next ask */
/* The source GUID one of our assets was made from, 0 when it has none (the registry's "helper_registry"). For the
   log lines that name our assets. */
uint32_t hlib_source_guid(uint32_t ours);

/* the running build and target resolution */
/* Resolve() checks the build identity, then validates each target. On an unknown build or a target
   that does not match, the whole library (or the owning module) stays inert and says why. */
unsigned char *hlib_image_base(void);  /* module base of the running game image, 0 until resolved */
int hlib_in_image(const void *p);
unsigned long long hlib_exe_addr(const void *p); /* p as the file's own address (preferred image base), else p itself */
void *hlib_target(int id);             /* runtime pointer for a target id (targets.h), 0 if unresolved */
int hlib_target_ok(int id);            /* 1 if that target validated against the running build */
int hlib_targets_ok(const int *ids, size_t n); /* 1 if every one of a module's needed targets did */

/* SEH-guarded module work */
/* Every module entry runs inside this; a fault logs the site, puts the thread's session scope back as it was
   and disables only that module. A C++ exception (the script engine's errors) and the debugger's notices pass
   through to their own handlers. */
typedef void (*HlibThunk)(void *ctx);
int hlib_guarded(const char *site, HlibThunk fn, void *ctx); /* 1 ok, 0 faulted */
int hlib_fault_filter(unsigned long code);                    /* the guard's filter, for an __except of its own */

/* A module's lock: its work runs under it, guarded; a fault switches the module off for the game (dead) and
   logs it. Recursive, so engine calls made inside may come back into the module. */
typedef int (*HlibScriptFn)(void *L);
typedef void (*HlibSessionFn)(void *key);
#define HLIB_LOCK_ENDS 8
typedef struct {
    CRITICAL_SECTION cs;
    volatile LONG ready, dead;
    const char *name;
    HlibSessionFn end_fn;            /* the module's session end, run under the lock */
    void *volatile ends[HLIB_LOCK_ENDS]; /* keys whose end waits for the lock */
    volatile LONG pending;            /* set after a key was stored in ends: the holder scans all slots */
    volatile LONG faults;             /* script readouts that faulted in a row (HLIB_SCRIPT_MUTE_AFTER mutes the module) */
    volatile LONG64 calls, inside, wait, max; /* the cost meter (performance counter ticks), the core reports it */
} HlibLock;
void hlib_lock_init(HlibLock *k, const char *name); /* once, before the module's hooks go in */
int hlib_locked(HlibLock *k, const char *site, HlibThunk fn, void *ctx); /* 1 ran clean; 0 not run or faulted */
/* A save block newer than the module reads (version over max): logged, the module off for the game; 1 then. */
int hlib_chunk_newer(HlibLock *k, const char *name, int version, int max);
/* a script readout under the lock: a fault makes that call answer 0; HLIB_SCRIPT_MUTE_AFTER in a row mute the module
   (dead) */
#define HLIB_SCRIPT_MUTE_AFTER 20
int hlib_locked_script(HlibLock *k, HlibScriptFn fn, void *L);
#define HLIB_LOCKED_SCRIPT(lock, fn) \
    static int lua_##fn(void *L) { return hlib_locked_script(&(lock), fn, L); }
/* A session end for the module (its hlib_on_session_end listener calls this): run now when the lock is free
   or already this thread's, else kept for the module's next locked entry. Never waits for another thread. */
void hlib_locked_end(HlibLock *k, HlibSessionFn fn, void *key);

/* module interface */
/* A feature module fills one of these and registers it from its hlib_register_<name>() entry,
   which core calls from a fixed list (no static-init-order surprises across files). */
typedef struct {
    const char *name;         /* also the feature-switch key */
    int (*resolve)(void);     /* 1 = targets present, enable; 0 = disable this module, no crash */
    void (*install)(void);    /* install hooks; called once, after the guard and resolve */
    void (*on_lua)(void *L);  /* the game's script state became available (may be 0) */
    const char *also_key;     /* an older switch that turns it on too when its own key is absent (may be 0) */
    /* The marker of a module that replaces or skips 117's work (HLIB_REPLACES / HLIB_SKIPS, else 0) and the layer
       route planned instead; logged when it comes up. */
    int overrides;
    const char *route;
    /* An event-only module that the live link may switch while the game runs: the framework withdraws or gives back
       its subscriptions, then calls this with on 0 / 1 to put back what the module changed in the session (1 done, 0
       it could not, HLIB_TOGGLE_LATER its steps will). 0: not switchable at run time. lane: HLIB_SIM when it changes
       the simulation (never switched in a game of several players), HLIB_PRESENT when it only reads. */
    int (*toggle)(int on);
    int lane;
} HlibModule;

/* The feature class a module registers with (owner, 2026-11-14): its default when the ini does not name its key. */
enum {
    HLIB_GAME = 1,     /* seen working in game: on; the ini can only turn it off */
    HLIB_DEV,          /* a developer's tool, debug or scaffold: off; the ini turns it on */
    HLIB_EXPERIMENTAL  /* built, not seen in game yet: off until verified; promoting it is the class at its register */
};
void hlib_register_module(const HlibModule *m, int feature_class);

/* The start mode of an edition: whether this start brings the modules up (the full start) or stays dormant, decided
   once after the ini is read. Safe mode always takes the full start, every module off. */
typedef struct {
    int (*decide)(int safe_mode);               /* 1 the full start, 0 the dormant one (safe mode: full anyway) */
    void (*dormant_reason)(char *out, int cap); /* why the start is dormant, for the log */
    int (*late_check)(int final);               /* a check after the start (final: the last try); 1 done; 0 none */
    const char *digest_key;                     /* the mode's key in the settings digest, 0 none */
    int (*active)(void);                        /* its value there: 1 while the mode runs */
} HlibStartMode;

/* The edition: what the core is linked into. The core asks it and never calls a module; the build provides it, the
   full library in mod_scaffolds.c, the link helper (build.cmd link) in link/linkonly.c. */
typedef struct {
    const char *name;               /* "full", "link": the log's word */
    int link_only;                  /* 1: no start mode, the start is the link start */
    void (*register_modules)(void); /* every module of the build, once (hlib_register_module) */
    void (*dormant)(void);          /* the dormant start's own part before the link (the title's switch button); 0 none */
    void (*before_report)(void);    /* the full start, the modules up, before the config report; 0 none */
    void (*after_report)(void);     /* the full start after the config report; 0 none */
    const HlibStartMode *mode;      /* 0: always the full start */
} HlibEdition;
const HlibEdition *hlib_edition(void);
/* Poll these targets until they match (the image is finalised shortly after load), each that does not is logged; 1
   when all matched. For a start that brings up only a few parts (the dormant start's). */
int hlib_resolve_only(const int *ids, int n);

/* The modules as the live link lists them, and the run-time switch (core.c). */
typedef struct {
    const char *name;
    int feature_class; /* HLIB_GAME / HLIB_DEV / HLIB_EXPERIMENTAL */
    int on;            /* 1 while it runs */
    int toggles;       /* 1 when it may be switched now; why not otherwise */
    int lane;          /* HLIB_SIM or HLIB_PRESENT (of a switchable module) */
    const char *why;
} HlibModuleState;
int hlib_module_count(void);
int hlib_module_state(int i, HlibModuleState *out); /* 1 when i is a module */
/* Switch a module at run time: 1 done; 0 and why (a sentence) when it is not switchable, not now, or failed. */
int hlib_module_set(const char *name, int on, const char **why);
/* toggle(0)'s answer when putting back needs simulation work (the toggle runs on the link's thread, which does no engine
   work): the module reads as off, its subscriptions stay called until its restore pass calls hlib_module_restored. */
#define HLIB_TOGGLE_LATER 2
void hlib_module_restored(const char *name);

/* Script binding: numbers and booleans, and a text the game itself made (the island's name). A script function is
   `int fn(void *L)`: it reads arguments with hlib_lua_arg (1 = first), pushes its results and returns how many. */
int hlib_lua_pushnumber(void *L, double v);
int hlib_lua_pushbool(void *L, int b);
int hlib_lua_pushstring(void *L, const char *s, int len);
/* The n-th argument (1 = first) of a call made with a colon or a dot: a leading table (self) is skipped. */
int hlib_lua_arg(void *L, int n, double *out);
double hlib_lua_num(void *L, int n); /* the same, 0 when it is no number */
int hlib_lua_argstring(void *L, int n, char *out, int cap);

/* Declared script functions (script_api.c, the source of the manifest script-api.json). A module binds its
   C function to a declared name in install(); the binding layer builds Anno2070<Module> with Revision()
   and the bound functions when the engine creates its script state, only while the module runs. Each call
   is guarded: a fault mutes that function. Readouts return what the last tick left; actions queue a
   synced command. hlib_script_changed bumps the table's Revision(). */
int hlib_script_bind(const char *table, const char *name, HlibScriptFn fn);
void hlib_script_changed(const char *table);
int hlib_module_active(const char *name); /* 1 while the module of that feature key runs */
/* The C side of a declared, bound function (0 when none or muted after a fault) and a table's Revision(), for the
   UI binding, which calls the readouts without the script engine. */
HlibScriptFn hlib_script_find(const char *table, const char *name);
long hlib_script_revision(const char *table);

/* The UI's frame (uiframe.c): fn runs once per UI frame on the UI thread, after the game's UI states updated their
   scenes (ui: the engine's state controller), guarded. The first listener places the observer; 1 when it is in
   place. Display only: a listener reads, it never changes game state. */
typedef void (*HlibUiFrameFn)(void *ui);
int hlib_on_ui_frame(const char *owner, HlibUiFrameFn fn);
/* The live link reloaded the scenes of some UI states (uiframe.c): every widget is a new object, so a module that keeps
   widget pointers registers fn and forgets them on its next frame. */
void hlib_on_scenes_reloaded(void (*fn)(void));
void hlib_scenes_reloaded(void);

/* central simulation tick */
/* Modules that need periodic work subscribe here. The dispatch runs on the simulation step for
   every session and island (looked at or not), never on frame or wall-clock time; it respects
   pause and speed because it rides the engine's own step. Work must be deterministic (see the
   participant and RNG helpers) so multiplayer peers stay in sync. */
typedef void (*HlibTickFn)(void *area);
void hlib_tick_register(const char *owner, HlibTickFn fn);
/* The dispatch runs at the engine's per-area step (100 ms of game time each) with the engine's own
   thread-local session context set, so the session accessors and hlib_rand are valid inside a
   subscriber. The core places it as soon as there is a subscriber and its target matched (tick.c). */
void hlib_tick_install(int final);
int hlib_tick_ms(void);                       /* game milliseconds one step stands for */

/* Session lifecycle. Per-session state is keyed by the session key (hlib_session_key), which can come back for
   another session once its game is over; the core's watch tells the listeners that a key's session is over before
   anything of the new one reaches a module. Listeners run guarded, on the thread that noticed it, and never wait for
   a lock (hlib_locked_end). */
void hlib_on_session_end(const char *owner, HlibSessionFn fn);
int hlib_session_listeners(void);
void hlib_session_note(int sid, void *key, void *ctx, long long now); /* the core's watch (engine.c, tick.c) */
void hlib_session_merge(int sid, void *key);                         /* an island merge (persist.c) */
void hlib_session_loaded(void *key);                                 /* a session read from a save (persist.c) */
void hlib_session_closed(int sid, void *key);                        /* the engine closed it (engine.c) */
int hlib_lifecycle_install(int final); /* the observer on the engine's session close, once a listener exists */

/* shared events (events.c): hook points several modules listen to, observed once, every listener guarded */
/* A factory's production cycle starts (finish 0) or ends (finish 1); called before the engine's own work. */
typedef void (*HlibFactoryFn)(void *factory, int finish);
void hlib_on_factory(const char *owner, HlibFactoryFn fn);
/* The engine's upkeep pass over a session's buildings, one Maintenance component per building (its object at
   +8), inside the session's context, on the simulation step: the place to walk the buildings. */
typedef void (*HlibBuildingsFn)(void *const *components, int count);
void hlib_on_buildings(const char *owner, HlibBuildingsFn fn);
/* A new session's map is loaded (never a save's): the generator has run, every island and its areas exist, the
   simulation has not stepped. On the session loader's thread, inside the session's context; the map seed is the
   generator's (0 without settings), the session its asset. hlib_each_island_area walks the island areas there. */
typedef void (*HlibMapFn)(uint32_t map_seed, uint32_t session);
void hlib_on_map_loaded(const char *owner, HlibMapFn fn);
typedef void (*HlibAreaFn)(int area_id, void *area, void *ctx);
/* the session in the thread's context; each island area as its construction area (an id without one yet is skipped);
   how many were handed on */
int hlib_each_island_area(HlibAreaFn fn, void *ctx);
/* Synced actions: hlib_command_send queues (area, code, arg[4]) on the lockstep buffer; on every client, on
   the same step, each listener sees it and returns 1 when the code is its own (the energy module's codes carry
   HLIB_CMD_ENERGY in their top byte, the trees' erase is 0x100 | mask); keep the arguments small and
   non-negative (0..4095). */
#define HLIB_CMD_SLOT 0x2070        /* the brush slot the commands ride on; the real brush never uses it */
#define HLIB_CMD_ENERGY 0x45000000  /* top byte of the energy module's codes */
#define HLIB_CMD_STARTFACTION 0x53000000
#define HLIB_CMD_LICENCES 0x4c000000
#define HLIB_CMD_ARKFACTIONS 0x41000000
#define HLIB_CMD_META 0x4d000000 /* the meta provider (the server's stand-in) */
#define HLIB_CMD_QUESTS 0x51000000
#define HLIB_CMD_ITEMS 0x49000000      /* research queues (mod_items.c) */
#define HLIB_CMD_PROPAGANDA 0x50000000 /* the stations' channels (mod_specials.c) */
#define HLIB_CMD_CATASTROPHES 0x43000000 /* the debug start (mod_catastrophes.c) */
#define HLIB_CMD_FLEET 0x46000000        /* a submarine's dive (mod_fleet.c) */
#define HLIB_CMD_SECTORS 0x58000000      /* a sector added to the game (mod_sessions.c) */
#define HLIB_CMD_TAX 0x54000000          /* the tax screw choices (mod_tax.c) */
#define HLIB_CMD_UPGRADES 0x55000000     /* the city centre's upgrade button (mod_upgrades.c; | 2) */
#define HLIB_CMD_DIPLOMACY 0x44000000    /* a diplomacy action of the mod (mod_diplomacy.c) */
#define HLIB_CMD_GODMODE 0x47000000      /* the god mode mod's flags and start tons (mod_godmode.c) */
#define HLIB_CMD_ACTIONS 0x4b000000      /* a special action's use (mod_specialactions.c) */
#define HLIB_CMD_ACTIVEITEMS 0x4a000000  /* a used island item's click (mod_activeitems.c) */
#define HLIB_CMD_FARMFIELDS 0x52000000   /* a farm's automatic fields (mod_farmautofill.c; | 1) */
/* The meta step: the diplomacy manager's own tick, once per meta step (100 ms of game time) on every peer, on the
   lane the net commands run on; fn runs after the engine's work, guarded. Game-wide work that must not run in the
   parallel area jobs (the mod's treaties, the trade prices) goes here; there is no session in the thread's context. */
typedef void (*HlibMetaFn)(void);
void hlib_on_meta_tick(const char *owner, HlibMetaFn fn);
typedef int (*HlibCommandFn)(short area, int32_t code, const int32_t arg[4]);
void hlib_on_command(const char *owner, HlibCommandFn fn);
int hlib_command_send(short area, int32_t code, const int32_t arg[4]);
int hlib_command_ready(void); /* 1 once the carrier is in place */
/* The tree brush itself, for the one module that owns it: called with the engine's original. */
typedef void *(*HlibBrushOrig)(void *wm, short slot, int count, short area, int *rect);
typedef void *(*HlibBrushFn)(HlibBrushOrig orig, void *wm, short slot, int count, short area, int *rect);
void hlib_on_brush(HlibBrushFn fn);
void hlib_events_install(int final); /* the core calls it after every resolution pass */

/* game objects (engine.c): the few fields every module reads, through the target table */
uint32_t hlib_obj_guid(const void *obj);
uint32_t hlib_obj_owner(const void *obj);       /* the owning participant's GUID */
int hlib_obj_area(const void *obj);              /* the area id (session, island, kind bits) */
long long hlib_obj_id(const void *obj);          /* the engine's object id (scripts name objects by it) */
const float *hlib_obj_pos(const void *obj);      /* x, y, z */
void *hlib_obj_component(void *obj, int which);  /* HLIB_COMP_* of the object, 0 if it has none */
void *hlib_obj_by_id(long long id);              /* the object of an id in the current session, 0 if gone */
enum { HLIB_COMP_PAUSABLE, HLIB_COMP_BUFFABLE, HLIB_COMP_FACTORY, HLIB_COMP_RESIDENCE, HLIB_COMP_LOGISTIC,
       HLIB_COMP_WAREHOUSE, HLIB_COMP_ITEMS, HLIB_COMP_COUNT };
void *hlib_factory_object(void *factory);        /* the game object of a factory component */
int hlib_paused_bits(void *obj);                 /* the pause reasons set on a building, -1 if it has none */
int hlib_set_paused(void *obj, int bit, int on); /* one pause reason; 1 if it changed */
/* Buffs on a building through its own buff component, as the engine's effects put them there. A buff the
   library adds carries a source id of its own, so it never touches an effect's buff. */
int hlib_buff_has(void *obj, uint32_t buff);
int hlib_buff_carried(uint32_t buff, void *obj); /* hlib_buff_has == 1, in the form the logic files call back */
int hlib_buff_add(void *obj, uint32_t buff);
int hlib_buff_remove(void *obj, uint32_t buff);
float hlib_factory_productivity(void *obj);      /* the building's own productivity 0..n, -1 when not a factory */
uint32_t hlib_factory_fertility(void *factory);  /* the fertility a factory component needs, 0 none */
float hlib_residents(void *obj);                 /* residents of a home, -1 when not one */
float hlib_residents_max(void *obj);             /* the most residents a home holds now (a cap step lowers it), -1 when not one */
/* The object becomes the asset `to` its asset's upgrade list names: swapped in place (the object id stays) or rebuilt
   as that entry says, in the object's area; no price is taken. 1 when the engine's call was made. */
int hlib_obj_upgrade(void *obj, int area, uint32_t to);
long long hlib_session_time_ms(void);            /* game time of the session in the thread's context, -1 none */
long long hlib_meta_time_ms(void);               /* the game clock without a session (the meta step's), -1 none */
/* Work on an area of any session: the thread's session context and area scope switch to that area's, as
   the engine's own scope guard does, and come back on leave. */
typedef struct {
    void *ctx, *aux, *scope;
} HlibScope;
int hlib_scope_enter(HlibScope *s, int area_id); /* 1 when switched (leave it then) */
void hlib_scope_leave(const HlibScope *s);
int hlib_scope_save(HlibScope *s);               /* the thread's current scope, for a later leave; 0 none */
char *hlib_tls_block(void);                       /* the game's thread-local block of this thread, 0 none */
void *hlib_session_ctx(void);                     /* the session context the thread holds, 0 none */
void *hlib_session_ctx_of(int sid);               /* the context of a session index, 0 none */
void *hlib_session_key(void);                    /* the session in the thread's context (the save key), 0 none */
int hlib_session_index(void);                    /* the slot of the session in the thread's context, -1 none */
/* the lowest slot that holds a session: game-wide work (a player's money, unlocks) runs in that session's pass
   only, so every peer does it in the same job of the same step */
int hlib_first_session(void);
/* The upkeep pass walks every session's buildings: fn runs once per session index that has an area in
   area_of (area_of[sid], 0 none), inside that session's scope. */
typedef void (*HlibSidFn)(int sid, void *ctx);
void hlib_each_session(const int area_of[64], HlibSidFn fn, void *ctx);
/* Storages: a building's own (its logistic node's) and an island's shared one (its warehouses). An amount and
   capacity of a product (-1: not a storage the library knows); a change through the storage's own add or take
   (negative), as a delivery makes it: simulation only, on the area's own step. */
void *hlib_obj_storage(void *obj);
void *hlib_area_storage(int area_id);
int hlib_storage_amount(void *storage, uint32_t product, int *capacity);
int hlib_storage_change(void *storage, uint32_t product, int amount);
int hlib_transporters_free(void *obj); /* its logistic node's transporters not out now, -1 none */
/* Every component of a property id in an area of the thread's session (fn per component); how many. */
typedef void (*HlibCompFn)(void *component, void *ctx);
int hlib_area_components(int area_id, uint16_t prop, HlibCompFn fn, void *ctx);
/* the same, but -1 when the area's component manager cannot be walked (not built yet, or being filled): the caller
   tries again later and does not take an empty answer for an island with no such component */
int hlib_area_components_ok(int area_id, uint16_t prop, HlibCompFn fn, void *ctx);
/* The island area of an id in the thread's session: the object the engine's area scope holds (TLS +0x1f0), with the
   component manager, the item storage and the incident manager. hlib_area gives the construction area of the same
   id (owner, economy, limited lodes): a different object. NULL when the session has no such area. */
void *hlib_island_area(int area_id);
/* the file stem of the island an island area belongs to (a2070_n_l16), 0 when unknown */
int hlib_island_stem(int area_id, char *out, int cap);
int hlib_lode_add(void *area, uint32_t fertility, int amount);    /* seed or refill, clamped to the maximum */
int hlib_lode_remove(void *area, uint32_t fertility, int amount); /* take, floor 0 */
/* The engine's own notifications (notify.c): one of our notification assets with an island as its "Area" context,
   for a participant (only that participant's client shows it). Queue it on the simulation path, the same on every
   peer; it is raised in the next upkeep pass. A module that raises any calls hlib_notify_use in install(). */
void hlib_notify_use(const char *owner);
int hlib_notify_ready(void);
int hlib_notify(uint32_t notification, int area_id, uint32_t participant); /* 1 when queued */
/* The mod's status markers over buildings (markers.c): the mod's buff of a mod-only state ("WoodResourceEmpty"), put
   on or taken off a building. A module that raises any calls hlib_markers_use in install(); set returns 1 when the
   building's buffs changed. */
void hlib_markers_use(const char *owner);
uint32_t hlib_marker_guid(const char *state); /* the buff, 0 when the mods name none */
int hlib_marker_set(void *obj, const char *state, int on);

/* participants (players, human and AI) */
/* Every module applies to all participants, not just the local human. For AI the mod's rules apply, including the
   mod's AI cheats. */
/* The list is the game's own, one for the whole game (session is unused), sorted by GUID, so every
   peer sees the same order; identify a participant by its GUID, the index only iterates. */
typedef struct {
    int index;            /* 0..count-1 */
    uint32_t guid;        /* the participant's asset: its identity in areas, saves and commands */
    int kind;             /* 0 human, 1 rival (second party), 2 third party (traders, pirates), 3 virtual, -1 unknown */
    int is_ai;            /* kind 1 or 2 */
    uint32_t profile;     /* AI: the character's asset (= guid); 0 for a human */
    int difficulty;       /* a rival's: 0 easy, 1 medium, 2 hard; -1 for everyone else */
    uint32_t ai_rules;    /* bitset of the mod's AI exceptions that apply, from the rules hook */
} HlibParticipant;
int hlib_participant_count(void *session);
int hlib_participant_info(void *session, int index, HlibParticipant *out); /* 1 on success */
int hlib_human_count(void);             /* the human participants of the game (more than one: multiplayer); one that
                                            cannot be read counts as one */
uint32_t hlib_local_participant(void);  /* GUID of the participant this client plays, 0 if none */
/* The per-participant hook: the module that owns the mod's AI rules (arkfactions) answers ai_rules for a participant
   whose other fields are filled. One provider; without one ai_rules is 0. */
typedef uint32_t (*HlibAiRulesFn)(const HlibParticipant *p);
void hlib_participant_rules(HlibAiRulesFn fn);
/* the ai_rules bits; each module applies the bits of its system */
#define HLIB_AI_REFILL 0x001u           /* coal / oil on its islands topped up (arkfactions) */
#define HLIB_AI_UPGRADE_NO_NEEDS 0x002u /* homes rise a level without needs */
#define HLIB_AI_NO_RESIDENT_SIM 0x004u  /* no needs, growth, ruins or protests for its homes */
#define HLIB_AI_BUDGET 0x008u           /* the mod's budget instead of credits (arkfactions) */
#define HLIB_AI_RECORD_SETS 0x010u      /* builds its faction's record sets: every faction open, no start choice */
#define HLIB_AI_FREE_SHIPS 0x020u
#define HLIB_AI_FREE_ITEMS 0x040u
#define HLIB_AI_PROGRESS_CAPPED 0x080u  /* its progress level never passes the human's */
#define HLIB_AI_NO_LICENCES 0x100u

/* synchronised randomness */
/* Deterministic across peers: draws from the game's own per-session/per-area generator, never from
   C rand(). Use for any simulation choice. A pure hash (util) is fine for a value that must be the
   same on every peer from the same inputs and is not drawn from the shared stream. */
uint32_t hlib_rand(void *session, uint32_t upper_exclusive);  /* session is unused: the context is thread-local */
/* The draws also carry the game's salt (util.h, rngsalt.c), so another game deals other numbers; no module changes.
   Work on one area draws from that area's own stream (util.h hlib_rng_draw, the session's game time), never from
   hlib_rand: the engine steps a session's areas as parallel jobs, so the session generator would hand its numbers
   out in thread order. hlib_rand stays for work on the session's own step. One stream id per purpose: */
enum { HLIB_RNG_TREES_STEP = 1, HLIB_RNG_TREES_FELL, HLIB_RNG_TREES_PLANT, HLIB_RNG_TREES_ERASE };
enum { HLIB_RNG_QUESTS = 0x51 }; /* the quest schedule's game-wide draws (one stream over a fixed area id) */
int hlib_rand_ok(void);                       /* 1 if the synchronised generator can be reached right now */

/* engine access (engine.c) */
void *hlib_asset(uint32_t guid);                 /* asset by GUID, 0 if none; 0 while an asset reload is pending */
void *hlib_assets_of_template(uint16_t tmpl);    /* begin/end pair holder, 0 if none; 0 while an asset reload is pending */
/* The asset reload's guard: from the request to the engine's own end line the asset lookups answer 0, so every
   module that looks an asset up fresh each frame skips its work instead of reading a database the engine swaps; a
   module keeps no asset or property pointer across frames, and one that must (a table made from property data)
   remembers hlib_asset_generation() and builds it again when that moved. A pending state whose engine start line was
   not seen ends by itself after HLIB_RELOAD_PENDING_MAX_MS (the engine dropped the reload); once the engine began
   its rebuild only its end line ends it. */
#define HLIB_RELOAD_PENDING_MAX_MS 90000
void hlib_asset_reload_begin(void);   /* an asset reload was asked for or noticed */
void hlib_asset_reload_started(void); /* the engine's start line of it was seen */
void hlib_asset_reload_end(void);     /* the engine's end line: the registry is read again, the generation moves */
int hlib_asset_reload_pending(void);
uint32_t hlib_asset_generation(void); /* moves with every asset reload that ended */
void *hlib_prop(void *asset, uint16_t prop_id);  /* a property's value pointer, 0 if absent */
int hlib_narrow_wstr(const void *engine_wstring, char *out, int cap); /* UTF-16 std string -> ascii */
int hlib_narrow_str(const void *engine_string, char *out, int cap);   /* 8-bit std string -> ascii */
int hlib_lode_amount(void *area, uint32_t fertility); /* current deposit stock on an island area, -1 if unavailable */
void *hlib_area(int area_id);  /* an area of the session in the thread's context, 0 if none */
int hlib_area_at(float x, float z);     /* the area id at a world position of that session, -1 none */
int hlib_ground_height(float x, float z, float *h); /* the terrain's height there; 0 (h 0) when it cannot be read */
uint32_t hlib_area_owner(int area_id);  /* the participant owning an area, 0 none */
uint32_t hlib_current_participant(void); /* the participant the engine acts for on this thread (a command's sender) */
uint32_t hlib_set_participant(uint32_t p); /* the thread's acting participant, as the engine's own guard sets it; the old */
/* A player's meta storage (money and the other one-per-player stocks, the same in every session): the amount, -1
   unknown. Adding (negative takes, the engine clamps at 0 and the capacity) changes the simulation: only on the
   synced path (a command every client runs). */
const char *hlib_meta_why(uint32_t participant); /* why a player's meta economy was not found, for a log line */
int hlib_meta_amount(uint32_t participant, uint32_t product, int *capacity);
int hlib_meta_add(uint32_t participant, uint32_t product, int amount);
int hlib_asset_unlocked(uint32_t participant, uint32_t guid); /* 1, 0, -1 unknown */
int hlib_unlock_thread(void); /* 1 on the thread the engine's unlocks run on (elsewhere the engine defers them) */
int hlib_lead_peer(void); /* 1 when this client plays the first human participant: it sends the game-wide commands */

/* persistence (persist.c) */
/* Each module stores its state as a versioned chunk inside the game's own save, through the engine's
   serializers. Old saves without the chunk load with defaults; a save written with this library and
   loaded without it keeps the chunk untouched and drops it on the next vanilla save. See the recipe. */
typedef struct HlibSaver HlibSaver;   /* opaque: a node in the engine's serializer */
typedef void (*HlibSaveFn)(HlibSaver *s, int version);
typedef void (*HlibLoadFn)(HlibSaver *s, int version);
/* 1 when registered; 0 (and a log line) when the chunk table is full */
int hlib_persist_register(const char *chunk, int version, HlibSaveFn save, HlibLoadFn load);
/* A chunk of game-wide state, written into every session's block: the save's game time leads each block and a load
   hands the newest of them on, the others as absent, so the module's load must keep its state on an absent block.
   hlib_persist_load_begins starts a new load's choice (the save marker's read and the game's end). */
int hlib_persist_register_global(const char *chunk, int version, HlibSaveFn save, HlibLoadFn load);
void hlib_persist_load_begins(void);
/* Inside a save callback: hlib_saver_put stores the chunk's one byte block; inside a load callback:
   hlib_saver_get returns it (valid until the callback returns), 0 if the save has none.
   hlib_persist_session is the session whose state is being written or read (its regrowth manager),
   so a module keeps one state per session. The core places the carrier once a chunk is registered. */
/* An island arriving in a running session (the engine merges it at a placement): the session and the
   engine's placement record, valid for the call. Up to four listeners, each guarding its own work. */
typedef void (*HlibIslandFn)(void *session, void *placement);
void hlib_persist_on_island(HlibIslandFn fn);
int hlib_saver_put(HlibSaver *s, const void *data, uint32_t len);
/* The same block format in any open serializer stream (the save's meta part carries the mode marker):
   put while writing, find while reading; the payload stays valid while the stream does. */
int hlib_stream_reading(void *stream);
int hlib_block_put(void *stream, const char *chunk, int version, const void *data, uint32_t len);
int hlib_block_find(void *stream, const char *chunk, int *version, const unsigned char **data, uint32_t *len);
int hlib_stream_name_id(void *stream, const char *name);           /* a field name's id there, -1 if none */
int hlib_stream_get_int(void *stream, const char *name, int *out); /* a 4-byte field of a stream being read */
uint32_t hlib_stream_offset(void *stream);   /* write side: where the next field goes in the stream's bytes */
unsigned char *hlib_stream_bytes(void *stream);
int hlib_saver_get(HlibSaver *s, const void **data, uint32_t *len);
void *hlib_persist_session(void);
void hlib_persist_install(int final);

/* Layers around engine calls (layer.c). A layer is our function with the target's signature that runs around 117's
   own: it calls *next (the next layer inside, or the original) exactly once and adjusts the inputs before or the
   result after. Several modules may layer one target: a later layer sits inside the earlier ones. A layer that may
   leave the original out declares so (REPLACES: it answers instead; SKIPS: the work is not done), with why and the
   route planned instead; the registry logs and lists every such declaration at start. A batch is all or nothing. */
typedef enum {
    HLIB_BEFORE = 1, /* reads or adjusts the inputs, then calls the original */
    HLIB_AFTER,      /* calls the original, then reads or adjusts (narrows) its result */
    HLIB_AROUND,     /* both, e.g. a scope set for the call and put back */
    HLIB_REPLACES,   /* may answer without the original (declared) */
    HLIB_SKIPS       /* may leave the original's work out (declared) */
} HlibLayerKind;
typedef struct {
    int target;      /* targets.h id */
    int kind;        /* HlibLayerKind */
    void *fn;        /* our function with the target's signature */
    void **next;     /* receives what fn calls on */
    const char *why; /* REPLACES / SKIPS: when, and the planned layer route; else 0 */
} HlibLayer;
int hlib_layers(const char *owner, const HlibLayer *layers, int count); /* 1 placed */
/* The same for a target that is a 5-byte jump thunk of ordinary code (its row's bytes `e9` + rel32): the first layer
   re-points the thunk, a later one is spliced in behind it like any layer; undone when the library unloads. */
int hlib_thunk_layer(const char *owner, const HlibLayer *layer);
/* The last resort, a patch inside 117's code where no entry layer can reach (catalogue section 8): a jump to our
   code over a target's recorded run (whole instructions; back = the first byte after), or bytes at an offset into
   targets. Recorded like the layers, with the kind of their effect on 117's work; low update stability. */
typedef struct {
    int target, offset;
    const void *bytes;
    int n;
} HlibPatch;
int hlib_site_jump(const char *owner, int target, int kind, void *to, void **back, const char *why);
int hlib_site_bytes(const char *owner, const HlibPatch *patches, int count, int kind, const char *why);

/* Dispatch at 117's safe points (dispatch.c). A module subscribes a function of the point's type with the lane it
   works on: simulation (lockstep, the same on every peer) or presentation (each client's own, reads only). A
   simulation subscriber on a presentation point is refused. Subscribers run in the order they came, each guarded.
   The typed helpers below are the usual way in. */
typedef enum {
    HLIB_AT_AREA_STEP,    /* HlibTickFn: each island area's step, in its session's context (tick.c) */
    HLIB_AT_UPKEEP,       /* HlibBuildingsFn: the upkeep pass over a session's buildings (events.c) */
    HLIB_AT_META_STEP,    /* HlibMetaFn: once per meta step, game-wide, no session (events.c) */
    HLIB_AT_SESSION_STEP, /* HlibSessionStepFn: a session's step before its areas (tick.c, the manager's slot) */
    HLIB_AT_POST_SESSION, /* HlibSessionStepFn: after the session's areas, one thread (tick.c, the manager's slot) */
    HLIB_AT_FACTORY,      /* HlibFactoryFn: a factory cycle's start or end (events.c) */
    HLIB_AT_MAP_LOADED,   /* HlibMapFn: a new session's map (events.c) */
    HLIB_AT_COMMAND,      /* HlibCommandFn: a synced command, the first that takes it (events.c) */
    HLIB_AT_SESSION_END,  /* HlibSessionFn: a session key's session is over (session.c) */
    HLIB_AT_UI_FRAME,     /* HlibUiFrameFn: once per UI frame, UI thread, presentation only (uiframe.c) */
    HLIB_AT_GAME_STEP,    /* HlibMetaFn: game-wide work once per step (tick.c, events.c; hlib_on_game_step) */
    HLIB_AT_COUNT
} HlibPoint;
typedef enum { HLIB_SIM, HLIB_PRESENT } HlibLane;
int hlib_subscribe(const char *owner, HlibPoint at, HlibLane lane, void *fn); /* 1 taken */
/* 1 once the point's source is in place on this build (its subscribers are called); a module that keeps an older
   path for a point whose target may be planned asks this before it leaves that path */
int hlib_point_live(HlibPoint at);
typedef void (*HlibSessionStepFn)(void *session_manager); /* the stepped session's game session manager */
/* Game-wide simulation work once per step, the same on every peer and outside the parallel area jobs: the first
   session's post-session step once it has run in its session's context, the meta step until then (lane 8; whether
   the meta step runs beside the session jobs is open). Replaces the "lead pass" inside an area job. */
void hlib_on_game_step(const char *owner, HlibMetaFn fn);
long long hlib_step_time_ms(void); /* game time where the game step runs: the session's there, else the meta clock */
/* A player's meta storage changed from an area job (the upkeep pass, the area step): the change is summed per player
   and product and paid in that session's post-session step, after its areas, in GUID order, so no two area jobs
   write one player's meta storage at once. Until that step is live the change is made at once, as hlib_meta_add. A
   module that pays this way calls hlib_meta_later_use in install(). 1 when queued or made. */
void hlib_meta_later_use(const char *owner);
int hlib_meta_add_later(uint32_t participant, uint32_t product, int amount);
/* Engine events as subscriptions (bus.c): the engine's own listener add delivers the event on a lane, the simulation's
   (8, every peer at the same point) or the UI's (10). Subscribe in install(); fn gets the event's payload, guarded.
   Where the listener add or a descriptor does not resolve nothing is delivered and hlib_engine_event_live says 0, so
   a module keeps its older path meanwhile. */
typedef enum { HLIB_EV_META_UNLOADED, HLIB_EV_SESSION_LOADED, HLIB_EV_DESYNC, HLIB_EV_COUNT } HlibEngineEvent;
typedef void (*HlibEventFn)(const void *payload);
int hlib_on_engine_event(const char *owner, HlibEngineEvent ev, HlibLane lane, HlibEventFn fn); /* 1 kept */
int hlib_engine_event_live(HlibEngineEvent ev, HlibLane lane);

/* The running game's difficulty setup (gamesetup.c, gamesetup_logic.h GS_*): the enum index of a choice the player made
   in 117's game setup, -1 when it is not readable; the same on every client. changed: the player moved it off the
   preset (1), did not (0), unknown (-1). */
int hlib_setup_value(int field);
int hlib_setup_changed(int field);
uint32_t hlib_setup_preset(void);
const char *hlib_setup_name(int field);
void hlib_setup_log(const char *why); /* one line with every choice, for the log */

#endif

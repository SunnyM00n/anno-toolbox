/* Pure helpers with no game or Windows dependency, so the host tests can exercise them. */
#ifndef HLIB_UTIL_H
#define HLIB_UTIL_H

#include <stdarg.h>
#include <stdint.h>
#include <stddef.h>

/* Bounded formatting with the C library's rules (floats, %lld / %llu, %p): always NUL-terminated, returns the
   length written, cut at cap - 1. Every log line goes through it. */
int hlib_vformat(char *out, int cap, const char *fmt, va_list ap);
int hlib_format(char *out, int cap, const char *fmt, ...);

/* The library's ini: one key=value per line; a UTF-8 BOM, blanks around key and value and '#' / ';' comment
   lines are allowed. Returns the pairs read (up to max); *cut = 1 when lines with a pair were left over. */
typedef struct {
    char key[48];
    char value[96];
    unsigned char used, warned;
} HlibIniPair;
int hlib_ini_parse(const char *text, HlibIniPair *out, int max, int *cut);
int hlib_ini_bool(const char *v);            /* 1/on/true/yes 1, 0/off/false/no 0, anything else -1 */
int hlib_ini_int(const char *v, int *out);   /* a decimal number or a switch word (1/0); 0 when neither */

/* Per-session records: each record starts with this head. The slot of key among the n records (stride bytes
   each); with create a new one takes the next free place or, when all max are taken, the least recently used
   (*reused = 1: the caller frees its old content). The caller fills a new record. -1 when absent or key 0. */
typedef struct {
    void *key;
    uint32_t used;
} HlibSessHead;
int hlib_sess_slot(void *records, size_t stride, int *n, int max, void *key, int create, uint32_t *clock, int *reused);

/* The sessions of the running game a module has seen (max places, NULL free), for state that belongs to the game
   and not to one session: note a key once; drop one at its session's end and learn how many are left (0: the game
   is over). */
void hlib_keys_note(void **keys, int max, void *key);
int hlib_keys_drop(void **keys, int max, void *key);

int hlib_cmp_u32(const void *a, const void *b); /* for qsort and bsearch over uint32_t */
int hlib_clamp(int v, int lo, int hi);

/* Per-island turns of a pass on the upkeep pass, which comes once per island area with that area's buildings only: a
   clock per area, so one island's call cannot use up another island's turn. An area is due when it is new, when
   `period` ms of game time passed since its last turn or when the clock went back; the turn is then taken. A full
   table forgets the area whose turn is oldest. */
#define HLIB_AREA_CLOCKS 128
typedef struct {
    int area;
    long long last_ms;
} HlibAreaClock;
typedef struct {
    HlibAreaClock c[HLIB_AREA_CLOCKS];
    int n;
} HlibAreaClocks;
/* 1 when due (the turn is taken; *dt the ms since the last turn, 0 at the first), 0 not yet */
int hlib_area_due(HlibAreaClocks *k, int area, long long now, long long period, long long *dt);

/* The areas of one pass call, each decided once: the first object of an area takes its turn, the others follow. */
#define HLIB_CALL_AREAS 64
typedef struct {
    int n;
    int area[HLIB_CALL_AREAS];
    signed char due[HLIB_CALL_AREAS];
    long long dt[HLIB_CALL_AREAS];
} HlibCallAreas;
int hlib_call_due(HlibCallAreas *c, HlibAreaClocks *k, int area, long long now, long long period);
/* 1 when the area took its turn in this call; *dt (may be 0) its time since the last turn */
int hlib_call_took(const HlibCallAreas *c, int area, long long *dt);
/* ms of a turn for the step: dt as hlib_area_due gives it (0 at an area's first turn), `first` then, and at most max */
long long hlib_turn_ms(long long dt, long long first, long long max);
/* A hash of a few counts: a log line of an island is written when it differs from the one written last. */
unsigned hlib_hash_ints(const int *v, int n);
#define HLIB_NOTES 64
typedef struct {
    int n;
    int area[HLIB_NOTES];
    unsigned hash[HLIB_NOTES];
} HlibNotes;
/* 1 when the counts v[0..nv) of the area differ from the ones noted last (or it has none yet), and notes them; a full
   table says 1 without noting. The module writes its line on a 1. */
int hlib_note_changed(HlibNotes *t, int area, const int *v, int nv);

/* Text scanning shared by the readers of the mods' files */
const char *hlib_find(const char *p, const char *end, const char *s); /* the first s in [p, end), 0 none */
/* the text of <tag>..</tag> in [p, end): its start and end, 0 when absent */
int hlib_xml_span(const char *p, const char *end, const char *tag, const char **b, const char **e);
/* the GUID of the nearest GUID="n" before p (at most window bytes back, not before start), 0 none */
uint32_t hlib_guid_before(const char *start, const char *p, int window);
/* a record of a table sorted by GUID whose first field is the GUID, 0 none */
const void *hlib_by_guid(const void *sorted, int n, size_t stride, uint32_t guid);
/* The status markers the mod's tools write for mod-only states (<mod>.markers.json, "markers"): the buff that
   carries the marker of a state, 0 when the file has none. */
uint32_t hlib_marker_buff(const char *json, size_t len, const char *state);

/* JSON of the mod's files, read in place: the value after "key": in [p, e) (0 when absent); the end of the value at
   p (an object or array past its closing bracket, a string past its quote, a number at its delimiter); the members
   of the object at p one by one (the key copied, its value; the position after it, 0 at the end). */
const char *hlib_json_value(const char *p, const char *e, const char *key);
/* the value of key, else of old_key (a file written before the key was renamed) */
const char *hlib_json_value_or(const char *p, const char *e, const char *key, const char *old_key);
/* 1 when the NUL-terminated text names key or old_key as a quoted key */
int hlib_json_has(const char *text, const char *key, const char *old_key);
const char *hlib_json_end(const char *p, const char *e);
const char *hlib_json_next(const char *p, const char *e, char *key, int cap, const char **value);
long long hlib_json_int(const char *p, const char *e);
/* The readers the logic files build their tables with. elem: the next element of the array at p (p moves on; 0 at its
   end). num: a member's number, dflt when it is absent or not one. str: a member's string copied (cut to cap). arr: a
   member's array (0 when absent), its end in *end. ints: the numbers of the array at v into out, up to max; how many. */
const char *hlib_json_ws(const char *p, const char *e); /* p past blanks and line ends */
const char *hlib_json_elem(const char *p, const char *e, const char **v);
long long hlib_json_num(const char *obj, const char *e, const char *key, long long dflt);
void hlib_json_str(const char *obj, const char *e, const char *key, char *out, int cap);
const char *hlib_json_arr(const char *obj, const char *e, const char *key, const char **end);
int hlib_json_ints(const char *v, const char *e, int *out, int max);

enum { HLIB_WORLD_UNKNOWN = 0, HLIB_WORLD_OURS = 1, HLIB_WORLD_VANILLA = 2 };

/* Classify a GUID against an inclusive [first,last] range; 0 (no GUID) stays unknown. */
int hlib_world_of(uint32_t guid, uint32_t first, uint32_t last);
const char *hlib_world_name(int world);

/* Copy a UTF-16 run into an ascii buffer, non-ascii -> '?', always NUL-terminated; returns length. */
int hlib_u16_to_ascii(const uint16_t *p, size_t n, char *out, int cap);

/* Case-insensitive compare of a UTF-16 run (length n) against a NUL-terminated UTF-16 string,
   treating '\\' and '/' as equal. 1 on full match. */
int hlib_u16_path_eq(const uint16_t *p, size_t n, const uint16_t *cstr);

/* Last path component of an ascii string (after the final / or \\). */
const char *hlib_tail(const char *p);

/* A decimal number from the mods' text, always with a point whatever the locale (strtod's rules otherwise: blanks,
   sign, digits, fraction, exponent); *end = s when there is no number. */
double hlib_strtod(const char *s, const char **end);
double hlib_atof(const char *s);
/* sin, cos and acos from additions and products only: the same bits on every machine (the C library's may take
   a CPU-dependent path), for values that feed the simulation. Error below 1e-15 over the angles used. */
double hlib_sin(double x);
double hlib_cos(double x);
double hlib_acos(double x);

/* The session watch's records, one per session slot (the slot the context itself names): the key, the context,
   the last game time seen. A key seen at another slot is the same session moved (its state is kept); a new key at
   a slot ends the key that was there; nothing else ends a key here (the engine's close and a load do,
   hlib_life_close). What a note did: */
typedef struct {
    void *key, *ctx;
    long long last;
} HlibLifeSlot;
typedef struct {
    void *ended;    /* the key a new key replaced at the slot, 0 none */
    int moved_from; /* the slot the key was seen at before, -1 none */
    int fresh;      /* the key is new to the watch */
} HlibLifeEvent;
#define HLIB_LIFE_SLOTS 64
void hlib_life_note(HlibLifeSlot *slots, int sid, void *key, void *ctx, long long now, HlibLifeEvent *ev);
void *hlib_life_close(HlibLifeSlot *slots, int sid, void *key); /* the key cleared (that slot's when key 0) */

/* Draws for work on one area. The engine steps the areas of a session as parallel jobs, so a shared generator
   would hand out its numbers in thread order. A draw here depends only on synced inputs: the area, the purpose
   (stream), the session's game time and the draw's index among that area's draws of that stream at that time;
   the cursor (one per area and stream, in the caller's own record) counts the index and restarts at a new time.
   Peers get the same numbers whatever order the areas run in. */
typedef struct {
    long long time;
    uint32_t idx;
} HlibRngCursor;
/* The game salt: one value per game (rngsalt.c says where it comes from and how it is saved) mixed into every draw,
   so a new game deals other numbers; 0, the value before a game set one, leaves the draws as they were. Not
   thread-safe: rngsalt.c holds a lock around these; hlib_rng_mix reads the salt alone. A salt is FORMING from the
   first map until a session steps, RUNNING after (a session added to a running game cannot change it). */
enum { HLIB_SALT_NONE, HLIB_SALT_FORMING, HLIB_SALT_RUNNING };
uint32_t hlib_rng_salt(void);
int hlib_rng_salt_state(void);
uint32_t hlib_rng_salt_of(uint32_t map_seed, uint32_t session);
/* A new session's map: the salt of the lowest session GUID while forming (the same on every peer whatever order the
   sessions load in); 1 when this map's was taken. sessions_alive: how many sessions are stepping now. */
int hlib_rng_salt_from_map(uint32_t map_seed, uint32_t session, int sessions_alive);
/* A save's block (present) or its absence: the salt is the save's. 1 when it is news (first, or a different value). */
int hlib_rng_salt_restore(uint32_t salt, int present);
void hlib_rng_salt_running(void); /* a session stepped */
void hlib_rng_salt_clear(void);   /* the game is over */
uint32_t hlib_rng_mix(uint32_t area, uint32_t stream, long long time, uint32_t idx, uint32_t upper); /* < upper */
uint32_t hlib_rng_draw(HlibRngCursor *c, uint32_t area, uint32_t stream, long long time, uint32_t upper);

/* FNV-1a over 32 bits of input, for deterministic per-(seed,area,fertility) draws that must be the
   same on every peer and across a reload. */
uint32_t hlib_fnv1a_u32(uint32_t seed, uint32_t value);
/* FNV-1a over a whole NUL-terminated text (the settings digest) */
uint32_t hlib_fnv1a_text(const char *s);
/* A long "k=v k=v ..." text in log lines: the length of its next piece of at most max characters, cut after a blank
   (a word longer than max is cut at max); 0 at the end. */
int hlib_text_piece(const char *text, int max);

/* Participant kinds as the game's profile type numbers them; the AI ones are the second and third parties. */
enum { HLIB_PK_HUMAN = 0, HLIB_PK_SECOND = 1, HLIB_PK_THIRD = 2, HLIB_PK_VIRTUAL = 3 };
int hlib_kind_is_ai(int kind);
/* The humans the multiplayer refusals count: those read, plus every participant that could not be read (its asset not
   found, as while an asset reload keeps the lookups off); a list that is there but cannot be read counts as two. */
int hlib_human_tally(int list_broken, int humans, int unreadable);

/* The asset reload's pending state (engine.c): 1 while it holds. One whose engine start line was not seen lapses after
   max_ms (a lost line cannot keep the lookups dark); once the engine began its rebuild only the end line ends it. */
int hlib_reload_holds(int pending, int started, uint64_t since_ms, uint64_t now_ms, uint64_t max_ms);

/* Difficulty rank of a rival from the GUIDs of its asset and its bases (nearest first): the first one
   that is one of the three difficulty templates gives 0 easy, 1 medium, 2 hard; none gives -1. */
int hlib_rival_rank(const uint32_t *chain, int n, uint32_t easy, uint32_t medium, uint32_t hard);

/* Mod profiles. profile.txt names the active profile; profiles/<name>.txt lists one mod per line ('#' in front
   disables, "EnableNewMods" lets unlisted mods in). The switch records the profile it replaced in a comment
   line of profile.txt, so going back needs no file of its own. */
int hlib_profile_active(const char *text, char *out, int cap);   /* the ActiveProfile value, "" when none */
int hlib_profile_previous(const char *text, char *out, int cap); /* the recorded profile, -1 when none */
/* 1 when the list enables mod_id: listed without '#', or unlisted while new mods are on and it is present */
int hlib_profile_enables(const char *list, const char *mod_id, int present);
/* profile.txt with ActiveProfile set to name and previous recorded (0 or "" records none); the length or -1 */
int hlib_profile_rewrite(const char *text, const char *name, const char *previous, char *out, int cap);

/* mod-loader.log as the game's loader writes it at every start: 1 mod_id is among the enabled mods, 0 it is not,
   -1 the list is not complete (no "Enabled mods:" summary yet); *count the summary's number, -1 without it. */
int hlib_loader_log_enables(const char *log, const char *mod_id, int *count);
/* The title's buttons of one group as the engine keeps them; ours is appended once (a type the game has none of). */
typedef struct {
    uint16_t type, pad[3];
    uint64_t text;
} HlibTitleButton;
/* the group with ours added in out: the new count, 0 when ours is already there, -1 when out is too small */
int hlib_title_buttons_add(const HlibTitleButton *b, int n, uint16_t type, uint64_t text, HlibTitleButton *out,
                            int cap);

/* 1 when a config path (UTF-16, n units; the engine keeps it lower case with forward slashes) lies under the mod's
   status markers, data/anno2070/graphics/ui/status/ (either case, either slash) */
int hlib_status_cfg_path(const uint16_t *s, size_t n);

/* Our assets are named a2070_<source GUID>_<name> (a2070_<name> without a source GUID): the source GUID an asset was
   made from, which the mod's registry keeps next to ours. The scan reads <GUID>n</GUID><Name>..</Name> pairs of an
   assets.xml text and returns how many of our assets with a source GUID it found (up to max). */
typedef struct {
    uint32_t ours, source;
} HlibSourcePair;
uint32_t hlib_name_source(const char *name); /* the source GUID in an asset name, 0 when it has none */
int hlib_scan_sources(const char *text, size_t len, HlibSourcePair *out, int max);

/* The game time a session context gives, as the engine's Maintenance tick reads it: the context's own clock, but in
   states 5 and 6 the game clock (global_ms, -1 when unknown); no context or state 8 (none): -1. */
long long hlib_game_time_pick(int has_ctx, int state, long long ctx_ms, long long global_ms);

/* The switch button's steps (modeswitch.c): a click asks (the game's popup, else a Windows box), Yes writes the
   profile, and the title's next update quits through the game's own Quit. A click while one runs is ignored.
   hlib_switch_step gives the state after an event, the same state when the event does not apply. */
enum { HLIB_SW_IDLE, HLIB_SW_ASKING, HLIB_SW_QUIT, HLIB_SW_LEAVING };
enum { HLIB_SW_EV_CLICK, HLIB_SW_EV_CANCEL, HLIB_SW_EV_FAILED, HLIB_SW_EV_WRITTEN, HLIB_SW_EV_TITLE };
int hlib_switch_step(int state, int event);
/* 1 when popup id is among the game's open popups: entries of stride bytes between begin and end, each starting with
   its id (at most 64 looked at; no id or no list: 0) */
int hlib_popup_listed(const unsigned char *begin, const unsigned char *end, unsigned stride, int id);

/* The cost meter: per module lock its calls, the time inside and waiting for the lock (microseconds) since the
   last report. The line names the busiest (most time inside first) up to top, those with at least min_us. */
typedef struct {
    const char *name;
    long long calls, inside_us, wait_us, max_us;
} HlibPerfRow;
int hlib_perf_line(HlibPerfRow *rows, int n, int top, long long min_us, int seconds, char *out, int cap);

#endif

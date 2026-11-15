/* Engine access: the asset and property system, the engine's string layout, the limited-lode read,
   and the participant and synchronised-randomness helpers the module recipe relies on. Reads run by
   mirroring the engine's own access, under the caller's SEH. */
#include "hlib.h"
#include "compwalk_logic.h"
#include "targets.h"
#include "util.h"
#include <intrin.h>

static void **asset_manager(void) {
    unsigned char *base = hlib_image_base();
    if (!base) return NULL;
    void *am = *(void **)(base + T_ASSET_MANAGER_RVA);
    if (!am) return NULL;
    void **vt = *(void ***)am;
    if (!hlib_in_image(vt) || !hlib_in_image(vt[T_ASSET_VT_GETASSET]) ||
        !hlib_in_image(vt[T_ASSET_VT_GETASSETS_OF_TEMPLATE]))
        return NULL;
    return (void **)am;
}

static volatile LONG g_reload_pending, g_reload_started, g_reload_gen;
static volatile ULONGLONG g_reload_since;

void hlib_asset_reload_begin(void) {
    InterlockedExchange64((volatile LONG64 *)&g_reload_since, (LONG64)GetTickCount64());
    InterlockedExchange(&g_reload_started, 0);
    InterlockedExchange(&g_reload_pending, 1);
}

void hlib_asset_reload_started(void) {
    if (g_reload_pending) InterlockedExchange(&g_reload_started, 1);
}

void hlib_asset_reload_end(void) {
    InterlockedIncrement(&g_reload_gen);
    InterlockedExchange(&g_reload_started, 0);
    InterlockedExchange(&g_reload_pending, 0);
    hlib_registry_invalidate(); /* our asset records point into the data the reload replaced */
}

int hlib_asset_reload_pending(void) {
    if (hlib_reload_holds(g_reload_pending, g_reload_started, (uint64_t)g_reload_since, GetTickCount64(),
                           HLIB_RELOAD_PENDING_MAX_MS))
        return 1;
    if (!g_reload_pending) return 0;
    hlib_log("asset reload: the engine began no rebuild in 90 s, the asset lookups work again");
    hlib_asset_reload_end();
    return 0;
}

uint32_t hlib_asset_generation(void) { return (uint32_t)g_reload_gen; }

void *hlib_asset(uint32_t guid) {
    if (hlib_asset_reload_pending()) return NULL;
    void **am = asset_manager();
    if (!am) return NULL;
    void **vt = *(void ***)am;
    return ((void *(*)(void *, unsigned))vt[T_ASSET_VT_GETASSET])(am, guid);
}

void *hlib_assets_of_template(uint16_t tmpl) {
    if (hlib_asset_reload_pending()) return NULL;
    void **am = asset_manager();
    if (!am) return NULL;
    void **vt = *(void ***)am;
    return ((void *(*)(void *, unsigned))vt[T_ASSET_VT_GETASSETS_OF_TEMPLATE])(am, tmpl);
}

/* Property container at asset+8; mode byte +0x28 is 0 for up to 3 inline (id,value) pairs, 1 for a
   sorted vector of 16-byte entries. Returns the value pointer or 0. */
void *hlib_prop(void *asset, uint16_t id) {
    if (!asset) return NULL;
    unsigned char *c = (unsigned char *)asset + T_OFF_ASSET_PROPS;
    if (c[T_OFF_PROPC_MODE] == 0) {
        unsigned long long n = *(unsigned long long *)(c + T_OFF_PROPC_COUNT);
        for (unsigned long long i = 0; i < n && i < T_PROPC_INLINE; i++)
            if (((unsigned short *)c)[i] == id) return ((void **)c)[i + 1];
        return NULL;
    }
    if (c[T_OFF_PROPC_MODE] != 1) return NULL;
    unsigned char *b = *(unsigned char **)c, *e = *(unsigned char **)(c + 8);
    for (int k = 0; b && b < e && k < 4096; b += T_PROPC_ENTRY, k++)
        if (*(unsigned short *)b == id) return *(unsigned char **)(b + T_OFF_PROPC_VALUE);
    return NULL;
}

/* MSVC std::wstring / std::string keep the data inline until capacity (+0x18) passes the small buffer. */
int hlib_narrow_wstr(const void *s, char *out, int cap) {
    const unsigned char *w = (const unsigned char *)s;
    unsigned long long n = *(unsigned long long *)(w + T_STD_STR_SIZE), res = *(unsigned long long *)(w + T_STD_STR_CAP);
    const uint16_t *p = res > 7 ? *(const uint16_t **)w : (const uint16_t *)w;
    return hlib_u16_to_ascii(p, n, out, cap);
}

int hlib_narrow_str(const void *s, char *out, int cap) {
    const unsigned char *w = (const unsigned char *)s;
    unsigned long long n = *(unsigned long long *)(w + T_STD_STR_SIZE), res = *(unsigned long long *)(w + T_STD_STR_CAP);
    const char *p = res > 15 ? *(const char **)w : (const char *)w;
    int i = 0;
    for (; i < cap - 1 && (unsigned long long)i < n; i++) out[i] = p[i];
    out[i] = 0;
    return i;
}

/* An island area's limited-lode component: the area keeps a pointer to it at a fixed offset, 0 when it has none
   (as the engine's own sum over a participant's areas reads it). */
static void *lode_component(void *area) {
    return area ? *(void **)((unsigned char *)area + T_AREA_LODE_COMPONENT_OFF) : NULL;
}

/* The limited-lode amount an island area currently holds for a deposit fertility (0 if none); the getter takes
   (component, fertilityGuid). */
int hlib_lode_amount(void *area, uint32_t fertility) {
    void *fn = hlib_target(T_LODE_GET_CURRENT);
    if (!fn || !area) return -1;
    void *comp = lode_component(area);
    return comp ? (int)((unsigned long long (*)(void *, uint32_t))fn)(comp, fertility) : 0;
}

/* Participants: read from the game's participant manager, as its own script bindings read it (any
   thread; callers hold the SEH guard). Kind from the asset's profile type, a rival's difficulty from the
   difficulty template among its bases (targets.h). */
static HlibAiRulesFn s_rules;
void hlib_participant_rules(HlibAiRulesFn fn) { s_rules = fn; }

static char *participant_mgr(void) {
    void **h = (void **)hlib_target(T_DATA_LOCAL_PLAYER);
    return h && *h ? (char *)*h - T_OFF_PM_HOLDER : NULL;
}

int hlib_participant_count(void *session) {
    (void)session;
    char *m = participant_mgr();
    if (!m) return 0;
    char *b = *(char **)(m + T_OFF_PM_LIST), *e = *(char **)(m + T_OFF_PM_LIST + 8);
    long long n = b && e > b ? (e - b) / T_PM_ENTRY_SIZE : 0;
    return n > 4096 ? 0 : (int)n;
}

uint32_t hlib_local_participant(void) {
    char *m = participant_mgr();
    return m ? *(uint32_t *)(m + T_OFF_PLAYER_INDEX) : 0;
}

static uint32_t asset_guid(void *asset) {
    char *std = (char *)hlib_prop(asset, T_PROP_STANDARD);
    return std ? *(uint32_t *)(std + T_OFF_PROPVAL_DATA) : 0;
}

int hlib_participant_info(void *session, int index, HlibParticipant *out) {
    char *m = participant_mgr();
    if (!m || index < 0 || index >= hlib_participant_count(session)) return 0;
    uint32_t guid = *(uint32_t *)(*(char **)(m + T_OFF_PM_LIST) + (size_t)index * T_PM_ENTRY_SIZE);
    char *asset = (char *)hlib_asset(guid);
    char *pp = asset ? (char *)hlib_prop(asset, T_PROP_PARTICIPANT) : NULL;
    uint32_t chain[6];
    int n = 0;
    for (char *a = asset; a && n < 6; a = *(char **)(a + T_OFF_ASSET_BASE)) chain[n++] = asset_guid(a);
    out->index = index;
    out->guid = guid;
    out->kind = pp ? *(int16_t *)(pp + T_OFF_PARTICIPANT_KIND) : -1;
    out->is_ai = hlib_kind_is_ai(out->kind);
    out->profile = out->is_ai ? guid : 0;
    out->difficulty =
        out->kind == HLIB_PK_SECOND ? hlib_rival_rank(chain, n, T_RIVAL_EASY, T_RIVAL_MEDIUM, T_RIVAL_HARD) : -1;
    out->ai_rules = 0;
    if (s_rules) out->ai_rules = s_rules(out);
    return 1;
}

/* a participant whose asset is not found (the lookups are off while an asset reload holds) may be a human: the
   multiplayer refusals must not read it as none */
int hlib_human_count(void) {
    int humans = 0, unreadable = 0, n = hlib_participant_count(NULL);
    char *m = participant_mgr(), *b = m ? *(char **)(m + T_OFF_PM_LIST) : NULL, *e = m ? *(char **)(m + T_OFF_PM_LIST + 8) : NULL;
    int broken = (b || e) && !n && b != e; /* a list there whose bounds make no sense */
    HlibParticipant p;
    for (int i = 0; i < n; i++) {
        if (!hlib_participant_info(NULL, i, &p) || (p.kind < 0 && !hlib_asset(p.guid))) unreadable++;
        else humans += p.kind == HLIB_PK_HUMAN;
    }
    return hlib_human_tally(broken, humans, unreadable);
}

/* An area of the session whose context the thread holds (the simulation step, a command, a script call
   the engine scoped to a session). */
void *hlib_area(int area_id) {
    char *(*gsm)(void) = (char *(*)(void))hlib_target(T_SESSION_GSM);
    void *(*get)(void *, short) = (void *(*)(void *, short))hlib_target(T_AREA_GET);
    char *g = gsm ? gsm() : NULL;
    return g && get ? get(g + T_OFF_GSM_AREAS, (short)area_id) : NULL;
}

/* Synchronised randomness: the session's own generator, the one the simulation draws from, reached
   through the engine's thread-local session context (valid on the simulation step and inside a command).
   Peers draw in the same order, so they get the same numbers. */
typedef void *(*RandMgrFn)(void);
typedef unsigned long long (*RangeFn)(void *state, int lo, int hi);

static void *rand_state(void) {
    RandMgrFn mgr = (RandMgrFn)hlib_target(T_SESSION_RANDOM);
    if (!mgr || !hlib_target(T_SIM_RANDOM_U32)) return NULL;
    void *m = mgr();
    return m ? *(void **)((char *)m + T_OFF_RANDOM_STATE) : NULL;
}

int hlib_rand_ok(void) { return rand_state() != NULL; }

uint32_t hlib_rand(void *session, uint32_t upper_exclusive) {
    (void)session;
    if (upper_exclusive <= 1) return 0;
    void *st = rand_state();
    if (!st) return 0;
    return (uint32_t)((RangeFn)hlib_target(T_SIM_RANDOM_U32))(st, 0, (int)(upper_exclusive - 1));
}

/* Game objects: GUID, owner, area, id and position at fixed offsets; components through the object's own
   lookup by property id, as every engine system fetches them. */
uint32_t hlib_obj_guid(const void *obj) { return obj ? *(const uint32_t *)((const char *)obj + T_OFF_OBJ_GUID) : 0; }
uint32_t hlib_obj_owner(const void *obj) { return obj ? *(const uint32_t *)((const char *)obj + T_OFF_OBJ_OWNER) : 0; }
int hlib_obj_area(const void *obj) { return obj ? *(const unsigned short *)((const char *)obj + T_OFF_OBJ_AREA) : -1; }
long long hlib_obj_id(const void *obj) {
    return obj ? *(const long long *)((const char *)obj + T_OFF_OBJ_ID) & T_OID_MASK : 0;
}
const float *hlib_obj_pos(const void *obj) { return (const float *)((const char *)obj + T_OFF_OBJ_POS); }

void *hlib_obj_component(void *obj, int which) {
    static const unsigned short ids[] = {T_PROP_PAUSABLE,  T_PROP_BUFFABLE,  T_PROP_FACTORY7,     T_PROP_RESIDENCE7,
                                         T_PROP_LOGISTIC,  T_PROP_WAREHOUSE, T_PROP_ITEMCONTAINER};
    if (!obj || which < 0 || which >= HLIB_COMP_COUNT) return NULL;
    void *(*getc)(void *, unsigned, int) = (void *(*)(void *, unsigned, int))(*(void ***)obj)[T_VT_OBJ_COMPONENT];
    return getc(obj, ids[which], 1);
}

void *hlib_obj_by_id(long long id) {
    void *(*mgr)(void) = (void *(*)(void))hlib_target(T_OBJ_MGR);
    void *(*byid)(void *, long long, int) = (void *(*)(void *, long long, int))hlib_target(T_OBJ_BY_ID);
    void *m = mgr ? mgr() : NULL;
    return m && byid && id > 0 ? byid(m, id, T_OBJ_BY_ID_FLAGS) : NULL;
}

void *hlib_factory_object(void *factory) { return factory ? *(void **)((char *)factory + T_OFF_FACTORY_OBJ) : NULL; }

int hlib_paused_bits(void *obj) {
    char *pc = (char *)hlib_obj_component(obj, HLIB_COMP_PAUSABLE);
    return pc ? (int)(*(unsigned *)(pc + T_OFF_PAUSE_FLAGS) & T_PAUSE_REASON_MASK) : -1;
}

int hlib_set_paused(void *obj, int bit, int on) {
    unsigned long long (*setr)(void *, int, char) = (unsigned long long (*)(void *, int, char))hlib_target(T_PAUSE_REASON);
    char *pc = (char *)hlib_obj_component(obj, HLIB_COMP_PAUSABLE);
    if (!pc || !setr || bit < 0 || bit > 11) return 0;
    int held = (*(unsigned *)(pc + T_OFF_PAUSE_FLAGS) >> bit) & 1;
    if (held == !!on) return 0;
    setr(pc, bit, (char)!!on);
    return 1;
}

/* The buff component keeps its buffs as 32-byte entries {GUID, pad, source id, ...}; the library's own carry
   T_BUFF_SOURCE, which the engine treats as no effect instance (no merge with an effect's buffs). */
int hlib_buff_has(void *obj, uint32_t buff) {
    char *bc = (char *)hlib_obj_component(obj, HLIB_COMP_BUFFABLE);
    if (!bc) return 0;
    char *b = *(char **)(bc + T_OFF_BUFFS_BEGIN), *e = *(char **)(bc + T_OFF_BUFFS_END);
    for (int k = 0; b && b < e && k < 4096; b += T_BUFF_ENTRY_SIZE, k++)
        if (*(uint32_t *)b == buff && *(long long *)(b + T_OFF_BUFF_SOURCE) == T_BUFF_SOURCE) return 1;
    return 0;
}

int hlib_buff_add(void *obj, uint32_t buff) {
    void (*add)(void *, int, long long, float) = (void (*)(void *, int, long long, float))hlib_target(T_BUFF_ADD);
    void *bc = hlib_obj_component(obj, HLIB_COMP_BUFFABLE);
    if (!add || !bc || !buff || hlib_buff_has(obj, buff)) return 0;
    add(bc, (int)buff, T_BUFF_SOURCE, 1.0f);
    return 1;
}

int hlib_buff_carried(uint32_t buff, void *obj) { return hlib_buff_has(obj, buff) == 1; }

int hlib_buff_remove(void *obj, uint32_t buff) {
    long long (*rem)(void *, int, long long) = (long long (*)(void *, int, long long))hlib_target(T_BUFF_REMOVE);
    void *bc = hlib_obj_component(obj, HLIB_COMP_BUFFABLE);
    if (!rem || !bc || !hlib_buff_has(obj, buff)) return 0;
    rem(bc, (int)buff, T_BUFF_SOURCE);
    return 1;
}

/* the factory's base productivity, as its script readout computes it (three factors, 0 while it cannot work) */
float hlib_factory_productivity(void *obj) {
    char *f = (char *)hlib_obj_component(obj, HLIB_COMP_FACTORY);
    if (!f) return -1.0f;
    if (!*(f + T_OFF_FACTORY_WORKING)) return 0.0f;
    const float *p = (const float *)(f + T_OFF_FACTORY_PRODUCTIVITY);
    return p[0] * p[1] * p[2];
}

uint32_t hlib_factory_fertility(void *factory) {
    return factory ? *(uint32_t *)((char *)factory + T_OFF_FACTORY_FERTILITY) : 0;
}

float hlib_residents(void *obj) {
    char *r = (char *)hlib_obj_component(obj, HLIB_COMP_RESIDENCE);
    return r ? *(float *)(r + T_OFF_RESIDENCE_COUNT) : -1.0f;
}

float hlib_residents_max(void *obj) {
    char *r = (char *)hlib_obj_component(obj, HLIB_COMP_RESIDENCE);
    return r ? *(float *)(r + T_OFF_RESIDENCE_MAX) : -1.0f;
}

/* 0 the asset is swapped in place (Change), 1 the object is rebuilt (Replace): as the lower tier's upgrade entry says */
static int upgrade_mode(uint32_t from, uint32_t to) {
    char *prop = (char *)hlib_prop(hlib_asset(from), T_PROP_UPGRADABLE);
    char *b = prop ? *(char **)(prop + T_OFF_UPGRADABLE_LIST) : NULL, *e = prop ? *(char **)(prop + T_OFF_UPGRADABLE_LIST + 8) : NULL;
    for (int k = 0; b && b < e && k < 64; b += T_UPGRADABLE_ENTRY, k++)
        if (*(uint32_t *)b == to) return *(int *)(b + T_OFF_UPGRADABLE_MODE) == 1;
    return 0;
}

int hlib_obj_upgrade(void *obj, int area, uint32_t to) {
    int64_t (*change)(void *, int, int, char) = (int64_t(*)(void *, int, int, char))hlib_target(T_OBJ_CHANGE_GUID);
    uint64_t (*replace)(void *, int, char, char) = (uint64_t(*)(void *, int, char, char))hlib_target(T_OBJ_REPLACE_GUID);
    HlibScope sc;
    if (!obj || !to || !change || !replace) return 0;
    int replace_mode = upgrade_mode(hlib_obj_guid(obj), to);
    if (!hlib_scope_enter(&sc, area)) return 0; /* not in the island's session: the engine call would run in the caller's */
    __try {
        if (replace_mode) replace(obj, (int)to, 0, 1);
        else change(obj, (int)to, *(int *)((char *)obj + T_OFF_OBJ_VARIATION), 1);
    } __finally {
        hlib_scope_leave(&sc); /* also when the engine call throws */
    }
    return 1;
}

/* the game's thread-local block (its TLS slot 0) of the calling thread */
char *hlib_tls_block(void) {
    char **slots = (char **)__readgsqword(0x58);
    return slots ? slots[0] : NULL;
}

void *hlib_session_ctx(void) {
    char *block = hlib_tls_block();
    return block ? *(void **)(block + T_TLS_CTX) : NULL;
}

/* the context of a session index, as the engine's scope guard reads it from the session table */
void *hlib_session_ctx_of(int sid) {
    void **holder = (void **)hlib_target(T_DATA_SESSION_TABLE);
    if (!holder || !*holder || sid < 0 || sid > 63) return NULL;
    char *ctx = *(char **)((char *)*holder - 8 + T_OFF_SESSTAB_LIST + (size_t)sid * 8);
    return ctx && *(int *)(ctx + T_OFF_CTX_STATE) != 8 ? ctx : NULL;
}

int hlib_session_index(void) {
    char *ctx = (char *)hlib_session_ctx();
    return ctx && *(int *)(ctx + T_OFF_CTX_STATE) != 8 ? *(int16_t *)(ctx + T_OFF_CTX_INDEX) : -1;
}

int hlib_first_session(void) {
    for (int sid = 0; sid < 64; sid++) {
        char *ctx = (char *)hlib_session_ctx_of(sid);
        int state = ctx ? *(int *)(ctx + T_OFF_CTX_STATE) : 8;
        if (state != 8) return sid; /* a session there (states 5 and 6 are the played ones too) */
    }
    return -1;
}

/* as the Maintenance tick reads it: the context's clock, in states 5 and 6 the game clock (util.c) */
long long hlib_meta_time_ms(void) {
    char **clock = (char **)hlib_target(T_DATA_GAME_CLOCK);
    return clock && *clock ? *(long long *)(*clock + T_OFF_GAME_CLOCK_MS) + T_GAME_CLOCK_LEAD_MS : -1;
}

long long hlib_session_time_ms(void) {
    char *ctx = (char *)hlib_session_ctx();
    if (!ctx) return -1;
    int state = *(int *)(ctx + T_OFF_CTX_STATE);
    char **clock = (char **)hlib_target(T_DATA_GAME_CLOCK);
    long long global = clock && *clock ? *(long long *)(*clock + T_OFF_GAME_CLOCK_MS) + T_GAME_CLOCK_LEAD_MS : -1;
    return hlib_game_time_pick(1, state, *(long long *)(ctx + T_OFF_CTX_TIME), global);
}

long long hlib_step_time_ms(void) {
    long long t = hlib_session_time_ms();
    return t >= 0 ? t : hlib_meta_time_ms();
}

int hlib_scope_save(HlibScope *s) {
    char *block = hlib_tls_block();
    if (!block) return 0;
    s->ctx = *(void **)(block + T_TLS_CTX);
    s->aux = *(void **)(block + T_TLS_CTX_AUX);
    s->scope = *(void **)(block + T_TLS_SCOPE);
    return 1;
}

int hlib_scope_enter(HlibScope *s, int area_id) {
    char *block = hlib_tls_block();
    char *ctx = area_id > 0 ? (char *)hlib_session_ctx_of(area_id & T_AREA_SESSION_MASK) : NULL;
    if (!block || !ctx || !hlib_scope_save(s)) return 0;
    *(void **)(block + T_TLS_CTX) = ctx;
    *(void **)(block + T_TLS_CTX_AUX) = *(void **)(ctx + T_OFF_CTX_AUX);
    void *area = hlib_island_area(area_id); /* the island area, as the engine's guard puts there (not hlib_area's) */
    if (area) *(void **)(block + T_TLS_SCOPE) = area;
    else { /* the context is the island's session, the scope still the caller's: seen in the log, not guessed at */
        static HlibLogRate rate;
        hlib_log_every(&rate, 60000, "scope: area %#x has no island area, only its session context is entered", area_id);
    }
    hlib_session_note(area_id & T_AREA_SESSION_MASK, hlib_session_key(), ctx, hlib_session_time_ms());
    return 1;
}

void hlib_scope_leave(const HlibScope *s) {
    char *block = hlib_tls_block();
    if (!block) return;
    *(void **)(block + T_TLS_CTX) = s->ctx;
    *(void **)(block + T_TLS_CTX_AUX) = s->aux;
    *(void **)(block + T_TLS_SCOPE) = s->scope;
}

void hlib_each_session(const int area_of[64], HlibSidFn fn, void *ctx) {
    for (int sid = 0; sid < 64; sid++) {
        HlibScope sc;
        if (!area_of[sid] || !hlib_scope_enter(&sc, area_of[sid])) continue;
        fn(sid, ctx);
        hlib_scope_leave(&sc);
    }
}

void *hlib_session_key(void) {
    void *(*f)(void) = (void *(*)(void))hlib_target(T_SESSION_REGROW);
    return f ? f() : NULL;
}

int hlib_lode_add(void *area, uint32_t fertility, int amount) {
    void (*f)(void *, uint32_t, int) = (void (*)(void *, uint32_t, int))hlib_target(T_LODE_ADD);
    void *comp = lode_component(area);
    if (!f || !comp || amount <= 0) return 0;
    f(comp, fertility, amount);
    return 1;
}

int hlib_lode_remove(void *area, uint32_t fertility, int amount) {
    void (*f)(void *, uint32_t, int) = (void (*)(void *, uint32_t, int))hlib_target(T_LODE_REMOVE);
    void *comp = lode_component(area);
    if (!f || !comp || amount <= 0) return 0;
    f(comp, fertility, amount);
    return 1;
}

/* the area id the session's area grid holds for a tile (16x16 blocks, a block of one id or its own tiles) */
int hlib_area_at(float x, float z) {
    char *(*gsm)(void) = (char *(*)(void))hlib_target(T_SESSION_GSM);
    char *g = gsm ? gsm() : NULL;
    int tx = (int)x, tz = (int)z;
    if (!g || x < 0 || z < 0) return -1;
    int w = *(int *)(g + T_OFF_GSM_AREAGRID_W), h = *(int *)(g + T_OFF_GSM_AREAGRID_H);
    if (tx >= w || tz >= h) return -1;
    char *cells = *(char **)(g + T_OFF_GSM_AREAGRID_T);
    long idx = (long)(((tx >> T_AREAGRID_SHIFT) + (tz >> T_AREAGRID_SHIFT) * *(int *)(g + T_OFF_GSM_AREAGRID_BR)) * T_AREAGRID_ENTRY);
    char *block = *(char **)(cells + idx + T_OFF_AREAGRID_BLOCK);
    return block ? *(unsigned short *)(block + (((tz & T_AREAGRID_CELL_MASK) << T_AREAGRID_SHIFT) + (tx & T_AREAGRID_CELL_MASK)) * 2) : *(unsigned short *)(cells + idx);
}

int hlib_ground_height(float x, float z, float *h) {
    void *(*mgr)(void) = (void *(*)(void))hlib_target(T_TERRAIN_MGR);
    void *(*get)(void *, float *, float *, void *, short) = (void *(*)(void *, float *, float *, void *, short))hlib_target(T_TERRAIN_HEIGHT);
    void *ctx = hlib_target(T_DATA_HEIGHT_CTX);
    void *m = mgr ? mgr() : NULL;
    float out[2] = {0, 0}, pos[2] = {x, z};
    *h = 0.0f;
    if (!m || !get || !ctx) return 0;
    get(m, out, pos, ctx, 0);
    if (!((unsigned char *)out)[T_OFF_HEIGHT_VALID]) return 0; /* the query's valid byte */
    *h = out[0];
    return 1;
}

uint32_t hlib_area_owner(int area_id) {
    char *a = area_id > 0 ? (char *)hlib_area(area_id) : NULL;
    return a ? *(uint32_t *)(a + T_OFF_AREA_OWNER) : 0;
}

uint32_t hlib_current_participant(void) {
    char *block = hlib_tls_block();
    return block ? *(uint32_t *)(block + T_TLS_PARTICIPANT) : 0;
}

uint32_t hlib_set_participant(uint32_t p) {
    char *block = hlib_tls_block();
    if (!block) return 0;
    uint32_t old = *(uint32_t *)(block + T_TLS_PARTICIPANT);
    *(uint32_t *)(block + T_TLS_PARTICIPANT) = p;
    return old;
}

static char *storage_entry(char *node, uint32_t product);

/* the economy manager's entry of a participant's meta economy (the engine's lookup for the acting participant) */
static char *meta_entry(uint32_t participant) {
    void **h = (void **)hlib_target(T_DATA_ECONOMY);
    if (!h || !*h || !participant) return NULL;
    char *em = (char *)*h - 8, *b = *(char **)(em + T_OFF_EM_META), *e = *(char **)(em + T_OFF_EM_META + 8);
    for (int k = 0; b && b < e && k < 256; b += T_META_ENTRY, k++)
        if (*(uint32_t *)b == participant) return *(char **)(b + T_OFF_META_ECONOMY);
    return NULL;
}

static int user_ptr(const void *p) { return (uintptr_t)p >= 0x10000 && (uintptr_t)p < 0x7fffffff0000ULL; }

/* The meta economy the library may read: the storage node's class (its add slot and the node's own), or, since the
   first session (2026-10-27) found the player's of another class, any whose map reads as a storage node's and holds
   the money. The engine's own add calls slot 24 on whatever the lookup gives, so the add stays the engine's. */
static int meta_class(char *econ) {
    void **vt = econ ? *(void ***)econ : NULL;
    if (!vt || !hlib_in_image(vt) || !hlib_target(T_META_ADD) || !hlib_target(T_STORAGE_ADD)) return 0;
    if (vt[T_VT_META_ADD] == hlib_target(T_META_ADD) && vt[T_VT_STORAGE_ADD] == hlib_target(T_STORAGE_ADD)) return 1;
    unsigned long long mask = *(unsigned long long *)(econ + T_OFF_SN_MASK);
    if (!hlib_in_image(vt[T_VT_META_ADD]) || !user_ptr(*(void **)(econ + T_OFF_SN_BUCKETS)) || !user_ptr(*(void **)(econ + T_OFF_SN_END)) ||
        mask > 0xfffff || (mask & (mask + 1)))
        return 0;
    return storage_entry(econ, T_PRODUCT_MONEY) ? 2 : 0;
}

static char *meta_economy(uint32_t participant) {
    static int n_layout;
    char *econ = meta_entry(participant);
    int kind = meta_class(econ);
    if (kind == 2) {
        void **vt = *(void ***)econ;
        hlib_log_n(&n_layout, 1, "economy: the meta economy of participant %u is read by its layout (vtable exe 0x%llX, slot 18 exe 0x%llX, slot 24 exe 0x%llX)",
                    participant, hlib_exe_addr(vt), hlib_exe_addr(vt[T_VT_STORAGE_ADD]), hlib_exe_addr(vt[T_VT_META_ADD]));
    }
    return kind ? econ : NULL;
}

/* why meta_economy finds nothing, for a log line (the first run of the info bar read no money and said nothing) */
const char *hlib_meta_why(uint32_t participant) {
    void **h = (void **)hlib_target(T_DATA_ECONOMY);
    if (!h || !*h) return "the economy manager is not there";
    if (!participant) return "no participant yet (read again on the next frame)";
    if (!hlib_target(T_META_ADD) || !hlib_target(T_STORAGE_ADD)) return "the meta storage targets are unresolved";
    char *em = (char *)*h - 8, *b = *(char **)(em + T_OFF_EM_META), *e = *(char **)(em + T_OFF_EM_META + 8);
    if (!b || b >= e) return "the economy manager lists no meta economy yet";
    static char buf[200];
    char *econ = meta_entry(participant);
    void **vt = econ ? *(void ***)econ : NULL;
    if (vt && hlib_in_image(vt)) {
        hlib_format(buf, (int)sizeof(buf),
                     "the meta economy of the participant is not a storage node of the expected class and its map does not read as one "
                     "(vtable exe 0x%llX, slot 18 exe 0x%llX, slot 24 exe 0x%llX)",
                     hlib_exe_addr(vt), hlib_exe_addr(vt[T_VT_STORAGE_ADD]), hlib_exe_addr(vt[T_VT_META_ADD]));
        return buf;
    }
    if (econ) return "the meta economy of the participant has no vtable in the image";
    hlib_format(buf, (int)sizeof(buf), "%d meta economies listed (the first for %u), none for participant %u", (int)((e - b) / 16),
                 *(uint32_t *)b, participant);
    return buf;
}

/* the storage node's entry of a product: its hash map, the bucket walked from its last node back to its first */
static char *storage_entry(char *node, uint32_t product) {
    unsigned long long h = 0xcbf29ce484222325ULL;
    for (int i = 0; i < 4; i++) h = (h ^ ((product >> (8 * i)) & 0xff)) * 0x100000001b3ULL;
    char *end = *(char **)(node + T_OFF_SN_END), *buckets = *(char **)(node + T_OFF_SN_BUCKETS);
    unsigned long long mask = *(unsigned long long *)(node + T_OFF_SN_MASK);
    if (!buckets) return NULL;
    char **bk = (char **)(buckets + ((h & mask) << 4));
    char *it = bk[1];
    for (int k = 0; it && it != end && k < 4096; k++) {
        if (*(uint32_t *)(it + T_OFF_SNE_KEY) == product) return it;
        if (it == bk[0]) break;
        it = *(char **)(it + 8);
    }
    return NULL;
}

/* a storage node the library reads: its add slot is the node's own or the island storage's */
static int known_storage(void *node) {
    void **vt = node ? *(void ***)node : NULL;
    return vt && hlib_in_image(vt) && hlib_target(T_STORAGE_ADD) &&
           (vt[T_VT_STORAGE_ADD] == hlib_target(T_STORAGE_ADD) || vt[T_VT_STORAGE_ADD] == hlib_target(T_AREA_ECONOMY_ADD));
}

void *hlib_obj_storage(void *obj) {
    char *ln = (char *)hlib_obj_component(obj, HLIB_COMP_LOGISTIC);
    return ln && known_storage(ln + T_OFF_LN_STORAGE) ? ln + T_OFF_LN_STORAGE : NULL;
}

void *hlib_area_storage(int area_id) {
    char *a = area_id > 0 ? (char *)hlib_area(area_id) : NULL;
    void *s = a ? *(void **)(a + T_OFF_AREA_STORAGE) : NULL;
    return known_storage(s) ? s : NULL;
}

int hlib_storage_amount(void *storage, uint32_t product, int *capacity) {
    if (!known_storage(storage)) return -1;
    char *e = storage_entry((char *)storage, product);
    if (capacity) *capacity = e ? *(int *)(e + T_OFF_SNE_CAPACITY) : 0;
    return e ? *(int *)(e + T_OFF_SNE_AMOUNT) : 0;
}

int hlib_storage_change(void *storage, uint32_t product, int amount) {
    if (!known_storage(storage) || !product || !amount) return 0;
    long long result[4] = {0}, info[8] = {0}; /* the result and the transfer's record, zeroed as the engine's own adds */
    typedef void (*ChangeFn)(void *, void *, void *, uint32_t, int, int, int);
    void **vt = *(void ***)storage;
    ChangeFn f = (ChangeFn)vt[amount > 0 ? T_VT_STORAGE_ADD : T_VT_STORAGE_TAKE];
    f(storage, result, info, product, amount > 0 ? amount : -amount, T_STORAGE_REASON_TRANSPORT, 0);
    return 1;
}

int hlib_transporters_free(void *obj) {
    int (*mx)(void *, int) = (int (*)(void *, int))hlib_target(T_LN_MAX_TRANSPORTERS);
    long long (*cur)(void *, int) = (long long (*)(void *, int))hlib_target(T_LN_CUR_TRANSPORTERS);
    void *ln = hlib_obj_component(obj, HLIB_COMP_LOGISTIC);
    return ln && mx && cur ? mx(ln, 0) - (int)cur(ln, 0) : -1;
}

int hlib_meta_amount(uint32_t participant, uint32_t product, int *capacity) {
    char *econ = meta_economy(participant);
    if (!econ) return -1;
    char *e = storage_entry(econ, product);
    if (capacity) *capacity = e ? *(int *)(e + T_OFF_SNE_CAPACITY) : 0;
    return e ? *(int *)(e + T_OFF_SNE_AMOUNT) : 0;
}

int hlib_meta_add(uint32_t participant, uint32_t product, int amount) {
    char *econ = meta_economy(participant);
    if (!econ || !product || !amount) return 0;
    uint32_t old = hlib_set_participant(participant);
    __try {
        ((void (*)(void *, uint32_t, int))(*(void ***)econ)[T_VT_META_ADD])(econ, product, amount);
    } __finally {
        hlib_set_participant(old); /* the guard puts the scope back, not the participant */
    }
    return 1;
}

int hlib_asset_unlocked(uint32_t participant, uint32_t guid) {
    unsigned long long (*f)(void *, uint32_t, int) = (unsigned long long (*)(void *, uint32_t, int))hlib_target(T_ASSET_UNLOCKED);
    if (!f || !guid) return -1;
    uint32_t old = hlib_set_participant(participant);
    int r = 0;
    __try {
        r = (int)(f(NULL, guid, 0) & 0xff) != 0;
    } __finally {
        hlib_set_participant(old);
    }
    return r;
}

int hlib_unlock_thread(void) {
    char (*f)(unsigned long long) = (char (*)(unsigned long long))hlib_target(T_THREAD_IS);
    return f && f(T_THREAD_UNLOCKS) != 0;
}

/* The island area the scope guard holds and the component manager hangs on: the session manager's area list, read
   as the guard's own lookup reads it (compwalk_logic.c). Not hlib_area: that is the construction area of the same id, whose
   +0xf0 is no component manager (the walk read garbage there until 2026-11-01). */
void *hlib_island_area(int area_id) {
    char *(*gsm)(void) = (char *(*)(void))hlib_target(T_SESSION_GSM);
    char *g = gsm && area_id > 0 ? gsm() : NULL;
    return cw_island_area(g, area_id);
}

/* -1: no island area of that id, or its component manager is not built yet (its maps hold other values then) */
static int components_walk(int area_id, uint16_t prop, HlibCompFn fn, void *ctx) {
    const char *maps[2];
    int n = 0;
    if (!cw_maps((const char *)hlib_island_area(area_id), maps)) return -1;
    for (int m = 0; m < 2; m++) {
        void **pool = (void **)cw_pool(maps[m], prop);
        void **vt = cw_followable(pool) ? *(void ***)pool : NULL;
        if (!vt || !hlib_in_image(vt) || !hlib_in_image(vt[T_VT_POOL_COUNT]) || !hlib_in_image(vt[T_VT_POOL_AT])) continue;
        int count = ((int (*)(void *))vt[T_VT_POOL_COUNT])(pool);
        for (int i = 0; i < count && i < 100000; i++) {
            void *c = ((void *(*)(void *, int))vt[T_VT_POOL_AT])(pool, i);
            if (cw_followable(c)) {
                fn(c, ctx);
                n++;
            }
        }
    }
    return n;
}

int hlib_area_components_ok(int area_id, uint16_t prop, HlibCompFn fn, void *ctx) {
    __try {
        return components_walk(area_id, prop, fn, ctx);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

int hlib_area_components(int area_id, uint16_t prop, HlibCompFn fn, void *ctx) {
    int n = hlib_area_components_ok(area_id, prop, fn, ctx);
    return n < 0 ? 0 : n;
}

/* the island component's file name: a std::wstring or std::string, ascii either way */
int hlib_island_stem(int area_id, char *out, int cap) {
    void *(*imgr)(void) = (void *(*)(void))hlib_target(T_SESSION_ISLANDMGR);
    void *(*find)(void *, short) = (void *(*)(void *, short))hlib_target(T_ISLANDMGR_FIND);
    void *m = imgr && area_id > 0 ? imgr() : NULL;
    char *isle = m && find ? (char *)find(m, (short)((area_id >> T_AREA_ISLAND_SHIFT) & T_AREA_ISLAND_MASK)) : NULL;
    char *comp = isle ? *(char **)(isle + T_OFF_ISLAND_COMP) : NULL;
    char name[260];
    if (!comp || cap < 2) return 0;
    const unsigned char *s = (const unsigned char *)comp + T_OFF_ISLANDCOMP_NAME;
    unsigned long long size = *(const unsigned long long *)(s + T_STD_STR_SIZE), room = *(const unsigned long long *)(s + T_STD_STR_CAP);
    if (size < 1 || size > 250 || room < size || room > 0x100000) return 0;
    for (int wide = 1; wide >= 0; wide--) {
        const unsigned char *d = room > (unsigned long long)(wide ? 7 : 15) ? *(const unsigned char *const *)s : s;
        int ok = 1;
        for (unsigned long long i = 0; ok && i < size; i++) {
            unsigned c = wide ? ((const unsigned short *)d)[i] : d[i];
            ok = c >= 0x20 && c <= 0x7e;
            name[i] = (char)c;
        }
        if (!ok) continue;
        name[size] = 0;
        const char *t = hlib_tail(name);
        int n = 0;
        while (t[n] && t[n] != '.' && n < cap - 1) {
            out[n] = t[n];
            n++;
        }
        out[n] = 0;
        return n;
    }
    return 0;
}

int hlib_lead_peer(void) {
    HlibParticipant p;
    uint32_t me = hlib_local_participant();
    for (int i = 0; me && i < hlib_participant_count(NULL); i++)
        if (hlib_participant_info(NULL, i, &p) && p.kind == HLIB_PK_HUMAN) return p.guid == me;
    return 0;
}

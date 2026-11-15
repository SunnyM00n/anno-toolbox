/* The session lifecycle (hlib.h): per session slot the key, the context and the last game time seen; a key's session
   ends when the engine closes it (the observer below), when another session's key takes its slot, or when the
   session is read from a save. A key seen at another slot is the same session moved. */
#include "core_int.h"
#include "framework.h"
#include "targets.h"
#include "util.h"
#include <string.h>

/* Session lifecycle (hlib.h): the listeners are the dispatch's session-end subscribers. One record per session
   slot (util.c hlib_life_note). */
static HlibLifeSlot s_life[HLIB_LIFE_SLOTS];
static SRWLOCK s_life_lock = SRWLOCK_INIT;
static int n_life_begin, n_life_end;

void hlib_on_session_end(const char *owner, HlibSessionFn fn) {
    hlib_subscribe(owner, HLIB_AT_SESSION_END, HLIB_SIM, (void *)fn);
}
int hlib_session_listeners(void) { return hlib_subscribers(HLIB_AT_SESSION_END); }

static int end_run(void *fn, void *key) {
    ((HlibSessionFn)fn)(key);
    return 0;
}

static void life_end(void *key, int sid, const char *why) {
    hlib_log_n(&n_life_end, 32, "session: the session of key %p (index %d) is over (%s); its module state is dropped", key, sid,
                why);
    hlib_dispatch(HLIB_AT_SESSION_END, end_run, key);
}

/* The slot a session context names itself (the engine's own test in its scope guard); the area id's low bits are
   not a session for every area, and the same context answers at several of them. -1 for no context. */
static int ctx_slot(void *ctx) {
    int s = ctx ? *(short *)((char *)ctx + T_OFF_CTX_INDEX) : -1;
    return s >= 0 && s < HLIB_LIFE_SLOTS ? s : -1;
}

/* a key moving slot is logged once per pair of slots, not at every flip */
static struct {
    void *key;
    int a, b;
} s_moves[16];
static int s_nmoves, n_life_move;

static int move_new(void *key, int a, int b) {
    for (int i = 0; i < s_nmoves; i++)
        if (s_moves[i].key == key && ((s_moves[i].a == a && s_moves[i].b == b) || (s_moves[i].a == b && s_moves[i].b == a))) return 0;
    s_moves[s_nmoves % 16].key = key;
    s_moves[s_nmoves % 16].a = a;
    s_moves[s_nmoves % 16].b = b;
    if (s_nmoves < 16) s_nmoves++;
    return 1;
}

/* The watch: the slot comes from the context when there is one (else the caller's, from the island placement).
   A key at another slot is the same session moved; only a new key at a slot ends the one that was there. The
   engine's close (below) and a load end a key otherwise. */
void hlib_session_note(int sid, void *key, void *ctx, long long now) {
    HlibLifeEvent ev;
    int own = ctx_slot(ctx);
    if (own >= 0) sid = own;
    if (sid < 0 || sid > 63 || !key) return;
    AcquireSRWLockExclusive(&s_life_lock);
    hlib_life_note(s_life, sid, key, ctx, now, &ev);
    int log_move = ev.moved_from >= 0 && move_new(key, ev.moved_from, sid);
    ReleaseSRWLockExclusive(&s_life_lock);
    if (ev.ended) life_end(ev.ended, sid, "another session took its index");
    if (log_move)
        hlib_log_n(&n_life_move, 16, "session: key %p moved from index %d to %d, the same session; its module state is kept", key,
                    ev.moved_from, sid);
    if (ev.fresh) {
        char text[1000];
        uint32_t h = hlib_config_digest(text, sizeof(text));
        hlib_log_n(&n_life_begin, 32, "session: index %d begins (key %p), simulation settings digest %08x", sid, key, h);
    }
    if (now >= 0) config_check_pending();
}

int hlib_sessions_live(void) {
    int n = 0;
    AcquireSRWLockShared(&s_life_lock);
    for (int i = 0; i < HLIB_LIFE_SLOTS; i++) n += s_life[i].key != NULL;
    ReleaseSRWLockShared(&s_life_lock);
    return n;
}

void hlib_session_merge(int sid, void *key) { hlib_session_note(sid, key, hlib_session_ctx_of(sid), -1); }

/* The engine closes a session (the observer below): its key ends here, whatever comes after it, so a new game
   that gets the same key and context back starts afresh. */
void hlib_session_closed(int sid, void *key) {
    AcquireSRWLockExclusive(&s_life_lock);
    key = hlib_life_close(s_life, sid, key);
    ReleaseSRWLockExclusive(&s_life_lock);
    if (key) life_end(key, sid, "the engine closed it");
    if (!hlib_sessions_live()) {
        hlib_rngsalt_game_over();
        hlib_persist_load_begins(); /* the next load chooses its game-wide blocks afresh */
    }
}

/* a session read from a save starts afresh: whatever a module kept under its key goes before the chunks load */
void hlib_session_loaded(void *key) {
    if (!key) return;
    AcquireSRWLockExclusive(&s_life_lock);
    hlib_life_close(s_life, -1, key);
    ReleaseSRWLockExclusive(&s_life_lock);
    life_end(key, -1, "it is read from a save");
}

/* The session lifecycle from the engine itself: an observer on its session close (it fires the engine's own
   "session unloaded" event), before anything of the session is freed. The closed session's context is found
   in the session table by its game session manager; its key ends there (core.c). The watch stays as the
   fallback for a close this observer does not see. */
static void *(*o_close)(void *gsm);
static int n_close, n_close_unplaced;

static void close_work(void *gsm) {
    int found = 0;
    void *done = NULL;
    for (int i = 0; i < 64; i++) { /* the table may list one context at several slots */
        char *ctx = (char *)hlib_session_ctx_of(i);
        if (!ctx || *(void **)(ctx + T_OFF_CTX_GSM) != gsm || ctx == done) continue;
        void *key = *(void **)(ctx + T_OFF_CTX_REGROW);
        int sid = ctx_slot(ctx) >= 0 ? ctx_slot(ctx) : i;
        hlib_log_n(&n_close, 32, "session: the engine closes session index %d (key %p)", sid, key);
        hlib_session_closed(sid, key);
        done = ctx;
        found++;
    }
    if (!found) hlib_log_n(&n_close_unplaced, 4, "session: the engine closes a session the table does not list (manager %p)", gsm);
}

static void *close_observer(void *gsm) {
    hlib_guarded("session close", close_work, gsm);
    return o_close(gsm);
}

/* The engine's own events, once the bus resolves (planned): the game's end beside the close observer (both idempotent),
   and the desync alarm that names this client's settings digest and game time for a two-log comparison. */
static void meta_unloaded(const void *payload) {
    (void)payload;
    hlib_log("session: the engine says the game is over (MetaGameUnloaded)");
    hlib_rngsalt_game_over();
    hlib_persist_load_begins();
}
static void desync(const void *payload) {
    (void)payload;
    hlib_logf("session: DESYNC reported by the engine at game time %lld ms; this client's settings digest %08x, library "
               "build %08x",
               hlib_meta_time_ms(), hlib_config_digest(NULL, 0), hlib_library_build());
}

int hlib_lifecycle_install(int final) {
    static int done, bus;
    if (!bus++) {
        hlib_on_engine_event("session", HLIB_EV_META_UNLOADED, HLIB_SIM, meta_unloaded);
        hlib_on_engine_event("session", HLIB_EV_DESYNC, HLIB_SIM, desync);
    }
    if (done || !hlib_session_listeners()) return done;
    if (!hlib_target_ok(T_SESSION_CLOSE) || !hlib_target_ok(T_DATA_SESSION_TABLE)) {
        if (final) {
            done = 1;
            hlib_log("session: the engine's session close is not available on this build, the watch alone ends sessions");
        }
        return 0;
    }
    done = 1;
    /* the close observer stays the session end while the bus is planned; MetaGameUnloaded joins it above */
    HlibLayer l = {T_SESSION_CLOSE, HLIB_BEFORE, (void *)close_observer, (void **)&o_close, 0};
    if (hlib_layers("session", &l, 1)) {
        hlib_point_set_live(HLIB_AT_SESSION_END);
        hlib_log("session: observing the engine's session close");
    }
    return 1;
}

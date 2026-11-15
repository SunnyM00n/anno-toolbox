/* The game salt for the per-area draws (util.c hlib_rng_mix): without it a new game dealt the same "random" outcomes
   for the same area and game time. One value per game: the salt of the lowest session GUID among the maps loaded
   before the first step (so the same on every peer whatever order the sessions load in), fixed from the first step
   on (a session added later cannot change it), saved as the chunk "rngsalt" in every session's block (a loaded game
   gets its own salt back, a save without the block the old streams, salt 0). The state machine and its tests:
   util.c, host_test.c. */
#include "core_int.h"
#include "targets.h"
#include <string.h>

#define CHUNK_VERSION 1

static SRWLOCK s_lock = SRWLOCK_INIT;
static int s_registered, n_map, n_kept, n_restore, n_none, n_fixed, n_over;

/* a new session's map: its seed and GUID make the salt unless the game already runs */
static void map_loaded(uint32_t seed, uint32_t session) {
    AcquireSRWLockExclusive(&s_lock);
    int taken = hlib_rng_salt_from_map(seed, session, hlib_sessions_live());
    uint32_t salt = hlib_rng_salt();
    ReleaseSRWLockExclusive(&s_lock);
    if (taken)
        hlib_log_n(&n_map, 16, "rng: game salt %08x (from the map seed of session %u)", salt, session);
    else
        hlib_log_n(&n_kept, 8, "rng: the map of session %u joins the game, its salt stays %08x", session, salt);
}

/* the first step of any session: the game runs, the salt is fixed */
static void step(void *area) {
    (void)area;
    if (hlib_rng_salt_state() != HLIB_SALT_FORMING) return;
    AcquireSRWLockExclusive(&s_lock);
    int was = hlib_rng_salt_state() == HLIB_SALT_FORMING;
    hlib_rng_salt_running();
    uint32_t salt = hlib_rng_salt();
    ReleaseSRWLockExclusive(&s_lock);
    if (was) hlib_log_n(&n_fixed, 8, "rng: game salt %08x is fixed, the game runs", salt);
}

/* every session of a game carries the same block, so any of them restores it */
static void salt_save(HlibSaver *sv, int version) {
    (void)version;
    AcquireSRWLockShared(&s_lock);
    uint32_t salt = hlib_rng_salt();
    int has = hlib_rng_salt_state() != HLIB_SALT_NONE;
    ReleaseSRWLockShared(&s_lock);
    if (has) hlib_saver_put(sv, &salt, sizeof(salt));
}

static void salt_load(HlibSaver *sv, int version) {
    const void *data;
    uint32_t len, salt = 0;
    int present = version >= 1 && version <= CHUNK_VERSION && hlib_saver_get(sv, &data, &len) && len >= sizeof(salt);
    if (present) memcpy(&salt, data, sizeof(salt));
    if (version > CHUNK_VERSION) hlib_logf("rng: the save's salt block is version %d, newer than this library; the old streams", version);
    AcquireSRWLockExclusive(&s_lock);
    int news = hlib_rng_salt_restore(salt, present);
    ReleaseSRWLockExclusive(&s_lock);
    if (!news) return;
    if (present)
        hlib_log_n(&n_restore, 8, "rng: game salt %08x restored from the save", salt);
    else
        hlib_log_n(&n_none, 8, "rng: game salt none in the save, the old streams");
}

/* the engine closed the last session: the next game starts with no salt */
void hlib_rngsalt_game_over(void) {
    AcquireSRWLockExclusive(&s_lock);
    int had = hlib_rng_salt_state() != HLIB_SALT_NONE;
    hlib_rng_salt_clear();
    ReleaseSRWLockExclusive(&s_lock);
    if (had) hlib_log_n(&n_over, 8, "rng: the last session is closed, the game salt is cleared");
}

/* A listener with no work: it turns on the session watch and the engine's close observer, which the game over
   (session.c) and the live count need. */
static void on_end(void *key) { (void)key; }

/* persist.c registers it with the first chunk of a module, as the settings chunk */
void hlib_rngsalt_register(void) {
    if (s_registered) return;
    s_registered = 1;
    hlib_on_map_loaded("rngsalt", map_loaded);
    hlib_tick_register("rngsalt", step);
    hlib_on_session_end("rngsalt", on_end);
    hlib_persist_register("rngsalt", CHUNK_VERSION, salt_save, salt_load);
}

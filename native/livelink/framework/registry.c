/* The asset registry service: one answer per GUID of ours from two sources, the engine's own asset (the asset
   manager's lookup, every field 117 has) and the library's table (the mod's data 117 lacks: the GUID an asset was
   made from, the slot kind it stands for), read once from the mod's own data files and indexed by GUID. A field both
   have is the engine's. Modules ask here instead of reading and parsing the mods' files each for itself. */
#include "hlib.h"
#include "registry_logic.h"
#include <string.h>

static RegTable g_table;
static volatile LONG g_generation = 1;
static volatile LONG g_loaded_generation;
static SRWLOCK g_lock = SRWLOCK_INIT;

static void conversion_file(const char *path, const char *text, size_t len, void *ctx) {
    (void)ctx;
    int got = reg_parse(&g_table, text, len);
    if (got) hlib_logf("registry: %d record(s) from %s", got, path);
}

static void load(void) {
    reg_free(&g_table);
    reg_init(&g_table);
    DWORD t0 = GetTickCount();
    hlib_mods_each("anno2070-conversion.json", conversion_file, NULL);
    reg_seal(&g_table);
    hlib_logf("registry: %d asset(s) of ours, %d of them with the source GUID they were made from, %d slot kind(s) "
               "(%u ms)",
               g_table.n, g_table.pairs, g_table.ntypes, (unsigned)(GetTickCount() - t0));
    if (!reg_consistent(&g_table))
        hlib_logf("registry: the files declare %d asset(s) but name %d; a part of the mod's data is missing or stale",
                   g_table.declared, g_table.pairs);
    InterlockedExchange(&g_loaded_generation, g_generation);
}

/* read on first use and again after an invalidation (the asset reload) */
static void ensure(void) {
    if (g_loaded_generation == g_generation) return;
    AcquireSRWLockExclusive(&g_lock);
    if (g_loaded_generation != g_generation) load();
    ReleaseSRWLockExclusive(&g_lock);
}

void hlib_registry_invalidate(void) { InterlockedIncrement(&g_generation); }

int hlib_rules(uint32_t guid, HlibRules *out) {
    ensure();
    memset(out, 0, sizeof(*out));
    out->guid = guid;
    AcquireSRWLockShared(&g_lock);
    const RegEntry *r = reg_find(&g_table, guid);
    if (r) {
        out->source = r->source;
        out->slot_type = r->slot_type;
        out->role = r->role;
    }
    ReleaseSRWLockShared(&g_lock);
    out->asset = hlib_asset(guid); /* the engine's, looked up now: a reload replaces it */
    out->known = r != NULL;
    return out->known || out->asset != NULL;
}

uint32_t hlib_source_guid(uint32_t ours) {
    ensure();
    AcquireSRWLockShared(&g_lock);
    const RegEntry *r = reg_find(&g_table, ours);
    uint32_t source = r ? r->source : 0;
    ReleaseSRWLockShared(&g_lock);
    return source;
}

int hlib_rules_each_slot(HlibRulesFn fn, void *ctx) {
    ensure();
    int n = 0;
    AcquireSRWLockShared(&g_lock);
    for (int i = 0; i < g_table.n; i++) {
        const RegEntry *r = &g_table.e[i];
        if (!r->slot_type) continue;
        HlibRules rec = {r->ours, r->source, r->slot_type, r->role, 1, NULL};
        fn(&rec, ctx);
        n++;
    }
    ReleaseSRWLockShared(&g_lock);
    return n;
}

int hlib_rules_slot_types(int *first) {
    ensure();
    if (first) *first = g_table.first;
    return g_table.ntypes;
}

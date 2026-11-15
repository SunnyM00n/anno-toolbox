/* The mod's status markers over buildings: the mod's tools write, per mod-only state 117 has no marker for, an
   info-layer icon and a BuildingBuff that carries it, and names both in <mod id>.markers.json. A module that knows a
   state puts that buff on the building and takes it away when the state ends; the buff changes nothing but the
   marker and the panel's line. The table is read once, when the first module asks for it in its install. */
#include "hlib.h"
#include "util.h"
#include <string.h>

/* every building state of the report; a unit's marker has no buff (no data hook puts an icon over a unit) */
static const char *const STATES[] = {"BelowMinimumEnergy", "Ecobalance_negative", "Ecobalance_positive", "Ecobalance_nuclear",
                                     "Neutral", "Research", "BuildmaterialBlocked", "WoodResourceEmpty", "BlackSmokerActive",
                                     "ProductivityBlackTide", "Fallout", "MonitoringStation", "Ozonemaker",
                                     "CargolifterRelocationTarget", "CargolifterRelocationPickup", "CrimeVisited", "TakeOverTarget"};
#define NSTATES ((int)(sizeof(STATES) / sizeof(STATES[0])))

static uint32_t s_buff[NSTATES];
static volatile LONG s_loaded;
static int n_set;

static void marker_file(const char *path, const char *text, size_t len, void *ctx) {
    (void)ctx;
    int n = 0;
    for (int i = 0; i < NSTATES; i++)
        if (!s_buff[i] && (s_buff[i] = hlib_marker_buff(text, len, STATES[i])) != 0) n++;
    if (n) hlib_logf("markers: %d building marker(s) of the mod's states from %s", n, path);
}

void hlib_markers_use(const char *owner) {
    if (InterlockedCompareExchange(&s_loaded, 1, 0) == 0) hlib_mods_each("*.markers.json", marker_file, NULL);
    (void)owner;
}

static int index_of(const char *state) {
    for (int i = 0; state && i < NSTATES; i++)
        if (!strcmp(STATES[i], state)) return i;
    return -1;
}

uint32_t hlib_marker_guid(const char *state) {
    int i = index_of(state);
    return i >= 0 ? s_buff[i] : 0;
}

int hlib_marker_set(void *obj, const char *state, int on) {
    uint32_t buff = hlib_marker_guid(state);
    if (!obj || !buff) return 0;
    int changed = on ? hlib_buff_add(obj, buff) : hlib_buff_remove(obj, buff);
    if (changed)
        hlib_log_n(&n_set, 24, "markers: %s %s on building %u", state, on ? "shown" : "taken off", hlib_obj_guid(obj));
    return changed;
}

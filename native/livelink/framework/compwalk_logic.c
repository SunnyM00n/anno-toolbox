/* The island area's component manager, pure (compwalk_logic.h). */
#include "compwalk_logic.h"
#include "targets.h"

int cw_followable(const void *p) {
    uintptr_t v = (uintptr_t)p;
    return v >= 0x10000 && v < 0x00007ffffffe0000ULL && !(v & 7);
}

void *cw_island_area(const char *gsm, int area_id) {
    if (!cw_followable(gsm) || area_id <= 0 || area_id > 0xffff) return NULL;
    const char *b = *(const char *const *)(gsm + T_OFF_GSM_AREA_LIST);
    const char *e = *(const char *const *)(gsm + T_OFF_GSM_AREA_LIST + 8);
    if (!cw_followable(b) || !cw_followable(e) || e < b) return NULL;
    long long lo = 0, hi = (long long)((e - b) / T_GSM_AREA_ENTRY);
    if (hi > CW_MAX_AREAS) return NULL;
    uint16_t id = (uint16_t)area_id;
    while (lo < hi) { /* the first entry whose id is not below the one asked for */
        long long mid = (lo + hi) / 2;
        if (*(const uint16_t *)(b + mid * T_GSM_AREA_ENTRY) < id) lo = mid + 1;
        else hi = mid;
    }
    const char *entry = b + lo * T_GSM_AREA_ENTRY;
    if (entry >= e || *(const uint16_t *)entry != id) return NULL;
    char *area = *(char *const *)(entry + T_OFF_GSM_AREA_ENTRY_AREA);
    return cw_followable(area) && *(const uint16_t *)(area + T_OFF_SCOPE_AREAID) == id ? area : NULL;
}

int cw_island_ids(const char *gsm, short *ids, int cap) {
    if (!cw_followable(gsm)) return 0;
    const char *b = *(const char *const *)(gsm + T_OFF_GSM_AREA_LIST);
    const char *e = *(const char *const *)(gsm + T_OFF_GSM_AREA_LIST + 8);
    int n = 0;
    if (!cw_followable(b) || !cw_followable(e) || e < b || (e - b) / T_GSM_AREA_ENTRY > CW_MAX_AREAS) return 0;
    for (const char *it = b; it < e && n < cap; it += T_GSM_AREA_ENTRY) {
        const char *area = *(const char *const *)(it + T_OFF_GSM_AREA_ENTRY_AREA);
        short id = cw_followable(area) ? *(const short *)(area + T_OFF_SCOPE_AREAID) : 0;
        if (id >= T_AREA_ISLAND_FLAG && (id & T_AREA_SESSION_MASK)) ids[n++] = id;
    }
    return n;
}

int cw_carea_id(const void *carea) { return cw_followable(carea) ? *(const unsigned short *)((const char *)carea + T_OFF_CAREA_ID) : 0; }

int cw_maps(const char *island_area, const char *maps[2]) {
    const char *mgr = cw_followable(island_area) ? *(const char *const *)(island_area + T_OFF_AREA_COMPONENTS) : NULL;
    if (!cw_followable(mgr)) return 0;
    for (int m = 0; m < 2; m++) {
        maps[m] = *(const char *const *)(mgr + T_OFF_COMPMGR_MAPS + 8 * m);
        if (!cw_followable(maps[m])) return 0;
    }
    return 1;
}

/* the bucket is walked from its last node back to its first, as the engine's lookup does */
void *cw_pool(const char *map, uint16_t prop) {
    unsigned long long h = 0xcbf29ce484222325ULL;
    uint32_t key = prop;
    for (int i = 0; i < 4; i++) h = (h ^ ((key >> (8 * i)) & 0xff)) * 0x100000001b3ULL;
    const char *end = *(const char *const *)(map + T_OFF_CMAP_END), *buckets = *(const char *const *)(map + T_OFF_CMAP_BUCKETS);
    if (!cw_followable(buckets)) return NULL;
    const char *const *bk = (const char *const *)(buckets + ((h & *(const unsigned long long *)(map + T_OFF_CMAP_MASK)) << 4));
    const char *it = bk[1];
    for (int k = 0; cw_followable(it) && it != end && k < 4096; k++) {
        if (*(const uint32_t *)(it + T_OFF_HMAP_KEY) == key) return *(void *const *)(it + T_OFF_HMAP_VALUE);
        if (it == bk[0]) break;
        it = *(const char *const *)(it + 8);
    }
    return NULL;
}

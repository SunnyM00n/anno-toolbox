/* The running game's difficulty setup, read-only. The engine keeps the preset the player picked in the setup manager
   with the choices as 16-bit enum indices in a DifficultySetup property instance; the modules read them here instead
   of from the ini. Peers share the setup (the engine sends it), so the values are the same on every client. Nothing
   is hooked; a bad read answers -1. */
#include "hlib.h"
#include "gamesetup_logic.h"
#include "targets.h"
#include "util.h"

static int n_log;

/* the live property instance and the preset's GUID; 0 when the setup is not there */
static const unsigned char *live_prop(uint32_t *preset) {
    void **h = (void **)hlib_target(T_DATA_GAME_SETUP);
    const unsigned char *holder, *prop = NULL;
    if (preset) *preset = 0;
    __try {
        holder = h ? (const unsigned char *)*h : NULL;
        if (!holder) return NULL;
        prop = *(const unsigned char *const *)(holder + T_OFF_SETUP_PROP);
        if (preset) *preset = *(const uint32_t *)(holder + T_OFF_SETUP_PRESET);
        if (prop) (void)*(volatile const unsigned short *)(prop + T_SETUP_VOLCANO_ACTIVE); /* readable to its end */
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        hlib_log_n(&n_log, 2, "gamesetup: the setup manager's layout is not as expected, no setup is read");
        return NULL;
    }
    return prop;
}

int hlib_setup_value(int field) {
    int v = -1;
    __try {
        v = gs_pick(live_prop(NULL), field);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        v = -1;
    }
    return v;
}

uint32_t hlib_setup_preset(void) {
    uint32_t g = 0;
    live_prop(&g);
    return g;
}

/* the player changed the choice from the preset's: 1, 0 the same, -1 when either is unknown */
int hlib_setup_changed(int field) {
    uint32_t g = 0;
    int live = -1, base = -1;
    __try {
        const unsigned char *prop = live_prop(&g);
        void *asset = g ? hlib_asset(g) : NULL;
        void *p = asset ? hlib_prop(asset, T_PROP_DIFFICULTY_SETUP) : NULL;
        live = gs_pick(prop, field);
        base = gs_pick(p, field);
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
    return live < 0 || base < 0 ? -1 : live != base;
}

const char *hlib_setup_name(int field) { return gs_field_name(field); }

void hlib_setup_log(const char *why) {
    char line[512];
    int len = 0;
    uint32_t g = 0;
    if (!live_prop(&g)) {
        hlib_logf("gamesetup: %s: the game setup is not readable", why);
        return;
    }
    len = hlib_format(line, (int)sizeof(line), "gamesetup: %s: preset %u", why, g);
    for (int f = 0; f < GS_COUNT && len < (int)sizeof(line) - 40; f++) {
        int v = hlib_setup_value(f), c = hlib_setup_changed(f);
        len += hlib_format(line + len, (int)sizeof(line) - len, ", %s %d (%s)%s", gs_field_name(f), v, gs_level_name(f, v),
                            c == 1 ? " changed" : "");
    }
    hlib_log(line);
}

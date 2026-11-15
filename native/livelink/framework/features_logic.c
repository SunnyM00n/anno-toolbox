/* Feature classes and the run-time switch, pure: features_logic.h. */
#include "features_logic.h"
#include "hlib.h"

int fc_class_ok(int c) { return c == HLIB_GAME || c == HLIB_DEV || c == HLIB_EXPERIMENTAL; }

int fc_default(int c) { return c == HLIB_GAME; }

/* GAME is on unless the ini says 0; DEV and EXPERIMENTAL are off unless it says 1 */
int fc_decide(int c, int ini) { return ini == 0 || ini == 1 ? ini : fc_default(c); }

const char *fc_class_name(int c) {
    return c == HLIB_GAME ? "game" : c == HLIB_DEV ? "dev" : c == HLIB_EXPERIMENTAL ? "experimental" : "none";
}

/* the framework's switches that are no module, each with the class of the default it always had */
static const struct {
    const char *name;
    int feature_class;
} k_switches[] = {
    {"dormant", HLIB_GAME},            /* outside the full mode no module comes up */
    {"mode_switch_button", HLIB_GAME}, /* the title's switch between the two games */
    {"safe_mode", HLIB_DEV},           /* every module off, for a start that crashes */
};

int fc_switch_class(const char *name) {
    for (unsigned i = 0; name && i < sizeof(k_switches) / sizeof(k_switches[0]); i++)
        if (0 == lstrcmpiA(k_switches[i].name, name)) return k_switches[i].feature_class;
    return 0;
}

const char *fc_toggle_check(const FcToggle *t, int want, int *noop) {
    *noop = 0;
    if (t->dormant) return "the start is dormant (its start mode brings no module up)";
    if (t->safe_mode) return "safe mode: every module stays off for this start";
    if (!t->registered) return "no module of that name";
    if (!t->toggles)
        return "it cannot be switched while the game runs (it places layers, registers assets or changes the start): "
               "set it in the ini and restart";
    if (t->dead) return "it is off for this game after a fault";
    if (!!want == !!t->active) return *noop = 1, (const char *)0;
    if (t->lane == HLIB_SIM && t->humans > 1) return "it changes the simulation: never switched in a game of several players";
    return 0;
}

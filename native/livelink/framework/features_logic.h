/* Feature classes and the run-time switch, the pure part (config.c, core.c): a class's default, the ini's word over
   it, the framework's own switches that are no module, and whether a module may be switched while the game runs. No
   engine call; framework_test.c runs it. */
#ifndef HLIB_FEATURES_LOGIC_H
#define HLIB_FEATURES_LOGIC_H

int fc_class_ok(int feature_class);           /* 1 for HLIB_GAME, HLIB_DEV, HLIB_EXPERIMENTAL */
int fc_default(int feature_class);            /* GAME 1; DEV, EXPERIMENTAL and an unknown class 0 */
int fc_decide(int feature_class, int ini);    /* ini: the ini's switch (1 / 0), -1 none or not a switch word */
const char *fc_class_name(int feature_class); /* "game", "dev", "experimental", "none" */
/* the class of a framework switch that is not a module (dormant, mode_switch_button, safe_mode); 0 when it is none */
int fc_switch_class(const char *name);

/* what the switch of one module at run time depends on */
typedef struct {
    int registered; /* a module of that name came up in this start's registry */
    int toggles;    /* it declares a run-time switch (HlibModule.toggle) */
    int lane;       /* HLIB_SIM or HLIB_PRESENT */
    int active;     /* it runs now */
    int dead;       /* off for the game after a fault */
    int humans;     /* the game's human players */
    int safe_mode, dormant;
} FcToggle;
/* NULL when the module may be switched to want (*noop 1 when it already is so), else the sentence why not */
const char *fc_toggle_check(const FcToggle *t, int want, int *noop);

#endif

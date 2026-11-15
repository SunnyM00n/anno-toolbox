/* What the core's three files share (core.c: logging, the build guard, targets, the SEH guard and the module
   locks, the module and tick registries, the start-up; config.c: the ini, the start mode, the settings
   every peer must share, their digest and the Config chunk; session.c: the session lifecycle; rngsalt.c: the game salt). */
#ifndef HLIB_CORE_INT_H
#define HLIB_CORE_INT_H

#include "hlib.h"
#include "util.h"

#define MAX_INI 160
#define MAX_MODULES 96 /* check_build_numbers.py fails when the registration sites outgrow it */

extern int s_safe_mode;
HlibIniPair *cfg_pair(const char *key);
void cfg_report_unused(void);
void load_config(void);
extern const HlibModule *s_modules[MAX_MODULES];
extern int s_module_count;
extern int s_module_active[MAX_MODULES];
extern int s_module_settled[MAX_MODULES];
int core_feature_class(const char *name); /* the class a module registered with, 0 when no module has that name */
void log_digest(const char *when);
void config_check_pending(void);
int hlib_dormant(void);                         /* 1: the start mode said dormant, nothing comes up */
void config_dormant_reason(char *out, int cap); /* why, for the log */
int config_late_check(int final);               /* the start mode's check after the start: 1 done */
int cfg_unused_count(void);
int hlib_sessions_live(void);     /* session.c: how many session slots hold a session now */
void hlib_rngsalt_game_over(void); /* session.c: the engine closed the last session */

#endif

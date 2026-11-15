/* The live link's module requests, the pure part (mod_livelink.c is the engine side): the answer of modules.list
   (every module with its class, whether it runs and whether it may be switched now) and of modules.set. No engine
   call; linkmodules_test.c runs it. */
#ifndef MODULES_LOGIC_H
#define MODULES_LOGIC_H

#include "hlib.h"
#include <stddef.h>

/* {"modules":[{"name":..,"class":"game|dev|experimental","on":bool,"switchable":bool,"lane":"simulation|presentation",
   "why":".."},...]}; the length, or -1 when it does not fit */
int ml_list_json(char *out, size_t cap, const HlibModuleState *m, int n);
/* {"module":..,"on":bool} after a switch; the length, or -1 */
int ml_set_json(char *out, size_t cap, const char *name, int on);

#endif

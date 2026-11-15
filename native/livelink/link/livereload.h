/* The live reload's entry points (mod_livereload.c, switch live_reload): what the key, the trigger file and later the
   live link's reload.models / reload.effects messages call. Every request is checked (an enabled mod's folder, a
   reloadable file) and queued; the engine's own file handlers run it at the render-safe point, one a frame. */
#ifndef LIVERELOAD_H
#define LIVERELOAD_H

#include <stdint.h>

/* Queue files of the enabled mods: each path absolute (inside an enabled mod's folder) or relative to the folder of
   mod_id (NULL or "": the first enabled mod that has the file). Models run before configs, configs before effects; an
   include (.fxh) is refused here, the caller names the effect files that include it. source names the caller in the
   log. How many were queued; -1 when the module is not running. */
int hlib_live_reload_files(const char *mod_id, const char *const *paths, int n, const char *source);
/* The same with the batch it was queued under (0 when nothing was queued or refused), and what each of its files came to. */
int hlib_live_reload_batch(const char *mod_id, const char *const *paths, int n, const char *source, uint32_t *batch);
enum { HLIB_RELOAD_DONE = 1, HLIB_RELOAD_HANDED, HLIB_RELOAD_NOT_LOADED, HLIB_RELOAD_REFUSED };
typedef struct {
    int kind, code; /* RL_* kind of reload_logic.h; one of the codes above */
    double ms;
    char path[120], why[100];
} HlibReloadResult;
/* The results so far of a batch (up to cap) and how many of its files still wait in the queue (0 when it is over). */
int hlib_live_reload_results(uint32_t batch, HlibReloadResult *out, int cap, int *pending);
/* The enabled mods the loader listed (id and folder, '/' separators); how many, 0 until the list is read. */
int hlib_live_reload_mods(char (*id)[64], char (*folder)[260], int cap);
/* 1 while a session loads (a reload waits then) */
int hlib_live_reload_loading(void);
/* The dormant start's bring-up (core.c): the targets, whether the switch live_reload is on, and the install. */
const int *hlib_live_reload_targets(int *n);
int hlib_live_reload_wanted(void);
int hlib_live_reload_install(void); /* 1 when running */
/* Queue every model, config and effect file of the enabled mods whose write time changed since the last scan (an
   include's change queues every effect of its mod that includes it). 1 started, 0 a scan runs already, -1 not running. */
int hlib_live_reload_scan(const char *source);

#endif

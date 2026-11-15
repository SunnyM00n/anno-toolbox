/* The framework's own interface, not for modules (they use hlib.h only): the hook primitives the layer registry
   places its sites with, the dispatch the hook sources call, and the start-up reports. */
#ifndef HLIB_FRAMEWORK_H
#define HLIB_FRAMEWORK_H

#include "hlib.h"

/* Hook primitives (hooks.c). install_entry_observers places a redirect at each entry's function that runs the
   observer and lets it forward to the original; a batch is all or nothing, ordinary code only. patch_bytes: n bytes
   at offset into each validated target, all or none; patch_jump: an absolute jump to our code over the target's
   recorded run (whole instructions), back = the first byte after. redirect_slot swaps a loader-filled or vtable
   pointer for our own and changes no code bytes. The layer registry is their one user, but for tick.c's step slots
   on a live manager's vtable (redirect_slot_at). */
typedef struct {
    int target;      /* targets.h id */
    void *observer;  /* our function with the target's signature */
    void **original; /* receives a callable that runs the original */
} HlibEntry;
int hlib_install_entry_observers(const HlibEntry *entries, int count);
int hlib_patch_bytes(const HlibPatch *patches, int count);
int hlib_patch_jump(int target, void *to, void **back);
int hlib_redirect_slot(int slot_target, void *replacement, void **previous);
int hlib_redirect_slot_at(void **slot, void *replacement, void **previous); /* a slot read at run time */
/* A 5-byte `jmp rel32` thunk re-pointed at fn through a relay near it; next = the thunk's old target. restore puts
   every old rel32 back (the library's unload); how many went back. */
int hlib_thunk_retarget(int target, void *fn, void **next);
int hlib_thunks_restore(void);

/* Dispatch (dispatch.c), for the sources that observe a point: run calls one subscriber's fn with the source's ctx
   and returns nonzero to stop there (a synced command's first taker). Each call is guarded; a subscriber that faults
   is muted where the point says so. The number of subscribers decides whether a source places its hook. */
typedef int (*HlibRunner)(void *fn, void *ctx);
int hlib_dispatch(HlibPoint at, HlibRunner run, void *ctx);
int hlib_subscribers(HlibPoint at);
const char *hlib_point_name(HlibPoint at);
void hlib_point_set_live(HlibPoint at); /* a source whose layer is in place says so (hlib_point_live) */
/* the measured cost against the budget, one line per report and a line per module over its line (no cut) */
void hlib_dispatch_budget(int seconds);
/* The run-time switch: the core names the module whose install runs on this thread (-1 after it), so its
   subscriptions are its own; withdraw keeps them in place uncalled (off 1) or calls them again (off 0), how many;
   ready is 0 (and the point) when one of them sits on a point whose source is not live. */
void hlib_dispatch_module(int module);
int hlib_dispatch_withdraw(int module, int off);
int hlib_dispatch_module_ready(int module, HlibPoint *missing);

/* The deferred meta storage changes (ledger.c): how many modules pay through it, and the post-session step's flush of
   one session's changes (tick.c, after that session's areas). */
int hlib_meta_later_users(void);
void hlib_meta_later_flush(int sid);
void hlib_bus_install(int final); /* bus.c: the engine events' listener adds, once their targets resolve */

/* start-up reports, once every module settled: the layers and patches by kind and every declared replace or skip
   (layer.c), the subscribers per point (dispatch.c), the targets by update stability (core.c) */
void hlib_layers_report(void);
void hlib_dispatch_report(void);
void hlib_targets_report(void);

#endif

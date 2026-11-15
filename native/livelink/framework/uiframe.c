/* The source of the dispatch's UI frame: one layer after the UI state controller's per-frame update, which runs on the
   UI thread and updates every active state's scene; the subscribers run after it, each guarded. Placed by the first
   subscriber (a module's install, once its own targets matched). The point is presentation: a subscriber reads the
   modules' readouts and the engine's UI and never changes game state; a subscriber that faults on 20 frames in a row
   is muted (dispatch.c). */
#include "framework.h"
#include "targets.h"

static volatile LONG s_placed; /* 0 not yet, 1 in place, 2 failed, 3 being placed */
static void *(*o_update)(void *ui);

static int run(void *fn, void *ui) {
    ((HlibUiFrameFn)fn)(ui);
    return 0;
}

static void *update_observer(void *ui) {
    void *r = o_update(ui);
    hlib_dispatch(HLIB_AT_UI_FRAME, run, ui);
    return r;
}

int hlib_on_ui_frame(const char *owner, HlibUiFrameFn fn) {
    if (!fn || !hlib_target_ok(T_UI_FRAME)) return 0;
    if (!hlib_subscribe(owner, HLIB_AT_UI_FRAME, HLIB_PRESENT, (void *)fn)) return 0;
    if (0 == InterlockedCompareExchange(&s_placed, 3, 0)) {
        HlibLayer l = {T_UI_FRAME, HLIB_AFTER, (void *)update_observer, (void **)&o_update, 0};
        int ok = 0;
        __try {
            ok = hlib_layers("ui frame", &l, 1);
        } __finally {
            InterlockedExchange(&s_placed, ok ? 1 : 2); /* a throw must not leave later callers spinning on 3 */
        }
        if (ok) hlib_point_set_live(HLIB_AT_UI_FRAME);
        hlib_logf(ok ? "ui frame: observing the UI's frame (first listener %s)" : "ui frame: the UI's frame could not be observed (%s)",
                   owner);
    }
    while (s_placed == 3) SwitchToThread();
    return s_placed == 1;
}

/* the scene reload's listeners (hlib.h): registered at a module's install, told on the reload's thread */
#define MAX_RELOADED 8
static void (*s_reloaded[MAX_RELOADED])(void);
static volatile LONG s_nreloaded;

void hlib_on_scenes_reloaded(void (*fn)(void)) {
    LONG i = InterlockedIncrement(&s_nreloaded) - 1;
    if (fn && i < MAX_RELOADED) s_reloaded[i] = fn;
}

void hlib_scenes_reloaded(void) {
    for (LONG i = 0; i < s_nreloaded && i < MAX_RELOADED; i++)
        if (s_reloaded[i]) s_reloaded[i]();
}

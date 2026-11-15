/* The engine's own notifications: the notification object of one of our assets, made the way the engine's systems
   make one, with an island as its "Area" context (the text's {NotificationContext:GetValue("Area")...}), handed to
   the notification manager for a participant; only that participant's client shows it. A module queues one on the
   simulation path (every peer queues the same); the queue is raised in the upkeep pass, one walk on one thread,
   never from the parallel area jobs. */
#include "hlib.h"
#include "targets.h"
#include <string.h>

#define QUEUE 64

typedef struct {
    uint32_t guid, participant;
    int area;
} Note;

static SRWLOCK s_lock = SRWLOCK_INIT;
static Note s_queue[QUEUE];
static int s_n, s_dropped;
static volatile LONG s_used;
static int n_fail, n_raise, n_full;

/* the engine's wide string of a short key, as its small-string form keeps it (up to 7 characters, no heap) */
typedef struct {
    wchar_t buf[8];
    unsigned long long size, cap;
} SmallWstr;

typedef void **(*CreateFn)(void *unused, void **out, uint32_t guid);
typedef void *(*ContextFn)(void *map, void *out, const SmallWstr *key, const void *variant);
typedef int *(*AddFn)(void *mgr, int *id, void **obj, uint32_t participant);

static int raise_one(const Note *n) {
    CreateFn create = (CreateFn)hlib_target(T_NOTIF_CREATE);
    ContextFn context = (ContextFn)hlib_target(T_NOTIF_CONTEXT);
    AddFn add = (AddFn)hlib_target(T_NOTIF_ADD);
    void **holder = (void **)hlib_target(T_DATA_NOTIF_MGR);
    if (!create || !context || !add || !holder || !*holder) return 0;
    void *obj = NULL;
    create(NULL, &obj, n->guid);
    if (!obj) return 0;
    if (n->area > 0) {
        SmallWstr key = {{L'A', L'r', L'e', L'a'}, 4, 7};
        unsigned char variant[T_NOTIF_VARIANT_SIZE], out[16];
        memset(variant, 0, sizeof(variant));
        *(int16_t *)variant = (int16_t)n->area;
        variant[T_OFF_NOTIF_VARIANT_TYPE] = T_NOTIF_VARIANT_AREA;
        context((char *)obj + T_OFF_NOTIF_CONTEXT, out, &key, variant);
    }
    int id = -1;
    add((char *)*holder - 8, &id, &obj, n->participant); /* takes the object, also when it refuses it */
    return id != -1;
}

static void drain(void *const *components, int count) {
    (void)components;
    (void)count;
    Note batch[QUEUE];
    int n;
    AcquireSRWLockExclusive(&s_lock);
    n = s_n;
    memcpy(batch, s_queue, sizeof(Note) * (size_t)n);
    s_n = 0;
    ReleaseSRWLockExclusive(&s_lock);
    for (int i = 0; i < n; i++) {
        HlibScope sc;
        int scoped = batch[i].area > 0 && hlib_scope_enter(&sc, batch[i].area);
        int ok = raise_one(&batch[i]);
        if (scoped) hlib_scope_leave(&sc);
        if (ok)
            hlib_log_n(&n_raise, 16, "notify: notification %u raised for participant %u (island area %#x)", batch[i].guid,
                        batch[i].participant, batch[i].area);
        else
            hlib_log_n(&n_fail, 8, "notify: notification %u for participant %u was not shown (no asset, or not this client's)",
                        batch[i].guid, batch[i].participant);
    }
}

void hlib_notify_use(const char *owner) {
    if (InterlockedCompareExchange(&s_used, 1, 0) == 0) hlib_on_buildings("notify", drain);
    hlib_logf("notify: %s raises the engine's notifications", owner);
}

int hlib_notify_ready(void) {
    return s_used && hlib_target_ok(T_NOTIF_CREATE) && hlib_target_ok(T_NOTIF_CONTEXT) && hlib_target_ok(T_NOTIF_ADD);
}

int hlib_notify(uint32_t notification, int area_id, uint32_t participant) {
    if (!notification || !participant || !hlib_notify_ready()) return 0;
    int ok = 0;
    AcquireSRWLockExclusive(&s_lock);
    if (s_n < QUEUE) {
        s_queue[s_n].guid = notification;
        s_queue[s_n].participant = participant;
        s_queue[s_n].area = area_id;
        s_n++;
        ok = 1;
    } else {
        s_dropped++;
    }
    ReleaseSRWLockExclusive(&s_lock);
    if (!ok) hlib_log_n(&n_full, 4, "notify: %d notifications wait already, notification %u dropped", QUEUE, notification);
    return ok;
}

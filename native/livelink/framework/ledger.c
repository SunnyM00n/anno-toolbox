/* The deferred meta storage changes (hlib.h hlib_meta_add_later): area jobs run in parallel, so a player's money
   changed from several of them at once is a race on the engine's meta storage. A change from an area job is summed
   here per session, player and product, and that session's post-session step pays the sums after its areas, in GUID
   order: the result no longer depends on which area job came first. Until the post-session step is live (tick.c says
   so at its first call in a session's context) a change is made at once, as before. */
#include "framework.h"
#include <stdlib.h>

#define LEDGER_MAX 512

typedef struct {
    int sid;
    uint32_t participant, product;
    long long amount;
} Entry;

static Entry s_led[LEDGER_MAX];
static int s_nled;
static volatile LONG s_led_users;
static SRWLOCK s_led_lock = SRWLOCK_INIT;
static volatile LONG s_full_told;

void hlib_meta_later_use(const char *owner) {
    InterlockedIncrement(&s_led_users);
    hlib_logf("ledger: %s pays meta storage from area jobs through the post-session step", owner);
}
int hlib_meta_later_users(void) { return (int)s_led_users; }

/* 1 summed into the session's row; 0 when the ledger is full (the caller pays at once) */
static int ledger_put(int sid, uint32_t participant, uint32_t product, int amount) {
    int ok = 1;
    AcquireSRWLockExclusive(&s_led_lock);
    int i = 0;
    while (i < s_nled && !(s_led[i].sid == sid && s_led[i].participant == participant && s_led[i].product == product))
        i++;
    if (i < s_nled) {
        s_led[i].amount += amount;
    } else if (s_nled < LEDGER_MAX) {
        s_led[s_nled].sid = sid, s_led[s_nled].participant = participant, s_led[s_nled].product = product;
        s_led[s_nled++].amount = amount;
    } else {
        ok = 0;
    }
    ReleaseSRWLockExclusive(&s_led_lock);
    return ok;
}

int hlib_meta_add_later(uint32_t participant, uint32_t product, int amount) {
    int sid = hlib_session_index();
    if (!amount) return 1;
    if (sid < 0 || !hlib_point_live(HLIB_AT_POST_SESSION)) return hlib_meta_add(participant, product, amount);
    if (ledger_put(sid, participant, product, amount)) return 1;
    hlib_log_once(&s_full_told, "ledger: more than %d deferred meta storage changes in one step, the rest is paid at once",
                   LEDGER_MAX);
    return hlib_meta_add(participant, product, amount);
}

static int by_key(const void *a, const void *b) {
    const Entry *x = (const Entry *)a, *y = (const Entry *)b;
    if (x->participant != y->participant) return x->participant < y->participant ? -1 : 1;
    return x->product < y->product ? -1 : x->product > y->product;
}

/* one session's sums, taken out under the lock and paid outside it in a fixed order */
void hlib_meta_later_flush(int sid) {
    Entry mine[LEDGER_MAX];
    int n = 0;
    AcquireSRWLockExclusive(&s_led_lock);
    for (int i = 0; i < s_nled;) {
        if (s_led[i].sid == sid) {
            mine[n++] = s_led[i];
            s_led[i] = s_led[--s_nled];
        } else {
            i++;
        }
    }
    ReleaseSRWLockExclusive(&s_led_lock);
    qsort(mine, (size_t)n, sizeof(mine[0]), by_key);
    for (int i = 0; i < n; i++) {
        long long a = mine[i].amount;
        if (a > 0x7fffffff) a = 0x7fffffff;
        if (a < -0x7fffffff) a = -0x7fffffff;
        if (a) hlib_meta_add(mine[i].participant, mine[i].product, (int)a);
    }
}

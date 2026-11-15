/* The layer registry: every module's layers around engine calls and its few patches, in one table. The first layer
   on a target places the entry redirect (hooks.c); a later one, of any module, is spliced in behind the innermost
   (it calls on what that layer called, that layer now calls it), so one target carries several modules' layers in
   the order they came and the original runs once. A layer that may leave the original out, and every patch, carries
   its kind and why: logged when placed, listed in the start's report. */
#include "framework.h"
#include "targets.h"

#define LY_MAX 192
#define LY_BATCH 16

enum { PLACED_LAYER, PLACED_JUMP, PLACED_BYTES, PLACED_THUNK };

typedef struct {
    const char *owner, *why;
    int target, kind, placed;
    void *fn;
    void **next;
} Row;

static Row s_rows[LY_MAX];
static int s_nrows;
static SRWLOCK s_lock = SRWLOCK_INIT;

static const char *kind_name(int k) {
    static const char *const names[] = {"?", "before", "after", "around", "replaces", "skips"};
    return k >= HLIB_BEFORE && k <= HLIB_SKIPS ? names[k] : names[0];
}
static const char *placed_name(int p) {
    return p == PLACED_JUMP ? "jump" : p == PLACED_BYTES ? "bytes" : p == PLACED_THUNK ? "thunk" : "layer";
}
static int splices(int p) { return p == PLACED_LAYER || p == PLACED_THUNK; } /* a later layer goes in behind it */

/* the innermost layer on a target so far, -1 none; whether a patch sits on it */
static int innermost(int target) {
    for (int i = s_nrows - 1; i >= 0; i--)
        if (s_rows[i].target == target && splices(s_rows[i].placed)) return i;
    return -1;
}
static int patched(int target) {
    for (int i = 0; i < s_nrows; i++)
        if (s_rows[i].target == target && !splices(s_rows[i].placed)) return 1;
    return 0;
}

/* NULL when the batch may go in, else why not */
static const char *check(const Row *in, int n) {
    if (n < 1 || n > LY_BATCH) return "a batch holds 1 to 16 rows";
    if (s_nrows + n > LY_MAX) return "the layer table is full";
    for (int i = 0; i < n; i++) {
        const Row *r = &in[i];
        if (r->target < 0 || r->target >= T_COUNT) return "an unknown target";
        if (r->kind < HLIB_BEFORE || r->kind > HLIB_SKIPS) return "a row without its kind";
        if (!r->fn || (splices(r->placed) && !r->next)) return "a layer without its function or next";
        if (r->kind >= HLIB_REPLACES && (!r->why || !r->why[0])) return "a replace or skip without its reason";
        if (patched(r->target)) return "a target that carries a patch";
        if (!splices(r->placed) && innermost(r->target) >= 0) return "a patch on a target that carries a layer";
        for (int t = 0; t < s_nrows; t++)
            if (s_rows[t].target == r->target && s_rows[t].fn == r->fn) return "the same layer twice";
        for (int j = 0; j < i; j++)
            if (in[j].target == r->target && (!splices(r->placed) || !splices(in[j].placed) || in[j].fn == r->fn))
                return "one target twice in a batch";
    }
    return NULL;
}

static void declare(const Row *r) {
    if (r->kind >= HLIB_REPLACES || r->placed != PLACED_LAYER)
        hlib_logf("layers: %s %s %s %s (%s)", r->owner, placed_name(r->placed), kind_name(r->kind),
                   g_target_names[r->target], r->why ? r->why : "no reason given");
}

/* the batch's first row on a target no layer holds yet places the site */
static int places_site(const Row *in, int i) {
    if (innermost(in[i].target) >= 0) return 0;
    for (int j = 0; j < i; j++)
        if (in[j].target == in[i].target) return 0;
    return 1;
}

int hlib_layers(const char *owner, const HlibLayer *layers, int count) {
    Row rows[LY_BATCH];
    HlibEntry entries[LY_BATCH];
    int site[LY_BATCH], ne = 0, ok = 0;
    if (count < 1 || count > LY_BATCH) {
        hlib_logf("layers: %s: a batch holds 1 to 16 layers, nothing placed", owner);
        return 0;
    }
    for (int i = 0; i < count; i++) {
        Row r = {owner, layers[i].why, layers[i].target, layers[i].kind, PLACED_LAYER, layers[i].fn, layers[i].next};
        rows[i] = r;
    }
    AcquireSRWLockExclusive(&s_lock);
    const char *bad = check(rows, count);
    if (!bad) {
        for (int i = 0; i < count; i++)
            if ((site[i] = places_site(rows, i)) != 0) {
                HlibEntry e = {rows[i].target, rows[i].fn, rows[i].next};
                entries[ne++] = e;
            }
        ok = !ne || hlib_install_entry_observers(entries, ne);
        for (int i = 0; ok && i < count; i++) {
            if (!site[i]) { /* behind the innermost: its next first, then the innermost calls it */
                void **prev = s_rows[innermost(rows[i].target)].next;
                *rows[i].next = *prev;
                InterlockedExchangePointer((void *volatile *)prev, rows[i].fn);
            }
            s_rows[s_nrows++] = rows[i];
            declare(&rows[i]);
        }
    }
    ReleaseSRWLockExclusive(&s_lock);
    if (bad) hlib_logf("layers: %s: %s, nothing placed", owner, bad);
    return ok;
}

/* one layer on a jump thunk: the first re-points the thunk (hooks.c), a later one is spliced in behind the innermost */
int hlib_thunk_layer(const char *owner, const HlibLayer *l) {
    Row r = {owner, l->why, l->target, l->kind, PLACED_THUNK, l->fn, l->next};
    int ok = 0;
    AcquireSRWLockExclusive(&s_lock);
    const char *bad = check(&r, 1);
    if (!bad) {
        int in = innermost(r.target);
        if (in < 0) {
            ok = hlib_thunk_retarget(r.target, r.fn, r.next);
        } else {
            void **prev = s_rows[in].next;
            *r.next = *prev;
            InterlockedExchangePointer((void *volatile *)prev, r.fn);
            ok = 1;
        }
        if (ok) {
            s_rows[s_nrows++] = r;
            declare(&r);
        }
    }
    ReleaseSRWLockExclusive(&s_lock);
    if (bad) hlib_logf("layers: %s: %s, the thunk layer is not placed", owner, bad);
    return ok;
}

int hlib_site_jump(const char *owner, int target, int kind, void *to, void **back, const char *why) {
    Row r = {owner, why, target, kind, PLACED_JUMP, to, back};
    int ok = 0;
    AcquireSRWLockExclusive(&s_lock);
    const char *bad = check(&r, 1);
    if (!bad && (ok = hlib_patch_jump(target, to, back)) != 0) {
        s_rows[s_nrows++] = r;
        declare(&r);
    }
    ReleaseSRWLockExclusive(&s_lock);
    if (bad) hlib_logf("layers: %s: %s, the jump is not placed", owner, bad);
    return ok;
}

int hlib_site_bytes(const char *owner, const HlibPatch *patches, int count, int kind, const char *why) {
    Row rows[LY_BATCH];
    int ok = 0;
    if (count < 1 || count > LY_BATCH) return 0;
    for (int i = 0; i < count; i++) {
        Row r = {owner, why, patches[i].target, kind, PLACED_BYTES, (void *)patches[i].bytes, NULL};
        rows[i] = r;
    }
    AcquireSRWLockExclusive(&s_lock);
    const char *bad = check(rows, count);
    if (!bad && (ok = hlib_patch_bytes(patches, count)) != 0)
        for (int i = 0; i < count; i++) {
            s_rows[s_nrows++] = rows[i];
            declare(&rows[i]);
        }
    ReleaseSRWLockExclusive(&s_lock);
    if (bad) hlib_logf("layers: %s: %s, the bytes are not written", owner, bad);
    return ok;
}

/* One summary line and one line per declared replace, skip and patch: the start's list for the audit. */
void hlib_layers_report(void) {
    int kinds[HLIB_SKIPS + 1] = {0}, jumps = 0, bytes = 0, thunks = 0, targets = 0, layers = 0;
    AcquireSRWLockShared(&s_lock);
    for (int i = 0; i < s_nrows; i++) {
        const Row *r = &s_rows[i];
        if (r->placed == PLACED_JUMP) jumps++;
        else if (r->placed == PLACED_BYTES) bytes++;
        else if (r->placed == PLACED_THUNK) thunks++;
        else {
            layers++;
            kinds[r->kind]++;
            targets += innermost(r->target) == i; /* counted once, at its innermost layer */
        }
    }
    hlib_logf("layers: %d layer(s) on %d target(s) (before %d, after %d, around %d, replaces %d, skips %d); %d patch(es) "
               "inside 117's code (jumps %d, bytes %d); %d thunk(s) re-pointed",
               layers, targets, kinds[HLIB_BEFORE], kinds[HLIB_AFTER], kinds[HLIB_AROUND], kinds[HLIB_REPLACES],
               kinds[HLIB_SKIPS], jumps + bytes, jumps, bytes, thunks);
    for (int i = 0; i < s_nrows; i++)
        if (s_rows[i].kind >= HLIB_REPLACES || s_rows[i].placed != PLACED_LAYER)
            hlib_logf("layers:   %s %s %s %s", s_rows[i].owner, placed_name(s_rows[i].placed), kind_name(s_rows[i].kind),
                       g_target_names[s_rows[i].target]);
    ReleaseSRWLockShared(&s_lock);
}

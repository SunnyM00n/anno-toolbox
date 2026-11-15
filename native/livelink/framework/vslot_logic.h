/* Pure: the vtables whose step slots the framework wrapped, and each one's original functions. An engine manager is
   wrapped on the vtable of its live object, so a wrapper is shared by every object of that class and finds its
   original by the object's vtable. A few classes at most; the table never forgets one (the vtables live as long as
   the process). framework_test.c covers it. */
#ifndef HLIB_VSLOT_LOGIC_H
#define HLIB_VSLOT_LOGIC_H

#include <stddef.h>

#define VS_MAX_CLASSES 8
#define VS_MAX_SLOTS 2

typedef struct {
    void *const *vtable[VS_MAX_CLASSES];
    void *original[VS_MAX_CLASSES][VS_MAX_SLOTS];
    int n;
} VSlotTable;

/* the row of a vtable, -1 when it was not wrapped */
static int vs_find(const VSlotTable *t, void *const *vtable) {
    for (int i = 0; i < t->n; i++)
        if (t->vtable[i] == vtable) return i;
    return -1;
}

/* the original of slot k behind an object's vtable, NULL when that class was never wrapped */
static void *vs_original(const VSlotTable *t, const void *object, int k) {
    if (!object || k < 0 || k >= VS_MAX_SLOTS) return NULL;
    int i = vs_find(t, *(void *const *const *)object);
    return i < 0 ? NULL : t->original[i][k];
}

/* a vtable to wrap: its row, or -1 when it is wrapped already or the table is full */
static int vs_add(VSlotTable *t, void *const *vtable, void *const originals[VS_MAX_SLOTS]) {
    if (!vtable || vs_find(t, vtable) >= 0 || t->n >= VS_MAX_CLASSES) return -1;
    int i = t->n;
    t->vtable[i] = vtable;
    for (int k = 0; k < VS_MAX_SLOTS; k++) t->original[i][k] = originals[k];
    t->n = i + 1;
    return i;
}

#endif

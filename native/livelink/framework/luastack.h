/* The script stack, read and written the way the engine's own inline code does it: 16-byte values from
   the frame's base to its top, a type tag in each, the call info's top kept at least as high as the
   stack's. No game dependency, so the host test runs these on a fake state; the layout is in targets.h. */
#ifndef HLIB_LUASTACK_H
#define HLIB_LUASTACK_H

#include <stdint.h>
#include <string.h>
#include "targets.h"

#define LS_FIELD(L, off) (*(unsigned char **)((unsigned char *)(L) + (off)))

static int ls_gettop(void *L) {
    return (int)((LS_FIELD(L, T_LUA_OFF_TOP) - LS_FIELD(L, T_LUA_OFF_BASE)) / T_LUA_TV_SIZE);
}

/* 1 when n more values fit below the stack's end without growing it */
static int ls_room(void *L, int n) {
    return LS_FIELD(L, T_LUA_OFF_STACK_LAST) - LS_FIELD(L, T_LUA_OFF_TOP) > (long long)n * T_LUA_TV_SIZE;
}

/* the value at a 1-based index of the frame, 0 when outside it */
static unsigned char *ls_slot(void *L, int i) {
    if (i < 1 || i > ls_gettop(L)) return 0;
    return LS_FIELD(L, T_LUA_OFF_BASE) + (size_t)(i - 1) * T_LUA_TV_SIZE;
}

static int ls_tag(void *L, int i) {
    unsigned char *v = ls_slot(L, i);
    return v ? *(int *)(v + T_LUA_OFF_TAG) : -1;
}

static void ls_settop(void *L, int n) {
    if (n >= 0 && n <= ls_gettop(L)) LS_FIELD(L, T_LUA_OFF_TOP) = LS_FIELD(L, T_LUA_OFF_BASE) + (size_t)n * T_LUA_TV_SIZE;
}

/* one value on top: 8 bytes of payload, 4 zero bytes, the tag */
static void ls_push_raw(void *L, uint64_t bits, int tag) {
    unsigned char *top = LS_FIELD(L, T_LUA_OFF_TOP);
    unsigned char *ci = LS_FIELD(L, T_LUA_OFF_CI);
    memcpy(top, &bits, 8);
    *(int *)(top + 8) = 0;
    *(int *)(top + T_LUA_OFF_TAG) = tag;
    top += T_LUA_TV_SIZE;
    LS_FIELD(L, T_LUA_OFF_TOP) = top;
    if (ci && LS_FIELD(ci, T_LUA_OFF_CI_TOP) < top) LS_FIELD(ci, T_LUA_OFF_CI_TOP) = top;
}

static void ls_push_number(void *L, double v) {
    uint64_t bits;
    memcpy(&bits, &v, 8);
    ls_push_raw(L, bits, T_LUA_TAG_NUMBER);
}

static void ls_push_bool(void *L, int b) { ls_push_raw(L, b ? 1u : 0u, T_LUA_TAG_BOOLEAN); }
static void ls_push_nil(void *L) { ls_push_raw(L, 0, T_LUA_TAG_NIL); }
static void ls_push_lightud(void *L, void *p) { ls_push_raw(L, (uint64_t)(uintptr_t)p, T_LUA_TAG_LIGHTUD); }

/* Index of the first argument: a call with a colon passes the table first, a call with a dot does not. */
static int ls_first_arg(void *L) { return ls_tag(L, 1) == T_LUA_TAG_TABLE ? 2 : 1; }

static int ls_number(void *L, int i, double *out) {
    unsigned char *v = ls_slot(L, i);
    if (!v || *(int *)(v + T_LUA_OFF_TAG) != T_LUA_TAG_NUMBER) return 0;
    memcpy(out, v, 8);
    return 1;
}

#endif

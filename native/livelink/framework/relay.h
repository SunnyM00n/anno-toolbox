/* The relay of an entry hook: the stolen opening bytes of a function, then a jump back behind them. A
   relative call that ends the stolen bytes is rewritten to call the same target through a pointer slot,
   because the relay lives far from the image. No dependency, so the host test runs it on real machine code. */
#ifndef HLIB_RELAY_H
#define HLIB_RELAY_H

#include <string.h>

#define HLIB_RELAY_SIZE 64
#define HLIB_REDIRECT_WIDTH 14 /* an absolute jump through an inline pointer */
#define HLIB_RELAY_CALL_END(call_off) ((call_off) + 7 + HLIB_REDIRECT_WIDTH) /* a call relay's code end */

/* 1 when a relay can be built for a prefix of `stolen` bytes whose call (if any) is at call_off: the redirect fits in
   the prefix, a call ends inside it, and the jump behind the call stays below the call's pointer slot (pad + 40) */
#define HLIB_RELAY_SLOT 40
static int hlib_relay_fits(int stolen, int call_off) {
    if (stolen < HLIB_REDIRECT_WIDTH || stolen > 32) return 0;
    if (!call_off) return stolen + HLIB_REDIRECT_WIDTH <= HLIB_RELAY_SIZE;
    return call_off > 0 && call_off + 5 <= stolen && HLIB_RELAY_CALL_END(call_off) <= HLIB_RELAY_SLOT;
}

static void hlib_abs_jump(unsigned char *at, void *to) {
    at[0] = 0xFF;
    at[1] = 0x25;
    at[2] = at[3] = at[4] = at[5] = 0; /* jmp [rip+0] */
    memcpy(at + 6, &to, sizeof(to));
}

/* pad: HLIB_RELAY_SIZE writable bytes; site: the function; stolen: bytes taken (a whole-instruction
   prefix); call_off: offset of an E8 call ending the prefix, 0 = none */
static void hlib_build_relay(unsigned char *pad, const unsigned char *site, int stolen, int call_off) {
    if (!call_off) {
        memcpy(pad, site, (size_t)stolen);
        hlib_abs_jump(pad + stolen, (void *)(site + stolen));
        return;
    }
    int rel;
    memcpy(&rel, site + call_off + 1, sizeof(rel));
    unsigned char *slot = pad + HLIB_RELAY_SLOT;
    const unsigned char *target = site + call_off + 5 + rel;
    memcpy(pad, site, (size_t)call_off);
    memcpy(slot, &target, sizeof(target));
    pad[call_off] = 0xFF;
    pad[call_off + 1] = 0x15; /* call [rip+disp] */
    int disp = (int)(slot - (pad + call_off + 6));
    memcpy(pad + call_off + 2, &disp, sizeof(disp));
    /* a nop where the call returns: the unwinder takes a jmp [rip] there for a tail-call epilogue and would
       not undo the stolen pushes (HLIB_RELAY_CALL_END) */
    pad[call_off + 6] = 0x90;
    hlib_abs_jump(pad + call_off + 7, (void *)(site + stolen));
}

/* Unwind data of a relay whose stolen bytes end in a call (call_off): while the callee runs the relay is a frame,
   and an exception thrown there is dispatched through it. Without unwind data the unwinder would take the stolen
   pushes for the return address. The function's own codes up to the call describe the relay exactly (the same
   bytes); handlers, chained data and epilog codes are left out. ui: the function's UNWIND_INFO; out:
   HLIB_UNWIND_SIZE bytes. 0 when the function's data cannot be used (chained). */
#define HLIB_UNWIND_SIZE 64

static int hlib_unwind_slots(unsigned char op, unsigned char info) {
    if (op == 1) return info ? 3 : 2;  /* alloc large */
    if (op == 4 || op == 8) return 2;  /* save nonvol, save xmm128 */
    if (op == 5 || op == 9) return 3;  /* their far forms */
    return 1;
}

static int hlib_relay_unwind(unsigned char *out, const unsigned char *ui, int call_off) {
    int count = ui[2], kept = 0, fp = 0;
    if ((ui[0] >> 3) & 4) return 0; /* chained */
    for (int i = 0; i < count;) {
        unsigned char off = ui[4 + 2 * i], op = ui[5 + 2 * i] & 0xf, info = ui[5 + 2 * i] >> 4;
        int n = op == 6 ? 1 : hlib_unwind_slots(op, info); /* 6: a version 2 epilog code */
        if (op != 6 && off <= call_off && 4 + 2 * (kept + n) <= HLIB_UNWIND_SIZE) {
            memcpy(out + 4 + 2 * kept, ui + 4 + 2 * i, (size_t)(2 * n));
            kept += n;
            fp |= op == 3; /* the frame register is set */
        }
        i += n;
    }
    out[0] = 1; /* version 1, no handler */
    out[1] = (unsigned char)(ui[1] < call_off ? ui[1] : call_off);
    out[2] = (unsigned char)kept;
    out[3] = fp ? ui[3] : 0;
    return 1;
}

#endif

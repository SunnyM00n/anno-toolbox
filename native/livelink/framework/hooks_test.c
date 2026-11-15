/* Host test of hooks.c's code writes: an entry redirect placed and taken out again, again and again, on a
   function three other threads call without pause; every call must still return its value. build.cmd test. */
#include "hooks.c"
#include <stdio.h>

static unsigned char *g_site;
void hlib_log(const char *m) { printf("hooks_test log: %s\n", m); }
void hlib_logf(const char *f, ...) { printf("hooks_test log: %s\n", f); }
void *hlib_target(int id) {
    (void)id;
    return g_site;
}
int hlib_target_ok(int id) {
    (void)id;
    return 1;
}

static volatile LONG g_calls, g_hooked, g_stop, g_wrong;
static int (*o_fn)(int);
static int hooked(int x) {
    InterlockedIncrement(&g_hooked);
    return o_fn(x);
}

static DWORD WINAPI caller(LPVOID p) {
    int (*fn)(int) = (int (*)(int))p;
    while (!g_stop) {
        if (fn(5) != 7) InterlockedIncrement(&g_wrong);
        InterlockedIncrement(&g_calls);
    }
    return 0;
}

/* the thunk retarget: a non-jump site refused, the old target kept as next, the rel32 re-pointed and put back */
static int (*o_thunk)(int);
static int thunked(int x) { return o_thunk(x) + 100; }
static int thunk_case(unsigned char *mem) {
    DWORD old;
    unsigned char *fn = mem + 0x200, *thunk = mem + 0x40;
    VirtualProtect(mem, 0x1000, PAGE_EXECUTE_READWRITE, &old);
    memcpy(fn, "\x8d\x41\x02\xc3", 4); /* lea eax, [rcx+2] ; ret */
    int32_t rel = (int32_t)(fn - (thunk + 5)), was;
    thunk[0] = 0x90;
    VirtualProtect(mem, 0x1000, PAGE_EXECUTE_READ, &old);
    g_site = thunk;
    int refused = !hlib_thunk_retarget(0, (void *)thunked, (void **)&o_thunk);
    VirtualProtect(mem, 0x1000, PAGE_EXECUTE_READWRITE, &old);
    thunk[0] = 0xE9;
    memcpy(thunk + 1, &rel, 4);
    VirtualProtect(mem, 0x1000, PAGE_EXECUTE_READ, &old);
    int placed = hlib_thunk_retarget(0, (void *)thunked, (void **)&o_thunk);
    int (*call)(int) = (int (*)(int))thunk;
    int through = call(5), next_ok = (unsigned char *)o_thunk == fn;
    int back = hlib_thunks_restore();
    memcpy(&was, thunk + 1, 4);
    int plain = call(5);
    if (!refused || !placed || !next_ok || through != 107 || back != 1 || was != rel || plain != 7) {
        printf("FAIL hooks_test thunk: refused %d, placed %d, next %d, through %d, back %d, rel %d, after %d\n", refused,
               placed, next_ok, through, back, was == rel, plain);
        return 0;
    }
    printf("hooks_test: the thunk retarget passed\n");
    return 1;
}

/* The prefix rule of the entry hooks: the stolen bytes are copied to the relay verbatim, so they must be whole
   instructions without a rip-relative operand or a relative branch, other than the one call hlib_build_relay
   rewrites. A small x64 length decoder over the prologue shapes; an opcode it does not know fails the test, so the
   decoder is extended with the table rather than a row going unchecked. 0: undecodable, else the length. */
static int decode(const unsigned char *p, int max, int *riprel, int *rel) {
    int i = 0, rex = 0, imm = 0, modrm = 0, opsize16 = 0;
    *riprel = *rel = 0;
    for (; i < max && (p[i] == 0x66 || p[i] == 0xF2 || p[i] == 0xF3 || p[i] == 0x2E || p[i] == 0x3E || p[i] == 0x64 ||
                       p[i] == 0x65 || p[i] == 0xF0); i++)
        if (p[i] == 0x66) opsize16 = 1;
    if (i < max && (p[i] & 0xF0) == 0x40) rex = p[i++];
    if (i >= max) return 0;
    unsigned char op = p[i++];
    if (op == 0xC5 || op == 0xC4) { /* a VEX prefix (2 / 3 bytes), then an opcode with a modrm and no immediate */
        i += op == 0xC5 ? 1 : 2;
        if (i >= max) return 0;
        i++;
        modrm = 1;
    } else if (op == 0x0F) {
        if (i >= max) return 0;
        unsigned char o2 = p[i++];
        if (o2 >= 0x80 && o2 <= 0x8F) {
            *rel = 1;
            return i + 4;
        }
        if (o2 == 0x05 || o2 == 0xA2) return i;
        if (o2 == 0x1F || o2 == 0x1E || (o2 >= 0x10 && o2 <= 0x17) || (o2 >= 0x28 && o2 <= 0x2F) || (o2 >= 0x40 && o2 <= 0x4F) ||
            (o2 >= 0x90 && o2 <= 0x9F) || o2 == 0xAF || o2 == 0xB6 || o2 == 0xB7 || o2 == 0xBE || o2 == 0xBF || o2 == 0xD6 ||
            o2 == 0xEF || o2 == 0x6F || o2 == 0x7F || o2 == 0x6E || o2 == 0x7E || o2 == 0x54 || o2 == 0x57 || o2 == 0xD4 || o2 == 0x11)
            modrm = 1;
        else
            return 0;
    } else if ((op < 0x40 && (op & 7) < 4) || op == 0x63 || (op >= 0x84 && op <= 0x8B) || op == 0x8D || op == 0x8F ||
               op == 0xFE || op == 0xFF || (op >= 0xD0 && op <= 0xD3)) {
        modrm = 1;
    } else if (op == 0x69 || op == 0x81 || op == 0xC7) {
        modrm = 1, imm = opsize16 ? 2 : 4;
    } else if (op == 0x6B || op == 0x80 || op == 0x83 || op == 0xC0 || op == 0xC1 || op == 0xC6) {
        modrm = 1, imm = 1;
    } else if (op == 0xF6 || op == 0xF7) {
        if (i >= max) return 0;
        modrm = 1, imm = ((p[i] >> 3) & 7) < 2 ? (op == 0xF6 ? 1 : 4) : 0;
    } else if ((op >= 0x50 && op <= 0x5F) || (op >= 0x90 && op <= 0x99) || op == 0xC3 || op == 0xCC) {
        return i;
    } else if (op < 0x40 && (op & 7) == 4) {
        return i + 1;
    } else if (op < 0x40 && (op & 7) == 5) {
        return i + 4;
    } else if (op == 0x6A || op == 0xA8 || (op >= 0xB0 && op <= 0xB7)) {
        return i + 1;
    } else if (op == 0x68 || op == 0xA9) {
        return i + 4;
    } else if (op >= 0xB8 && op <= 0xBF) {
        return i + ((rex & 8) ? 8 : 4);
    } else if (op == 0xE8 || op == 0xE9) {
        *rel = 1;
        return i + 4;
    } else if (op == 0xEB || (op >= 0x70 && op <= 0x7F) || (op >= 0xE0 && op <= 0xE3)) {
        *rel = 1;
        return i + 1;
    } else {
        return 0;
    }
    if (i >= max) return 0;
    unsigned char m = p[i++], mod = m >> 6, rm = m & 7;
    if (mod != 3 && rm == 4) {
        if (i >= max) return 0;
        unsigned char sib = p[i++];
        if (mod == 0 && (sib & 7) == 5) i += 4;
    }
    if (mod == 0 && rm == 5) *riprel = 1, i += 4;
    else if (mod == 1) i += 1;
    else if (mod == 2) i += 4;
    return i + imm;
}

/* every entry hook of the table: the prefix is whole instructions, position independent, and fits a relay */
static int targets_case(void) {
    int bad = 0, checked = 0;
    for (int id = 0; id < T_COUNT; id++) {
        const HlibTarget *t = &g_targets[id];
        if (!t->rva || !t->is_entry_hook) continue;
        checked++;
        if (!hlib_relay_fits(t->verify_len, t->call_off)) {
            printf("FAIL hooks_test targets: %s: prefix %d / call at %d does not fit a relay\n", g_target_names[id], t->verify_len,
                   t->call_off);
            bad++;
            continue;
        }
        if (t->call_off && t->verify[t->call_off] != 0xE8) {
            printf("FAIL hooks_test targets: %s: no E8 at call_off %d\n", g_target_names[id], t->call_off);
            bad++;
        }
        int at = 0;
        while (at < t->verify_len) {
            int riprel, rel, n = decode(t->verify + at, t->verify_len - at, &riprel, &rel);
            if (!n || at + n > t->verify_len) {
                printf("FAIL hooks_test targets: %s: instruction at +%d is not decodable (or is cut by the prefix end); extend the "
                       "decoder or the prefix\n", g_target_names[id], at);
                bad++;
                break;
            }
            if (riprel || (rel && !(t->call_off && at == t->call_off))) {
                printf("FAIL hooks_test targets: %s: instruction at +%d is %s, not position independent\n", g_target_names[id], at,
                       riprel ? "rip-relative" : "a relative branch");
                bad++;
            }
            at += n;
        }
    }
    if (bad) return 0;
    printf("hooks_test: the prefix rule holds for %d entry hook(s)\n", checked);
    return 1;
}

int main(void) {
    unsigned char *mem = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    unsigned char *pads = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    DWORD old;
    HANDLE t[3];
    int ok = mem && pads;
    if (!ok) return 1;
    /* a 16-aligned function: 15 bytes of whole stores into the home area, then lea eax, [rcx+2] ; ret */
    g_site = mem + 0x100;
    memcpy(g_site, "\x48\x89\x5c\x24\x08\x48\x89\x74\x24\x10\x48\x89\x7c\x24\x18\x8d\x41\x02\xc3", 19);
    VirtualProtect(mem, 0x1000, PAGE_EXECUTE_READ, &old);
    hlib_build_relay(pads, g_site, 15, 0);
    o_fn = (int (*)(int))pads;
    for (int i = 0; i < 3; i++) t[i] = CreateThread(NULL, 0, caller, g_site, 0, NULL);
    Sleep(50);
    unsigned char orig[15], code[15];
    memcpy(orig, g_site, 15);
    hlib_abs_jump(code, (void *)hooked);
    code[14] = 0x90;
    for (int round = 0; round < 30 && ok; round++) {
        Write on = {g_site, code, 15, 0, {0, 0}}, off = {g_site, orig, 15, 0, {0, 0}};
        ok = write_all(&on, 1, WRITE_CODE);
        Sleep(5);
        ok = ok && write_all(&off, 1, WRITE_CODE);
        Sleep(2);
    }
    g_stop = 1;
    WaitForMultipleObjects(3, t, TRUE, 5000);
    /* values: one atomic store inside an instruction */
    unsigned char three = 3;
    Write val = {g_site + 0x11, &three, 1, 0, {0, 0}}; /* lea eax, [rcx+3] */
    ok = ok && write_all(&val, 1, WRITE_VALUES);
    int after = ((int (*)(int))g_site)(5);
    if (!ok || g_wrong || !g_hooked || after != 8) {
        printf("FAIL hooks_test: written %d, wrong results %ld, hooked calls %ld, after the value patch %d\n", ok, g_wrong,
               g_hooked, after);
        return 1;
    }
    if (!thunk_case(mem) || !targets_case()) return 1;
    printf("hooks_test: all passed (%ld calls, %ld through the redirect)\n", g_calls, g_hooked);
    return 0;
}

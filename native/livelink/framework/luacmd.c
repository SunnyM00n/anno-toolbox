/* The command runner's one caller (luacmd.h). The records are the runner's own: an MSVC std::string for the command,
   and a 0x30-byte result the runner writes whole, so a smaller record would let it write over the stack cookie
   (session 2 died so, 2026-10-27). */
#include "hlib.h"
#include "luacmd.h"
#include "targets.h"
#include <string.h>

typedef struct {
    union {
        char buf[16];
        const char *ptr;
    } u;
    size_t size, cap; /* inline while the capacity is 15 */
} MsvcString;

typedef struct {
    void *ref;
    MsvcString text;
    uint64_t tail;
} ScriptResult;
typedef char script_result_size[sizeof(ScriptResult) == T_SCRIPT_RESULT_SIZE ? 1 : -1];

/* the result's text is freed by its own call since 3.0: without it every text over 15 characters would leak */
static const int g_need[] = {T_SCRIPT_RUN_COMMAND, T_SCRIPT_RESULT_FREE, T_RECRUIT_FREE_STR, T_DATA_SCRIPT_MANAGER};

int hlib_lua_run_ok(void) {
    for (size_t i = 0; i < sizeof g_need / sizeof g_need[0]; i++)
        if (!hlib_target_ok(g_need[i])) return 0;
    return 1;
}

int hlib_lua_run(const char *cmd, char *text, int cap) {
    void *(*run)(void *, ScriptResult *, MsvcString *, int) =
        (void *(*)(void *, ScriptResult *, MsvcString *, int))hlib_target(T_SCRIPT_RUN_COMMAND);
    void (*drop)(ScriptResult *) = (void (*)(ScriptResult *))hlib_target(T_SCRIPT_RESULT_FREE);
    void (*drop_text)(MsvcString *) = (void (*)(MsvcString *))hlib_target(T_RECRUIT_FREE_STR);
    void **g = (void **)hlib_target(T_DATA_SCRIPT_MANAGER);
    MsvcString in = {0};
    ScriptResult out = {0};
    size_t len = cmd ? strlen(cmd) : 0;
    int ran = 0;
    if (text && cap > 0) text[0] = 0;
    if (!len || !run || !drop || !drop_text || !g || !*g) return 0;
    if (len < 16) memcpy(in.u.buf, cmd, len + 1), in.cap = 15;
    else in.u.ptr = cmd, in.cap = len; /* read only: the runner copies it into its own "return ..." */
    in.size = len;
    out.text.cap = 15;
    __try {
        run((char *)*g - T_OFF_SCRIPTMGR_HOLDER, &out, &in, T_SCRIPT_SOURCE_KEY);
        ran = 1;
        if (text && cap > 0 && out.text.size && out.text.size < 1u << 20) {
            const char *p = out.text.cap > 15 ? out.text.u.ptr : out.text.u.buf;
            size_t n = out.text.size < (size_t)cap - 1 ? out.text.size : (size_t)cap - 1;
            memcpy(text, p, n), text[n] = 0;
        }
        drop(&out); /* as the key binding does: the Lua reference, then the text (its own call since 3.0) */
        drop_text(&out.text);
    } __except (hlib_fault_filter(GetExceptionCode())) {
        hlib_log("lua command: a fault in the runner");
    }
    return ran;
}

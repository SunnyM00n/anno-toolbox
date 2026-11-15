/* Run a Lua command string the way a key binding does (the script manager's command path, source 2): the one caller
   of the engine's command runner. Only from the UI thread, outside a script call (queue it and run at the frame's
   end); the strings are the module's own fixed ones, never text from a peer. */
#ifndef HLIB_LUACMD_H
#define HLIB_LUACMD_H

/* 1 when the runner was called; text (cap bytes, may be NULL) gets the result's text, "" when it has none. */
int hlib_lua_run(const char *cmd, char *text, int cap);
/* 1 when every target the runner needs matched this build */
int hlib_lua_run_ok(void);

#endif

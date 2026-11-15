/* The live link module's entry points (mod_livelink.c, switch live_link): what the core's dormant start calls to
   bring the link up in a start without the full mode, and what the library's unload calls. */
#ifndef LIVELINK_H
#define LIVELINK_H

/* the targets the link needs (the dormant start resolves them first), whether the switch live_link is on, the install
   (1 when the pipe is open), and the end: the session file is deleted */
const int *hlib_live_link_targets(int *n);
const int *hlib_live_link_scene_targets(int *n); /* optional: the scene reload is served when these match too */
const int *hlib_live_link_bank_targets(int *n);  /* optional: the bank reload is served when these match too */
const int *hlib_live_link_guard_targets(int *n); /* the guards' reads: a start that resolves its own lists needs these */
int hlib_live_link_wanted(void);
int hlib_live_link_install(void);
void hlib_live_link_shutdown(void);
/* the script table HelperLink: 1 while a client is connected; 1 when the selected object was queued as an
   edit request (sent on the next UI frame), 0 when no client is connected */
int hlib_live_link_connected(void);
int hlib_live_link_edit_selected(void);
/* The guards' reads (any thread; also the live reload's queue): NULL when the game is quiet, else the sentence of a busy
   answer: a session loads, a save is written, the session changes, an asset reload holds; needs_assets adds a pending
   scenario action (a request that replaces the asset database would overwrite it). */
const char *hlib_live_link_busy(int needs_assets);

#endif

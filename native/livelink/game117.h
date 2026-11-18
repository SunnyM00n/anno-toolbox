/* Anno 117: Pax Romana. See game.h for what a game header holds. */
#ifndef HLIB_GAME117_H
#define HLIB_GAME117_H

#define G_ID "anno117"                                       /* the welcome's "game": the Toolbox's game key */
#define G_USER_FOLDER "/Anno 117 - Pax Romana/"              /* under Documents, with both slashes */
#define G_PIPE_BASE "\\\\.\\pipe\\anno117-live-link-"        /* the process id is appended */
#define G_GAME_LOG_DIR "log"                                 /* the game's own log, in the user folder */
#define G_GAME_LOG_PREFIX "Anno117_LOGFILE_"
#define G_LOADER_LOG "mods/mod-loader.log"                   /* the mod loader's log */
#define G_TRIGGER_FILE "mods/live-reload.txt"
#define G_HAS_ENGINE 1                                       /* the link's engine side is built for this game */

/* the helper's own files, the same place in every game */
#define G_SETTINGS_FILE "log/helper-library.ini"
#define G_LOG_FILE "log/helper-library.log"
#define G_LOG_OLD_FILE "log/helper-library.old.log"
#define G_SESSION_DIR "live-link"
#define G_SESSION_FILE "live-link/session.json"

#endif

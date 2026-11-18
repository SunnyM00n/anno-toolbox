/* Anno 1800. See game.h for what a game header holds. The engine side is not built yet: the link answers the
   handshake and ping, and everything else with "unavailable". The game's own log and the mod loader's files are not
   used until it is (their names stay empty rather than guessed). */
#ifndef HLIB_GAME1800_H
#define HLIB_GAME1800_H

#define G_ID "anno1800"
#define G_USER_FOLDER "/Anno 1800/"
#define G_PIPE_BASE "\\\\.\\pipe\\anno1800-live-link-"
#define G_GAME_LOG_DIR "log"
#define G_GAME_LOG_PREFIX ""
#define G_LOADER_LOG ""
#define G_TRIGGER_FILE ""
#define G_HAS_ENGINE 0

#define G_SETTINGS_FILE "log/helper-library.ini"
#define G_LOG_FILE "log/helper-library.log"
#define G_LOG_OLD_FILE "log/helper-library.old.log"
#define G_SESSION_DIR "live-link"
#define G_SESSION_FILE "live-link/session.json"

#endif

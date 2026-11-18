/* The game this build of the helper is for: one per build target (build.cmd: the default is Anno 117, "game1800" and
   "game2205" the others). A game header names what is fixed to a game - the user folder under Documents, the pipe,
   the id the welcome gives, the game's own log, the settings file - and says whether the engine side exists
   (G_HAS_ENGINE); the numbers of the game's build, its guard and its targets are in its build header (build<game>.h,
   via targets.h). Everything else in the core is the same for every game. */
#ifndef HLIB_GAME_H
#define HLIB_GAME_H

#if defined(HLIB_GAME_1800)
#include "game1800.h"
#elif defined(HLIB_GAME_2205)
#include "game2205.h"
#else
#include "game117.h"
#endif

#endif

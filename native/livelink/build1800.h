/* The numbers of the game build the library knows for Anno 1800 (an executable of 430828056 bytes): the
   build guard. The target table is empty: on this game the library answers the handshake and ping only. The
   layout values below are Anno 117's, included so the sources compile; nothing reads them here. */
#ifndef HLIB_BUILD1800_H
#define HLIB_BUILD1800_H

#define T_BUILD_FILESIZE 430828056ULL
#define T_BUILD_TIMESTAMP 0x673210E1u
#define T_BUILD_SIZEOFIMAGE 0x1A057000u

#define HLIB_BUILD117_TABLE
#include "build117.h"

#endif

#if defined(HLIB_TARGETS_H) && !defined(HLIB_BUILD1800_TABLE)
#define HLIB_BUILD1800_TABLE
static const HlibTarget g_targets[T_COUNT];
#endif

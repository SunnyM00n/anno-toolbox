/* The numbers of the game build the library knows for Anno 2205 (an executable of 172685744 bytes): the
   build guard, read from the file's size and the header's plain values. The target table is empty: on this
   game the library answers the handshake and ping only. The layout values below are Anno 117's, included
   so the sources compile; nothing reads them here. */
#ifndef HLIB_BUILD2205_H
#define HLIB_BUILD2205_H

#define T_BUILD_FILESIZE 172685744ULL
#define T_BUILD_TIMESTAMP 0x5BAA3655u
#define T_BUILD_SIZEOFIMAGE 0xA4FA000u

#define HLIB_BUILD117_TABLE
#include "build117.h"

#endif

#if defined(HLIB_TARGETS_H) && !defined(HLIB_BUILD2205_TABLE)
#define HLIB_BUILD2205_TABLE
static const HlibTarget g_targets[T_COUNT];
#endif

/* The island area's component manager, read as the engine reads it, pure: the island area of an area id in the area
   manager's list, the manager's two maps and a property's pool in them. engine.c walks the pools with them
   (hlib_area_components); modules_test.c covers them on memory laid out as build117.h says. Every pointer is checked
   before it is followed: a manager the engine has not built yet holds other values. */
#ifndef HLIB_COMPWALK_LOGIC_H
#define HLIB_COMPWALK_LOGIC_H

#include <stdint.h>

/* an address of user space, aligned to 8 */
int cw_followable(const void *p);

#define CW_MAX_AREAS 4096 /* a sanity bound on the session's area list */

/* The island area of `area_id` in the game session manager's area list (16-byte {u16 id, area} entries sorted by id),
   as the scope guard looks it up; not the construction area of the same id, which the area getter returns. NULL when
   the list has no such id or the entry's area carries another one. */
void *cw_island_area(const char *gsm, int area_id);

/* The ids of the island areas in the game session manager's area list (an island flag and a session slot), in list
   order, at most cap; how many. */
int cw_island_ids(const char *gsm, short *ids, int cap);

/* the id of a construction area (what hlib_area gives and the central tick passes), 0 for none */
int cw_carea_id(const void *carea);

/* the island area's component maps; 0 while the manager or one of its maps cannot be followed */
int cw_maps(const char *island_area, const char *maps[2]);

/* the pool of property `prop` in one map (an MSVC hash map keyed by the property id, FNV-1a 64 of its four bytes) */
void *cw_pool(const char *map, uint16_t prop);

#endif

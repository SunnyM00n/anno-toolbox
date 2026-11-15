/* The asset registry's table, pure (no game, no Windows): the mod's two blocks of anno2070-conversion.json,
   "helper_registry" (every asset of ours with the source GUID it came from; the header carries their count) and
   "helper_slots" (the slot kinds as separate types: the type order, the slot assets and the buildings on them). One
   record per GUID of ours, sorted, found by binary search. */
#ifndef HLIB_REGISTRY_LOGIC_H
#define HLIB_REGISTRY_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define REG_MAX_TYPES 16 /* the slot manager keeps 16 lists */
#define REG_NAME 24

enum { REG_ROLE_NONE = 0, REG_ROLE_SLOT = 1, REG_ROLE_BUILDING = 2 };

typedef struct {
    uint32_t ours;
    uint32_t source; /* the source GUID it was made from, 0 none */
    int slot_type;   /* the slot type value of its Slot/SlotType (7..), 0 when it names no kind of ours */
    int role;        /* REG_ROLE_*: a slot asset, a building that stands on one */
} RegEntry;

typedef struct {
    RegEntry *e;
    int n, cap;
    int declared; /* the sum of the "count" headers read */
    int pairs;    /* the source/ours pairs the "assets" members gave */
    int first;    /* the value of the first own slot type */
    int ntypes;
    char types[REG_MAX_TYPES][REG_NAME];
    int sealed;
} RegTable;

void reg_init(RegTable *t);
void reg_free(RegTable *t);
/* read the two blocks of one data file; returns the records added (0 when the file has neither block) */
int reg_parse(RegTable *t, const char *json, size_t len);
/* sort and merge the records of one GUID (a field that is set wins over one that is not); call once after the parses */
void reg_seal(RegTable *t);
const RegEntry *reg_find(const RegTable *t, uint32_t ours);
/* 1 when the header counts agree with the pairs read (no block at all counts as agreeing) */
int reg_consistent(const RegTable *t);
/* the name of slot type value v (first..first+ntypes-1), "" otherwise */
const char *reg_type_name(const RegTable *t, int v);

#endif

/* The running game's difficulty setup, the pure part: the choices the engine keeps as 16-bit enum indices in the
   setup's DifficultySetup property, which option of the mod each stands for in the modules, and the percent a choice
   means. No game dependency; the module test covers it. */
#ifndef HLIB_GAMESETUP_LOGIC_H
#define HLIB_GAMESETUP_LOGIC_H

#include <stddef.h>

enum {
    GS_DIFFICULTY,
    GS_INCIDENTS,
    GS_INCIDENT_PREVENTION,
    GS_CONTRACT_COOLDOWN,
    GS_START_CREDITS,
    GS_FERTILITY,
    GS_RAW_MATERIAL,
    GS_NEED_CONSUMPTION,
    GS_RESEARCH_SPEED,
    GS_VOLCANO_ACTIVE,
    GS_COUNT
};

const char *gs_field_name(int field);
/* A choice of a DifficultySetup property instance (the live one or a preset asset's): its enum index, -1 when the field
   is unknown or the value is not a small index. */
int gs_pick(const void *prop, int field);

/* 117's SetupIncidents (Easy, Medium, Hard = 0, 1, 2) as the catastrophes' difficulty; -1 when not a level. */
int gs_incident_level(int value);
/* 117's SetupContractCooldown (Often, Normal, Rare) as the percent of the mod's quest intervals; 0 when not a
   choice. */
int gs_quest_percent(int value);
/* The names of an enum index in the order of 117's asset (Easy, Medium, Hard ...), for the log: "?" outside. */
const char *gs_level_name(int field, int value);

#endif

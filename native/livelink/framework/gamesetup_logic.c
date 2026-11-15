/* The running game's difficulty setup, the pure part (gamesetup_logic.h). */
#include "gamesetup_logic.h"
#include "targets.h"
#include <string.h>

static const struct {
    const char *name;
    unsigned off;
} FIELD[GS_COUNT] = {
    {"DifficultyLevel", T_SETUP_DIFFICULTY},
    {"Incidents", T_SETUP_INCIDENTS},
    {"IncidentPrevention", T_SETUP_INCIDENT_PREVENTION},
    {"ContractCooldown", T_SETUP_CONTRACT_COOLDOWN},
    {"StartCredits", T_SETUP_START_CREDITS},
    {"Fertility", T_SETUP_FERTILITY},
    {"RawMaterial", T_SETUP_RAW_MATERIAL},
    {"NeedConsumption", T_SETUP_NEED_CONSUMPTION},
    {"ResearchSpeed", T_SETUP_RESEARCH_SPEED},
    {"VolcanoEruptionActive", T_SETUP_VOLCANO_ACTIVE},
};

const char *gs_field_name(int field) { return field >= 0 && field < GS_COUNT ? FIELD[field].name : "?"; }

int gs_pick(const void *prop, int field) {
    unsigned short v;
    if (!prop || field < 0 || field >= GS_COUNT) return -1;
    memcpy(&v, (const unsigned char *)prop + FIELD[field].off, sizeof(v));
    return v <= 16 ? (int)v : -1;
}

int gs_incident_level(int value) { return value >= 0 && value <= 2 ? value : -1; }

int gs_quest_percent(int value) {
    static const int pct[3] = {85, 100, 130}; /* Often, Normal, Rare = the mod's Often, Standard, Sometimes */
    return value >= 0 && value <= 2 ? pct[value] : 0;
}

const char *gs_level_name(int field, int value) {
    static const char *const three[3] = {"Easy", "Medium", "Hard"}, *const cooldown[3] = {"Often", "Normal", "Rare"},
                              *const five[5] = {"VeryEasy", "Easy", "Medium", "Hard", "VeryHard"};
    if (value < 0) return "?";
    switch (field) {
    case GS_DIFFICULTY: return value < 5 ? five[value] : "?";
    case GS_INCIDENTS: return value < 3 ? three[value] : "?";
    case GS_CONTRACT_COOLDOWN: return value < 3 ? cooldown[value] : "?";
    default: return "?"; /* the other enums' order is not verified */
    }
}

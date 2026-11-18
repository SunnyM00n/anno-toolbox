/* The live widget values (ui.values; docs/live-link.md section 17), the pure part (mod_livevalues.c is the engine
   side): the walk of one open scene's widget tree through a reader the engine side fills (a synthetic tree in the host
   test), the widgets of the one file the editor has open, and per widget only what it shows: its text, its image id,
   whether it is visible and the values of its own properties. No engine call, no Windows call; livevalues_test.c. */
#ifndef LIVEVALUES_LOGIC_H
#define LIVEVALUES_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define LV_TEXT 512
/* The limits against the game's 136 dumped scenes (2026-11-17): a widget holds up to 42 values, a placed file up to
   146 widgets (36 KB answered), a scene up to 18,974 widgets. */
#define LV_MAX_VALUES 64      /* the property values of one widget that are answered */
#define LV_MAX_ANSWERED 2000  /* widgets in one answer */
#define LV_MAX_WALK 40000     /* widgets looked at to find the file's ones */
#define LV_MAX_DEPTH 64
#define LV_MAX_KIDS 4096
#define LV_DATA_MAX (100 * 1024) /* the answer's data: under the link's answer buffer */

/* a property value as it is answered: a number, a switch or a text; anything else is left out */
enum { LV_NUMBER = 1, LV_BOOL, LV_STRING };
typedef struct {
    uint32_t uid; /* the property's UniqueId, as the file names it */
    int kind;
    double num;   /* LV_NUMBER, and LV_BOOL as 0 or 1 */
    char text[LV_TEXT];
} LvValue;

/* who a widget is: what the editor matches it by, the widget file it places, its own Visible */
typedef struct {
    char name[LV_TEXT];
    int has_name, has_crc, has_guid, has_tmpl, visible;
    uint32_t name_crc;
    uint8_t guid[16], tmpl[16]; /* tmpl: the widget file a container instance or the scene places */
} LvIdentity;

/* what a widget shows, read only for the widgets that are answered */
typedef struct {
    int has_text, has_image, nvalues;
    char text[LV_TEXT], image[LV_TEXT];
    LvValue values[LV_MAX_VALUES];
} LvShown;

typedef struct {
    int (*identify)(void *ctx, const void *w, LvIdentity *out); /* 1 when read */
    void (*shown)(void *ctx, const void *w, const LvIdentity *id, LvShown *out);
    int (*children)(void *ctx, const void *w, const void **out, int cap, int *cut); /* how many, at most cap */
    void *ctx;
} LvReader;

/* a fixed buffer: what does not fit is left out whole, and `cut` says so */
typedef struct {
    char *p;
    size_t n, cap;
    int cut, scenes;
} LvBuf;

typedef struct {
    int walked, answered, cut, found;
} LvStats;

void lv_buf_init(LvBuf *b, char *mem, size_t cap);
void lv_str(LvBuf *b, const char *s); /* a JSON string (widgetread_logic.h) */
int lv_guid_parse(const char *text, uint8_t out[16]); /* wt_guid_text's text back; 0 when it is not a GUID */
/* {"file":<guid or null>,"scenes":[ ... lv_scene ... ],"cut":<bool>} */
void lv_open(LvBuf *b, const uint8_t *file);
void lv_close(LvBuf *b);
/* One open scene: {"state":..., "widgets":[...], "cut":...} appended when the file is placed in it (file NULL: the
   scene's own widgets). The anchor is the first widget that places the file; the answered widgets are those it places
   directly, nested files' widgets not. 1 written, 0 the file is not in this scene. */
int lv_scene(LvBuf *b, const char *state, const uint8_t *file, const LvReader *r, const void *root, LvStats *st);

/* Following the open scenes (the ui.scene event, docs/live-link.md section 18): which UI states with a scene are open
   now, against what the client was told, as opened and closed changes. The engine side fills LsOpen. */
#define LS_MAX 160    /* the UI state ids */
#define LS_NAME 64
#define LS_EVENTS 64  /* changes sent per look; the rest follow on the next */
typedef struct {
    int id, depth;
    char name[LS_NAME], parent[LS_NAME];
} LsOpen;
/* what the client was told, per state id; `next` numbers the openings */
typedef struct {
    uint8_t open[LS_MAX];
    uint32_t order[LS_MAX], next;
    char name[LS_MAX][LS_NAME];
} LsSeen;
typedef struct {
    int opened; /* 1 opened, 0 closed */
    uint32_t order;
    LsOpen st;
} LsChange;
void ls_reset(LsSeen *s); /* a new client or subscription: it was told nothing */
/* 1 when the name is plain (letters, digits, '_'; not empty, under LS_NAME): only such states are reported */
int ls_name_ok(const char *name);
/* the scene file's name of a state, as the game names it: "ConstructionMenu" -> "constructionmenuscene"; 0 not plain */
int ls_scene_name(const char *state, char *out, int cap);
/* The changes from what `s` holds to the `n` states open now: the closings first (the newest opening first), then
   the openings by depth, then id, each with the next order number; at most cap. `s` takes only the changes returned,
   so the rest come with the next call. */
int ls_diff(LsSeen *s, const LsOpen *now, int n, LsChange *out, int cap);
/* the event's data: {"change","state","scene","parent","depth","order","window"}; window left out when w or h is 0;
   the length, or -1 when it does not fit */
int ls_event_json(char *out, size_t cap, const LsChange *c, int win_w, int win_h);

#endif

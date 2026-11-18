/* The live widget tree, the engine side every reader of it shares (widgetread.c): a widget's children, who it is, the
   class chain's built-in properties through the engine's own readers and the property ids of a widget file. Read
   only. mod_livevalues.c answers the live widget values with it. A reader runs on the UI frame under the scene
   client's own lock; the calls marked "under the caller's guard" fault like any read of the game's memory. */
#ifndef WIDGETREAD_H
#define WIDGETREAD_H

#include "hlib.h"
#include <stdint.h>

#define WR_TEXT 512
#define WR_MAX_CUSTOM 96 /* the property values of one widget that are read */

/* the variant types the property readers write (the scene format's CustomPropertyType) */
enum { WR_BOOL = 0, WR_INT = 1, WR_UINT = 2, WR_FLOAT = 3, WR_STRING = 4, WR_VEC2 = 5, WR_VEC3 = 6, WR_OBJECT = 8 };
enum { WR_COLOR = 18, WR_EMPTY = 20, WR_EMPTY2 = 24, WR_INT64 = 25, WR_UINT64 = 26 };

/* who a widget is: its name, its file's ids and the widget file a container instance or the scene places */
typedef struct {
    char name[WR_TEXT];
    int has_name, has_tmpl;
    uint32_t name_crc, type_hash;
    uint8_t guid[16], tmpl[16];
} WrIdentity;

/* one walk's state of the built-in readers */
typedef struct {
    CRITICAL_SECTION *cs; /* the scene client's lock, held by the walk */
    const void *prop_vt0; /* slot 0 of the properties' accessors (events and actions have another) */
    int getters;          /* 0 once a reader faulted: no built-in property is read for the rest of the walk */
    int faults;
    const char *who;      /* the reader's name in the log */
} WrProps;

/* 1 when the widget classes are the ones this build's layouts were read from; 0 logs nothing (the caller says it) */
int wr_classes_ok(void);
int wr_readable(const void *p);
/* a string block of the scene format (its length, then the text) into out; 0 when there is none */
int wr_string_of(const char *h, char *out, int cap);
int wr_keeps_children(const char *file);
/* the scene client of a UI state when it has a root widget, else NULL; under the caller's guard */
char *wr_client_of(char *state);
/* a widget's children, at most cap (*cut set when there are more or the read faulted); guarded */
int wr_children(const void *w, const void **out, int cap, int *cut, const char *who);
/* under the caller's guard; `file` is the widget's file instance */
void wr_identity(const char *rt, const char *file, WrIdentity *out);
/* The widget's built-in properties, the class chain upwards: `each` gets the property's record, the object the
   readers take and its name; it returns 0 to end the walk. *class_name (may be NULL) is the widget's class. Under
   the caller's guard. */
typedef int (*WrEachProperty)(void *ctx, const void *rec, void *props, const char *name);
void wr_each_property(WrProps *c, char *rt, WrEachProperty each, void *ctx, const char **class_name);
/* One built-in property through its own reader: `take` gets the variant before it is freed and returns what
   wr_read_property returns. A fault is contained, the client's lock given back to its depth and the readers ended. */
int wr_read_property(WrProps *c, const void *rec, void *props, int (*take)(void *out, const unsigned char *var),
                     void *out);
/* the widget file a container instance's file places, resolved; NULL when there is none; under the caller's guard */
const char *wr_template_of(const char *file);
/* each custom property the widget file has made: its record, its value index and its UniqueId; under the guard */
typedef void (*WrEachCustom)(void *ctx, const char *cpi, int index, uint32_t uid);
void wr_each_custom(const char *tmpl, WrEachCustom each, void *ctx);

#endif

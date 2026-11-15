/* The UI dump probe, pure (mod_uidump.c): the walk of one scene's live widget tree through a reader the engine side
   fills (a synthetic tree in the host test), what each widget inherits (shown, alpha product, the widget file it
   belongs to), its screen rectangle from the world matrix, and the JSON written per scene. uidump_test.c covers it;
   the format is docs/ui-editor.md "Comparing with the game". */
#ifndef HLIB_UIDUMP_LOGIC_H
#define HLIB_UIDUMP_LOGIC_H

#include <stddef.h>
#include <stdint.h>

#define UD_FORMAT 1
#define UD_MAX_PROPS 80
#define UD_MAX_CUSTOM 96
#define UD_TEXT 512
#define UD_MAX_KIDS 4096
#define UD_MAX_UNNAMED 32
#define UD_MAX_UNKNOWN 4

/* how a value reads: the engine's variant type byte decides it (the tags) */
enum { UD_NONE, UD_BOOL, UD_INT, UD_FLOAT, UD_STRING, UD_VEC, UD_COLOR, UD_OBJECT, UD_RAW };

typedef struct {
    const char *name; /* a built-in property's name (the engine's own string); NULL for a custom property */
    uint32_t uid;     /* a custom property's UniqueId, 0 when its template does not say */
    int index;        /* a custom property's value index; -1 for a built-in one */
    int type;         /* the variant's type byte */
    int kind, n;      /* UD_*; n components of a UD_VEC */
    double v[4];
    long long i;
    char text[UD_TEXT];
    uint8_t raw[24];
} UdValue;

/* a serialised field whose name the format does not know yet (ui_dump_unnamed): where it was read and its value */
enum { UD_ON_INSTANCE, UD_ON_TEMPLATE, UD_ON_PROPERTY };
typedef struct {
    uint32_t crc, owner, code, elem, offset, uid; /* owner: the type record's hash; uid: the custom property's */
    int on;
    UdValue v;
} UdField;

typedef struct {
    const void *mask_owner; /* the widget whose mask covers this one: its id is looked up, never written */
    char name[UD_TEXT];
    char cls[40]; /* the built-in class: Widget, CompositeWidget, LabelWidget, ImageWidget ... */
    uint8_t guid[16], tmpl[16];
    int has_name, has_crc, has_guid, has_tmpl, has_world, container, props_read, props_fault;
    int visible, drawable, receives_mask, mask_intersect; /* the flags' meaning, from the engine side */
    uint32_t name_crc, type_hash, dirty;
    uint16_t flags;
    uint8_t mask_mode;
    int z;
    float local[16], world[16]; /* row vectors: p' = p M, the translation in 12..14 */
    float pos[3], pivot[3], scale[3], comp[2], bb_pos[3], bb_size[2], size[2], rot, alpha;
    int nprops, ncustom;
    UdValue props[UD_MAX_PROPS], custom[UD_MAX_CUSTOM];
    int nunnamed, nunknown;
    UdField unnamed[UD_MAX_UNNAMED];
    uint32_t unknown_types[UD_MAX_UNKNOWN]; /* type hashes the format has no name for, met on this widget */
} UdNode;

typedef struct {
    int (*read)(void *ctx, const void *w, UdNode *out);                       /* 1 when read */
    int (*children)(void *ctx, const void *w, const void **out, int cap, int *cut); /* how many, at most cap; *cut: more */
    void *ctx;
    const char *(*getters)(void *ctx); /* the built-in readers' state at the end: "read", "faulted" or "off"; may be NULL */
} UdReader;

typedef struct {
    char *p;
    size_t n, cap;
    int oom;
} UdBuf;

typedef struct {
    const char *scene, *state_name, *stamp, *trigger;
    int state_id, load_state;
    uint32_t out_w, out_h, ref_w, ref_h;
    int root_x, root_y, win_w, win_h, screen_w, screen_h;
    unsigned long long game_build;
    uint32_t library;
    int max_nodes, max_depth;
} UdHeader;

typedef struct {
    int nodes, unread, truncated, deep, props_faults, masks_unresolved;
} UdStats;

void ud_buf_free(UdBuf *b);
void ud_put(UdBuf *b, const char *s);
void ud_putf(UdBuf *b, const char *fmt, ...);
void ud_str(UdBuf *b, const char *s, int len); /* a JSON string, escaped; len < 0: up to the NUL */
void ud_num(UdBuf *b, double v);               /* null for NaN and infinities */
void ud_guid_text(const uint8_t g[16], char out[37]); /* the files' little-endian UUID layout, as uuid bytes_le */
void ud_point(const float m[16], float x, float y, float out[2]);
int ud_rect(const UdNode *n, float out[4]);    /* the bounding box's corners through the world matrix: x0 y0 x1 y1 */
int ud_scene_listed(const char *list, const char *state_name); /* 1 when a comma list names the state or its scene */
void ud_file_name(char *out, int cap, const char *stamp, const char *scene);
/* a field's bytes as its file-format code says (scalars, vectors, ids); a list or a reference stays raw */
void ud_field_value(UdValue *v, uint32_t code, const uint8_t mem[24]);
int ud_listed(const uint32_t *list, int n, uint32_t h);
int ud_dump(UdBuf *b, const UdHeader *h, const UdReader *r, const void *root, UdStats *st); /* 1 written */
/* The file whole at `path` or not at all: written to `path`.tmp, checked, moved over `path`. 0 on any failure (the
   temporary file removed, an existing file kept), *err the Windows error. */
int ud_write_atomic(const char *path, const char *data, size_t n, unsigned long *err);

#endif

/* The link helper's edition (core/build.cmd, "build.cmd link"): the core alone, with the link, the reload it brings
   up and the UI dump its ui.dump reads through as its only modules; never the full mode. The full library's edition
   is mod_scaffolds.c. */
#include "hlib.h"

void hlib_register_live_link(void);
void hlib_register_live_reload(void);
void hlib_register_ui_dump(void);

static void register_modules(void) {
    hlib_register_live_link();
    hlib_register_live_reload();
    hlib_register_ui_dump();
}

static const HlibEdition k_edition = {"link", 1, register_modules, 0, 0, 0, 0};

const HlibEdition *hlib_edition(void) { return &k_edition; }

/* The link helper's edition (core/build.cmd, "build.cmd link"): the core alone, with the link, the reload it brings
   up, no other module; never the full mode. The full library's edition is mod_scaffolds.c. */
#include "hlib.h"

void hlib_register_live_link(void);
void hlib_register_live_reload(void);

static void register_modules(void) {
    hlib_register_live_link();
    hlib_register_live_reload();
}

static const HlibEdition k_edition = {"link", 1, register_modules, 0, 0, 0, 0};

const HlibEdition *hlib_edition(void) { return &k_edition; }

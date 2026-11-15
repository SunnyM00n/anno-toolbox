/* Configuration: the ini (helper-library.ini) and safe mode, the edition's start mode (full or dormant), the
   settings that change the simulation (every peer must share them), their digest, and the Config chunk a save
   carries and a load compares. */
#include "core_int.h"
#include "features_logic.h"
#include "util.h"
#include <stdlib.h>
#include <string.h>

/* configuration and safe mode */

static HlibIniPair s_ini[MAX_INI];
static int s_nini;
int s_safe_mode;

HlibIniPair *cfg_pair(const char *key) {
    for (int i = 0; i < s_nini; i++)
        if (0 == lstrcmpiA(s_ini[i].key, key)) {
            s_ini[i].used = 1;
            return &s_ini[i];
        }
    return NULL;
}

static void cfg_warn(HlibIniPair *p, const char *what, int dflt) {
    if (p->warned) return;
    p->warned = 1;
    hlib_logf("config: %s=%s is not %s, the default %d is used", p->key, p->value, what, dflt);
}

/* the default is the feature's class: a module's from its register, else the framework switch's (features_logic.c) */
int hlib_feature_on(const char *name) {
    int cls = core_feature_class(name);
    if (!cls) cls = fc_switch_class(name);
    int dflt = fc_default(cls);
    HlibIniPair *p = cfg_pair(name);
    int b = p ? hlib_ini_bool(p->value) : -1;
    if (p && b < 0) cfg_warn(p, "a switch (1/0, on/off, true/false, yes/no)", dflt);
    return fc_decide(cls, b);
}

int hlib_config_int(const char *key, int dflt) {
    HlibIniPair *p = cfg_pair(key);
    int v;
    if (!p) return dflt;
    if (hlib_ini_int(p->value, &v)) return v;
    cfg_warn(p, "a number or a switch", dflt);
    return dflt;
}

int hlib_config_str(const char *key, char *out, int cap) {
    HlibIniPair *p = cfg_pair(key);
    if (cap < 1) return 0;
    out[0] = 0;
    if (!p) return 0;
    lstrcpynA(out, p->value, cap);
    return 1;
}

/* the keys no module asked for: a typo, or a key of a module that is off */
void cfg_report_unused(void) {
    for (int i = 0; i < s_nini; i++)
        if (!s_ini[i].used) hlib_logf("config: %s is not read by this start (unknown key, or its module is off)", s_ini[i].key);
}

int cfg_unused_count(void) {
    int n = 0;
    for (int i = 0; i < s_nini; i++) n += !s_ini[i].used;
    return n;
}

/* The start mode (the edition's, hlib.h HlibStartMode): full or dormant, decided once after the ini; the link
   helper has none and takes its link start, an edition without one always the full start. */
static int s_full = 1;

static void decide_start(void) {
    const HlibEdition *ed = hlib_edition();
    if (ed->link_only) {
        s_full = 0;
        hlib_log("mode: the link helper build (live link only): no start mode, no module of the full library");
        return;
    }
    s_full = !ed->mode || !ed->mode->decide || ed->mode->decide(s_safe_mode) || s_safe_mode;
}

int hlib_dormant(void) { return !s_full; }

void config_dormant_reason(char *out, int cap) {
    const HlibStartMode *m = hlib_edition()->mode;
    if (m && m->dormant_reason)
        m->dormant_reason(out, cap);
    else
        lstrcpynA(out, "the edition's start mode decided so", cap);
}

int config_late_check(int final) {
    const HlibStartMode *m = hlib_edition()->mode;
    return m && m->late_check ? m->late_check(final) : 1;
}

void load_config(void) {
    static char text[16384];
    char path[MAX_PATH];
    int cut = 0, len = -1;
    if (hlib_user_path(path, "log/helper-library.ini")) len = hlib_read_text(path, text, sizeof(text));
    if (len >= 0) {
        s_nini = hlib_ini_parse(text, s_ini, MAX_INI, &cut);
        hlib_logf("config: %d setting(s) read from helper-library.ini", s_nini);
        if (len >= (int)sizeof(text) - 1 || cut) hlib_log("config: the ini is longer than the library reads, the rest is ignored");
    }
    s_safe_mode = hlib_feature_on("safe_mode") || (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0;
    if (s_safe_mode) hlib_log("config: safe mode, every module stays off");
    decide_start();
}

/* Settings that change the simulation: what every peer of a game must share. Each is logged at start and at every
   session's start, and rides the save. */
#define MAX_SIM 160
#define DIGEST_CAP 8192 /* the whole "k=v ..." text: the hash and the save carry every key */
#define LOG_PIECE 900   /* a log line holds about 1 KB: the text goes out in pieces */

static struct {
    const char *key;
    int value;
    volatile LONG *off;
} s_sim[MAX_SIM];
static volatile LONG s_nsim;

int hlib_config_sim(const char *key, int dflt, volatile LONG *off) {
    int v = hlib_config_int(key, dflt);
    for (int i = 0; i < s_nsim; i++)
        if (0 == lstrcmpA(s_sim[i].key, key)) {
            s_sim[i].value = v;
            if (off) s_sim[i].off = off;
            return v;
        }
    if (s_nsim < MAX_SIM) {
        s_sim[s_nsim].key = key;
        s_sim[s_nsim].value = v;
        s_sim[s_nsim].off = off;
        InterlockedIncrement(&s_nsim);
    }
    return v;
}

/* the hash is over the whole text whatever cap the caller gives */
uint32_t hlib_config_digest(char *text, int cap) {
    char all[DIGEST_CAP];
    const HlibStartMode *m = hlib_edition()->mode;
    int n = 0;
    if (m && m->digest_key) n = hlib_format(all, DIGEST_CAP, "%s=%d ", m->digest_key, m->active ? m->active() : 0);
    /* the library's build and save format: two builds with the same settings still play other rules (determinism V7) */
    n += hlib_format(all + n, DIGEST_CAP - n, "library=%08x save_format=%d", hlib_library_build(), HLIB_SAVE_FORMAT);
    for (int i = 0; i < s_nsim; i++) n += hlib_format(all + n, DIGEST_CAP - n, " %s=%d", s_sim[i].key, s_sim[i].value);
    n += hlib_format(all + n, DIGEST_CAP - n, " modules=");
    for (int i = 0, first = 1; i < s_module_count; i++)
        if (s_module_active[i]) {
            n += hlib_format(all + n, DIGEST_CAP - n, first ? "%s" : ",%s", s_modules[i]->name);
            first = 0;
        }
    static int told;
    if (n >= DIGEST_CAP - 1 && !told++) hlib_log("config: the simulation settings outgrow the digest text, the last ones are cut");
    if (cap > 0) lstrcpynA(text, all, cap);
    return hlib_fnv1a_text(all);
}

void log_digest(const char *when) {
    char text[DIGEST_CAP];
    uint32_t h = hlib_config_digest(text, sizeof(text));
    int pieces = 0;
    for (const char *p = text; *p; p += hlib_text_piece(p, LOG_PIECE)) pieces++;
    hlib_logf("config: simulation settings %s, digest %08x (every player of a game must have the same), %d key(s) in %d "
               "line(s):",
               when, h, (int)s_nsim + 3 + (hlib_edition()->mode && hlib_edition()->mode->digest_key), pieces);
    for (const char *p = text; *p;) {
        int k = hlib_text_piece(p, LOG_PIECE);
        hlib_logf("config:   %.*s", k, p);
        p += k;
    }
}

/* The settings a save was written with ride it as the chunk "Config"; a load compares them with this start's.
   Peers of one game cannot be asked, so a game of several players switches off the module of every setting
   that differs from the save (the save is the one thing they all share); a single player is only told. */
#define MAX_DIFF 16

static struct {
    char key[48];
    int saved, now;
    volatile LONG *off;
} s_diff[MAX_DIFF];
static volatile LONG s_ndiff, s_diff_pending;

static void config_save(HlibSaver *sv, int version) {
    char text[DIGEST_CAP];
    (void)version;
    hlib_config_digest(text, sizeof(text));
    hlib_saver_put(sv, text, (uint32_t)lstrlenA(text));
}

/* the value of key in a digest text ("k=v k=v ..."), 0 when absent */
static int digest_value(const char *text, const char *key, char *out, int cap) {
    int kl = lstrlenA(key);
    for (const char *p = text; *p;) {
        const char *e = p;
        while (*e && *e != ' ') e++;
        if (e - p > kl && p[kl] == '=' && 0 == memcmp(p, key, (size_t)kl)) {
            int n = (int)(e - p - kl - 1) < cap - 1 ? (int)(e - p - kl - 1) : cap - 1;
            memcpy(out, p + kl + 1, (size_t)n);
            out[n] = 0;
            return 1;
        }
        p = *e ? e + 1 : e;
    }
    return 0;
}

/* a saved digest against this start's: every key the save names that differs here is logged; a simulation
   setting among them is kept for the check on the first step */
static void config_compare(const char *saved, uint32_t len) {
    char *was = (char *)malloc(DIGEST_CAP), *now = (char *)malloc(DIGEST_CAP), key[48], a[200], b[200];
    int n = 0;
    if (!was || !now) {
        free(was);
        free(now);
        return;
    }
    if (len >= DIGEST_CAP) len = DIGEST_CAP - 1;
    memcpy(was, saved, len);
    was[len] = 0;
    hlib_config_digest(now, DIGEST_CAP);
    for (const char *p = was; *p;) {
        const char *e = p, *eq = p;
        while (*e && *e != ' ') e++;
        while (eq < e && *eq != '=') eq++;
        if (eq < e && eq - p < (int)sizeof(key)) {
            memcpy(key, p, (size_t)(eq - p));
            key[eq - p] = 0;
            digest_value(was, key, a, sizeof(a));
            if (!digest_value(now, key, b, sizeof(b))) lstrcpynA(b, "(not set)", sizeof(b));
            if (lstrcmpA(a, b) != 0) {
                hlib_logf("config: the save was written with %s=%s, this start has %s", key, a, b);
                for (int i = 0; i < s_nsim && n < MAX_DIFF; i++)
                    if (0 == lstrcmpA(s_sim[i].key, key) && s_sim[i].off) {
                        lstrcpynA(s_diff[n].key, key, sizeof(s_diff[n].key));
                        hlib_ini_int(a, &s_diff[n].saved);
                        s_diff[n].now = s_sim[i].value;
                        s_diff[n].off = s_sim[i].off;
                        n++;
                    }
            }
        }
        p = *e ? e + 1 : e;
    }
    s_ndiff = n;
    InterlockedExchange(&s_diff_pending, n > 0);
    free(was);
    free(now);
}

static void config_load(HlibSaver *sv, int version) {
    static uint32_t last;
    const void *data;
    uint32_t len, h = 2166136261u;
    if (version < 1 || !hlib_saver_get(sv, &data, &len)) return;
    for (uint32_t i = 0; i < len; i++) h = (h ^ ((const unsigned char *)data)[i]) * 16777619u;
    if (h == last && s_diff_pending) return; /* every session of one load carries the same block */
    last = h;
    config_compare((const char *)data, len);
}

static void count_humans(void *p) { *(int *)p = hlib_human_count(); }

/* on the first step after the load, when the participants are known */
void config_check_pending(void) {
    int humans = 0;
    if (!s_diff_pending || 0 == InterlockedExchange(&s_diff_pending, 0)) return;
    hlib_guarded("config check", count_humans, &humans);
    for (int i = 0; i < s_ndiff; i++) {
        if (humans > 1) {
            InterlockedExchange(s_diff[i].off, 1);
            hlib_logf("config: %s differs from the save (%d, here %d) in a game of %d players: its module is off for "
                       "this game, every player must use the save's value",
                       s_diff[i].key, s_diff[i].saved, s_diff[i].now, humans);
        } else {
            hlib_logf("config: %s differs from the save (%d, here %d): a single player's game, this start's value applies",
                       s_diff[i].key, s_diff[i].saved, s_diff[i].now);
        }
    }
}

/* persist.c registers it with the first chunk of a module */
void hlib_config_persist(void) { hlib_persist_register("Config", 1, config_save, config_load); }

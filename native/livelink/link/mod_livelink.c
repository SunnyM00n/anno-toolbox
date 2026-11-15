/* The live link (switch live_link, off): a named pipe of the current user the tool suite connects to. The pipe
   thread reads, checks and answers what needs no engine (the handshake, ping, subscriptions, what a changed file
   needs); every other request waits in one slot for the UI frame, which runs the game's own reloads and calls (a Lua
   command as a key binding runs it, the live reload's queue for models and effects), one at a time, and answers when
   they are over. The pure part is livelink_logic.c (host-tested). Unverified in game. */
#include "hlib.h"
#include "debug_logic.h"
#include "livelink.h"
#include "livelink_logic.h"
#include "livelink_module.h"
#include "livereload.h"
#include "luacmd.h"
#include "modules_logic.h"
#include "stability_logic.h"
#include "targets.h"
#include "util.h"
#include <sddl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PIPE_BASE "\\\\.\\pipe\\anno117-live-link-"
#define SESSION_DIR "live-link"
#define SESSION_FILE "live-link/session.json"
#define GAME_NAME "anno117"
#define HANDSHAKE_MS 5000
#define WRITE_MS 5000
#define OUT_MAX 256
#ifndef LL_STALL_MIN_MS /* the host test of the pipe shortens the waits */
#define LL_STALL_MIN_MS 5000
#endif

BOOLEAN NTAPI SystemFunction036(PVOID buffer, ULONG length); /* RtlGenRandom, in advapi32 */

enum { MB_EMPTY, MB_FILLED, MB_BUSY, MB_LOADING };

typedef struct Out {
    struct Out *next;
    size_t n;
    uint32_t epoch;
    int is_event;
    uint8_t data[1];
} Out;

static volatile LONG s_on, s_client, s_topics, s_epoch, s_mb, s_installed, s_edit_asked;
static char s_token[40], s_pipe_name[96], s_session_path[MAX_PATH], s_build[24];
static HANDLE s_stop, s_wake, s_pipe;
static int s_kinds, s_edit_key = 'E', s_sel_ms = 500, s_log_ms = 500;
static LLRequest s_req, s_parse; /* the slot the UI frame reads; the pipe thread's own decode target */
static uint32_t s_req_epoch;
static SRWLOCK s_olock = SRWLOCK_INIT;
static Out *s_head, *s_tail;
static int s_out_n;
static int n_log;

LLFlight llm_fl;
/* the stall watchdog: written by the UI thread, read by the pipe thread, which calls nothing in the engine */
static volatile LONG64 s_last_frame_ms;
volatile LONG64 llm_asset_since_ms;
static uint64_t s_reported_for; /* the pipe thread's: the request whose stall it reported */
static int s_stall_ms = SL_STALL_MS;
uint64_t llm_log_off;
static char s_sel_last[96];
void *llm_ui;
static int s_title_on;

/* outgoing frames: the UI thread queues, the pipe thread writes (an answer of an old connection is dropped) */

static void out_push(const char *json, int len, uint32_t epoch, int is_event) {
    Out *o;
    if (len < 0 || !s_client) return;
    o = (Out *)malloc(sizeof(Out) + (size_t)len + 4);
    if (!o) return;
    o->n = ll_frame_wrap(json, (size_t)len, o->data, (size_t)len + 4);
    if (!o->n) {
        free(o);
        return;
    }
    o->next = NULL, o->epoch = epoch, o->is_event = is_event;
    AcquireSRWLockExclusive(&s_olock);
    if (s_out_n >= OUT_MAX && is_event) {
        free(o); /* events are dropped when the peer does not read; an answer never is */
    } else {
        if (s_tail) s_tail->next = o;
        else s_head = o;
        s_tail = o, s_out_n++;
    }
    ReleaseSRWLockExclusive(&s_olock);
    SetEvent(s_wake);
}

static Out *out_pop(void) {
    Out *o;
    AcquireSRWLockExclusive(&s_olock);
    o = s_head;
    if (o && !(s_head = o->next)) s_tail = NULL;
    if (o) s_out_n--;
    ReleaseSRWLockExclusive(&s_olock);
    return o;
}

static void out_clear(void) {
    Out *o;
    while ((o = out_pop()) != NULL) free(o);
}

/* the pipe thread */

typedef struct {
    HANDLE pipe;
    LLInbuf in;
    LLRate rate;
    int shaken;
    char challenge[40];
    uint32_t epoch;
    ULONGLONG deadline;
} Conn;

static int write_frame(HANDLE pipe, const uint8_t *buf, size_t n) {
    OVERLAPPED ov = {0};
    DWORD done = 0;
    int ok;
    ov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
    if (!ov.hEvent) return 0;
    if (!WriteFile(pipe, buf, (DWORD)n, NULL, &ov)) {
        if (GetLastError() != ERROR_IO_PENDING) { /* nothing in flight */
            CloseHandle(ov.hEvent);
            return 0;
        }
        if (WaitForSingleObject(ov.hEvent, WRITE_MS) != WAIT_OBJECT_0) {
            CancelIoEx(pipe, &ov);
            GetOverlappedResult(pipe, &ov, &done, TRUE); /* the write is over before ov and buf go */
            CloseHandle(ov.hEvent);
            return 0;
        }
    }
    ok = GetOverlappedResult(pipe, &ov, &done, TRUE) && done == n;
    CloseHandle(ov.hEvent);
    return ok;
}

static int send_json(Conn *c, const char *json, int len) {
    static uint8_t wire[LLM_JSON_MAX + 8];
    size_t n = len < 0 ? 0 : ll_frame_wrap(json, (size_t)len, wire, sizeof wire);
    return n && write_frame(c->pipe, wire, n);
}

static int reply(Conn *c, const LLRequest *r, const char *status, const char *message, const char *data) {
    static char json[LLM_JSON_MAX];
    hlib_log_n(&n_log, 400, "live link: %s (request %u): %s%s%s", r->type, r->id, status, *message ? ", " : "", message);
    return send_json(c, json, ll_answer_json(json, sizeof json, r->id, status, message, data));
}

static int random_hex(char *out, int bytes) {
    uint8_t b[32];
    if (bytes > 32 || !SystemFunction036(b, (ULONG)bytes)) return 0;
    ll_hex(b, (size_t)bytes, out);
    return 1;
}

static int hello(Conn *c, const LLRequest *r) {
    static char data[LLM_JSON_MAX];
    static char ids[64][64], folders[64][260];
    char proof[65];
    int n;
    if (!ll_request_is(r, "hello") || r->protocol != LL_PROTOCOL || !ll_proof_ok(s_token, "client", c->challenge, r->proof)) {
        reply(c, r, "refused", "the handshake failed", NULL);
        return 0;
    }
    ll_proof(s_token, "game", r->nonce, proof);
    n = hlib_live_reload_mods(ids, folders, 64);
    if (ll_welcome_json(data, sizeof data, GAME_NAME, s_build, HLIB_VERSION, s_kinds, ids, folders, n, proof) < 0) return 0;
    c->shaken = 1;
    InterlockedExchange(&s_topics, 0);
    InterlockedExchange(&s_client, 1);
    return reply(c, r, "ok", "", data);
}

static int subscribe(Conn *c, const LLRequest *r, int on) {
    LONG bits = 0;
    for (int i = 0; i < r->nnames; i++) bits |= ll_topic_bit(r->names[i]);
    if (on) InterlockedOr(&s_topics, bits);
    else InterlockedAnd(&s_topics, ~bits);
    return reply(c, r, "ok", "", NULL);
}

static int files_changed(Conn *c, const LLRequest *r) {
    static char data[LLM_JSON_MAX], ids[64][64], folders[64][260];
    int known = hlib_live_reload_mods(ids, folders, 64), found = !r->has_mod || !known;
    for (int i = 0; i < known && !found; i++) found = !_stricmp(ids[i], r->mod);
    if (!found) return reply(c, r, "refused", "not the id of an enabled mod", NULL);
    if (ll_needs_json(r, data, sizeof data) < 0) return reply(c, r, "error", "too many files for one answer", NULL);
    return reply(c, r, "ok", "", data);
}

/* one decoded request; 0 ends the connection */
static int handle(Conn *c, const char *json, size_t len) {
    char err[160];
    int rc = ll_request_parse(json, len, &s_parse, err, sizeof err);
    const LLRequest *r = &s_parse;
    if (!c->shaken) {
        if (rc != 1) {
            hlib_log_n(&n_log, 400, "live link: the first frame was not a valid hello: %s", err);
            return 0;
        }
        return hello(c, r);
    }
    if (!ll_rate_take(&c->rate, GetTickCount64())) return reply(c, r, "busy", "too many requests a second", NULL);
    if (rc < 0) return reply(c, r, "unknown", err, NULL);
    if (rc == 0) return reply(c, r, "error", err, NULL);
    if (ll_request_is(r, "ping")) return reply(c, r, "ok", "", "{\"pong\":true}");
    if (ll_request_is(r, "bye")) return 0;
    if (ll_request_is(r, "hello")) return reply(c, r, "refused", "already connected", NULL);
    if (ll_request_is(r, "subscribe")) return subscribe(c, r, 1);
    if (ll_request_is(r, "unsubscribe")) return subscribe(c, r, 0);
    if (ll_request_is(r, "files.changed")) return files_changed(c, r);
    if (InterlockedCompareExchange(&s_mb, MB_LOADING, MB_EMPTY) != MB_EMPTY)
        return reply(c, r, "busy", "another request is running", NULL);
    memcpy(&s_req, r, sizeof s_req);
    s_req_epoch = c->epoch;
    InterlockedExchange(&s_mb, MB_FILLED);
    return 1;
}

static int take_frames(Conn *c) {
    static char frame[LL_REQ_MAX + 8];
    size_t len;
    int rc;
    while ((rc = ll_inbuf_next(&c->in, frame, sizeof frame, &len)) == 1)
        if (!handle(c, frame, len)) return 0;
    return rc == 0;
}

static int flush_out(Conn *c) {
    Out *o;
    while ((o = out_pop()) != NULL) {
        int ok = o->epoch == c->epoch ? write_frame(c->pipe, o->data, o->n) : 1;
        free(o);
        if (!ok) return 0;
    }
    return 1;
}

/* The watchdog (stability guard 5): the UI thread writes the time of its last frame and of the asset request in flight;
   when no frame came for 30 s after the request, the client is told once, so it says so instead of waiting in silence. */
static void check_stall(uint32_t epoch) {
    char data[512], json[768];
    ULONGLONG now = GetTickCount64();
    uint64_t frame = (uint64_t)s_last_frame_ms;
    if (!sl_stalled(frame, (uint64_t)llm_asset_since_ms, now, (uint64_t)s_stall_ms, &s_reported_for)) return;
    if (sl_stalled_json(data, sizeof data, now - (frame > (uint64_t)llm_asset_since_ms ? frame : (uint64_t)llm_asset_since_ms)) < 0) return;
    hlib_log("live link: no UI frame for a long while after an asset request: the game may hang in the asset load");
    out_push(json, ll_event_json(json, sizeof json, "stalled", data), epoch, 1);
}

static void serve(void *pipe_handle) {
    HANDLE pipe = (HANDLE)pipe_handle;
    Conn c = {0};
    static uint8_t chunk[8192];
    OVERLAPPED rov = {0};
    DWORD got = 0;
    int reading = 0, alive;
    char greeting[160];
    c.pipe = pipe, c.epoch = (uint32_t)InterlockedIncrement(&s_epoch), c.deadline = GetTickCount64() + HANDSHAKE_MS;
    ll_inbuf_init(&c.in), ll_rate_init(&c.rate, GetTickCount64());
    alive = random_hex(c.challenge, 16);
    rov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
    out_clear();
    alive = alive && send_json(&c, greeting, ll_greeting_json(greeting, sizeof greeting, c.challenge));
    while (alive) {
        HANDLE h[3];
        DWORD w;
        h[0] = rov.hEvent, h[1] = s_wake, h[2] = s_stop;
        if (!reading) {
            ResetEvent(rov.hEvent);
            if (!ReadFile(pipe, chunk, sizeof chunk, NULL, &rov) && GetLastError() != ERROR_IO_PENDING) break;
            reading = 1;
        }
        w = WaitForMultipleObjects(3, h, FALSE, 200);
        if (w == WAIT_OBJECT_0) {
            reading = 0;
            if (!GetOverlappedResult(pipe, &rov, &got, FALSE) || !got) break;
            if (!ll_inbuf_feed(&c.in, chunk, got) || !take_frames(&c)) break;
        } else if (w == WAIT_OBJECT_0 + 2) {
            break;
        }
        if (!flush_out(&c)) break;
        if (c.shaken) check_stall(c.epoch);
        if (!c.shaken && GetTickCount64() > c.deadline) {
            hlib_log("live link: a client did not finish the handshake in time");
            break;
        }
    }
    if (reading) {
        CancelIoEx(pipe, &rov);
        GetOverlappedResult(pipe, &rov, &got, TRUE);
    }
    CloseHandle(rov.hEvent);
    if (c.shaken) hlib_log("live link: the client left");
    InterlockedExchange(&s_client, 0);
    InterlockedExchange(&s_topics, 0);
    ll_inbuf_free(&c.in);
    out_clear();
}

static DWORD WINAPI pipe_main(void *unused) {
    (void)unused;
    for (;;) {
        OVERLAPPED ov = {0};
        BOOL connected;
        DWORD err;
        ov.hEvent = CreateEventA(NULL, TRUE, FALSE, NULL);
        connected = ConnectNamedPipe(s_pipe, &ov);
        err = GetLastError();
        if (!connected && err == ERROR_IO_PENDING) {
            HANDLE h[2];
            h[0] = ov.hEvent, h[1] = s_stop;
            if (WaitForMultipleObjects(2, h, FALSE, INFINITE) != WAIT_OBJECT_0) {
                DWORD unused;
                CancelIoEx(s_pipe, &ov);
                GetOverlappedResult(s_pipe, &ov, &unused, TRUE); /* the connect is over before ov goes */
                CloseHandle(ov.hEvent);
                return 0;
            }
        } else if (!connected && err != ERROR_PIPE_CONNECTED) {
            CloseHandle(ov.hEvent);
            Sleep(1000);
            continue;
        }
        CloseHandle(ov.hEvent);
        hlib_guarded("live link pipe", serve, s_pipe);
        DisconnectNamedPipe(s_pipe);
        if (WaitForSingleObject(s_stop, 0) == WAIT_OBJECT_0) return 0;
    }
}

/* the UI frame: the requests, the events, the title */

static void finish(void) {
    InterlockedExchange64(&llm_asset_since_ms, 0);
    memset(&llm_fl, 0, sizeof llm_fl);
    InterlockedExchange(&s_mb, MB_EMPTY);
}

void llm_answer(const char *status, const char *message, const char *data) {
    static char json[LLM_JSON_MAX], msg[640], dat[1400];
    if (llm_fl.paused_from) { /* an asset request the link paused the game for: the speed goes back first */
        llr_pause_release(status, message, data, msg, sizeof msg, dat, sizeof dat);
        message = msg, data = dat;
    }
    hlib_log_n(&n_log, 400, "live link: %s (request %u): %s%s%s", llm_fl.type, llm_fl.id, status, *message ? ", " : "", message);
    if (llm_fl.epoch == (uint32_t)s_epoch)
        out_push(json, ll_answer_json(json, sizeof json, llm_fl.id, status, message, data), llm_fl.epoch, 0);
    finish();
}

static void post_event(const char *event, const char *data) {
    static char json[LLM_JSON_MAX];
    out_push(json, ll_event_json(json, sizeof json, event, data), (uint32_t)s_epoch, 1);
}

/* 1 when the request was answered here: its kind switched off, the engine busy, or refused by the gate */
static int request_gated(const LLRequest *r) {
    const char *why = "";
    int gate, debug, reload;
    if (ll_kind_of_request(r->type) == LL_KIND_DEBUG && !(s_kinds & LL_KIND_DEBUG)) {
        llm_answer("unavailable", "the debug commands are off: set live_link_commands=1 in the helper library's settings", NULL);
        return 1;
    }
    debug = ll_request_is(r, "debug.run") || ll_request_is(r, "debug.cheat") || ll_request_is(r, "modules.set");
    reload = !strncmp(r->type, "reload.", 7);
    /* every reload and every debug change: not while a session loads, a save is written, the session changes or an
       asset reload runs; first, as the human count is not to be trusted while an asset reload holds */
    if ((debug || reload) && (why = hlib_live_link_busy(ll_request_is(r, "reload.assets") || ll_request_is(r, "reload.texts")))) {
        llm_answer("busy", why, NULL);
        return 1;
    }
    gate = ll_gate(r, hlib_human_count() > 1, s_kinds, &why);
    if (gate) {
        llm_answer(gate == 1 ? "refused" : "unavailable", why, NULL);
        return 1;
    }
    return 0;
}

/* select and jump: one Lua command each, or the asset's object looked up */
static void start_select(const LLRequest *r) {
    char cmd[96];
    if (r->has_object && ll_lua_select(cmd, sizeof cmd, r->object)) llq_run_simple(cmd, "");
    else if (r->has_guid) llq_select_guid(r->guid);
    else llm_answer("refused", "select an object id, or an asset guid", NULL);
}

static void start_jump(const LLRequest *r) {
    char cmd[96];
    if (r->npos && !r->has_session) llm_answer("refused", "a position is inside a session: give the session's guid too", NULL);
    else if (r->has_session ? ll_lua_jump_session(cmd, sizeof cmd, r->session, r->npos ? r->pos : NULL)
                            : r->has_object && ll_lua_jump_object(cmd, sizeof cmd, r->object))
        llq_run_simple(cmd, "");
    else llm_answer("refused", "jump to an object id, to a session by its guid, or to a position (three numbers) inside one", NULL);
}

static void start_request(ULONGLONG now) {
    const LLRequest *r = &s_req;
    char cmd[96];
    memset(&llm_fl, 0, sizeof llm_fl);
    llm_fl.id = r->id, llm_fl.epoch = s_req_epoch, llm_fl.started = now;
    snprintf(llm_fl.type, sizeof llm_fl.type, "%s", r->type);
    if (s_req_epoch != (uint32_t)s_epoch || !s_client) {
        finish(); /* the client that asked is gone */
        return;
    }
    if (request_gated(r)) return;
    if (ll_request_is(r, "reload.assets")) llr_start_pass(now, r->has_mode && !strcmp(r->mode, "rebuild"), "assets");
    else if (ll_request_is(r, "reload.texts")) llr_start_pass(now, 0, "texts");
    else if (ll_request_is(r, "reload.ambient") && ll_lua_reload_ambient(cmd, sizeof cmd))
        llq_run_simple(cmd, "asked: the ambient settings are read again; the game gives no confirmation");
    else if (ll_request_is(r, "reload.models")) llr_start_files(r, now, "models");
    else if (ll_request_is(r, "reload.effects")) llr_start_files(r, now, "effects");
    else if (ll_request_is(r, "ui.dump")) llq_start_dump(r);
    else if (ll_request_is(r, "reload.scenes")) llr_start_scenes(r, now);
    else if (ll_request_is(r, "reload.banks")) llr_start_banks(r, now);
    else if (ll_request_is(r, "reload.session")) llr_start_session(now);
    else if (ll_request_is(r, "select")) start_select(r);
    else if (ll_request_is(r, "jump")) start_jump(r);
    else if (ll_request_is(r, "read")) llq_start_read(r);
    else if (ll_request_is(r, "debug.list")) llq_start_debug_list();
    else if (ll_request_is(r, "debug.run")) llq_start_debug_run(r);
    else if (ll_request_is(r, "debug.cheat")) llq_start_debug_cheat(r);
    else if (ll_request_is(r, "modules.list")) llq_start_modules_list();
    else if (ll_request_is(r, "modules.set")) llq_start_modules_set(r);
    else llm_answer("unavailable", "not served by this game build", NULL);
}

/* the selection as the game holds it: "id,guid,session", or "" */
static int current_selection(char *text, int cap) {
    char cmd[512];
    return ll_lua_selection(cmd, sizeof cmd) && hlib_lua_run(cmd, text, cap);
}

static void poll_selection(ULONGLONG now) {
    static ULONGLONG next;
    char text[96], json[256], session[40];
    int64_t object, guid;
    if (!(s_topics & LL_TOPIC_SELECTION) || now < next || llm_fl.kind) return;
    next = now + (ULONGLONG)s_sel_ms;
    if (!current_selection(text, sizeof text) || !strcmp(text, s_sel_last)) return;
    snprintf(s_sel_last, sizeof s_sel_last, "%s", text);
    if (ll_selection_parse(text, &object, &guid, session, sizeof session)) ll_selection_json(json, sizeof json, object, guid, session);
    else ll_selection_json(json, sizeof json, -1, 0, "");
    post_event("selection", json);
}

/* the selected object as an edit request for the suite (Ctrl+Shift+key, or the script table's EditSelected) */
static void send_edit_request(void) {
    char text[96], data[200], session[40];
    int64_t object, guid;
    if (hlib_human_count() > 1) hlib_log("live link: edit this object: not in a multiplayer game");
    else if (!current_selection(text, sizeof text) || !ll_selection_parse(text, &object, &guid, session, sizeof session))
        hlib_log("live link: edit this object: nothing is selected (or the game gave no text)");
    else {
        snprintf(data, sizeof data, "{\"game\":\"" GAME_NAME "\",\"object\":%lld,\"guid\":%lld,\"session\":\"%s\"}", (long long)object,
                 (long long)guid, session);
        hlib_logf("live link: edit this object: object %lld, guid %lld", (long long)object, (long long)guid);
        post_event("edit.request", data);
    }
}

static void edit_key(void) {
    static SHORT was;
    DWORD pid = 0;
    SHORT down;
    if (InterlockedExchange(&s_edit_asked, 0)) send_edit_request(); /* the companion mod's key binding asked */
    if (!s_edit_key) return;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    down = pid == GetCurrentProcessId() && (GetAsyncKeyState(VK_CONTROL) & 0x8000) && (GetAsyncKeyState(VK_SHIFT) & 0x8000) &&
           (GetAsyncKeyState(s_edit_key) & 0x8000);
    if (down && !was) send_edit_request();
    was = down;
}

/* the loader's log: a pass the player started by hand, and the new lines for a client that follows them */
static void poll_log(ULONGLONG now) {
    static ULONGLONG next;
    static char buf[256 * 1024], json[LLM_JSON_MAX];
    uint64_t size;
    size_t n;
    if (!(s_topics & (LL_TOPIC_RELOAD | LL_TOPIC_LOG)) || now < next || llm_fl.kind == FL_PASS || llm_fl.kind == FL_CONFIRM) return;
    next = now + (ULONGLONG)s_log_ms;
    n = llr_log_from(llm_log_off, buf, sizeof buf, &size);
    llm_log_off = (size < llm_log_off ? 0 : llm_log_off) + n;
    if (!n) return;
    if (s_topics & LL_TOPIC_RELOAD) {
        LLPass p;
        ll_pass_scan(buf, n, &p);
        if (p.completed && ll_pass_json(json, sizeof json, &p) > 0) post_event("reload", json);
    }
    if (s_topics & LL_TOPIC_LOG) {
        size_t used = 0, at = 0;
        int lines = 0;
        used += (size_t)snprintf(json, sizeof json, "{\"lines\":[");
        while (at < n && lines < 20 && used + 1200 < sizeof json) {
            char line[512], esc[1100];
            size_t k = 0;
            while (at + k < n && buf[at + k] != '\n' && k < sizeof line - 1) k++;
            memcpy(line, buf + at, k), line[k] = 0;
            at += k + (at + k < n && buf[at + k] == '\n');
            if (k && line[k - 1] == '\r') line[k - 1] = 0;
            if (ll_json_escape(line, esc, sizeof esc) < 0) continue;
            used += (size_t)snprintf(json + used, sizeof json - used, "%s\"%s\"", lines ? "," : "", esc);
            lines++;
        }
        snprintf(json + used, sizeof json - used, "]}");
        if (lines) post_event("log", json);
    }
}

/* The game window's title says a client is connected. The text is read without a message (InternalGetWindowText: a
   window message to another thread can stall this frame); only the change is sent, and a failed try waits a second. */
#define TITLE_RETRY_MS 1000
typedef struct {
    DWORD pid;
    HWND found;
} FindWin;

static BOOL CALLBACK find_window(HWND h, LPARAM p) {
    FindWin *f = (FindWin *)p;
    DWORD pid = 0;
    WCHAR one[2];
    GetWindowThreadProcessId(h, &pid);
    if (pid == f->pid && IsWindowVisible(h) && !GetWindow(h, GW_OWNER) && InternalGetWindowText(h, one, 2) > 0) {
        f->found = h;
        return FALSE;
    }
    return TRUE;
}

static void title_sync(ULONGLONG at) {
    static ULONGLONG next;
    FindWin f = {GetCurrentProcessId(), NULL};
    WCHAR cur[512];
    uint16_t out[540];
    DWORD_PTR sent = 0;
    int want = s_client != 0, n, now;
    if (want == s_title_on || at < next) return;
    next = at + TITLE_RETRY_MS;
    EnumWindows(find_window, (LPARAM)&f);
    if (!f.found || (n = InternalGetWindowText(f.found, cur, 512)) <= 0) return;
    now = ll_title_mark((const uint16_t *)cur, n, out, 540, want);
    if (now < 0 || !SendMessageTimeoutW(f.found, WM_SETTEXT, 0, (LPARAM)out, SMTO_ABORTIFHUNG, 200, &sent)) return;
    s_title_on = want;
}

/* the request's step, guarded: a fault answers the request and frees the slot (and gives the speed back) instead of
   leaving the link busy for good */
static void request_step(void *now) {
    if (s_mb == MB_FILLED) {
        InterlockedExchange(&s_mb, MB_BUSY);
        start_request(*(ULONGLONG *)now);
    } else if (llm_fl.kind) {
        llr_progress(*(ULONGLONG *)now);
    } else {
        llr_engine_watch(*(ULONGLONG *)now); /* an asset reload the link did not ask for */
    }
}

static void answer_fault(void *unused) {
    (void)unused;
    llm_answer("error", "the helper library caught a fault in this request (see its log); nothing more of it runs", NULL);
}

static void frame_listener(void *ui) {
    ULONGLONG now = GetTickCount64();
    if (!s_on) return; /* placed before the pipe: an install that failed later leaves it idle */
    llm_ui = ui;
    InterlockedExchange64(&s_last_frame_ms, (LONG64)now);
    title_sync(now);
    if (!hlib_guarded("live link request", request_step, &now) && s_mb == MB_BUSY &&
        !hlib_guarded("live link fault answer", answer_fault, NULL))
        finish(); /* the answer faulted too: the slot is freed at least */
    if (!s_client) return;
    edit_key();
    poll_selection(now);
    poll_log(now);
}

/* install: the pipe, its session file, the thread */

static PSECURITY_DESCRIPTOR user_only(void) {
    HANDLE tok = NULL;
    DWORD n = 0;
    TOKEN_USER *u;
    char *sid = NULL, sddl[256];
    PSECURITY_DESCRIPTOR sd = NULL;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &tok)) return NULL;
    GetTokenInformation(tok, TokenUser, NULL, 0, &n);
    u = (TOKEN_USER *)malloc(n ? n : 1);
    if (u && GetTokenInformation(tok, TokenUser, u, n, &n) && ConvertSidToStringSidA(u->User.Sid, &sid)) {
        snprintf(sddl, sizeof sddl, "D:P(A;;GA;;;%s)", sid); /* the one account that runs the game, nobody else */
        ConvertStringSecurityDescriptorToSecurityDescriptorA(sddl, SDDL_REVISION_1, &sd, NULL);
        LocalFree(sid);
    }
    free(u);
    CloseHandle(tok);
    return sd;
}

static int open_pipe(void) {
    SECURITY_ATTRIBUTES sa = {sizeof sa, NULL, FALSE};
    sa.lpSecurityDescriptor = user_only();
    if (!sa.lpSecurityDescriptor) return 0;
    s_pipe = CreateNamedPipeA(s_pipe_name, PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE,
                              PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 65536, 65536, 0, &sa);
    LocalFree(sa.lpSecurityDescriptor);
    return s_pipe != INVALID_HANDLE_VALUE;
}

static int write_session(void) {
    char dir[MAX_PATH], json[1024];
    HANDLE h;
    DWORD put = 0;
    int n;
    if (!hlib_user_path(dir, SESSION_DIR) || !hlib_user_path(s_session_path, SESSION_FILE)) return 0;
    CreateDirectoryA(dir, NULL);
    n = ll_session_json(json, sizeof json, s_pipe_name, s_token, GAME_NAME, s_build, HLIB_VERSION, GetCurrentProcessId());
    if (n < 0) return 0;
    h = CreateFileA(s_session_path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    if (!WriteFile(h, json, (DWORD)n, &put, NULL)) put = 0;
    CloseHandle(h);
    return put == (DWORD)n;
}

/* an install that failed after the pipe: nothing of it stays, no token on disk */
static void install_undo(void) {
    if (s_session_path[0]) DeleteFileA(s_session_path);
    s_session_path[0] = 0;
    if (s_pipe && s_pipe != INVALID_HANDLE_VALUE) CloseHandle(s_pipe);
    if (s_stop) CloseHandle(s_stop);
    if (s_wake) CloseHandle(s_wake);
    s_pipe = s_stop = s_wake = NULL;
    SecureZeroMemory(s_token, sizeof s_token);
}

void hlib_live_link_shutdown(void) {
    if (s_session_path[0]) DeleteFileA(s_session_path);
    s_session_path[0] = 0;
    if (s_stop) SetEvent(s_stop);
}

static const int g_need[] = {T_UI_FRAME,         T_SCRIPT_RUN_COMMAND, T_SCRIPT_RESULT_FREE,
                             T_RECRUIT_FREE_STR, T_DATA_SCRIPT_MANAGER, T_DATA_LOCAL_PLAYER};

const int *hlib_live_link_targets(int *n) {
    *n = (int)(sizeof g_need / sizeof g_need[0]);
    return g_need;
}

static const int g_scene_need[] = {T_UI_STATE_ID, T_DATA_UI_CONTROLLER, T_UI_NEW};

const int *hlib_live_link_scene_targets(int *n) {
    *n = (int)(sizeof g_scene_need / sizeof g_scene_need[0]);
    return g_scene_need;
}

static const int g_bank_need[] = {T_AK_LOAD_BANK, T_AK_UNLOAD_BANK, T_AK_PREPARE_BANK, T_DATA_SOUND_ENGINE};

const int *hlib_live_link_bank_targets(int *n) {
    *n = (int)(sizeof g_bank_need / sizeof g_bank_need[0]);
    return g_bank_need;
}

/* the guards' reads (livelink_reload.c): a session switch, a scheduled scenario action, the pause; a start that
   resolves only its own targets (dormant, the link helper build) must resolve these too, else the guards are off */
static const int g_guard_need[] = {T_DATA_SES_TRANSITIONS, T_DATA_ASSETDATA_MGR, T_DATA_PAUSE_MANAGER, T_DATA_SESSION_TABLE};

const int *hlib_live_link_guard_targets(int *n) {
    *n = (int)(sizeof g_guard_need / sizeof g_guard_need[0]);
    return g_guard_need;
}

int hlib_live_link_connected(void) { return s_on && s_client != 0; }

int hlib_live_link_edit_selected(void) {
    if (!hlib_live_link_connected()) return 0;
    InterlockedExchange(&s_edit_asked, 1);
    return 1;
}

int hlib_live_link_wanted(void) { return hlib_feature_on("live_link"); }

static int ll_resolve(void) {
    for (size_t i = 0; i < sizeof g_need / sizeof g_need[0]; i++)
        if (!hlib_target_ok(g_need[i])) return 0;
    return 1;
}

int hlib_live_link_install(void) {
    if (InterlockedCompareExchange(&s_installed, 1, 0)) return (int)s_on;
    if (!ll_resolve()) {
        hlib_log("live link: a target of this game build was not found, no link");
        return 0;
    }
    s_edit_key = hlib_config_int("live_link_edit_key", 'E');
    s_sel_ms = hlib_config_int("live_link_selection_ms", 500);
    s_log_ms = hlib_config_int("live_link_log_ms", 500);
    if (s_sel_ms < 100) s_sel_ms = 100;
    if (s_log_ms < 100) s_log_ms = 100;
    s_stall_ms = hlib_config_int("live_link_stall_s", SL_STALL_MS / 1000) * 1000;
    if (s_stall_ms < LL_STALL_MIN_MS) s_stall_ms = LL_STALL_MIN_MS;
    /* the UI frame first (its listener idles until s_on), then the pipe and its file, undone when they fail; the reload
       and the UI dump only after them, the thread last (a client's welcome lists the kinds) */
    if (!hlib_on_ui_frame("live link", frame_listener)) {
        hlib_log("live link: the UI's frame was not reached, no link");
        return 0;
    }
    snprintf(s_build, sizeof s_build, "%llu", (unsigned long long)T_BUILD_FILESIZE);
    snprintf(s_pipe_name, sizeof s_pipe_name, PIPE_BASE "%lu", GetCurrentProcessId());
    s_stop = CreateEventA(NULL, TRUE, FALSE, NULL);
    s_wake = CreateEventA(NULL, FALSE, FALSE, NULL);
    if (!s_stop || !s_wake || !random_hex(s_token, 16) || !open_pipe() || !write_session()) {
        hlib_log("live link: the pipe or its session file could not be made, no link");
        install_undo();
        return 0;
    }
    llr_glog_sync();
    if (hlib_config_int("live_link_models", 1) && hlib_live_reload_install()) s_kinds |= LL_KIND_MODELS | LL_KIND_EFFECTS;
    if (hlib_target_ok(T_UI_STATE_ID) && hlib_target_ok(T_DATA_UI_CONTROLLER) && hlib_target_ok(T_UI_NEW))
        s_kinds |= LL_KIND_SCENES;
    if (hlib_config_int("live_link_ui", 1) && hlib_ui_dump_install_quiet()) s_kinds |= LL_KIND_UIDUMP;
    if (hlib_target_ok(T_AK_LOAD_BANK) && hlib_target_ok(T_AK_UNLOAD_BANK) && hlib_target_ok(T_AK_PREPARE_BANK) &&
        hlib_target_ok(T_DATA_SOUND_ENGINE))
        s_kinds |= LL_KIND_BANKS;
    if (hlib_lua_run_ok() && hlib_config_int("live_link_commands", 0)) s_kinds |= LL_KIND_DEBUG;
    if (hlib_lua_run_ok())
        s_kinds |= LL_KIND_ASSETS | LL_KIND_TEXTS | LL_KIND_AMBIENT | LL_KIND_SELECT | LL_KIND_JUMP | LL_KIND_READ |
                   LL_KIND_SESSION;
    llm_log_off = llr_log_size();
    {
        HANDLE thread = CreateThread(NULL, 0, pipe_main, NULL, 0, NULL);
        if (!thread) {
            hlib_log("live link: its thread could not be started, no link");
            install_undo();
            return 0;
        }
        CloseHandle(thread);
    }
    InterlockedExchange(&s_on, 1);
    if (!hlib_target_ok(T_DATA_SES_TRANSITIONS) || !hlib_target_ok(T_DATA_PAUSE_MANAGER) || !hlib_target_ok(T_DATA_SESSION_TABLE))
        hlib_logf("live link: guards not located on this build: %s%s%s", hlib_target_ok(T_DATA_SES_TRANSITIONS) ? "" : "session switch ",
                   hlib_target_ok(T_DATA_PAUSE_MANAGER) ? "" : "pause ", hlib_target_ok(T_DATA_SESSION_TABLE) ? "" : "session (no pause) ");
    hlib_logf("live link: ready on %s (reloads: %s%s%s%s%s%s%s; one client of this user, the token in %s); unverified in game", s_pipe_name,
               s_kinds & LL_KIND_ASSETS ? "assets texts ambient " : "", s_kinds & LL_KIND_SCENES ? "scenes " : "",
               s_kinds & LL_KIND_MODELS ? "models effects " : "",
               s_kinds & LL_KIND_BANKS ? "banks " : "", s_kinds & LL_KIND_SESSION ? "session " : "",
               s_kinds & LL_KIND_SELECT ? "select jump read " : "", s_kinds & LL_KIND_DEBUG ? "debug " : "", SESSION_FILE);
    return 1;
}

static void ll_install(void) { hlib_live_link_install(); }

static const HlibModule g_mod = {"live_link", ll_resolve, ll_install, 0};
void hlib_register_live_link(void) {
    if (hlib_edition()->link_only)
        hlib_register_module(&g_mod, HLIB_GAME); /* the link helper exists for the link: on unless live_link=0 */
    else
        hlib_register_module(&g_mod, HLIB_DEV);
}

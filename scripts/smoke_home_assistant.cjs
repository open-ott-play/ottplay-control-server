#!/usr/bin/env node
// Real controller + fake HA WebSocket + production player + CLI, entirely local.
// Prerequisite: npm ci && npm run build in the supplied player repository.
// Run: node scripts/smoke_home_assistant.cjs /path/to/ottplay-foss
const assert = require("node:assert/strict");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { spawn, execFile: execFileCallback } = require("node:child_process");
const { promisify } = require("node:util");
const execFile = promisify(execFileCallback);

const root = path.resolve(__dirname, "..");
const player = process.argv[2] && path.resolve(process.argv[2]);
if (!player || process.argv.length !== 3) {
    console.error("Usage: node scripts/smoke_home_assistant.cjs /path/to/built/ottplay-foss");
    process.exit(2);
}
for (const file of ["dist/index.html", "dist/player.js", "tests/helpers/device-browser-server.cjs"]) {
    if (!fs.existsSync(path.join(player, file))) {
        console.error("Missing player build/helper; run npm ci && npm run build in the supplied player repository.");
        process.exit(2);
    }
}
let chromium;
try {
    ({ chromium } = require(require.resolve("playwright", { paths: [player] })));
} catch (_) {
    console.error("Playwright is unavailable in the supplied player repository; run npm ci and npx playwright install chromium there.");
    process.exit(2);
}

const temp = fs.mkdtempSync(path.join(os.tmpdir(), "ott-ha-smoke-"));
const children = [];
let browser;
let cleanupTask;
let timeout;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const adminToken = "a".repeat(32);
const deviceToken = "b".repeat(32);
const haToken = "local_fixture_ha_token_not_a_real_credential_123456789";
const text = '<img src=x onerror="window.__haInjected=true"> Door opened';

const fixtureSource = String.raw`package main

import (
    "context"
    "encoding/json"
    "flag"
    "net/http"
    "os"
    "strings"
    "sync"
    "time"

    "github.com/coder/websocket"
    "github.com/coder/websocket/wsjson"
)

func main() {
    address := flag.String("listen", "", "loopback listen address")
    tokenPath := flag.String("token-file", "", "fixture credential")
    flag.Parse()
    raw, err := os.ReadFile(*tokenPath)
    if err != nil { os.Exit(1) }
    token := strings.TrimSpace(string(raw))
    var mu sync.Mutex
    clients := map[*websocket.Conn]float64{}
    mux := http.NewServeMux()
    mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
        mu.Lock(); count := len(clients); mu.Unlock()
        w.Header().Set("Content-Type", "application/json")
        _ = json.NewEncoder(w).Encode(map[string]any{"clients": count})
    })
    mux.HandleFunc("/api/websocket", func(w http.ResponseWriter, r *http.Request) {
        c, err := websocket.Accept(w, r, nil)
        if err != nil { return }
        defer c.CloseNow()
        c.SetReadLimit(16384)
        ctx, cancel := context.WithCancel(context.Background())
        defer cancel()
        defer func() { mu.Lock(); delete(clients, c); mu.Unlock() }()
        handshake, finish := context.WithTimeout(ctx, 3*time.Second)
        defer finish()
        if wsjson.Write(handshake, c, map[string]any{"type":"auth_required"}) != nil { return }
        var auth map[string]any
        if wsjson.Read(handshake, c, &auth) != nil || auth["type"] != "auth" || auth["access_token"] != token {
            _ = wsjson.Write(handshake, c, map[string]any{"type":"auth_invalid"})
            return
        }
        if wsjson.Write(handshake, c, map[string]any{"type":"auth_ok"}) != nil { return }
        var sub map[string]any
        if wsjson.Read(handshake, c, &sub) != nil || sub["type"] != "subscribe_events" || sub["event_type"] != "ottplay_notification" { return }
        id, ok := sub["id"].(float64)
        if !ok || id < 1 { return }
        if wsjson.Write(handshake, c, map[string]any{"id":id,"type":"result","success":true}) != nil { return }
        mu.Lock(); clients[c] = id; mu.Unlock()
        for {
            var command map[string]any
            if wsjson.Read(ctx, c, &command) != nil { return }
            if command["type"] == "ping" {
                writeCtx, done := context.WithTimeout(ctx, 2*time.Second)
                err := wsjson.Write(writeCtx, c, map[string]any{"id":command["id"],"type":"pong"})
                done()
                if err != nil { return }
            }
        }
    })
    mux.HandleFunc("/emit", func(w http.ResponseWriter, r *http.Request) {
        if r.Method != http.MethodPost { w.WriteHeader(405); return }
        var data map[string]string
        if json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&data) != nil { w.WriteHeader(400); return }
        event := map[string]any{"event_type":"ottplay_notification","time_fired":time.Now().UTC().Format(time.RFC3339Nano),"data":data}
        sent := 0
        mu.Lock()
        for client, id := range clients {
            ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
            if wsjson.Write(ctx, client, map[string]any{"id":id,"type":"event","event":event}) == nil { sent++ }
            cancel()
        }
        mu.Unlock()
        w.Header().Set("Content-Type", "application/json")
        _ = json.NewEncoder(w).Encode(map[string]any{"sent":sent})
    })
    server := &http.Server{Addr:*address,Handler:mux,ReadHeaderTimeout:3*time.Second}
    if server.ListenAndServe() != nil { os.Exit(1) }
}
`;

function fixture(command, args, env = {}) {
    const child = spawn(command, args, { cwd: root, env: { ...process.env, ...env }, stdio: ["ignore", "pipe", "pipe"] });
    child.fixtureOutput = "";
    for (const stream of [child.stdout, child.stderr]) stream.on("data", (part) => {
        child.fixtureOutput = (child.fixtureOutput + part.toString()).slice(-4096);
    });
    child.on("error", (error) => { child.fixtureError = error; });
    children.push(child);
    return child;
}

function checkChildren() {
    for (const child of children) {
        if (!child.buildTask && (child.fixtureError || child.exitCode !== null)) {
            throw new Error("Local fixture exited unexpectedly: " + (child.fixtureError?.message || child.fixtureOutput || child.exitCode));
        }
    }
}

function build(args, env) {
    const task = execFile("go", args, { cwd: root, env, timeout: 120000, maxBuffer: 4 * 1024 * 1024 });
    task.child.buildTask = true;
    children.push(task.child);
    return task;
}

async function freePort() {
    const listener = net.createServer();
    await new Promise((resolve, reject) => {
        listener.once("error", reject);
        listener.listen(0, "127.0.0.1", resolve);
    });
    const port = listener.address().port;
    await new Promise((resolve) => listener.close(resolve));
    return port;
}

async function waitFor(check, label, milliseconds = 7500) {
    const deadline = Date.now() + milliseconds;
    while (Date.now() < deadline) {
        checkChildren();
        if (await check()) return;
        await pause(100);
    }
    throw new Error("Timed out: " + label);
}

async function json(url, options = {}) {
    const response = await fetch(url, { ...options, signal: AbortSignal.timeout(2000) });
    assert.equal(response.status, 200, "local fixture HTTP request failed");
    return response.json();
}

async function cleanup() {
    if (cleanupTask) return cleanupTask;
    cleanupTask = (async () => {
        clearTimeout(timeout);
        if (browser) await browser.close().catch(() => {});
        await Promise.all(children.map(async (child) => {
            if (child.exitCode !== null || child.signalCode !== null) return;
            child.kill("SIGTERM");
            await Promise.race([new Promise((resolve) => child.once("exit", resolve)), pause(2000)]);
            if (child.exitCode === null && child.signalCode === null) {
                child.kill("SIGKILL");
                await Promise.race([new Promise((resolve) => child.once("exit", resolve)), pause(1000)]);
            }
        }));
        fs.rmSync(temp, { recursive: true, force: true });
    })();
    return cleanupTask;
}

async function smoke() {
    const [haPort, controlPort, playerPort] = await Promise.all([freePort(), freePort(), freePort()]);
    const haURL = "http://127.0.0.1:" + haPort;
    const controlURL = "http://127.0.0.1:" + controlPort;
    const playerURL = "http://127.0.0.1:" + playerPort;
    const tokenFile = path.join(temp, "ha-token");
    const configFile = path.join(temp, "server.json");
    const cliFile = path.join(temp, "cli.json");
    fs.writeFileSync(tokenFile, haToken + "\n", { mode: 0o600 });
    fs.writeFileSync(configFile, JSON.stringify({
        admin_token: adminToken,
        allowed_origins: [playerURL],
        listen: "127.0.0.1:" + controlPort,
        devices: [{ id: "smoke_player", token: deviceToken }],
        home_assistant: {
            enabled: true, url: haURL, token_file: tokenFile,
            rules: [{ id: "smoke", event_type: "ottplay_notification", title: "{{title}}", message: "{{message}}", severity: "warning", cooldown_seconds: 1, duration_ms: 15000, ttl_seconds: 15 }],
        },
    }), { mode: 0o600 });
    fs.writeFileSync(cliFile, JSON.stringify({ server: controlURL, server_config: configFile, players: { smoke: "smoke_player" } }), { mode: 0o600 });
    fixture(path.join(temp, "fake-ha"), ["--listen", "127.0.0.1:" + haPort, "--token-file", tokenFile]);
    fixture(path.join(temp, "controller"), ["serve", "--config", configFile]);
    fixture(process.execPath, [path.join(player, "tests/helpers/device-browser-server.cjs")], { OTTP_DEVICE_TEST_PORT: String(playerPort), OTTP_DEVICE_TEST_DIAGNOSTICS: "0", CI_BROWSER_WEB_ROOT: "" });
    await waitFor(async () => {
        try {
            await Promise.all([json(haURL + "/healthz"), json(controlURL + "/readyz"), json(playerURL + "/__device_test_health")]);
            return true;
        } catch (_) { return false; }
    }, "local fixtures ready");
    const status = () => json(controlURL + "/api/home-assistant/status", { headers: { Authorization: "Bearer " + adminToken } });
    let lastEmit = 0;
    const emit = async (message) => {
        // Inactive deliveries still consume the configured per-rule cooldown.
        await pause(Math.max(0, 1100 - (Date.now() - lastEmit)));
        const result = await json(haURL + "/emit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Fixture home", message }) });
        lastEmit = Date.now();
        assert.equal(result.sent, 1, "fixture must send event through exactly one real HA subscription");
    };

    browser = await chromium.launch({ headless: true, timeout: 7500 });
    const context = await browser.newContext({ serviceWorkers: "block" });
    await context.route("**/*", (route) => {
        const origin = new URL(route.request().url()).origin;
        return origin === playerURL || origin === controlURL ? route.continue() : route.abort("blockedbyclient");
    });
    await context.routeWebSocket("**/*", (socket) => socket.close());
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(playerURL + "/f/pc/", { waitUntil: "domcontentloaded", timeout: 7500 });
    await page.waitForFunction(() => !!window.__ottCommandServer && !document.body.classList.contains("booting"), null, { timeout: 7500 });
    await page.evaluate(({ address, token }) => window.__ottCommandServer.configure({ address, enabled: true, token }), { address: controlURL, token: deviceToken });
    await waitFor(async () => {
        const data = await status();
        return data.bridge.state === "connected" && data.players[0]?.active;
    }, "HA subscription and active player presence");

    await emit(text);
    const banner = page.locator(".remote-notify");
    await banner.waitFor({ state: "visible", timeout: 7500 });
    assert.equal(await banner.textContent(), "Fixture home\n" + text);
    assert.equal(await banner.locator("img").count(), 0, "event text must never become HTML");
    assert.equal(await page.evaluate(() => window.__haInjected), undefined);
    console.log("PASS real HA authentication/subscription -> Go queue -> production player plaintext banner");

    await page.evaluate(() => window.__ottStandby.set(true));
    assert.equal(await banner.count(), 0, "standby must clear the existing banner immediately");
    await waitFor(async () => !(await status()).players[0].active, "inactive player presence");
    await emit("This event must not wake or replay");
    await waitFor(async () => (await status()).bridge.notifications_skipped === 1, "inactive event skipped");
    assert.equal(await banner.count(), 0);
    await page.evaluate(() => window.__ottStandby.set(false));
    await waitFor(async () => (await status()).players[0].active, "player presence after wake");
    assert.equal(await banner.count(), 0, "wake must not replay sleeping-period events");
    console.log("PASS standby clears banners, inactive events are skipped, wake has no replay");

    await emit("Fresh event after wake");
    await banner.waitFor({ state: "visible", timeout: 7500 });
    assert.equal(await banner.textContent(), "Fixture home\nFresh event after wake");
    const cli = await execFile(process.env.PYTHON || "python3", [path.join(root, "cli/ott.py"), "-c", cliFile, "-t", "5", "-j", "ha", "status"], { cwd: root, timeout: 7000 });
    const report = JSON.parse(cli.stdout);
    assert.equal(report.bridge.state, "connected");
    assert.equal(report.bridge.events_received, 3);
    assert.equal(report.bridge.notifications_queued, 2);
    assert.equal(report.bridge.notifications_skipped, 1);
    assert.deepEqual(report.delivery, { queued: 2, polled: 2, skipped: 1, dropped: 0 });
    assert.equal(report.players[0].active, true);
    assert.equal(report.players[0].pending, 0);
    for (const secret of [haToken, adminToken, deviceToken, tokenFile, text]) assert(!cli.stdout.includes(secret), "CLI exposed a fixture credential or notification body");
    assert.deepEqual(errors, [], "production player reported a JavaScript error");
    console.log("PASS fresh post-wake notification and real ott ha status counters without credentials/content");
}

(async () => {
    try {
        fs.writeFileSync(path.join(temp, "fake-ha.go"), fixtureSource, { mode: 0o600 });
        console.log("Building real Go controller and local HA WebSocket fixture");
        const env = { ...process.env, ...(process.env.OTT_HA_SMOKE_GOPROXY ? { GOPROXY: process.env.OTT_HA_SMOKE_GOPROXY } : {}) };
        await Promise.all([
            build(["build", "-o", path.join(temp, "controller"), "./cmd/ottplay-control-server"], env),
            build(["build", "-o", path.join(temp, "fake-ha"), path.join(temp, "fake-ha.go")], env),
        ]);
        const started = Date.now();
        await Promise.race([
            smoke(),
            new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Cross-stack smoke exceeded 30 seconds after build")), 30000); }),
        ]);
        console.log("Home Assistant cross-stack smoke passed in " + ((Date.now() - started) / 1000).toFixed(1) + "s; no real HA/device contacted");
    } catch (error) {
        console.error("Home Assistant smoke failed: " + String(error?.message || error).replaceAll(haToken, "[fixture-token]"));
        process.exitCode = 1;
    } finally {
        await cleanup();
    }
})();

for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => { void cleanup().finally(() => process.exit(130)); });

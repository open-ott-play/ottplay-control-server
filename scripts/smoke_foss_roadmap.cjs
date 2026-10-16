// Local integration: real Go controller + compiled FOSS transport/handlers + Python CLI.
// The host's storage/display ports are fixtures; no stream, account or device is contacted.
// Run: node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const net = require('node:net');
const { spawn, execFile: execFileCallback } = require('node:child_process');
const execFile = require('node:util').promisify(execFileCallback);
const root = path.resolve(__dirname, '..');
const foss = path.resolve(process.argv[2] || process.env.OTT_FOSS_ROOT || '../foss');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ott-roadmap-smoke-'));
const emitted = path.join(temp, 'player');
const controllers = [];
let server;
const clone = value => JSON.parse(JSON.stringify(value));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const unexpected = () => assert.fail('unexpected host port invoked');
const sharedCore = require(path.join(foss, 'tests/helpers/shared-core-runtime.cjs'));

function moduleOf(file, dependency, host) {
    const context = vm.createContext({ console, Date, URL, exports: {}, require: dependency, window: host });
    sharedCore(context, { vendorOnly: true });
    const filename = path.join(emitted, file + '.js');
    vm.runInContext(fs.readFileSync(filename, 'utf8'), context, { filename });
    return context.exports;
}

(async () => {
    console.log('Building current controller and compiling the full FOSS TypeScript project');
    await Promise.all([
        execFile('go', ['build', '-o', path.join(temp, 'controller'), './cmd/ottplay-control-server'], { cwd: root }),
        execFile(process.execPath, [require.resolve('typescript/bin/tsc', { paths: [foss] }),
            '--project', path.join(foss, 'tsconfig.json'), '--module', 'commonjs', '--outDir', emitted], { cwd: foss }),
    ]);
    const listener = net.createServer();
    await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
    const port = listener.address().port;
    await new Promise(resolve => listener.close(resolve));
    const address = 'http://127.0.0.1:' + port;
    const config = { admin_token: 'a'.repeat(32), listen: '127.0.0.1:' + port,
        devices: [{ id: 'dev_one', token: 'b'.repeat(32) }, { id: 'dev_two', token: 'c'.repeat(32) }],
        journal: { path: path.join(temp, 'private', 'operations.json'), max_records: 128, retention_seconds: 86400 } };
    fs.writeFileSync(path.join(temp, 'server.json'), JSON.stringify(config), { mode: 0o600 });
    server = spawn(path.join(temp, 'controller'), ['serve', '--config', path.join(temp, 'server.json')], { stdio: 'ignore' });
    let ready = false;
    for (let i = 0; i < 60; i++) {
        if (server.exitCode !== null) throw new Error('controller exited before readiness');
        try { ready = (await fetch(address + '/readyz')).ok; } catch (_) {}
        if (ready) break;
        await pause(50);
    }
    assert(ready, 'controller readiness');
    const settings = { name: 'Local Тест', playlist: 'https://source.invalid/list.m3u?token=PRIVATE_FIXTURE', history_hours: 24, vportal: '' };
    const plex = { server: 'https://plex.invalid', token: 'PRIVATE_FIXTURE_PLEX' };
    const stalker = { name: 'Portal', server: 'https://stalker.invalid', mac: '02:00:00:00:00:01' };
    const preset = { m3u: [{ number: 1, ...settings }], plex, stalker: [{ number: 1, ...stalker }], active_profile: 1 };
    fs.writeFileSync(path.join(temp, 'cli.json'), JSON.stringify({ server: address, server_config: path.join(temp, 'server.json'),
        players: { one: 'dev_one', two: 'dev_two' }, presets: { Local: preset } }), { mode: 0o600 });
    const wire = require(path.join(emitted, 'shared/wire-contracts.js'));
    function player(device, rowID) {
        const configuration = { active: 0, M3Us: Array.from({ length: 15 }, (_, i) => ({ name: i ? '' : settings.name,
            www: i ? '' : settings.playlist, rechours: i ? 0 : 24, medUrl: '' })) };
        const storage = { m3um3uArr: JSON.stringify(configuration), plexcfg: JSON.stringify({ address: plex.server, token: plex.token }),
            stalkerstalker_data: JSON.stringify({ name: stalker.name, portal: stalker.server, mac: stalker.mac }) };
        const beforeStorage = JSON.stringify(storage);
        const plays = [], responses = [], received = [], transportFailures = [];
        const host = { setTimeout, clearTimeout, location: { protocol: 'http:' }, deviceUUID: device.id,
            __ottActiveProviderDriver: { id: 'm3u', configuration: () => clone(configuration), fixedSlot: () => -1, saveConfiguration: unexpected },
            __ottParental: { needs: () => false }, parentalArray: [], p_pref: 'm3u', m3uArr: configuration,
            stbGetItem: key => storage[key] || null, stbSetItem: unexpected,
            catsArray: ['all'], cats: { all: [rowID] },
            playChannel: (category, index) => plays.push({ kind: 'live', id: host.cats[host.catsArray[category]][index] }),
            setCurrent: (category, index) => plays.push({ kind: 'select', id: host.cats[host.catsArray[category]][index] }),
            getArchiveUrl: () => 'https://target.invalid/archive?token=PRIVATE_FIXTURE_ARCHIVE',
            __ottClassicArchive: { open: start => plays.push({ kind: 'archive', start }) } };
        moduleOf('provider/source-identity', unexpected, host);
        // Real codec output has no itemId: local indices and display names differ.
        host.channels = host.OttPlayCore.parseProviderPlaylist('#EXTM3U\n#EXTINF:-1 tvg-id="fixture",' + device.id +
            '\nhttps://stream.invalid/live?token=PRIVATE_FIXTURE_STREAM\n', 'm3u', () => rowID, 24).channels;
        assert(host.channels[rowID], 'real codec produced expected channel');
        assert(!host.channels[rowID].itemId, 'exercise URL fallback identity');
        host.channels[rowID].rec = 24;
        const state = { phase: 'playing', position: 30, target: { kind: 'live', channelId: String(rowID), sourceId: host.__ottSourceIdentity.current(host) } };
        host.__ottClassicPlayback = { snapshot: () => state };
        const dependency = name => {
            if (name === './remote-handoff' || name === './remote-profiles') return moduleOf('commands/' + name.slice(2), dependency, host);
            if (name === '../provider') return { isProviderAllowed: () => true, checkProviderUrl: () => true };
            if (name === '../plugins/vportal') return { parseVPortalLink: value => { assert.equal(value, ''); return null; } };
            // Other actions must not be involved in this focused smoke.
            return new Proxy({}, { get: () => unexpected });
        };
        const execute = moduleOf('commands/remote-requests', dependency, host).executeRemoteRequest;
        const transport = moduleOf('plugins/command-server', () => wire, host);
        const send = (request, complete) => {
            const abort = new AbortController();
            fetch(request.url, { method: request.method, headers: request.headers, body: request.body, signal: abort.signal })
                .then(async response => {
                    const body = await response.text();
                    if (request.url.endsWith('/api/responses')) {
                        responses.push(JSON.parse(request.body));
                        if (response.status !== 200) transportFailures.push(response.status);
                    }
                    if (!abort.signal.aborted) complete({ body, status: response.status });
                }, () => { if (!abort.signal.aborted) complete(); });
            return () => abort.abort();
        };
        const controller = transport.createCommandServer(host, send, () => {}, unexpected, (request, done, afterReply) => {
            received.push(request.action);
            return execute(request, done, afterReply);
        });
        controller.configure({ address, enabled: true, token: device.token });
        controllers.push(controller);
        return { host, state, plays, responses, received, transportFailures, storage, beforeStorage };
    }
    const one = player(config.devices[0], 101), two = player(config.devices[1], 909);
    async function cli(args, fail = false) {
        try {
            const result = await execFile('python3', [path.join(root, 'cli/ott.py'), '-c', path.join(temp, 'cli.json'), '-t', '10', '-j', ...args], { timeout: 15000 });
            assert(!fail, 'CLI should reject this operation');
            assert(!result.stdout.includes('PRIVATE_FIXTURE'), 'CLI output must redact credentials');
            return JSON.parse(result.stdout);
        } catch (error) {
            if (!fail) throw error;
            assert.equal(error.code, 1);
            assert(!error.stderr.includes('PRIVATE_FIXTURE'), 'CLI error must redact credentials');
            if (fail === 'different') return JSON.parse(error.stdout);
        }
    }
    async function api(route, payload) {
        const response = await fetch(address + route, { method: payload ? 'POST' : 'GET', headers: { Authorization: 'Bearer ' + config.admin_token,
            'Content-Type': 'application/json' }, ...(payload ? { body: JSON.stringify(payload) } : {}) });
        return { status: response.status, body: await response.json() };
    }
    const verified = await cli(['one', 'load', 'Local', 'verify']);
    assert.equal(verified.status, 'verified');
    assert.equal(verified.comparisons.length, 3);
    assert(verified.comparisons.every(row => row.matched));
    one.storage.plexcfg = JSON.stringify({ address: plex.server, token: 'different' });
    const different = await cli(['one', 'load', 'Local', 'verify'], 'different');
    assert.equal(different.status, 'different');
    assert.equal(different.comparisons.find(row => row.step === 'plex').matched, false);
    one.storage.plexcfg = JSON.stringify({ address: plex.server, token: plex.token });
    assert.equal(JSON.stringify(one.storage), one.beforeStorage);
    assert.equal(one.plays.length, 0);
    console.log('PASS exact M3U/Plex/Stalker comparisons, mismatch detection, no storage or playback mutation');

    const result = await cli(['handoff', 'one', 'two']);
    assert.equal(result.kind, 'live'); assert.equal(result.dispatched, true); assert.equal(result.source_stopped, false);
    assert.deepEqual(two.plays, [{ kind: 'live', id: 909 }]);
    assert.equal(one.plays.length, 0);
    const exported = one.responses.find(row => row.data && row.data.handoff).data.handoff;
    assert.match(exported.item_id, /^channel-v1:[0-9a-f]{8}\.[0-9a-f]{8}$/);
    assert(!JSON.stringify(exported).includes('PRIVATE_FIXTURE'));
    assert(!JSON.stringify(exported).includes('https://'));
    const rejected = await api('/api/requests?device_id=dev_two', { action: 'playback_handoff_import', params: { handoff: { ...exported, url: 'https://unexpected.invalid' } } });
    assert.equal(rejected.status, 400);
    const rejectedRaw = await api('/api/requests?device_id=dev_two', { action: 'playback_handoff_import', params: { handoff: { ...exported, item_id: 'https://unexpected.invalid' } } });
    assert.equal(rejectedRaw.status, 400);
    two.host.m3uArr.M3Us[0].www = 'https://different.invalid/list';
    await cli(['handoff', 'one', 'two'], true);
    assert.equal(two.plays.length, 1);
    two.host.m3uArr.M3Us[0].www = settings.playlist;
    console.log('PASS live handoff across different codec row IDs, opaque wire data, strict schema and source mismatch');

    const start = Math.floor(Date.now() / 1000) - 300;
    one.state.target.kind = 'archive'; one.state.target.archiveStart = start;
    const archived = await cli(['handoff', 'one', 'two']);
    assert.equal(archived.kind, 'archive');
    assert.deepEqual(two.plays.slice(-2), [{ kind: 'select', id: 909 }, { kind: 'archive', start: start + 30 }]);
    two.host.getArchiveUrl = () => '';
    await cli(['handoff', 'one', 'two'], true);
    assert.equal(two.plays.length, 3, 'failed archive resolution must not select or play');
    assert.deepEqual(one.transportFailures, []); assert.deepEqual(two.transportFailures, []);
    assert.equal(JSON.stringify(one.storage), one.beforeStorage);
    assert.equal(JSON.stringify(two.storage), two.beforeStorage);
    console.log('PASS archive seek offset and failed-resolution fence; every FOSS response passed Go validation');
    for (const [name, cwd] of [['control', root], ['foss', foss]]) {
        const revision = await execFile('git', ['rev-parse', 'HEAD'], { cwd });
        const dirty = await execFile('git', ['status', '--porcelain'], { cwd });
        console.log(name + ': ' + revision.stdout.trim() + (dirty.stdout ? ' (working tree changes included)' : ''));
    }
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
    for (const controller of controllers) controller.configure({ address: '', enabled: false, token: '' });
    if (server && server.exitCode === null) {
        const stopped = new Promise(resolve => server.once('exit', resolve));
        server.kill('SIGTERM');
        const force = setTimeout(() => server.kill('SIGKILL'), 2000);
        await stopped; clearTimeout(force);
    }
    fs.rmSync(temp, { recursive: true, force: true });
});

import { Endpoint, Environment, Logger, ServerNode, VendorId } from '@matter/main';
import { OnOffPlugInUnitDevice } from '@matter/main/devices/on-off-plug-in-unit';
import { OnOffServer } from '@matter/main/behaviors/on-off';
import { BridgedDeviceBasicInformationServer } from '@matter/main/behaviors/bridged-device-basic-information';
import { AggregatorEndpoint } from '@matter/main/endpoints/aggregator';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { StartGate } from './start_gate.mjs';

const execFileAsync = promisify(execFile);
const here = dirname(fileURLToPath(import.meta.url));

export async function createStartNode({ storage, run, requestEnd = async () => {}, requestInput, id = 'jarvis-work-start', port = 5540 }) {
    Logger.level = 'error'; // Pairing credentials must never enter service logs.
    Environment.default.vars.set('storage.path', storage);
    const node = await ServerNode.create({
        id,
        network: { port },
        productDescription: { name: 'Jarvis Work Start', deviceType: OnOffPlugInUnitDevice.deviceType },
        basicInformation: {
            vendorName: 'Jarvis', vendorId: VendorId(0xfff1), productId: 0x8000,
            productName: 'Jarvis Work Start', nodeLabel: 'Jarvis Work Start',
            serialNumber: 'jarvis-work-start-1', uniqueId: 'jarvis-work-start-1',
        },
    });
    const endGate = new StartGate(requestEnd, { cooldownMs: 15000 });
    let endPending = Promise.resolve();
    class RoutineOnOffServer extends OnOffServer {
        async off() {
            await super.off();
            // Handle an explicit OFF command even when the momentary plug is OFF.
            // Attribute resets and startup restoration never invoke this method.
            endPending = endGate.accept(true).catch(() => { console.error('work_end_prompt_failed'); });
            await endPending;
        }
    }
    const button = new Endpoint(OnOffPlugInUnitDevice.with(RoutineOnOffServer), { id: 'start' });
    await node.add(button);
    // Restore to OFF before subscribing, so restart cannot replay a stored ON.
    await button.set({ onOff: { onOff: false } });
    const gate = new StartGate(run);
    let pending = Promise.resolve();
    button.events.onOff.onOff$Changed.on(value => {
        if (!value) return;
        pending = gate.accept(value).catch(() => {
            console.error('work_start_failed');
        }).finally(async () => { await button.set({ onOff: { onOff: false } }); });
    });
    let inputButton, inputPending = Promise.resolve();
    if (requestInput) {
        // Keep the original endpoint ID/number and pairing storage intact.
        const bridge = new Endpoint(AggregatorEndpoint, { id: 'voice-input-bridge' });
        await node.add(bridge);
        inputButton = new Endpoint(OnOffPlugInUnitDevice.with(BridgedDeviceBasicInformationServer), {
            id: 'voice-input',
            bridgedDeviceBasicInformation: {
                nodeLabel: 'Jarvis Voice Input', productName: 'Jarvis Voice Input',
                serialNumber: 'jarvis-voice-input-1', uniqueId: 'jarvis-voice-input-1', reachable: true,
            },
        });
        await bridge.add(inputButton);
        await inputButton.set({ onOff: { onOff: false } });
        const inputGate = new StartGate(requestInput, { cooldownMs: 15000 });
        inputButton.events.onOff.onOff$Changed.on(value => {
            if (!value) return;
            inputPending = inputGate.accept(true).catch(() => {
                console.error('dictation_start_failed');
            }).finally(async () => { await inputButton.set({ onOff: { onOff: false } }); });
        });
    }
    return { node, button, inputButton, idle: () => Promise.all([pending, endPending, inputPending]) };
}

async function main() {
    process.umask(0o077);
    const state = resolve(process.env.JARVIS_MATTER_STATE || join(homedir(), 'Library/Application Support/JarvisGoogleHome'));
    await mkdir(state, { recursive: true, mode: 0o700 });
    const jarvisState = join(homedir(), 'Library/Application Support/JarvisMacOSS');
    const { node, idle } = await createStartNode({
        storage: join(state, 'matter'),
        run: () => execFileAsync('/opt/homebrew/bin/python3', [join(here, 'run_work_start.py'), '--state-dir', jarvisState, '--receipt-dir', state], { timeout: 25000, maxBuffer: 4096 }),
        requestEnd: () => execFileAsync('/opt/homebrew/bin/python3', [join(here, 'request_work_end.py'), '--state-dir', jarvisState], { timeout: 4000, maxBuffer: 4096 }),
        requestInput: () => execFileAsync('/opt/homebrew/bin/python3', [join(here, 'request_dictation.py'), '--state-dir', jarvisState], { timeout: 4000, maxBuffer: 4096 }),
    });
    await writeFile(join(state, 'pairing.json'), JSON.stringify(node.state.commissioning.pairingCodes), { mode: 0o600 });
    const status = () => writeFile(join(state, 'status.json'), JSON.stringify({ pid: process.pid, online: node.lifecycle.isOnline, commissioned: node.state.commissioning.commissioned, updated_at: Date.now() }), { mode: 0o600 });
    await node.start();
    await status();
    const timer = setInterval(() => { status().catch(() => {}); }, 5000);
    const close = async () => { clearInterval(timer); await idle(); await node.close(); process.exit(0); };
    process.once('SIGTERM', close);
    process.once('SIGINT', close);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
    main().catch(() => { console.error('matter_start_failed'); process.exitCode = 1; });
}

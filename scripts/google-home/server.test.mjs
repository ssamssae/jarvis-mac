import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createStartNode, createVoiceNode } from './server.mjs';

test('isolated voice node starts once and never changes original work endpoint', async () => {
    const storage = await mkdtemp(join(tmpdir(), 'jarvis-input-test-'));
    let node, voice, inputs = 0, work = 0;
    try {
        const original = await createStartNode({ storage, run: async () => { work++; } });
        node = original.node;
        const originalNumber = original.button.number;
        let fixture = await createVoiceNode({ storage, requestInput: async () => { inputs++; } });
        voice = fixture.node;
        assert.equal(original.button.number, originalNumber);
        assert.equal(inputs, 0);
        await fixture.button.act(agent => agent.onOff.on());
        await fixture.idle();
        assert.equal(inputs, 1);
        assert.equal(work, 0);
        assert.equal(fixture.button.state.onOff.onOff, false);
        await fixture.button.act(agent => agent.onOff.off());
        await fixture.button.act(agent => agent.onOff.on());
        await fixture.idle();
        assert.equal(inputs, 1);
        await voice.close();
        fixture = await createVoiceNode({ storage, requestInput: async () => { inputs++; } });
        voice = fixture.node;
        assert.equal(inputs, 1);
        assert.equal(original.button.number, originalNumber);
        assert.equal(fixture.button.state.onOff.onOff, false);
        assert.equal(work, 0);
    } finally {
        if (voice) await voice.close();
        if (node) await node.close();
        await rm(storage, { recursive: true, force: true });
    }
});

test('ON starts; only an explicit OFF command requests confirmation, never reset or restart', async () => {
    const storage = await mkdtemp(join(tmpdir(), 'jarvis-matter-test-'));
    let calls = 0, ends = 0, node;
    try {
        const fixture = await createStartNode({ storage, run: async () => { calls++; }, requestEnd: async () => { ends++; } });
        node = fixture.node;
        assert.equal(calls, 0);
        await fixture.button.set({ onOff: { onOff: true } });
        await fixture.idle();
        assert.equal(calls, 1);
        assert.equal(fixture.button.state.onOff.onOff, false);
        await fixture.button.set({ onOff: { onOff: false } });
        assert.equal(calls, 1);
        assert.equal(ends, 0);
        await fixture.button.act(agent => agent.onOff.off());
        await fixture.idle();
        assert.equal(ends, 1);
        await fixture.button.act(agent => agent.onOff.off());
        assert.equal(ends, 1); // duplicate requests cannot extend authority
        await node.close();
        const restarted = await createStartNode({ storage, run: async () => { calls++; }, requestEnd: async () => { ends++; } });
        node = restarted.node;
        assert.equal(calls, 1);
        assert.equal(ends, 1);
        assert.equal(restarted.button.state.onOff.onOff, false);
    } finally {
        if (node) await node.close();
        await rm(storage, { recursive: true, force: true });
    }
});

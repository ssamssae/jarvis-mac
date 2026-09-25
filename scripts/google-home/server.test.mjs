import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createStartNode } from './server.mjs';

test('Matter endpoint calls the existing adapter on ON only and resets', async () => {
    const storage = await mkdtemp(join(tmpdir(), 'jarvis-matter-test-'));
    let calls = 0, node;
    try {
        const fixture = await createStartNode({ storage, run: async () => { calls++; } });
        node = fixture.node;
        assert.equal(calls, 0);
        await fixture.button.set({ onOff: { onOff: true } });
        await fixture.idle();
        assert.equal(calls, 1);
        assert.equal(fixture.button.state.onOff.onOff, false);
        await fixture.button.set({ onOff: { onOff: false } });
        assert.equal(calls, 1);
        await node.close();
        const restarted = await createStartNode({ storage, run: async () => { calls++; } });
        node = restarted.node;
        assert.equal(calls, 1);
        assert.equal(restarted.button.state.onOff.onOff, false);
    } finally {
        if (node) await node.close();
        await rm(storage, { recursive: true, force: true });
    }
});

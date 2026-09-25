import { test } from 'node:test';
import assert from 'node:assert/strict';
import { StartGate } from './start_gate.mjs';

test('OFF, duplicate ON and concurrent ON cannot invoke work again', async () => {
    let time = 0, calls = 0, release;
    const gate = new StartGate(() => { calls++; return new Promise(r => { release = r; }); }, { now: () => time });
    assert.equal(await gate.accept(false), false);
    const first = gate.accept(true);
    time = 6000;
    assert.equal(await gate.accept(true), false);
    release(); await first;
    time = 0;
    assert.equal(await gate.accept(true), false);
    time = 6000;
    const next = gate.accept(true);
    release(); await next;
    assert.equal(calls, 2);
});

test('runner failure releases the busy lock without immediate retry', async () => {
    let time = 0, calls = 0;
    const gate = new StartGate(async () => { calls++; throw Error('failed'); }, { now: () => time });
    await assert.rejects(gate.accept(true));
    assert.equal(await gate.accept(true), false);
    time = 5001;
    await assert.rejects(gate.accept(true));
    assert.equal(calls, 2);
});

import assert from 'node:assert/strict';
import test from 'node:test';

import { commandLine, planNarrative, planOutcome, stepState } from '../src/fixplan.js';

test('commandLine quotes only tokens that need it', () => {
  const argv = ['/proj/.venv/bin/python', '-m', 'pip', 'install', 'six==1.16.0'];
  assert.equal(commandLine(argv), '/proj/.venv/bin/python -m pip install "six==1.16.0"');
});

test('stepState reflects the receipt', () => {
  assert.equal(stepState({}), 'pending');
  assert.equal(stepState({ receipt: { error: 'exit status 1' } }), 'error');
  assert.equal(stepState({ receipt: { after: { status: 'satisfied' } } }), 'satisfied');
  assert.equal(stepState({ receipt: { after: { status: 'version-mismatch' } } }), 'done');
});

test('planNarrative summarizes writable plans and explains blocked ones', () => {
  const blocked = { target: { writable: false, block_reason: 'This is a shared system interpreter.' }, steps: [] };
  assert.equal(planNarrative(blocked), 'This is a shared system interpreter.');

  const empty = { target: { writable: true }, steps: [] };
  assert.match(planNarrative(empty), /Nothing to do/);

  const plan = {
    target: { writable: true },
    steps: [{ action: 'upgrade', package: 'six' }, { action: 'install', package: 'idna' }],
  };
  assert.equal(planNarrative(plan), '2 actions: upgrade six, install idna.');
});

test('planOutcome is null until applied, then reports before/after', () => {
  assert.equal(planOutcome({ verdict_before: 'missing', verdict_after: null }), null);
  assert.deepEqual(
    planOutcome({ verdict_before: 'missing', verdict_after: 'satisfied' }),
    { before: 'missing', after: 'satisfied', fixed: true },
  );
});

import assert from 'node:assert/strict';
import test from 'node:test';

import { freshnessText, proofSentence, shellCommand } from '../src/evidence.js';

test('shellCommand preserves the exact argv as a rerunnable POSIX command', () => {
  const script = "print('hello world')\n";
  assert.equal(
    shellCommand(['/demo/bin/python', '-I', '-c', script]),
    `/demo/bin/python -I -c 'print('"'"'hello world'"'"')\n'`,
  );
});

test('proofSentence uses only the retained raw distribution record', () => {
  assert.equal(
    proofSentence({
      name: 'six', status: 'version-mismatch', specifier: '==1.16.0',
      evidence: { reported_distribution: { name: 'six', version: '1.15.0' } },
    }, { available: true }),
    'reported 1.15.0 · requires ==1.16.0 · fails',
  );
  assert.equal(
    proofSentence({
      name: 'idna', status: 'missing', specifier: '>=3.0',
      evidence: { reported_distribution: null },
    }, { available: true }),
    'idna was absent from the returned distribution list · requires >=3.0 · fails',
  );
});

test('proofSentence is honest when the query itself is unavailable', () => {
  assert.equal(
    proofSentence(
      { name: 'idna', status: 'missing', specifier: '>=3.0' },
      { available: false, reason: 'package query failed' },
    ),
    'Query evidence unavailable: package query failed',
  );
});

test('freshnessText retains the topology timestamp verbatim', () => {
  const generatedAt = '2026-07-20T12:34:56+00:00';
  assert.equal(freshnessText(generatedAt), `Live reading captured ${generatedAt}`);
});

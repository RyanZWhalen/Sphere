import assert from 'node:assert/strict';
import test from 'node:test';

import { indexRepositoryRequiresEdges } from '../src/topology.js';

test('node selection keeps only the exact repository-to-target requires diff', () => {
  const repositoryId = 'repository:/demo';
  const interpreterId = 'interpreter:/framework/python3.14';
  const allMissing = [
    { name: 'six', status: 'missing', installed_version: null },
    { name: 'idna', status: 'missing', installed_version: null },
    { name: 'typing-extensions', status: 'missing', installed_version: null },
  ];
  const edges = [
    { type: 'resolves-to', from: 'context:/demo', to: interpreterId },
    { type: 'based-on', from: 'environment:/good', to: interpreterId },
    {
      type: 'requires',
      from: 'repository:/other',
      to: interpreterId,
      evidence: { python_path: '/wrong/python', raw_stdout: '[{"name":"idna","version":"3.18"}]' },
      diff: [{ name: 'idna', status: 'satisfied', installed_version: '3.18' }],
    },
    {
      type: 'requires',
      from: repositoryId,
      to: interpreterId,
      evidence: { python_path: '/framework/python3.14', raw_stdout: '[]\n' },
      diff: allMissing,
    },
    {
      type: 'requires',
      from: repositoryId,
      to: interpreterId,
      diff: [{ name: 'six', status: 'satisfied', installed_version: '1.16.0' }],
    },
  ];

  const selected = indexRepositoryRequiresEdges(edges, repositoryId).get(interpreterId);

  assert.equal(selected.from, repositoryId);
  assert.equal(selected.to, interpreterId);
  assert.equal(selected.evidence.python_path, '/framework/python3.14');
  assert.equal(selected.evidence.raw_stdout, '[]\n');
  assert.deepEqual(selected.diff, allMissing);
  assert.ok(selected.diff.every((item) => item.installed_version === null));
});

const test = require('node:test');
const assert = require('node:assert/strict');
const report = require('./report-live-contracts.cjs');

function fixture(issues = []) {
  const calls = [];
  const github = {
    paginate: async (_, args) => { calls.push(['list', args]); return issues; },
    rest: {issues: {
      listForRepo: () => {},
      create: async args => calls.push(['create', args]),
      update: async args => calls.push(['update', args])
    }}
  };
  const context = {repo: {owner: 'owner', repo: 'repo'}, serverUrl: 'https://github.com',
    runId: 123, ref: 'refs/heads/develop', sha: 'abc123'};
  return {calls, github, context};
}

test('success and cancellation do not create or modify an issue', async () => {
  const f = fixture();
  await report({...f, outcomes: {pytest: 'success', smoke: 'success'}});
  await report({...f, outcomes: {pytest: 'cancelled', smoke: 'skipped'}});
  assert.deepEqual(f.calls, []);
});

test('first failure creates a labeled issue with run provenance', async () => {
  const f = fixture([{number: 1, body: 'unrelated issue'}]);
  await report({...f, outcomes: {pytest: 'failure', smoke: 'success'}});
  const [method, args] = f.calls.at(-1);
  assert.equal(method, 'create');
  assert.deepEqual(args.labels, ['bug', 'area: http']);
  assert.match(args.body, /actions\/runs\/123/);
  assert.match(args.body, /pytest: failure/);
  assert.match(args.body, /abc123/);
});

for (const state of ['open', 'closed']) {
  test(`repeated failure reuses the ${state} issue, including an injected failure`, async () => {
    const f = fixture([{number: 9, state, body: '<!-- tuik-live-contracts:v1 -->\nold run'}]);
    await report({...f, outcomes: {pytest: 'skipped', smoke: 'skipped', exercise: 'failure'}});
    const [method, args] = f.calls.at(-1);
    assert.equal(method, 'update');
    assert.equal(args.issue_number, 9);
    assert.equal(args.state, 'open');
    assert.match(args.body, /exercise: failure/);
    assert.equal(f.calls.filter(([action]) => action === 'create').length, 0);
  });
}

test('pagination requests closed issues too and ignores pull requests', async () => {
  const f = fixture([{number: 10, body: '<!-- tuik-live-contracts:v1 -->', pull_request: {}}]);
  await report({...f, outcomes: {install: 'failure'}});
  assert.equal(f.calls[0][1].state, 'all');
  assert.equal(f.calls.at(-1)[0], 'create');
});

test('a changed label does not create a duplicate and custom labels are preserved', async () => {
  const f = fixture([{number: 9, body: '<!-- tuik-live-contracts:v1 -->',
    labels: [{name: 'triage'}, 'bug']}]);
  await report({...f, outcomes: {pytest: 'failure'}});
  assert.equal(f.calls[0][1].labels, undefined);
  const [method, args] = f.calls.at(-1);
  assert.equal(method, 'update');
  assert.equal(args.issue_number, 9);
  assert.deepEqual(args.labels, ['triage', 'bug', 'area: http']);
});

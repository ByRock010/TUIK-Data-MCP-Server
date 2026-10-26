const test = require('node:test');
const assert = require('node:assert/strict');
const branchPolicy = require('./branch-policy.cjs');

const repository = 'ByRock010/TUIK-Data-MCP-Server';
function check(head, base, headRepository = repository) {
  return branchPolicy({repository, pull: {
    head: {ref: head, repo: {full_name: headRepository}},
    base: {ref: base, repo: {full_name: repository}}
  }});
}

test('only the canonical develop branch can promote to master', () => {
  assert.equal(check('develop', 'master'), 'develop -> master');
  assert.equal(check('develop', 'master', repository.toLowerCase()), 'develop -> master');
  for (const head of ['feature/15-crawler', 'release/0.2.0', 'hotfix/0.1.1', 'master']) {
    assert.throws(() => check(head, 'master'), /only from/);
  }
  assert.throws(() => check('develop', 'master', 'someone/fork'), /only from/);
});

test('topic branches target develop, including contributions from forks', () => {
  for (const prefix of ['feature', 'fix', 'bugfix', 'hotfix', 'docs', 'chore',
    'refactor', 'test', 'perf', 'build', 'ci', 'revert']) {
    assert.equal(check(`${prefix}/15-change`, 'develop'), `${prefix}/15-change -> develop`);
  }
  assert.equal(check('docs/readme', 'develop', 'someone/fork'), 'docs/readme -> develop');
});

test('long-lived branches and malformed topic names cannot merge into develop', () => {
  for (const head of ['master', 'develop', 'release/0.2.0', 'feature/',
    'feature/15/nested', 'feature/has space', 'misc/change']) {
    assert.throws(() => check(head, 'develop'), /topic branch/);
  }
});

test('unknown targets and incomplete metadata fail closed', () => {
  assert.throws(() => check('feature/15-change', 'other'), /Unsupported target/);
  assert.throws(() => check('develop', 'master', null), /metadata/);
  assert.throws(() => branchPolicy({repository, pull: null}), /metadata/);
  assert.throws(() => branchPolicy({repository: 'someone/other', pull: {
    base: {ref: 'master', repo: {full_name: repository}},
    head: {ref: 'develop', repo: {full_name: repository}}
  }}), /metadata/);
});

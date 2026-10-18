const test = require('node:test');
const assert = require('node:assert/strict');
const closeIssues = require('./close-develop-issues.cjs');
const {issueNumbers} = closeIssues;
const repo = {owner: 'ByRock010', repo: 'TUIK-Data-MCP-Server'};

test('parses supported closing keywords, lists, punctuation and duplicates', () => {
  const body = 'Closes #3\nfixes: #4\n- RESOLVES #12.\nClosed #26, fixes #3; #4\nclose #7\nfixed #8\nresolve #9\nresolved #10';
  assert.deepEqual(issueNumbers(body, repo), [3, 4, 12, 26, 7, 8, 9, 10]);
});

test('qualified references must belong to this exact repository', () => {
  const body = [
    'Closes byrock010/tuik-data-mcp-server#3',
    'Fixes https://github.com/ByRock010/TUIK-Data-MCP-Server/issues/4',
    'Closes other/repo#5',
    'Closes ByRock010/other#6',
    'Closes https://github.com/other/repo/issues/7'
  ].join('\n');
  assert.deepEqual(issueNumbers(body, repo), [3, 4]);
});

test('references, prose, titles and malformed issue numbers never close issues', () => {
  const body = 'Refs #28\nRelated to #3\nfeat: parser (#3)\nThis does not close #4\nCloses #0\nCloses #9007199254740993\nCloses #5 when tests pass\nCloses #6-extra';
  assert.deepEqual(issueNumbers(body, repo), []);
  assert.deepEqual(issueNumbers(null, repo), []);
});

test('ignores examples in Markdown, HTML, quotes and indented code', () => {
  const body = [
    '<!--\nCloses #1\n-->',
    '```text\nCloses #2\n```',
    '~~~~\nFixes #3\n~~~\nCloses #4\n~~~~',
    '> Closes #5', '`Closes #6`', '    Closes #7', '\tCloses #8',
    '<pre>\nCloses #9\n</pre>', '<code>Closes #10</code>',
    'Closes #11'
  ].join('\n');
  assert.deepEqual(issueNumbers(body, repo), [11]);
});

function fixture() {
  const calls = [];
  const pulls = [{number: 31}];
  const pull = {number: 31, merged: true, base: {ref: 'develop', repo: {
    full_name: `${repo.owner}/${repo.repo}`
  }}, merge_commit_sha: 'merged-sha', body: 'Closes #3\nCloses #4\nRefs #28'};
  const issues = new Map([
    [3, {state: 'open'}], [4, {state: 'closed'}], [28, {state: 'open'}]
  ]);
  const github = {
    paginate: async (_, args) => {calls.push(['associated', args]); return pulls;},
    rest: {
      repos: {listPullRequestsAssociatedWithCommit: () => {}},
      pulls: {get: async args => {calls.push(['pull', args]); return {data: pull};}},
      issues: {
        get: async args => {
          calls.push(['issue', args]);
          if (!issues.has(args.issue_number)) throw Object.assign(new Error('missing'), {status: 404});
          return {data: issues.get(args.issue_number)};
        },
        update: async args => {
          calls.push(['close', args]);
          issues.get(args.issue_number).state = args.state;
        }
      }
    }
  };
  const context = {eventName: 'push', ref: 'refs/heads/develop', sha: 'merged-sha', repo,
    payload: {created: false, deleted: false, forced: false}};
  const core = {
    info: message => calls.push(['info', message]),
    warning: message => calls.push(['warning', message])
  };
  return {calls, pulls, pull, issues, github, context, core};
}

test('a real develop merge closes only explicit open issues, as completed', async () => {
  const f = fixture();
  assert.deepEqual(await closeIssues(f), [3]);
  assert.deepEqual(f.calls.filter(([kind]) => kind === 'close'), [
    ['close', {...repo, issue_number: 3, state: 'closed', state_reason: 'completed'}]
  ]);
  assert.equal(f.issues.get(28).state, 'open');
  assert.equal(f.calls[0][1].commit_sha, 'merged-sha');
});

test('retries are idempotent and already-closed issues are not changed', async () => {
  const f = fixture();
  await closeIssues(f);
  assert.deepEqual(await closeIssues(f), []);
  assert.equal(f.calls.filter(([kind]) => kind === 'close').length, 1);
});

for (const condition of ['master', 'created', 'deleted', 'forced', 'pull_request']) {
  test(`ignores ${condition} events before any GitHub call`, async () => {
    const f = fixture();
    if (condition === 'master') f.context.ref = 'refs/heads/master';
    else if (condition === 'pull_request') f.context.eventName = condition;
    else f.context.payload[condition] = true;
    assert.deepEqual(await closeIssues(f), []);
    assert.deepEqual(f.calls, []);
  });
}

test('a direct push without an associated merged PR does not close anything', async () => {
  const f = fixture();
  f.pulls.length = 0;
  assert.deepEqual(await closeIssues(f), []);
  assert.equal(f.calls.length, 1);
});

for (const mismatch of ['not-merged', 'wrong-base', 'wrong-sha', 'wrong-repo']) {
  test(`rejects a PR with ${mismatch}`, async () => {
    const f = fixture();
    if (mismatch === 'not-merged') f.pull.merged = false;
    if (mismatch === 'wrong-base') f.pull.base.ref = 'master';
    if (mismatch === 'wrong-sha') f.pull.merge_commit_sha = 'different-sha';
    if (mismatch === 'wrong-repo') f.pull.base.repo.full_name = 'other/repo';
    assert.deepEqual(await closeIssues(f), []);
    assert.equal(f.calls.some(([kind]) => kind === 'issue'), false);
  });
}

test('issue references pointing at PRs or missing issues are skipped', async () => {
  const f = fixture();
  f.pull.body = 'Closes #30\nCloses #999\nCloses #3';
  f.issues.set(30, {state: 'open', pull_request: {}});
  assert.deepEqual(await closeIssues(f), [3]);
  assert.equal(f.calls.filter(([kind]) => kind === 'warning').length, 1);
});

test('permission failures propagate instead of reporting success', async () => {
  const f = fixture();
  f.github.rest.issues.update = async () => {throw Object.assign(new Error('forbidden'), {status: 403});};
  await assert.rejects(closeIssues(f), /forbidden/);
});

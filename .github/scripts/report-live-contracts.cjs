const marker = '<!-- tuik-live-contracts:v1 -->';

module.exports = async function reportLiveContracts({github, context, outcomes}) {
  if (!Object.values(outcomes).includes('failure')) return;

  const repo = context.repo;
  const runUrl = `${context.serverUrl}/${repo.owner}/${repo.repo}/actions/runs/${context.runId}`;
  const body = [
    marker,
    'A live contract check failed. Inspect the run and its JSON/JUnit/log artifacts.',
    '',
    `Latest run: ${runUrl}`,
    `Ref: \`${context.ref}\``,
    `Commit: \`${context.sha}\``,
    '',
    ...Object.entries(outcomes).map(([step, outcome]) => `- ${step}: ${outcome || 'not run'}`),
    '',
    'For an injected failure, this is a reporting exercise, not an upstream outage.',
    'See CONTRIBUTING.md for triage. Live checks are not required PR checks.',
    'Confirm a successful recovery run before closing this issue.'
  ].join('\n');
  const issues = await github.paginate(github.rest.issues.listForRepo, {
    ...repo, state: 'all', per_page: 100
  });
  const existing = issues.find(issue => !issue.pull_request && issue.body?.includes(marker));
  if (existing) {
    const labels = [...new Set([
      ...(existing.labels || []).map(label => typeof label === 'string' ? label : label.name),
      'bug', 'area: http'
    ])];
    await github.rest.issues.update({...repo, issue_number: existing.number, body, state: 'open', labels});
  } else {
    await github.rest.issues.create({
      ...repo, title: 'bug: TÜİK live contract check failed', body,
      labels: ['bug', 'area: http']
    });
  }
};

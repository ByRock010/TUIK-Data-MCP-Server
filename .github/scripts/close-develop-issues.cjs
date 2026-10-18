// Only explicit PR-body directives close issues; branch names and commits do not.
const keyword = '(?:close[sd]?|fix(?:es|ed)?|resolve[sd]?)';
const reference = '(?:#[1-9]\\d*|[\\w.-]+/[\\w.-]+#[1-9]\\d*|https://github\\.com/[\\w.-]+/[\\w.-]+/issues/[1-9]\\d*)';
const directive = new RegExp(
  `^\\s*(?:[-*]\\s+)?${keyword}\\s*:?\\s+(${reference}(?:\\s*[,;]\\s*(?:${keyword}\\s*:?\\s+)?${reference})*)\\s*[.!]?\\s*$`,
  'i'
);

function issueNumbers(body, repo) {
  const numbers = new Set();
  let fence = null;
  const text = (body || '')
    .replace(/<!--[\s\S]*?(?:-->|$)/g, '')
    .replace(/<(pre|code)\b[^>]*>[\s\S]*?(?:<\/\1\s*>|$)/gi, '');
  for (const line of text.split(/\r?\n/)) {
    if (/^(?: {4}|\t)/.test(line)) continue;
    const delimiter = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (delimiter) {
      const token = delimiter[1];
      if (!fence) fence = token;
      else if (token[0] === fence[0] && token.length >= fence.length &&
               line.slice(delimiter[0].length).trim() === '') fence = null;
      continue;
    }
    if (fence) continue;
    const match = line.match(directive);
    if (!match) continue;
    for (const [value] of match[1].matchAll(new RegExp(reference, 'gi'))) {
      let number;
      if (value.startsWith('#')) number = Number(value.slice(1));
      else {
        const qualified = value.match(/^(?:https:\/\/github\.com\/)?([^/]+)\/([^/#]+)(?:#|\/issues\/)([1-9]\d*)$/i);
        if (!qualified || qualified[1].toLowerCase() !== repo.owner.toLowerCase() ||
            qualified[2].toLowerCase() !== repo.repo.toLowerCase()) continue;
        number = Number(qualified[3]);
      }
      if (Number.isSafeInteger(number)) numbers.add(number);
    }
  }
  return [...numbers];
}

async function closeDevelopIssues({github, context, core}) {
  const event = context.payload;
  if (context.eventName !== 'push' || context.ref !== 'refs/heads/develop' ||
      event.deleted || event.created || event.forced) return [];

  const repo = context.repo;
  const pulls = await github.paginate(github.rest.repos.listPullRequestsAssociatedWithCommit, {
    ...repo, commit_sha: context.sha, per_page: 100
  });
  const closed = [];
  for (const candidate of pulls) {
    // Re-fetch the canonical PR, then prove this exact develop push merged it.
    const {data: pull} = await github.rest.pulls.get({...repo, pull_number: candidate.number});
    if (!pull.merged || pull.base.ref !== 'develop' || pull.merge_commit_sha !== context.sha ||
        pull.base.repo.full_name.toLowerCase() !== `${repo.owner}/${repo.repo}`.toLowerCase()) continue;
    const numbers = issueNumbers(pull.body, repo);
    for (const number of numbers) {
      let issue;
      try {
        ({data: issue} = await github.rest.issues.get({...repo, issue_number: number}));
      } catch (error) {
        if (error.status !== 404) throw error;
        core.warning(`PR #${pull.number} references missing issue #${number}; skipped.`);
        continue;
      }
      if (issue.pull_request || issue.state === 'closed') continue;
      await github.rest.issues.update({
        ...repo, issue_number: number, state: 'closed', state_reason: 'completed'
      });
      core.info(`Closed issue #${number} after PR #${pull.number} merged into develop (${context.sha}).`);
      closed.push(number);
    }
  }
  return closed;
}

module.exports = closeDevelopIssues;
module.exports.issueNumbers = issueNumbers;

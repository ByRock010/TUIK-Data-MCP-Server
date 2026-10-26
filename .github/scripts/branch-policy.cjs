// Validate PR metadata only; branch names never become executable commands.
module.exports = function branchPolicy({pull, repository}) {
  if (!pull?.base?.repo?.full_name || !pull?.head?.repo?.full_name ||
      !repository || pull.base.repo.full_name.toLowerCase() !== repository.toLowerCase()) {
    throw new Error('Missing or mismatched pull request repository metadata.');
  }

  const {base, head} = pull;
  if (base.ref === 'master') {
    if (head.ref !== 'develop' ||
        head.repo.full_name.toLowerCase() !== repository.toLowerCase()) {
      throw new Error('master accepts pull requests only from this repository\'s develop branch.');
    }
  } else if (base.ref === 'develop') {
    if (!/^(feature|fix|bugfix|hotfix|docs|chore|refactor|test|perf|build|ci|revert)\/[^/\s]+$/.test(head.ref)) {
      throw new Error('develop requires a topic branch, such as feature/15-metadata-crawler or fix/18-parser.');
    }
  } else {
    throw new Error(`Unsupported target branch: ${base.ref}`);
  }

  return `${head.ref} -> ${base.ref}`;
};

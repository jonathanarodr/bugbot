# Git access to the reviewed repositories.
#
# A pull request's content is untrusted, so every git call that can touch its files runs
# with hooks disabled and content filters, textconv and fsmonitor off: a .gitattributes in
# the PR must not be able to make git execute anything on this machine. LFS smudge is
# skipped too, which also keeps a worktree from downloading large binaries.

bb_git() {
  local dir=$1
  shift
  GIT_TERMINAL_PROMPT=0 GIT_LFS_SKIP_SMUDGE=1 \
    git -C "$dir" \
      -c core.hooksPath=/dev/null \
      -c core.fsmonitor=false \
      -c diff.external= \
      -c protocol.file.allow=never \
      -c submodule.recurse=false \
      -c fetch.recurseSubmodules=false \
      "$@"
}

# owner/name from a GitHub remote URL (https, ssh, scp-like); credentials are never kept.
bb_github_slug() {
  local url=$1

  REPLY=''
  [[ $url =~ '^(https?://|ssh://)?([^@/]+@)?([^:/@]*github[^:/]*)(:[0-9]+)?[:/]([^/]+)/([^/]+)/?$' ]] || return 1
  REPLY="${match[5]}/${match[6]%.git}"
}

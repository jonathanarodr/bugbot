# Names derived from a pull request, shared by every command that touches one.
#
# The repository is part of every name. PR numbers repeat across repositories, and a flat
# "<owner>-<name>-<N>" key is ambiguous because owners and names may both contain hyphens
# (a-b/c and a/b-c would share a directory). A path separator cannot appear in either, so
# nesting is unambiguous:
#
#   paths  <owner>/<name>/pr-<N>                       under cache/, worktrees/, state/locks/
#                                                      of BUGBOT_DATA_DIR, and under BUGBOT_RETRO_DIR
#   refs   refs/bugbot/<owner>/<name>/pull/<N>/{head,base,reviewed}   inside the repository's clone
#          (reviewed: the head of the last bot review, pinned so a force-push cannot lose it)
#
# Owner and name are lowercased, since GitHub treats them case-insensitively, and the PR
# number is normalised (0262 and 262 are the same pull request).

# owner/name and number as GitHub allows them; "." and ".." would escape the data directory.
bb_pr_valid() {
  local repo=$1 pr=$2 name=${1#*/}

  [[ $repo =~ '^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+$' ]] || { REPLY="invalid repository: $repo"; return 1 }
  [[ $name != . && $name != .. ]] || { REPLY="invalid repository: $repo"; return 1 }
  [[ $pr =~ '^[0-9]+$' ]] && (( 10#$pr > 0 )) || { REPLY="invalid PR number: $pr"; return 1 }
}

# A ref component cannot start with "." nor end with "." or ".lock", nor contain "..",
# all of which a GitHub repository name may do. "+" never appears in a GitHub name, so
# escaping with it cannot make two repositories collide.
bb_ref_component() {
  local s=$1
  [[ $s == .* ]] && s="+$s"
  while [[ $s == *..* ]]; do s=${s//../.+.}; done
  [[ $s == *. || $s == *.lock ]] && s="$s+"
  REPLY=$s
}

# Sets BB_PR_NUMBER, BB_PR_PATH and BB_PR_REFS for owner/name and a PR number.
bb_pr_names() {
  local repo=$1 owner name ref_owner
  local -i pr

  bb_pr_valid "$repo" "$2" || return 1
  pr=$(( 10#$2 ))
  owner=${${repo%%/*}:l}
  name=${${repo#*/}:l}

  bb_ref_component "$owner"; ref_owner=$REPLY
  bb_ref_component "$name"

  typeset -g BB_PR_NUMBER=$pr
  typeset -g BB_PR_PATH="$owner/$name/pr-$pr"
  typeset -g BB_PR_REFS="refs/bugbot/$ref_owner/$REPLY/pull/$pr"

  git check-ref-format "$BB_PR_REFS/head" \
    || { REPLY="cannot build a valid ref for $repo#$pr"; return 1 }
}

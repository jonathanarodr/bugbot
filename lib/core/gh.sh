# GitHub access through the gh CLI, as whoever is logged in on this machine.
#
# Output is always captured before it is used: a pipeline would hand back the status of its
# last command, so a 401, a rate limit or a network error would read as an empty answer.
# Reads go through the REST endpoints, which are consistent immediately, rather than search.

bb_gh() {
  GH_NO_UPDATE_NOTIFIER=1 GH_PROMPT_DISABLED=1 gh "$@"
}

# The account gh acts as. Writes must check this right before they happen; an ambient
# GH_TOKEN or GITHUB_TOKEN silently replaces the keyring login, so it is reported.
bb_gh_identity() {
  local var login

  BB_GH_ENV_TOKEN=''
  for var in GH_TOKEN GITHUB_TOKEN; do
    [[ -n ${(P)var:-} ]] && BB_GH_ENV_TOKEN=$var
  done

  login=$(bb_gh api user --jq .login 2>&1) || { REPLY=$login; return 1 }
  REPLY=$login
}

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
  # As the App: the token was minted for it, and an installation token cannot read /user.
  if [[ -n $BB_APP_ACTIVE ]]; then
    REPLY="${BUGBOT_APP_SLUG}[bot]"
    return 0
  fi
  for var in GH_TOKEN GITHUB_TOKEN; do
    [[ -n ${(P)var:-} ]] && BB_GH_ENV_TOKEN=$var
  done

  login=$(bb_gh api user --jq .login 2>&1) || { REPLY=$login; return 1 }
  REPLY=$login
}

# ── GitHub App (github.app) ──────────────────────────────────────────────────────────────────
# Repositories of github.app.owner are read and written as the App, never as the person: an
# installation token scoped to the repository goes into GH_TOKEN, and GH_CONFIG_DIR points at an
# empty directory so a failed mint can never fall back to the stored gh login. Repositories of
# other owners keep using the gh login.

typeset -g BB_APP_ACTIVE=''

# True when owner/name belongs to the configured App's owner.
bb_app_owns() {
  local repo=$1
  (( ${BUGBOT_APP_ID:-0} > 0 )) && [[ -n ${BUGBOT_APP_OWNER:-} && ${(L)repo%%/*} == ${(L)BUGBOT_APP_OWNER} ]]
}

# Installation token for one or more repositories of the owner, on stdout.
bb_app_token() {
  local -a repos=()
  local repo
  for repo in "$@"; do repos+=(--repo "$repo"); done
  python3 "$BUGBOT_HOME/lib/core/app.py" token --owner "$BUGBOT_APP_OWNER" --app-id "$BUGBOT_APP_ID" \
    --installation-id "$BUGBOT_APP_INSTALLATION_ID" --keychain-service "$BUGBOT_APP_KEYCHAIN_SERVICE" \
    --slug "$BUGBOT_APP_SLUG" --cache-dir "$BUGBOT_DATA_DIR/state/app" "${repos[@]}"
}

# The isolated gh config directory: empty, so gh has no login to fall back to.
bb_app_gh_dir() {
  REPLY=$BUGBOT_DATA_DIR/state/app/gh
  mkdir -p -m 700 "$REPLY" && chmod 700 "$REPLY"
}

# For a command about one repository: when the App owns it, every gh call from here on (this
# shell, its subshells and the Python it runs) acts as the App, and review.author becomes the
# App's bot login. Fails, rather than falling back to the person's login, when no token is minted.
bb_app_use() {
  local repo=$1 tok
  bb_app_owns "$repo" || return 0
  tok=$(bb_app_token "$repo") || return 1
  [[ -n $tok ]] || { print -u2 "bugbot: GitHub App: no token for $repo"; return 1 }
  bb_app_gh_dir || return 1
  unset GITHUB_TOKEN
  export GH_TOKEN=$tok GH_CONFIG_DIR=$REPLY GH_HOST=github.com
  typeset -g BB_APP_ACTIVE=1
  typeset -g BUGBOT_REVIEW_AUTHOR="${BUGBOT_APP_SLUG}[bot]"
}

# gh for one repository, as the App when it owns it, without changing the environment of the
# caller: for commands that walk several repositories (clean).
bb_gh_as() {
  local repo=$1 tok
  shift
  if bb_app_owns "$repo"; then
    tok=$(bb_app_token "$repo") || return 1
    bb_app_gh_dir || return 1
    GITHUB_TOKEN= GH_TOKEN=$tok GH_CONFIG_DIR=$REPLY GH_HOST=github.com bb_gh "$@"
  else
    bb_gh "$@"
  fi
}

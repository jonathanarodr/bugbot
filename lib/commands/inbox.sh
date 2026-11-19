# bugbot inbox — the open PRs whose review was requested from you, filtered by the hooks, to pick
# from and send straight to fetch + triage.
#
#   search (one GraphQL query, built from the hooks) → per-PR hooks → selector → fetch → triage
#
# "Requested from you" is GitHub's review-requested:@me: by name, or through any team you are in
# (CODEOWNERS or a manual request). Your own PRs never show. Hooks that GitHub search can express
# (draft, labels.skip, labels.trigger, stale_days as an updated: bound) go into the query; the rest
# (stale_days exactly, exclude_teams, reviewers.max, ci with required checks)
# run on each result with triage's own code. A PR whose current head already has a decision is
# hidden until new commits arrive. Writes inbox.json to the run directory (schemas/inbox.schema.json).

bb_inbox_usage() {
  print -u2 "usage: bugbot inbox [options]

List the open PRs whose review was requested from you (by name or through
your teams) in the workspace's repositories, filtered by the hooks, pick
some, and run 'bugbot fetch' and 'bugbot triage' on each.

Hooks used in the search: draft, labels.skip, labels.trigger, stale_days.
Hooks applied to each PR: stale_days, exclude_teams, reviewers.max (repo
overrides too) and ci, with the PR's required checks, like triage.
A PR whose current head already has a triage decision is hidden.

Options:
  --list         print the list and stop (no selector); the default without a terminal
  --hidden       also show what the hooks hid, and why
  --json         print inbox.json to stdout and stop
  --query        print the GitHub search string and stop
  --no-color     no colors
  -h, --help     this help"
}

bb_inbox_step_search() {
  python3 "$BUGBOT_HOME/lib/inbox/inbox.py" list --hooks "$BB_RUN_DIR/hooks.json" \
    --repos "$BB_RUN_DIR/repos.tsv" --home "$BUGBOT_HOME" --data-dir "$BUGBOT_DATA_DIR" \
    --out-dir "$BB_RUN_DIR" > "$BB_RUN_DIR/inbox.json" || return 1
  python3 "$BUGBOT_HOME/lib/inbox/inbox.py" rows --file "$BB_RUN_DIR/inbox.json" > "$BB_RUN_DIR/rows.tsv" || return 1
  local -a s=("${(@ps:\t:)$(tail -n 1 "$BB_RUN_DIR/rows.tsv")}")
  bb_note "${s[2]} requested · ${s[3]} to review · ${s[4]} hidden"
}

# Hint after the PR number: days since the last activity, reviewers so far, CI, title.
#   3d · Rev. 0/3 · CI FAIL · Add retry to the upload screen
bb_inbox_hint() {
  local -a f=("$@")
  local ci
  case ${f[6]} in
    pass)    ci='CI PASS' ;;
    fail)    ci='CI FAIL' ;;
    pending) ci='CI RUNNING' ;;
    none)    ci='CI NOT REQUIRED' ;;
    *)       ci='CI UNREAD' ;;
  esac
  REPLY="${f[11]}d · Rev. ${f[8]} · $ci · ${f[9]}"
}

# PRs per repository, in the order the rows come (already grouped by inbox.py).
bb_inbox_counts() {
  local line
  local -a f
  typeset -gA BB_INBOX_COUNT=()
  for line in $rows; do
    f=("${(@ps:\t:)line}")
    [[ ${f[1]} == items ]] && (( BB_INBOX_COUNT[${f[2]}]++ ))
  done
}

bb_cmd_inbox() {
  local mode=select hidden=false color=true line pr_ref
  local -a f picked rows
  local -i i n failed=0

  while (( $# )); do
    case $1 in
      --list)     mode=list; shift ;;
      --json)     mode=json; shift ;;
      --query)    mode=query; shift ;;
      --hidden)   hidden=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_inbox_usage; return 0 ;;
      *)          print -u2 "bugbot inbox: unknown option: $1"; bb_inbox_usage; return 2 ;;
    esac
  done
  [[ $mode == select ]] && ! bb_is_tty && mode=list

  bb_term_init $color
  bb_run_dir_new inbox || { print -u2 "bugbot inbox: cannot create the run directory"; return 1 }
  bb_hooks_json > "$BB_RUN_DIR/hooks.json" || { print -u2 "bugbot inbox: failed to read hooks"; return 1 }
  python3 "$BUGBOT_HOME/lib/core/workspace.py" list --current "$BUGBOT_WORKSPACE_FILE" > "$BB_RUN_DIR/repos.tsv" \
    || { print -u2 "bugbot inbox: no workspace — run 'bugbot setup'"; return 1 }

  if [[ $mode == query ]]; then
    python3 "$BUGBOT_HOME/lib/inbox/inbox.py" query --hooks "$BB_RUN_DIR/hooks.json" --repos "$BB_RUN_DIR/repos.tsv"
    return
  fi

  if [[ $mode == json ]]; then
    bb_inbox_step_search >/dev/null 2>"$BB_RUN_DIR/search.err" \
      || { print -u2 "bugbot inbox: $(<"$BB_RUN_DIR/search.err")"; return 1 }
    cat "$BB_RUN_DIR/inbox.json"
    return 0
  fi

  bb_say_open "bugbot inbox"
  bb_cursor_hide
  bb_board_init "Search requested reviews"
  bb_run_step 1 search bb_inbox_step_search || {
    bb_board_freeze; bb_cursor_show
    bb_log_tail "$BB_RUN_DIR/search.log"
    bb_say_close "${c_red}search failed${c_reset} · $REPLY"
    return 1
  }
  bb_board_freeze
  bb_cursor_show

  rows=("${(@f)$(<"$BB_RUN_DIR/rows.tsv")}")
  local -a summary=("${(@ps:\t:)rows[-1]}")
  rows=("${(@)rows[1,-2]}")

  if [[ $hidden == true ]] && (( summary[4] )); then
    bb_say_bar
    bb_say_line "${c_dim}hidden by hooks or an earlier decision:${c_reset}"
    for line in $rows; do
      f=("${(@ps:\t:)line}")
      [[ ${f[1]} == hidden ]] || continue
      bb_say_line "  ${c_dim}${f[2]#*/}#${f[3]}  ${f[9]}${c_reset}"
    done
  fi

  if (( summary[3] == 0 )); then
    bb_say_close "${c_green}nothing to review${c_reset} · ${summary[2]} requested, ${summary[4]} hidden$([[ $hidden == false ]] && (( summary[4] )) && print -n ' (--hidden shows why)')"
    return 0
  fi

  bb_inbox_counts
  local group=''

  if [[ $mode == list ]]; then
    for line in $rows; do
      f=("${(@ps:\t:)line}")
      [[ ${f[1]} == items ]] || continue
      if [[ ${f[2]} != $group ]]; then
        group=${f[2]}
        bb_say_bar
        bb_say_line "${c_bold}${group#*/}${c_reset}  ${c_dim}${BB_INBOX_COUNT[$group]} PR(s)${c_reset}"
      fi
      bb_inbox_hint "${f[@]}"
      bb_say_line "  #${(r:6:)f[3]} ${c_dim}${REPLY}${c_reset}"
    done
    bb_say_close "${summary[3]} to review · ${summary[4]} hidden$([[ $hidden == false ]] && (( summary[4] )) && print -n ' (--hidden shows why)')"
    return 0
  fi

  bb_select_reset
  for line in $rows; do
    f=("${(@ps:\t:)line}")
    [[ ${f[1]} == items ]] || continue
    if [[ ${f[2]} != $group ]]; then
      group=${f[2]}
      bb_select_add_header "$group" "${BB_INBOX_COUNT[$group]} PR(s)"
    fi
    bb_inbox_hint "${f[@]}"
    bb_select_add "#${f[3]}" "$REPLY" 0 1 "${f[2]}#${f[3]}"
  done

  bb_say_bar
  bb_say_note "repositories by most recent activity · → shows the PRs · / searches every PR"
  bb_select_run "PRs to review" || { bb_say_close "${c_dim}nothing selected${c_reset}"; return 0 }
  for (( i = 1; i <= ${#BB_SEL_ON}; i++ )); do
    (( BB_SEL_ON[i] )) && picked+=("${BB_SEL_OPTS[i]}")
  done
  (( ${#picked} )) || { bb_say_close "${c_dim}nothing selected${c_reset}"; return 0 }
  bb_say_close "${#picked} selected"

  # Each PR goes through the same commands a person would type, one after the other, so the
  # cards and decisions are exactly what fetch and triage produce on their own.
  n=0
  for pr_ref in $picked; do
    (( ++n ))
    print -u2 ''
    print -u2 -- "${c_dim}[$n/${#picked}]${c_reset} ${c_bold}$pr_ref${c_reset}"
    "$BUGBOT_HOME/bin/bugbot" fetch "$pr_ref" $([[ $color == false ]] && print -- --no-color) || { (( ++failed )); continue }
    "$BUGBOT_HOME/bin/bugbot" triage "$pr_ref" $([[ $color == false ]] && print -- --no-color) || (( ++failed ))
  done
  (( failed )) && return 1
  return 0
}

# bugbot fetch — bring a pull request to this machine for review.
#
#   resolve → pr → refs → diff → [worktree]
#
# Writes .bugbot/cache/<owner>/<name>/pr-<N>/ (diff.patch, files.txt, meta.json) and, with
# --worktree, a detached checkout at .bugbot/worktrees/<owner>/<name>/pr-<N>/. The repository's
# clone only gains refs under refs/bugbot/; its branch and working tree are never touched.

bb_fetch_usage() {
  print -u2 "usage: bugbot fetch <pr> [options]

<pr> is the PR URL, owner/name#N, or the number with --repo.
The repository must be in workspace.json (see 'bugbot setup').

Writes diff.patch, files.txt and meta.json to .bugbot/cache/<owner>/<name>/pr-<N>/.
The repository's clone only gains refs under refs/bugbot/; its branch and files never change.

Options:
  --repo <owner/name>  repository of the PR, when <pr> is just the number
  --worktree           also prepare a checkout of the PR in .bugbot/worktrees/
  --plan               show what would be done, fetch nothing
  --json               print meta.json to stdout at the end
  --no-color           no colors
  -h, --help           this help

Examples:
  bugbot fetch https://github.com/acme/web/pull/123
  bugbot fetch 123 --repo acme/web --worktree"
}

bb_cmd_fetch() {
  local target='' repo='' worktree=false plan=false json=false color=true
  local lock

  while (( $# )); do
    case $1 in
      --repo)     [[ -n ${2:-} ]] || { bb_fetch_usage; return 2 }; repo=$2; shift 2 ;;
      --worktree) worktree=true; shift ;;
      --plan)     plan=true; shift ;;
      --json)     json=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_fetch_usage; return 0 ;;
      -*)         print -u2 "bugbot fetch: unknown option: $1"; bb_fetch_usage; return 2 ;;
      *)          [[ -z $target ]] || { print -u2 "bugbot fetch: give a single PR"; return 2 }
                  target=$1; shift ;;
    esac
  done

  [[ -n $target ]] || { bb_fetch_usage; return 2 }
  bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot fetch: $REPLY"; return 2 }
  bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot fetch: $REPLY"; return 2 }

  typeset -g BB_FETCH_REPO=${reply[1]} BB_FETCH_PR=$BB_PR_NUMBER
  typeset -g BB_FETCH_DIR=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH
  typeset -g BB_FETCH_HEAD_REF=$BB_PR_REFS/head
  typeset -g BB_FETCH_BASE_REF=$BB_PR_REFS/base
  typeset -g BB_FETCH_WORKTREE_PATH=''
  [[ $worktree == true ]] && BB_FETCH_WORKTREE_PATH=$BUGBOT_DATA_DIR/worktrees/$BB_PR_PATH
  lock=$BUGBOT_DATA_DIR/state/locks/$BB_PR_PATH

  bb_term_init $color
  bb_run_dir_new fetch || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }

  bb_say_open "bugbot fetch"
  bb_say_bar
  bb_say_kv pr "$BB_FETCH_REPO#$BB_FETCH_PR"
  bb_tilde "$BB_FETCH_DIR"
  bb_say_kv cache "$REPLY"
  if [[ -n $BB_FETCH_WORKTREE_PATH ]]; then
    bb_tilde "$BB_FETCH_WORKTREE_PATH"
    bb_say_kv worktree "$REPLY"
  fi
  bb_say_bar

  if [[ $plan == true ]]; then
    bb_say_line "1. look up the clone of $BB_FETCH_REPO in workspace.json"
    bb_say_line "2. read the PR with gh (read-only)"
    bb_say_line "3. git fetch the base and pull/$BB_FETCH_PR/head into $BB_PR_REFS/"
    bb_say_line "4. write diff.patch, files.txt and meta.json to the cache"
    [[ $worktree == true ]] && bb_say_line "5. create or update the worktree (detached checkout of the head)"
    bb_say_bar
    bb_say_close "${c_dim}nothing fetched${c_reset}"
    return 0
  fi

  if ! bb_lock "$lock"; then
    bb_say_close "${c_red}$BB_FETCH_REPO#$BB_FETCH_PR is in use by another process${c_reset} (pid $REPLY)"
    return 1
  fi

  local -a labels=("Resolve repository" "Read pull request" "Fetch refs" "Write diff and metadata")
  [[ $worktree == true ]] && labels+=("Prepare worktree")

  bb_cursor_hide
  bb_board_init "${labels[@]}"
  bb_run_step 1 resolve bb_fetch_step_resolve || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_run_step 2 pr      bb_fetch_step_pr      || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_run_step 3 refs    bb_fetch_step_refs    || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_run_step 4 diff    bb_fetch_step_diff    || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  if [[ $worktree == true ]]; then
    bb_run_step 5 worktree bb_fetch_step_worktree || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  fi
  bb_board_freeze
  bb_cursor_show
  bb_unlock "$lock"

  local -a p=("${(@0)$(<"$BB_RUN_DIR/pr")}")
  bb_say_bar
  [[ ${p[2]} != OPEN ]] && bb_say_warn "the PR is ${p[2]:l}"
  [[ ${p[3]} == true ]] && bb_say_warn "the PR is a draft"
  [[ ${p[7]} == true ]] && bb_say_note "PR from a fork (${p[8]})"
  bb_tilde "$BB_FETCH_DIR/meta.json"
  bb_say_close "${c_green}done${c_reset} · $REPLY"

  [[ $json == true ]] && cat "$BB_FETCH_DIR/meta.json"
  return 0
}

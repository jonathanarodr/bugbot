# bugbot clean — remove what belongs to merged pull requests, or to one named PR.
#
#   inventory → state of each PR on GitHub → list → confirm → remove
#
# Without a PR, a PR is removed only when GitHub reports it merged: its worktree, its refs in
# the clone, its cache and lock. Open PRs, PRs closed without merge (they can be reopened), PRs
# whose state cannot be read and PRs in use by another process are kept. Worktree
# registrations of the bot whose directory no longer exists are dropped too: they are pure
# leftovers and would make the next `fetch --worktree` fail.
#
# With a PR, naming it is the decision: that PR is removed whatever its state (still showing
# the state, so an open one is not removed by accident), and nothing else is touched.
# For everything at once, see `bugbot purge`. The retro record is never touched here.

bb_clean_usage() {
  print -u2 "usage: bugbot clean [<pr>] [options]

Without <pr>: remove what belongs to merged PRs (worktree, refs in the clone,
cache and lock). Open PRs, PRs closed without merge, PRs whose state cannot be
read and PRs in use are kept.

With <pr>: remove that PR only, whatever its state. <pr> is the PR URL,
owner/name#N, or the number with --repo.

The retro record is never touched. To delete everything the bot created, use
'bugbot purge'.

Options:
  --repo <owner/name>  repository of the PR, when <pr> is just the number
  --plan               show what would be removed, remove nothing
  --yes                remove without asking for confirmation
  --no-color           no colors
  -h, --help           this help"
}

bb_clean_step_inventory() {
  bb_clean_inventory_run || return 1
  bb_clean_parse || return 1
  bb_note "${#BB_CL_PR} PR(s) · ${#BB_CW_PATH} registered worktree(s) · ${#BB_CC_CLONE} clone(s) with refs"
}

bb_clean_step_states() {
  local res
  local -i i bad=0

  : > "$BB_RUN_DIR/states"
  for (( i = 1; i <= ${#BB_CL_PR}; i++ )); do
    if res=$(bb_gh_as "${BB_CL_SHOW[i]}" api "repos/${BB_CL_SHOW[i]}/pulls/${BB_CL_PR[i]}" \
               --jq '[.state, (.merged | tostring)] | @tsv' 2>&1); then
      print -r -- "$i"$'\t'"$res" >> "$BB_RUN_DIR/states"
    else
      print -r -- "$i"$'\t'"error"$'\t'"false" >> "$BB_RUN_DIR/states"
      print -r -- "${BB_CL_SHOW[i]}#${BB_CL_PR[i]}: ${res//$'\n'/ }"
      (( ++bad ))
    fi
  done

  REPLY="${#BB_CL_PR} checked"
  (( bad )) && REPLY+=" · $bad unanswered"
  bb_note "$REPLY"
  (( bad )) && return $BB_STEP_WARN
  return 0
}

bb_clean_step_remove() {
  local -i i n=0 kept=0 rc

  for i in $BB_CLEAN_GO; do
    bb_clean_remove_pr "${BB_CL_OWNER[i]}" "${BB_CL_NAME[i]}" "${BB_CL_PR[i]}" \
      "${BB_CL_CLONE[i]}" "${BB_CL_WTADM[i]}"
    rc=$?
    case $rc in
      0) (( ++n )) ;;
      3) (( ++kept )) ;;
      *) return 1 ;;
    esac
  done

  for i in $BB_CLEAN_STALE; do
    bb_clean_rm_admin "${BB_CW_CLONE[i]}" "${BB_CW_ADMIN[i]}" || return 1
    print -r -- "removed orphan registration: ${BB_CW_PATH[i]}"
  done

  REPLY="$n PR(s)"
  (( ${#BB_CLEAN_STALE} )) && REPLY+=" · ${#BB_CLEAN_STALE} orphan registration(s)"
  (( kept )) && REPLY+=" · $kept in use"
  bb_note "$REPLY"
  (( kept )) && return $BB_STEP_WARN
  return 0
}

bb_cmd_clean() {
  local plan=false yes=false color=true line why mark target='' repo='' only=''
  local -a f st
  local -A state merged
  local -i i j skip
  typeset -ga BB_CLEAN_GO=() BB_CLEAN_STALE=()

  while (( $# )); do
    case $1 in
      --repo)     [[ -n ${2:-} ]] || { bb_clean_usage; return 2 }; repo=$2; shift 2 ;;
      --plan)     plan=true; shift ;;
      --yes|-y)   yes=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_clean_usage; return 0 ;;
      -*)         print -u2 "bugbot clean: unknown option: $1"; bb_clean_usage; return 2 ;;
      *)          [[ -z $target ]] || { print -u2 "bugbot clean: give a single PR"; return 2 }
                  target=$1; shift ;;
    esac
  done

  # The target grammar and its validation are fetch's, so a PR is named the same way everywhere.
  if [[ -n $target ]]; then
    source "$BUGBOT_HOME/lib/fetch/steps.sh"
    bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot clean: $REPLY"; return 2 }
    bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot clean: $REPLY"; return 2 }
    only=$BB_PR_PATH
  elif [[ -n $repo ]]; then
    print -u2 "bugbot clean: --repo only applies with a PR number"
    return 2
  fi

  bb_term_init $color
  bb_run_dir_new clean || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }

  bb_say_open "bugbot clean"
  bb_say_bar
  if [[ -n $only ]]; then
    bb_say_kv pr "${reply[1]}#$BB_PR_NUMBER (any state)"
  else
    bb_say_kv rule "merged PRs"
  fi
  bb_say_bar

  bb_cursor_hide
  bb_board_init "Take inventory" "Check PR states"
  bb_run_step 1 inventory bb_clean_step_inventory || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_clean_parse || { bb_say_close "${c_red}failed${c_reset}"; return 1 }

  # Narrow the inventory to the named PR before asking GitHub anything.
  if [[ -n $only ]]; then
    local -i hit=0
    for (( i = 1; i <= ${#BB_CL_PR}; i++ )); do
      [[ ${BB_CL_OWNER[i]}/${BB_CL_NAME[i]}/pr-${BB_CL_PR[i]} == $only ]] && { hit=$i; break }
    done
    for f in BB_CL_OWNER BB_CL_NAME BB_CL_PR BB_CL_SHOW BB_CL_CLONE BB_CL_CACHE BB_CL_WTDIR \
             BB_CL_WTADM BB_CL_REFS BB_CL_LOCK; do
      if (( hit )); then set -A $f "${${(P)f}[hit]}"; else set -A $f; fi
    done
    BB_CW_PATH=()
  fi

  bb_run_step 2 states bb_clean_step_states || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show

  while IFS= read -r line; do
    st=("${(@ps:\t:)line}")
    state[${st[1]}]=${st[2]} merged[${st[1]}]=${st[3]}
  done < "$BB_RUN_DIR/states"

  bb_say_bar
  for (( i = 1; i <= ${#BB_CL_PR}; i++ )); do
    bb_clean_parts $i
    if [[ ${merged[$i]} == true ]]; then
      why=merged
    elif [[ ${state[$i]} == error ]]; then
      why="unreadable state"
    elif [[ ${state[$i]} == open ]]; then
      why=open
    else
      why="closed without merge"
    fi

    if (( BB_CL_LOCK[i] == 2 )); then
      bb_say_line "${c_dim}= ${BB_CL_SHOW[i]}#${BB_CL_PR[i]}  in use · $REPLY${c_reset}"
    elif [[ -n $only || $why == merged ]]; then
      BB_CLEAN_GO+=($i)
      mark="${c_red}-${c_reset}"
      [[ -n $only && $why != merged ]] && why="${c_yellow}$why${c_reset}${c_dim}"
      bb_say_line "$mark ${BB_CL_SHOW[i]}#${BB_CL_PR[i]}  ${c_dim}$why · $REPLY${c_reset}"
    else
      bb_say_line "${c_dim}= ${BB_CL_SHOW[i]}#${BB_CL_PR[i]}  $why · $REPLY${c_reset}"
    fi
  done
  if [[ -n $only ]] && (( ! ${#BB_CL_PR} )); then
    bb_say_note "nothing from the bot found for this PR"
  fi

  # Registrations whose directory is gone, unless the PR above already takes care of it.
  for (( i = 1; i <= ${#BB_CW_PATH}; i++ )); do
    (( BB_CW_EXISTS[i] )) && continue
    skip=0
    for j in $BB_CLEAN_GO; do [[ ${BB_CL_WTADM[j]} == ${BB_CW_ADMIN[i]} ]] && skip=1; done
    (( skip )) && continue
    BB_CLEAN_STALE+=($i)
    bb_tilde "${BB_CW_PATH[i]}"
    bb_say_line "${c_red}-${c_reset} worktree registration without a directory  ${c_dim}$REPLY${c_reset}"
  done
  if [[ -z $only ]] && (( ! ${#BB_CL_PR} + ${#BB_CLEAN_STALE} )); then
    bb_say_note "nothing from the bot found"
  fi
  bb_say_bar

  if (( ! ${#BB_CLEAN_GO} && ! ${#BB_CLEAN_STALE} )); then
    bb_say_close "${c_dim}nothing to remove${c_reset}"
    return 0
  fi

  REPLY="${#BB_CLEAN_GO} PR(s)${BB_CLEAN_STALE:+ and ${#BB_CLEAN_STALE} orphan registration(s)}"
  if [[ $plan == true ]]; then
    bb_say_close "${c_dim}$REPLY would be removed · nothing removed${c_reset}"
    return 0
  fi
  if [[ $yes != true ]]; then
    bb_is_tty || { bb_say_close "${c_red}confirmation needs a terminal${c_reset} — use --yes"; return 2 }
    bb_confirm "Remove $REPLY?" || { bb_say_close "${c_dim}nothing removed${c_reset}"; return 1 }
  fi

  bb_cursor_hide
  bb_board_init "Remove"
  bb_run_step 1 remove bb_clean_step_remove || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show
  bb_say_close "${c_green}clean${c_reset}"
}

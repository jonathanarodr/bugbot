# bugbot purge — remove everything the bot created, whatever the state of each PR.
#
# The force version of clean, for a reset: every worktree of the bot (and its registration
# in the clone), every ref under refs/bugbot/ in every known clone, and the data directory
# (cache, worktrees, state, run logs).
#
# The retro record (BUGBOT_RETRO_DIR) is not cache: it is the permanent record of review
# decisions that `bugbot retro` reads. It is kept unless --retro is given, and deleting it
# asks for its own confirmation. workspace.json and the settings files are configuration
# and are always kept.
#
# Asks for confirmation by typing "purge"; --yes skips it, except for --retro, which also
# needs --yes-retro. A PR in use by another process stops the purge before anything is removed.

source "$BUGBOT_HOME/lib/clean/steps.sh"

bb_purge_usage() {
  print -u2 "usage: bugbot purge [options]

Delete everything the bot created, whatever the state of each PR:
worktrees (and their registration in the clones), refs under refs/bugbot/ in
every known clone, and the data directory (cache, worktrees, state and logs).

The retro record (permanent record of review decisions) is kept, and so are
workspace.json, bugbot.json and repos/.

Options:
  --retro       also delete the retro record (asks for its own confirmation)
  --plan        show what would be deleted, delete nothing
  --yes         delete without asking (with --retro, --yes-retro is also needed)
  --yes-retro   confirm deleting the retro record without asking
  --no-color    no colors
  -h, --help    this help"
}

bb_purge_step_inventory() {
  bb_clean_inventory_run || return 1
  bb_clean_parse || return 1
  bb_note "${#BB_CL_PR} PR(s) · ${#BB_CW_PATH} registered worktree(s) · ${#BB_CC_CLONE} clone(s) with refs"
}

bb_purge_step_remove() {
  local clone ref entry
  local -a refs
  local -i i nrefs=0 nwt=0

  for (( i = 1; i <= ${#BB_CW_PATH}; i++ )); do
    if (( BB_CW_EXISTS[i] )); then
      bb_git "${BB_CW_CLONE[i]}" worktree remove --force "${BB_CW_PATH[i]}" || return 1
    else
      bb_clean_rm_admin "${BB_CW_CLONE[i]}" "${BB_CW_ADMIN[i]}" || return 1
    fi
    (( ++nwt ))
  done

  for clone in "${BB_CC_CLONE[@]}"; do
    refs=(${(f)"$(bb_git "$clone" for-each-ref --format='%(refname)' refs/bugbot/)"})
    for ref in $refs; do
      bb_git "$clone" update-ref -d "$ref" || return 1
      (( ++nrefs ))
    done
  done

  for entry in "$BUGBOT_DATA_DIR"/{cache,worktrees,state}(N); do
    bb_clean_rm "$entry" || return 1
  done

  # Previous runs only: this run's log directory is still being written.
  for entry in "$BUGBOT_DATA_DIR"/runs/*(N/); do
    [[ $entry == $BB_RUN_DIR ]] || { bb_clean_rm "$entry" || return 1 }
  done

  if [[ $BB_PURGE_RETRO == true ]]; then
    for entry in "$BUGBOT_RETRO_DIR"/*(DN); do
      bb_clean_rm "$entry" "$BUGBOT_RETRO_DIR" || return 1
    done
    print -r -- "retro deleted: $BUGBOT_RETRO_DIR"
  fi

  mkdir -p "$BUGBOT_DATA_DIR"/{cache,worktrees,state} || return 1
  REPLY="$nwt worktree(s) · $nrefs ref(s) · data directory reset"
  [[ $BB_PURGE_RETRO == true ]] && REPLY+=" · retro deleted"
  bb_note "$REPLY"
}

bb_cmd_purge() {
  local plan=false yes=false yes_retro=false color=true answer runs size retro_size
  local -i i busy=0 retro_files
  typeset -g BB_PURGE_RETRO=false

  while (( $# )); do
    case $1 in
      --retro)      BB_PURGE_RETRO=true; shift ;;
      --plan)       plan=true; shift ;;
      --yes|-y)     yes=true; shift ;;
      --yes-retro)  yes_retro=true; shift ;;
      --no-color)   color=false; shift ;;
      -h|--help)    bb_purge_usage; return 0 ;;
      *)            print -u2 "bugbot purge: unknown option: $1"; bb_purge_usage; return 2 ;;
    esac
  done
  if [[ $yes_retro == true && $BB_PURGE_RETRO != true ]]; then
    print -u2 "bugbot purge: --yes-retro only applies with --retro"
    return 2
  fi

  bb_term_init $color
  bb_run_dir_new purge || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }

  bb_say_open "bugbot purge"
  bb_say_bar
  bb_tilde "$BUGBOT_DATA_DIR"
  bb_say_kv data "$REPLY"
  bb_tilde "$BUGBOT_RETRO_DIR"
  bb_say_kv retro "$REPLY"
  bb_say_bar

  bb_cursor_hide
  bb_board_init "Take inventory"
  bb_run_step 1 inventory bb_purge_step_inventory || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_clean_parse || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show

  bb_say_bar
  for (( i = 1; i <= ${#BB_CL_PR}; i++ )); do
    bb_clean_parts $i
    if (( BB_CL_LOCK[i] == 2 )); then
      (( ++busy ))
      bb_say_line "${c_yellow}!${c_reset} ${BB_CL_SHOW[i]}#${BB_CL_PR[i]}  ${c_dim}in use · $REPLY${c_reset}"
    else
      bb_say_line "${c_red}-${c_reset} ${BB_CL_SHOW[i]}#${BB_CL_PR[i]}  ${c_dim}$REPLY${c_reset}"
    fi
  done
  for (( i = 1; i <= ${#BB_CC_CLONE}; i++ )); do
    bb_tilde "${BB_CC_CLONE[i]}"
    bb_say_note "  ${BB_CC_REFS[i]} ref(s) under refs/bugbot/ · $REPLY"
  done
  runs=${#${(@)$(print -l "$BUGBOT_DATA_DIR"/runs/*(N/))}}
  size=${$(du -sh "$BUGBOT_DATA_DIR" 2>/dev/null | cut -f1)//[[:space:]]/}
  bb_say_line "${c_red}-${c_reset} logs of $(( runs > 0 ? runs - 1 : 0 )) previous run(s) and state/"

  retro_files=${#${(@)$(print -l "$BUGBOT_RETRO_DIR"/**/*(DN.))}}
  retro_size=${$(du -sh "$BUGBOT_RETRO_DIR" 2>/dev/null | cut -f1)//[[:space:]]/}
  if [[ $BB_PURGE_RETRO == true ]]; then
    bb_say_line "${c_red}-${c_reset} retro: $retro_files file(s), ${retro_size:-0} ${c_yellow}(permanent record)${c_reset}"
  else
    bb_say_note "  retro kept: $retro_files file(s), ${retro_size:-0} (use --retro to delete it)"
  fi
  bb_say_note "  data directory uses ${size:-?} · workspace.json, bugbot.json and repos/ are not touched"
  bb_say_bar

  if (( busy )); then
    bb_say_close "${c_red}$busy PR(s) in use by another process${c_reset} · nothing deleted"
    return 1
  fi
  if [[ $plan == true ]]; then
    bb_say_close "${c_dim}nothing deleted${c_reset}"
    return 0
  fi

  if [[ $yes != true ]]; then
    bb_is_tty || { bb_say_close "${c_red}confirmation needs a terminal${c_reset} — use --yes"; return 2 }
    print -rn -u2 -- "${c_cyan}◆${c_reset}  This cannot be undone. Type ${c_bold}purge${c_reset} to confirm: "
    read -r answer </dev/tty
    [[ $answer == purge ]] || { bb_say_close "${c_dim}nothing deleted${c_reset}"; return 1 }
  fi

  # The retro is the one thing here that cannot be rebuilt, so it gets its own answer.
  if [[ $BB_PURGE_RETRO == true && $yes_retro != true ]]; then
    bb_is_tty || { bb_say_close "${c_red}deleting the retro record needs a terminal${c_reset} — use --yes-retro"; return 2 }
    print -rn -u2 -- "${c_cyan}◆${c_reset}  The retro record cannot be rebuilt. Type ${c_bold}retro${c_reset} to delete it too: "
    read -r answer </dev/tty
    if [[ $answer != retro ]]; then
      BB_PURGE_RETRO=false
      bb_say_note "  retro kept"
    fi
  fi

  bb_cursor_hide
  bb_board_init "Delete"
  bb_run_step 1 remove bb_purge_step_remove || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show
  bb_say_close "${c_green}bot data reset${c_reset}$([[ $BB_PURGE_RETRO == true ]] || print -n ' · retro kept')"
}

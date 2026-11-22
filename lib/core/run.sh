# Runs shell functions as board steps and owns the per-run directory.
#
# A step runs in a subshell with its output captured to <run>/<key>.log, so it hands
# results to the caller through files in BB_RUN_DIR, and a one-line headline through
# bb_note. Exit 0 passes, 10 passes with a warning, anything else fails.
#
# Steps are functions, never command strings: nothing here goes through eval.

typeset -g BB_RUN_DIR='' BB_CHILD_PID='' BB_TMP_CLEAN=''
typeset -ga BB_FRAMES=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
readonly BB_STEP_WARN=10

# Everything the bot writes lives under one directory, kept out of Spotlight and
# Time Machine: worktrees and build caches are large and fully reproducible.
bb_data_init() {
  mkdir -p "$BUGBOT_DATA_DIR"/{runs,state,cache,worktrees} "$BUGBOT_RETRO_DIR" || return 1

  if [[ ! -e $BUGBOT_DATA_DIR/.metadata_never_index ]]; then
    : > "$BUGBOT_DATA_DIR/.metadata_never_index"
    if (( $+commands[tmutil] )); then
      tmutil addexclusion "$BUGBOT_DATA_DIR" >/dev/null 2>&1
    fi
  fi

  return 0
}

bb_run_dir_new() {
  bb_data_init || return 1
  BB_RUN_DIR=$BUGBOT_DATA_DIR/runs/$(strftime %Y%m%d-%H%M%S $EPOCHSECONDS)-$1
  mkdir -p "$BB_RUN_DIR"
}

# Headline shown next to the step on the board, written from inside the step.
bb_note() {
  [[ -n ${BB_STEP_NOTE:-} ]] || return 0
  print -rn -- "$*" > "$BB_STEP_NOTE"
}

# Last non-empty line of a log, with colour sequences removed as whole sequences.
bb_log_tail() {
  setopt local_options extended_glob
  local -a rows
  local last

  REPLY=''
  [[ -s $1 ]] || return 0

  rows=(${(f)"$(tail -n 5 "$1" 2>/dev/null)"})
  last=${rows[-1]:-}
  last=${last//$'\r'/}
  last=${last//${BB_ESC}\[[0-9;?]#[a-zA-Z]/}
  REPLY=${last##[[:space:]]#}
}

bb_run_step() {
  local -i index=$1 rc frame=0
  local key=$2 fn=$3
  shift 3

  local log=$BB_RUN_DIR/$key.log note_file=$BB_RUN_DIR/$key.note
  local label=${BB_BOARD_LABELS[index]} note='' meta state line
  local -F started=$EPOCHREALTIME

  rm -f -- "$note_file"
  BB_BOARD_STATE[index]=running
  BB_BOARD_META[index]=''

  ( BB_STEP_NOTE=$note_file; $fn "$@" ) >"$log" 2>&1 &
  BB_CHILD_PID=$!

  if bb_is_tty; then
    while kill -0 $BB_CHILD_PID 2>/dev/null; do
      BB_BOARD_META[index]=$(bb_fmt_elapsed $(( EPOCHREALTIME - started )))
      bb_log_tail "$log"
      bb_board_render $index ${BB_FRAMES[frame % ${#BB_FRAMES} + 1]} "$REPLY"
      (( ++frame ))
      bb_tick
    done
  fi

  wait $BB_CHILD_PID
  rc=$?
  BB_CHILD_PID=''

  [[ -f $note_file ]] && note=$(<"$note_file")
  meta="$(bb_fmt_elapsed $(( EPOCHREALTIME - started )))${note:+ · $note}"

  case $rc in
    0)             state=passed ;;
    $BB_STEP_WARN) state=warned ;;
    *)             state=failed; meta+=" · exit $rc" ;;
  esac

  BB_BOARD_STATE[index]=$state
  BB_BOARD_META[index]=$meta
  bb_board_render 0
  bb_board_echo $state "$label" "$meta"

  [[ $state == failed ]] || return 0

  bb_board_freeze
  bb_say_bar
  tail -n 20 "$log" 2>/dev/null | tr -d '\r' | while IFS= read -r line; do
    bb_say_note "$line"
  done
  bb_say_bar
  bb_say_note "full log: $log"
  return $rc
}

bb_on_interrupt() {
  [[ -n ${BB_CHILD_PID:-} ]] && kill $BB_CHILD_PID 2>/dev/null
  bb_board_freeze
  bb_tty_restore
  (( $+functions[bb_unlock_all] )) && bb_unlock_all
  print -u2 ''
  bb_say_close "${c_yellow}interrupted${c_reset}${BB_RUN_DIR:+ · logs in $BB_RUN_DIR}"
  exit 130
}

bb_on_exit() {
  bb_tty_restore
  (( $+functions[bb_unlock_all] )) && bb_unlock_all
  [[ -n ${BB_TMP_CLEAN:-} && -d $BB_TMP_CLEAN ]] && rm -rf -- "$BB_TMP_CLEAN"
  return 0
}

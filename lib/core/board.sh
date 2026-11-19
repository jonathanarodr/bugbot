# Status board redrawn in place: every step is listed up front as pending and each row
# is rewritten as it changes state, so the whole run stays visible instead of scrolling.
#
#   ○ pending   ◒ running   ● passed   ▲ passed with a warning   ■ failed
#
# The region has a fixed height (one row per step plus the log-tail row), which is what
# makes rewinding the cursor by that many lines safe.

typeset -ga BB_BOARD_LABELS BB_BOARD_STATE BB_BOARD_META
typeset -gi BB_BOARD_LINES=0 BB_BOARD_WIDTH=0

bb_board_init() {
  local label
  local -i i

  BB_BOARD_LABELS=("$@")
  BB_BOARD_STATE=()
  BB_BOARD_META=()
  BB_BOARD_WIDTH=0
  BB_BOARD_LINES=0

  for (( i = 1; i <= $#; i++ )); do
    BB_BOARD_STATE[i]=pending
    BB_BOARD_META[i]=''
  done

  for label in "$@"; do
    (( ${#label} > BB_BOARD_WIDTH )) && BB_BOARD_WIDTH=${#label}
  done

  bb_board_render 0
}

bb_board_glyph() {
  case $1 in
    running) REPLY="${c_cyan}◒${c_reset}" ;;
    passed)  REPLY="${c_green}●${c_reset}" ;;
    warned)  REPLY="${c_yellow}▲${c_reset}" ;;
    failed)  REPLY="${c_red}■${c_reset}" ;;
    *)       REPLY="${c_dim}○${c_reset}" ;;
  esac
}

# Every line is cleared before it is written so a shrinking row leaves no residue.
bb_board_render() {
  local -i active=${1:-0} i
  local frame=${2:-} tail=${3:-} out='' meta glyph

  bb_is_tty || return 0

  (( BB_BOARD_LINES > 0 )) && out+="${BB_ESC}[${BB_BOARD_LINES}A"

  for (( i = 1; i <= ${#BB_BOARD_LABELS}; i++ )); do
    bb_board_glyph ${BB_BOARD_STATE[i]}
    glyph=$REPLY

    bb_trunc "${BB_BOARD_META[i]}" $(( BB_TERM_COLS - BB_BOARD_WIDTH - 10 ))
    meta="${c_dim}${REPLY}${c_reset}"
    (( i == active )) && [[ -n $frame ]] && meta="${c_cyan}${frame}${c_reset} ${meta}"

    out+="${BB_ESC}[2K${glyph}  ${(r:BB_BOARD_WIDTH+2:)BB_BOARD_LABELS[i]}${meta}"$'\n'
  done

  # The tail row is always emitted, blank when idle, to keep the region a fixed height.
  out+="${BB_ESC}[2K${c_dim}│${c_reset}"
  if [[ -n $tail ]]; then
    bb_trunc "$tail" $(( BB_TERM_COLS - 7 ))
    out+="  ${c_dim}↳ ${REPLY}${c_reset}"
  fi
  out+=$'\n'

  print -rn -u2 -- "$out"
  BB_BOARD_LINES=$(( ${#BB_BOARD_LABELS} + 1 ))
}

# Detaches the board so whatever is printed next scrolls below it instead of over it.
bb_board_freeze() { BB_BOARD_LINES=0 }

# Plain-text row for a non-tty stderr, printed once per finished step.
bb_board_echo() {
  local st=$1 label=$2 extra=${3:-}

  bb_is_tty && return 0
  bb_board_glyph $st
  print -r -u2 -- "${REPLY}  ${label}${extra:+  $extra}"
}

# Terminal primitives shared by every command: colour, cursor, width, quiet input.
#
# All UI goes to stderr so stdout stays free for data (--json, TSV). A non-tty stderr
# (pipe, redirect, CI) gets no colour, no cursor movement and no animation, so the
# transcript stays readable as plain text.

zmodload zsh/datetime zsh/zselect zsh/stat 2>/dev/null

typeset -g BB_ESC=$'\e'
typeset -g c_reset='' c_dim='' c_bold='' c_green='' c_red='' c_yellow='' c_cyan=''
typeset -gi BB_TERM_COLS=80 BB_TERM_ROWS=24
typeset -g BB_TTY_SAVED=''

bb_is_tty() { [[ -t 2 ]] }

bb_term_init() {
  local use_color=${1:-true}

  if [[ $use_color == true && -z ${NO_COLOR:-} ]] && bb_is_tty; then
    c_reset="${BB_ESC}[0m"
    c_dim="${BB_ESC}[2m"
    c_bold="${BB_ESC}[1m"
    c_green="${BB_ESC}[32m"
    c_red="${BB_ESC}[31m"
    c_yellow="${BB_ESC}[33m"
    c_cyan="${BB_ESC}[36m"
  fi

  bb_term_refresh
}

# Asked of the tty itself: $COLUMNS is only kept current by interactive shells.
bb_term_refresh() {
  local size rows cols
  bb_is_tty && size=$(stty size 2>/dev/null </dev/tty)
  rows=${size%% *}
  cols=${size##* }

  if [[ $cols == <-> ]] && (( cols >= 40 )); then BB_TERM_COLS=$cols; else BB_TERM_COLS=80; fi
  if [[ $rows == <-> ]] && (( rows >= 10 )); then BB_TERM_ROWS=$rows; else BB_TERM_ROWS=24; fi
}

bb_cursor_hide() { bb_is_tty && print -rn -u2 -- "${BB_ESC}[?25l"; return 0 }
bb_cursor_show() { bb_is_tty && print -rn -u2 -- "${BB_ESC}[?25h"; return 0 }

# Keystrokes typed while a prompt redraws must not echo over it.
bb_tty_quiet() {
  bb_is_tty || return 0
  [[ -n $BB_TTY_SAVED ]] && return 0
  BB_TTY_SAVED=$(stty -g </dev/tty 2>/dev/null)
  stty -echo </dev/tty 2>/dev/null
  return 0
}

bb_tty_restore() {
  if [[ -n $BB_TTY_SAVED ]]; then
    stty "$BB_TTY_SAVED" </dev/tty 2>/dev/null
    BB_TTY_SAVED=''
  fi
  bb_cursor_show
}

# Display-width aware truncation; the result is left in REPLY so a redraw does not fork.
bb_trunc() {
  local text=$1
  local -i max=$2

  (( max < 1 )) && max=1

  if (( ${(m)#text} > max )); then
    REPLY="${text[1,max-1]}…"
  else
    REPLY=$text
  fi
}

bb_tilde() { REPLY=${1/#$HOME/\~} }

bb_fmt_elapsed() {
  local -F seconds=$1
  local -i whole=${seconds%.*}

  if (( seconds < 10 )); then
    printf '%.1fs' $seconds
  elif (( whole < 60 )); then
    printf '%ds' $whole
  else
    printf '%dm%02ds' $(( whole / 60 )) $(( whole % 60 ))
  fi
}

bb_tick() { zselect -t 8 2>/dev/null || sleep 0.08 }

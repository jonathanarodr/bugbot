# Session framing: ┌ opens, │ is the gutter, └ closes with the outcome.
#
#   ●  done    ▲  warning    ■  failed    ◆  waiting for input

bb_say()       { print -r -u2 -- "$@" }
bb_say_open()  { bb_say "${c_dim}┌${c_reset}  ${c_bold}$1${c_reset}" }
bb_say_bar()   { bb_say "${c_dim}│${c_reset}" }
bb_say_line()  { bb_say "${c_dim}│${c_reset}  $1" }
bb_say_note()  { bb_say "${c_dim}│${c_reset}  ${c_dim}$1${c_reset}" }
# Label padded by display width: ${(r:N:)} would count the bytes of "ç" or "ã" as columns.
bb_say_kv() {
  local label=$1 pad
  local -i gap=$(( 10 - ${(m)#label} ))
  (( gap < 1 )) && gap=1
  pad=$(printf '%*s' $gap '')
  bb_say "${c_dim}│${c_reset}  ${c_dim}${label}${pad}${c_reset}$2"
}
bb_say_done()  { bb_say "${c_green}●${c_reset}  $1" }
bb_say_step()  { bb_say "${c_cyan}◇${c_reset}  $1" }
bb_say_warn()  { bb_say "${c_yellow}▲${c_reset}  $1" }
bb_say_fail()  { bb_say "${c_red}■${c_reset}  $1" }
bb_say_close() { bb_say "${c_dim}└${c_reset}  $1" }

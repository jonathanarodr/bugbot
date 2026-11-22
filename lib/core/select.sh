# Interactive prompts: a multiselect whose items may carry a one-of choice (which clone
# of a repo to use), and a yes/no confirmation. Both need a tty; callers fall back to
# flags when there is none.
#
# Items are parallel arrays so a redraw never forks:
#   BB_SEL_LABEL  title            BB_SEL_HINT  dim text after it
#   BB_SEL_ON     0|1 selected     BB_SEL_PICK  chosen option, 1-based
#   BB_SEL_OPTS   options joined by \x1f, shown under the item the cursor is on
#   BB_SEL_HEAD   1 for a group header; the items after it, up to the next header, are its
#                 group. Groups start collapsed: → opens, ← closes, space (de)selects the group.
#   BB_SEL_OPEN   1 when a header's group is expanded

typeset -ga BB_SEL_LABEL BB_SEL_HINT BB_SEL_ON BB_SEL_OPTS BB_SEL_PICK BB_SEL_VIS BB_SEL_HEAD BB_SEL_OPEN
typeset -gi BB_SEL_LINES=0 BB_SEL_CUR=1 BB_SEL_TOP=1
typeset -g  BB_SEL_FILTER='' BB_SEL_MODE=nav
readonly BB_SEL_SEP=$'\x1f'

bb_select_reset() {
  BB_SEL_LABEL=() BB_SEL_HINT=() BB_SEL_ON=() BB_SEL_OPTS=() BB_SEL_PICK=() BB_SEL_VIS=()
  BB_SEL_HEAD=() BB_SEL_OPEN=()
  BB_SEL_LINES=0 BB_SEL_CUR=1 BB_SEL_TOP=1 BB_SEL_FILTER='' BB_SEL_MODE=nav
}

# bb_select_add <label> <hint> <on 0|1> <pick> [option...]
bb_select_add() {
  local label=$1 hint=$2 on=$3 pick=$4
  shift 4
  BB_SEL_LABEL+=("$label")
  BB_SEL_HINT+=("$hint")
  BB_SEL_ON+=("$on")
  BB_SEL_PICK+=("$pick")
  BB_SEL_OPTS+=("${(pj:\x1f:)@}")
  BB_SEL_HEAD+=(0)
  BB_SEL_OPEN+=(0)
}

# bb_select_add_header <label> <hint> [open 0|1] — starts a group; the items added after it
# belong to it.
bb_select_add_header() {
  BB_SEL_LABEL+=("$1")
  BB_SEL_HINT+=("$2")
  BB_SEL_ON+=(0)
  BB_SEL_PICK+=(1)
  BB_SEL_OPTS+=('')
  BB_SEL_HEAD+=(1)
  BB_SEL_OPEN+=(${3:-0})
}

# Indexes of the items of the group that header $1 starts, in reply.
bb_select_group() {
  local -i i
  reply=()
  for (( i = $1 + 1; i <= ${#BB_SEL_HEAD} && !BB_SEL_HEAD[i]; i++ )); do reply+=($i); done
}

# Header of the group item $1 belongs to, in REPLY (0 when there are no groups).
bb_select_parent() {
  local -i i
  REPLY=0
  for (( i = $1; i >= 1; i-- )); do (( BB_SEL_HEAD[i] )) && { REPLY=$i; return }; done
}

# Put the cursor on item $1 when it is visible.
bb_select_goto() {
  local -i k
  for (( k = 1; k <= ${#BB_SEL_VIS}; k++ )); do
    (( BB_SEL_VIS[k] == $1 )) && { BB_SEL_CUR=$k; return 0 }
  done
  return 1
}

bb_select_count() {
  local -i n=0 i
  for (( i = 1; i <= ${#BB_SEL_ON}; i++ )); do (( BB_SEL_ON[i] )) && (( n++ )); done
  REPLY=$n
}

bb_select_items() {
  local -i n=0 i
  for (( i = 1; i <= ${#BB_SEL_HEAD}; i++ )); do (( BB_SEL_HEAD[i] )) || (( n++ )); done
  REPLY=$n
}

# Without a filter: every header, and the items of open groups (all items when there are no
# groups). With a filter: items whose label or hint matches, or whose header does, each under its
# header, expanded whatever its state.
bb_select_filter() {
  local -i i head=0 head_shown=0 head_match=0
  local needle=${(L)BB_SEL_FILTER}

  BB_SEL_VIS=()
  for (( i = 1; i <= ${#BB_SEL_LABEL}; i++ )); do
    if (( BB_SEL_HEAD[i] )); then
      head=$i head_shown=0 head_match=0
      if [[ -z $needle ]]; then
        BB_SEL_VIS+=($i); head_shown=1
      elif [[ ${(L)BB_SEL_LABEL[i]} == *${needle}* ]]; then
        BB_SEL_VIS+=($i); head_shown=1 head_match=1
      fi
      continue
    fi
    if [[ -z $needle ]]; then
      (( head == 0 || BB_SEL_OPEN[head] )) && BB_SEL_VIS+=($i)
    elif (( head_match )) || [[ ${(L)BB_SEL_LABEL[i]} == *${needle}* || ${(L)BB_SEL_HINT[i]} == *${needle}* ]]; then
      (( head && !head_shown )) && { BB_SEL_VIS+=($head); head_shown=1 }
      BB_SEL_VIS+=($i)
    fi
  done

  (( BB_SEL_CUR > ${#BB_SEL_VIS} )) && BB_SEL_CUR=${#BB_SEL_VIS}
  (( BB_SEL_CUR < 1 )) && BB_SEL_CUR=1
  return 0
}

bb_select_render() {
  local title=$1 out='' row label hint mark cur bar="${c_dim}│${c_reset}"
  local -a lines opts
  local -i i j k item win label_w=0 old=$BB_SEL_LINES choices=0 groups=0 picked

  for i in $BB_SEL_VIS; do (( !BB_SEL_HEAD[i] && ${#BB_SEL_LABEL[i]} > label_w )) && label_w=${#BB_SEL_LABEL[i]}; done
  for i in {1..${#BB_SEL_OPTS}}; do [[ ${BB_SEL_OPTS[i]} == *$BB_SEL_SEP* ]] && { choices=1; break }; done
  (( ${BB_SEL_HEAD[(I)1]} )) && groups=1
  (( label_w > 40 )) && label_w=40

  # Rows left for the list once the frame, the expanded options and the help are drawn.
  win=$(( BB_TERM_ROWS - 14 ))
  (( win < 4 )) && win=4
  (( BB_SEL_CUR < BB_SEL_TOP )) && BB_SEL_TOP=$BB_SEL_CUR
  (( BB_SEL_CUR >= BB_SEL_TOP + win )) && BB_SEL_TOP=$(( BB_SEL_CUR - win + 1 ))

  bb_select_items; k=$REPLY
  bb_select_count
  row="${c_cyan}◆${c_reset}  ${title} ${c_dim}(${REPLY}/${k})${c_reset}"
  if [[ $BB_SEL_MODE == filter || -n $BB_SEL_FILTER ]]; then
    row+="   ${c_dim}filter:${c_reset} ${BB_SEL_FILTER}"
    [[ $BB_SEL_MODE == filter ]] && row+="${c_cyan}▏${c_reset}"
  fi
  lines+=("$row")

  (( BB_SEL_TOP > 1 )) && lines+=("$bar    ${c_dim}↑ $(( BB_SEL_TOP - 1 )) more${c_reset}")

  if (( ${#BB_SEL_VIS} == 0 )); then
    lines+=("$bar    ${c_dim}nothing matches the filter${c_reset}")
  fi

  for (( k = BB_SEL_TOP; k <= ${#BB_SEL_VIS} && k < BB_SEL_TOP + win; k++ )); do
    item=${BB_SEL_VIS[k]}
    (( k == BB_SEL_CUR )) && cur="${c_cyan}❯${c_reset}" || cur=' '
    if (( BB_SEL_HEAD[item] )); then
      bb_select_group $item
      picked=0
      for i in $reply; do (( BB_SEL_ON[i] )) && (( ++picked )); done
      if (( BB_SEL_OPEN[item] )) || [[ -n $BB_SEL_FILTER ]]; then mark="${c_cyan}▾${c_reset}"; else mark="${c_dim}▸${c_reset}"; fi
      label=${BB_SEL_LABEL[item]}
      (( k == BB_SEL_CUR )) && label="${c_bold}${label}${c_reset}"
      hint="${c_dim}${BB_SEL_HINT[item]}${c_reset}"
      (( picked )) && hint+="  ${c_green}${picked} selected${c_reset}"
      lines+=("$bar  $cur $mark $label  $hint")
      continue
    fi
    (( BB_SEL_ON[item] )) && mark="${c_green}●${c_reset}" || mark="${c_dim}○${c_reset}"

    bb_trunc "${BB_SEL_LABEL[item]}" $label_w
    label=${(r:label_w:)REPLY}
    (( k == BB_SEL_CUR )) && label="${c_bold}${label}${c_reset}"
    bb_trunc "${BB_SEL_HINT[item]}" $(( BB_TERM_COLS - label_w - 12 ))
    hint="${c_dim}${REPLY}${c_reset}"

    (( groups )) && lines+=("$bar    $cur $mark $label  $hint") || lines+=("$bar  $cur $mark $label  $hint")

    opts=("${(@ps:\x1f:)BB_SEL_OPTS[item]}")
    if (( k == BB_SEL_CUR && ${#opts} > 1 )); then
      for (( j = 1; j <= ${#opts}; j++ )); do
        bb_trunc "${opts[j]}" $(( BB_TERM_COLS - 14 ))
        if (( j == BB_SEL_PICK[item] )); then
          lines+=("$bar        ${c_cyan}◉${c_reset} ${REPLY}")
        else
          lines+=("$bar        ${c_dim}○ ${REPLY}${c_reset}")
        fi
      done
    fi
  done

  i=$(( ${#BB_SEL_VIS} - BB_SEL_TOP - win + 1 ))
  (( i > 0 )) && lines+=("$bar    ${c_dim}↓ $i more${c_reset}")

  lines+=("$bar")
  if [[ $BB_SEL_MODE == filter ]]; then
    lines+=("$bar  ${c_dim}type to filter · enter applies · esc clears${c_reset}")
  else
    lines+=("$bar  ${c_dim}↑↓ move$( (( groups )) && print -n ' · → open · ← close') · space select$( (( choices )) && print -n ' · ←→ option') · a all · / filter · enter confirm · q quit${c_reset}")
  fi

  (( old > 0 )) && out+="${BB_ESC}[${old}A"
  for row in "${lines[@]}"; do out+="${BB_ESC}[2K${row}"$'\n'; done

  # A shorter frame than the last one: blank the leftover rows, then come back up.
  if (( old > ${#lines} )); then
    for (( i = ${#lines}; i < old; i++ )); do out+="${BB_ESC}[2K"$'\n'; done
    out+="${BB_ESC}[$(( old - ${#lines} ))A"
  fi

  print -rn -u2 -- "$out"
  BB_SEL_LINES=${#lines}
}

bb_read_key() {
  local k rest

  REPLY=''
  read -r -s -k 1 -u 0 k </dev/tty || { REPLY=eof; return 0 }

  case $k in
    $'\e')
      if read -r -s -t 0.05 -k 1 -u 0 rest </dev/tty && [[ $rest == '[' || $rest == O ]]; then
        read -r -s -t 0.05 -k 1 -u 0 rest </dev/tty
        case $rest in
          A) REPLY=up ;; B) REPLY=down ;; C) REPLY=right ;; D) REPLY=left ;; *) REPLY=other ;;
        esac
      else
        REPLY=esc
      fi ;;
    $'\n'|$'\r') REPLY=enter ;;
    ' ')         REPLY=space ;;
    $'\x7f'|$'\b') REPLY=backspace ;;
    $'\x03')     REPLY=interrupt ;;
    *)           REPLY="char:$k" ;;
  esac
}

# Returns 0 when confirmed, 1 when cancelled. The result stays in BB_SEL_ON/BB_SEL_PICK.
bb_select_run() {
  local title=$1 key
  local -a opts
  local -i item n i all

  bb_is_tty || return 2
  bb_tty_quiet
  stty -icanon min 1 time 0 </dev/tty 2>/dev/null
  bb_cursor_hide
  bb_select_filter

  while true; do
    bb_select_render "$title"
    bb_read_key
    key=$REPLY
    item=${BB_SEL_VIS[BB_SEL_CUR]:-0}

    if [[ $BB_SEL_MODE == filter ]]; then
      case $key in
        enter)     BB_SEL_MODE=nav ;;
        esc)       BB_SEL_MODE=nav; BB_SEL_FILTER='' ;;
        backspace) BB_SEL_FILTER=${BB_SEL_FILTER[1,-2]} ;;
        char:*)    [[ ${key#char:} == [[:print:]] ]] && BB_SEL_FILTER+=${key#char:} ;;
        interrupt) bb_on_interrupt ;;
      esac
      BB_SEL_CUR=1 BB_SEL_TOP=1
      bb_select_filter
      continue
    fi

    case $key in
      up|char:k)   (( BB_SEL_CUR > 1 )) && (( BB_SEL_CUR-- )) ;;
      down|char:j) (( BB_SEL_CUR < ${#BB_SEL_VIS} )) && (( BB_SEL_CUR++ )) ;;
      space)
        (( item )) || continue
        if (( BB_SEL_HEAD[item] )); then
          bb_select_group $item
          all=1
          for i in $reply; do (( BB_SEL_ON[i] )) || all=0; done
          for i in $reply; do BB_SEL_ON[i]=$(( 1 - all )); done
        else
          BB_SEL_ON[item]=$(( 1 - BB_SEL_ON[item] ))
        fi ;;
      right|left|char:l|char:h)
        (( item )) || continue
        if (( BB_SEL_HEAD[item] )); then
          [[ $key == right || $key == char:l ]] && BB_SEL_OPEN[item]=1 || BB_SEL_OPEN[item]=0
          bb_select_filter; bb_select_goto $item
          continue
        fi
        # Split into an array first: a single option has no separator, and ${#…} on the
        # unsplit string would count its characters instead of its options.
        opts=("${(@ps:\x1f:)BB_SEL_OPTS[item]}")
        n=${#opts}
        if (( n <= 1 )); then
          # A plain item: ← closes its group and goes back to the header.
          if [[ $key == left || $key == char:h ]] && [[ -z $BB_SEL_FILTER ]]; then
            bb_select_parent $item
            (( REPLY )) && { i=$REPLY; BB_SEL_OPEN[i]=0; bb_select_filter; bb_select_goto $i }
          fi
          continue
        fi
        if [[ $key == right || $key == char:l ]]; then
          BB_SEL_PICK[item]=$(( BB_SEL_PICK[item] % n + 1 ))
        else
          BB_SEL_PICK[item]=$(( (BB_SEL_PICK[item] + n - 2) % n + 1 ))
        fi
        BB_SEL_ON[item]=1 ;;
      char:a)
        # Everything shown, and for a collapsed group header every item of its group.
        local -a targets=()
        for i in $BB_SEL_VIS; do
          if (( BB_SEL_HEAD[i] )); then bb_select_group $i; targets+=($reply); else targets+=($i); fi
        done
        targets=(${(u)targets})
        all=1
        for i in $targets; do (( BB_SEL_ON[i] )) || all=0; done
        for i in $targets; do BB_SEL_ON[i]=$(( 1 - all )); done ;;
      char:/)      BB_SEL_MODE=filter ;;
      enter)       break ;;
      esc)
        # With a filter applied, esc clears it; otherwise it leaves.
        if [[ -n $BB_SEL_FILTER ]]; then
          BB_SEL_FILTER='' BB_SEL_CUR=1 BB_SEL_TOP=1
          bb_select_filter
        else
          bb_select_done; return 1
        fi ;;
      char:q|eof) bb_select_done; return 1 ;;
      interrupt)   bb_on_interrupt ;;
    esac
  done

  bb_select_done
  return 0
}

bb_select_done() {
  bb_tty_restore
  BB_SEL_LINES=0
}

# bb_confirm <question> → 0 for yes. Defaults to no: enter alone does not write anything.
bb_confirm() {
  local question=$1 answer=''

  bb_is_tty || return 2
  print -rn -u2 -- "${c_cyan}◆${c_reset}  ${question} ${c_dim}[y/N]${c_reset} "
  bb_tty_quiet
  stty -icanon min 1 time 0 </dev/tty 2>/dev/null
  read -r -s -k 1 -u 0 answer </dev/tty
  bb_tty_restore

  case $answer in
    y|Y) print -u2 -- "${c_green}yes${c_reset}"; return 0 ;;
    $'\x03') print -u2 ''; bb_on_interrupt ;;
    *)       print -u2 -- "${c_dim}no${c_reset}"; return 1 ;;
  esac
}

# bugbot format — rewrite the bot's own JSON files to the code style in .editorconfig.
#
# Files: bugbot.json, bugbot.local.json, workspace.json, schemas/*.json, repos/*/*.json.
# Each rewrite is json.dumps(indent=2) of the parsed data, so content never changes.
# `bugbot doctor` reports the same files without touching them.
# Runs with broken settings, like doctor: a malformed bugbot.json is one of the files it reports.

bb_format_usage() {
  print -u2 "usage: bugbot format [--check] [--no-color]

Rewrite the bot's JSON files to the code style in .editorconfig:
bugbot.json, bugbot.local.json, workspace.json, schemas/*.json and
repos/<owner>/<name>.json. Content is kept.

Options:
  --check     only list the files that are not in the code style; exit 1 if any
  --no-color  no colors
  -h, --help  this help"
}

bb_cmd_format() {
  local color=true check=false line state file detail out
  local -a f args
  local -i ok=0 changed=0 invalid=0

  while (( $# )); do
    case $1 in
      --check)    check=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_format_usage; return 0 ;;
      *)          print -u2 "bugbot format: unknown option: $1"; bb_format_usage; return 2 ;;
    esac
  done

  args=(scan --home "$BUGBOT_HOME" --workspace "$BUGBOT_WORKSPACE_FILE")
  [[ $check == true ]] || args+=(--write)
  out=$(python3 "$BUGBOT_HOME/lib/format/format.py" "${args[@]}") || {
    print -u2 "bugbot format: the formatter itself crashed"
    return 2
  }

  bb_term_init $color
  bb_say_open "bugbot format"
  bb_say_bar
  for line in "${(@f)out}"; do
    [[ -n $line ]] || continue
    f=("${(@ps:\t:)line}")
    state=$f[1] detail=${f[3]:-}
    file=${f[2]#$BUGBOT_HOME/}
    [[ $file == /* ]] && { bb_tilde "$file"; file=$REPLY }
    case $state in
      ok)      (( ++ok )) ;;
      changed) (( ++changed )); bb_say_line "  ${c_yellow}▲${c_reset} ${file}  ${detail}" ;;
      *)       (( ++invalid )); bb_say_line "  ${c_red}■${c_reset} ${file}  ${detail}" ;;
    esac
  done
  (( changed + invalid )) && bb_say_bar

  REPLY="$ok conforming"
  if [[ $check == true ]]; then
    (( changed )) && REPLY+=" · $changed to format"
  else
    (( changed )) && REPLY+=" · $changed reformatted"
  fi
  (( invalid )) && REPLY+=" · $invalid invalid"
  bb_say_close "$REPLY"

  (( invalid )) && return 1
  [[ $check == true ]] && (( changed )) && return 1
  return 0
}

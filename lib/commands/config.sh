# bugbot config — every resolved setting and path, where each value came from and what it does.

bb_config_usage() {
  print -u2 "usage: bugbot config [--json] [--no-color]

Show the settings in use, where each value came from (environment,
bugbot.local.json or bugbot.json) and what each one controls.
The format is defined in schemas/settings.schema.json.

Options:
  --json      print {settings, origins, paths} to stdout (for agents and scripts)
  --no-color  no colors
  -h, --help  this help"
}

bb_cmd_config() {
  local color=true json=false row key value
  local -a f

  while (( $# )); do
    case $1 in
      --no-color) color=false; shift ;;
      --json)     json=true; shift ;;
      -h|--help)  bb_config_usage; return 0 ;;
      *)          print -u2 "bugbot config: unknown option: $1"; bb_config_usage; return 2 ;;
    esac
  done

  if [[ $json == true ]]; then
    export BUGBOT_HOME BUGBOT_WORKSPACE_FILE BUGBOT_DATA_DIR BUGBOT_RETRO_DIR
    print -rn -- "$BB_SETTINGS_RAW" | python3 "$BUGBOT_HOME/lib/core/settings.py" json --home "$BUGBOT_HOME"
    return
  fi

  bb_term_init $color
  bb_say_open "bugbot config"
  bb_say_bar

  # env  path  value  origin  description
  while IFS= read -r row; do
    f=("${(@ps:\t:)row}")
    bb_say_line "${c_bold}${f[2]}${c_reset}  ${f[3]}  ${c_dim}${f[4]}${c_reset}"
    bb_say_note "  ${f[5]} · ${f[1]}"
  done < <(print -rn -- "$BB_SETTINGS_RAW" | python3 "$BUGBOT_HOME/lib/core/settings.py" describe --home "$BUGBOT_HOME")

  bb_say_bar
  for key in BUGBOT_HOME BUGBOT_WORKSPACE_FILE BUGBOT_DATA_DIR BUGBOT_RETRO_DIR; do
    bb_tilde "${(P)key}"
    value=$REPLY
    bb_say_line "${(r:22:)key}${value}  ${c_dim}derived${c_reset}"
  done

  bb_say_bar
  [[ -f $BUGBOT_HOME/bugbot.local.json ]] \
    && REPLY="bugbot.local.json in use" \
    || REPLY="no bugbot.local.json"
  bb_say_close "${c_dim}${REPLY}${c_reset}"
}

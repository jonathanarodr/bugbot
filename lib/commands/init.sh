# bugbot init — create bugbot.json with the default values.
#
# The values come from schemas/settings.schema.json (`x-init` of each setting, the reference
# configuration), so the schema stays the single source of truth. The organization is never
# built in: it is given with --org or BUGBOT_ORG. Runs without settings, since it creates them.

bb_init_usage() {
  print -u2 "usage: bugbot init --org <name> [options]

Create bugbot.json with the default value of every setting (schemas/settings.schema.json,
x-init). Edit it afterwards, or put personal overrides in bugbot.local.json.

Options:
  --org <name>  GitHub organization whose repositories the bot reviews (default: \$BUGBOT_ORG)
  --force       replace an existing bugbot.json
  --print       print the file to stdout, write nothing
  --no-color    no colors
  -h, --help    this help

Next step: bugbot setup"
}

bb_cmd_init() {
  local org=${BUGBOT_ORG:-} force=false show=false color=true out
  local -a flags

  while (( $# )); do
    case $1 in
      --org)      [[ -n ${2:-} ]] || { bb_init_usage; return 2 }; org=$2; shift 2 ;;
      --force)    force=true; shift ;;
      --print)    show=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_init_usage; return 0 ;;
      *)          print -u2 "bugbot init: unknown option: $1"; bb_init_usage; return 2 ;;
    esac
  done

  [[ -n $org ]] || { print -u2 "bugbot init: give the GitHub organization with --org <name>"; return 2 }
  [[ $force == true ]] && flags+=(--force)
  [[ $show == true ]] && flags+=(--print)

  if [[ $show == true ]]; then
    python3 "$BUGBOT_HOME/lib/core/settings.py" init --home "$BUGBOT_HOME" --org "$org" $flags
    return
  fi

  bb_term_init $color
  bb_say_open "bugbot init"
  bb_say_bar
  bb_say_kv org "$org"
  out=$(python3 "$BUGBOT_HOME/lib/core/settings.py" init --home "$BUGBOT_HOME" --org "$org" $flags 2>&1) || {
    bb_say_close "${c_red}${out#bugbot: }${c_reset}"
    return 2
  }
  bb_tilde "$out"
  bb_say_kv written "$REPLY"
  bb_say_bar
  bb_say_close "${c_green}done${c_reset} · next: bugbot setup"
}

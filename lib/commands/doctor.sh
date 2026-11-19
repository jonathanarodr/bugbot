# bugbot doctor — read-only health check of the bot: code style of its JSON files, environment,
# settings, schemas, workspace, repo configs, cache and documentation.
#
# Runs even when settings are broken or tools are missing — that is when it is needed — so the
# dispatcher skips settings loading and the tool check for this command. Nothing is written,
# fetched or changed (`bugbot format` is what fixes the code style); the only network call is
# `gh api user` (skipped with --offline).

bb_doctor_usage() {
  print -u2 "usage: bugbot doctor [options]

Check that the bot is healthy, without changing anything:
  format        bot JSON files follow .editorconfig ('bugbot format' fixes)
  environment   tools, versions, gh login, token overrides
  settings      bugbot.json and bugbot.local.json against their schema
  schemas       every schema loads with the bundled validator
  workspace     workspace.json is valid, clones exist, remotes still match
  repos         repo configs are valid, configured and match their clone
  cache         cached meta.json and triage.json are valid
  docs          README.md documents every schema key

Options:
  --only <groups>  comma-separated groups to run (e.g. format,repos)
  --quick          skip comparing repo configs with their clones
  --offline        skip the gh login check (no network)
  --json           print the results as JSON to stdout
  --no-color       no colors
  -h, --help       this help

Exit code: 0 when nothing failed (warnings allowed), 1 otherwise."
}

bb_cmd_doctor() {
  local only='' quick=false offline=false json=false color=true group state label detail last=''
  local -a args f
  local -i ok=0 warn=0 fail=0 rc

  while (( $# )); do
    case $1 in
      --only)     [[ -n ${2:-} ]] || { bb_doctor_usage; return 2 }; only=$2; shift 2 ;;
      --quick)    quick=true; shift ;;
      --offline)  offline=true; shift ;;
      --json)     json=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_doctor_usage; return 0 ;;
      *)          print -u2 "bugbot doctor: unknown option: $1"; bb_doctor_usage; return 2 ;;
    esac
  done
  if [[ -n $only && ! $only =~ '^(format|environment|settings|schemas|workspace|repos|cache|docs)(,(format|environment|settings|schemas|workspace|repos|cache|docs))*$' ]]; then
    print -u2 "bugbot doctor: --only takes format, environment, settings, schemas, workspace, repos, cache, docs"
    return 2
  fi

  if ! (( $+commands[python3] )); then
    print -u2 "bugbot doctor: python3 not found in PATH — it is required by every command"
    return 1
  fi

  args=(--home "$BUGBOT_HOME" --workspace "$BUGBOT_WORKSPACE_FILE" --data-dir "$BUGBOT_DATA_DIR")
  [[ -n $only ]] && args+=(--only "$only")
  [[ $quick == true ]] && args+=(--quick)
  [[ $offline == true ]] && args+=(--offline)

  local out
  out=$(python3 "$BUGBOT_HOME/lib/doctor/doctor.py" "${args[@]}")
  rc=$?
  if (( rc > 1 )); then
    print -u2 "bugbot doctor: the check itself crashed (exit $rc)"
    return 2
  fi

  if [[ $json == true ]]; then
    print -rn -- "$out" | python3 -c '
import json, sys
rows = [line.split("\t", 3) for line in sys.stdin.read().splitlines() if line]
print(json.dumps([{"group": g, "status": s, "check": c, "detail": d} for g, s, c, d in rows], indent=2))'
    return $rc
  fi

  bb_term_init $color
  bb_say_open "bugbot doctor"

  for line in "${(@f)out}"; do
    [[ -n $line ]] || continue
    f=("${(@ps:\t:)line}")
    group=$f[1] state=$f[2] label=$f[3] detail=${f[4]:-}
    if [[ $group != $last ]]; then
      bb_say_bar
      bb_say_line "${c_bold}${group}${c_reset}"
      last=$group
    fi
    case $state in
      ok)   (( ++ok ));   bb_say_line "  ${c_green}●${c_reset} ${label}  ${c_dim}${detail}${c_reset}" ;;
      warn) (( ++warn )); bb_say_line "  ${c_yellow}▲${c_reset} ${label}  ${detail}" ;;
      *)    (( ++fail )); bb_say_line "  ${c_red}■${c_reset} ${label}  ${detail}" ;;
    esac
  done

  bb_say_bar
  REPLY="${ok} ok · ${warn} warning(s) · ${fail} failure(s)"
  if (( fail )); then
    bb_say_close "${c_red}unhealthy${c_reset} · $REPLY"
  elif (( warn )); then
    bb_say_close "${c_yellow}healthy with warnings${c_reset} · $REPLY"
  else
    bb_say_close "${c_green}healthy${c_reset} · $REPLY"
  fi
  return $rc
}

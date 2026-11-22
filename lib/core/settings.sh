# Settings bridge: lib/core/settings.py reads bugbot.json, bugbot.local.json and the
# environment and validates them against schemas/settings.schema.json; this file turns its
# output into BUGBOT_* shell variables.
#
# The output is NUL-separated data assigned with typeset, never evaluated, so nothing in a
# settings file can run as code. List settings (scan.roots, scan.prune) become zsh arrays.

typeset -gA BB_SETTINGS_ORIGIN=()
# Raw `load` output, kept so `bugbot config` describes exactly what was loaded. Re-running
# settings.py later would not see the environment: a list setting becomes a zsh array here,
# and arrays are not exported to child processes.
typeset -g BB_SETTINGS_RAW=''

bb_settings_load() {
  local out name origin kind
  local -a f
  local -i i=1 n

  out=$(python3 "$BUGBOT_HOME/lib/core/settings.py" load --home "$BUGBOT_HOME") || exit 2
  BB_SETTINGS_RAW=$out
  f=("${(@0)out}")

  while (( i + 3 <= ${#f} )); do
    name=$f[i] origin=$f[i+1] kind=$f[i+2] n=$f[i+3]
    [[ $name =~ '^BUGBOT_[A-Z0-9_]+$' ]] || { print -u2 "bugbot: unexpected output from settings.py"; exit 2 }

    # A value inherited from the environment is a scalar; a list setting must become an array.
    unset $name
    if [[ $kind == list ]]; then
      typeset -ga $name
      set -A $name "${(@)f[i+4,i+3+n]}"
    else
      typeset -g "$name=${f[i+4]}"
    fi

    BB_SETTINGS_ORIGIN[$name]=$origin
    (( i += 4 + n ))
  done
}

# JSON array from shell words, for handing a list setting to Python without any quoting games.
bb_json_array() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@"
}

# The global hooks as one JSON object (shape of bugbot.json `hooks`), from the settings already
# validated by settings.py, for the Python side of triage and inbox.
bb_hooks_json() {
  python3 -c 'import json,sys; print(json.dumps({"draft": sys.argv[1] == "true", "stale_days": int(sys.argv[8]), "exclude_teams": json.loads(sys.argv[7]), "labels": {"skip": json.loads(sys.argv[2]), "trigger": json.loads(sys.argv[3])}, "reviewers": {"max": int(sys.argv[4]), "ignore": json.loads(sys.argv[5])}, "ci": sys.argv[6]}))' \
    "$BUGBOT_HOOKS_DRAFT" "$(bb_json_array "${BUGBOT_HOOKS_SKIP_LABELS[@]}")" \
    "$(bb_json_array "${BUGBOT_HOOKS_TRIGGER_LABELS[@]}")" "$BUGBOT_HOOKS_REVIEWERS_MAX" \
    "$(bb_json_array "${BUGBOT_HOOKS_REVIEWERS_IGNORE[@]}")" "$BUGBOT_HOOKS_CI" \
    "$(bb_json_array "${BUGBOT_HOOKS_EXCLUDE_TEAMS[@]}")" "$BUGBOT_HOOKS_STALE_DAYS"
}

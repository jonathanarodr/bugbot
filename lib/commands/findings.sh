# bugbot findings — assemble and preview a PR round's findings.json.
#
#   candidates/<finder>.json  +  verdicts/<finder>.<index>.<verifier>.json  →  findings.json
#
# The finders and verifiers (agents) write the inputs; this command counts the verdicts, applies the
# counts verdicts, drops duplicates, ranks by severity, validates and writes findings.json, then shows it.
# Nothing is posted: publishing is a separate, confirmed step. Rules: lib/findings/findings.py.

bb_findings_usage() {
  print -u2 "usage: bugbot findings <pr> [options]

<pr> is the PR URL, owner/name#N, or the number with --repo.

Assemble findings.json from the finders' candidates and the verifiers' verdicts
for the PR's current head, then show it. Kept: a strict majority of keep verdicts; severity is the highest level supported by a keep verdict.
Past review.max_lows, the lowest-ranked Lows are cut (0: no limit).
Nothing is posted to GitHub.

Options:
  --repo <owner/name>  repository of the PR, when <pr> is just the number
  --partial            assemble even when verdicts are missing (those candidates are cut)
  --show               only show the existing findings.json, assemble nothing
  --all                also list the cut findings and why
  --json               print findings.json to stdout
  --no-color           no colors
  -h, --help           this help"
}

bb_cmd_findings() {
  local target='' repo='' partial=false show=false all=false json=false color=true
  local meta triage file out line
  local -a f s args

  while (( $# )); do
    case $1 in
      --repo)     [[ -n ${2:-} ]] || { bb_findings_usage; return 2 }; repo=$2; shift 2 ;;
      --partial)  partial=true; shift ;;
      --show)     show=true; shift ;;
      --all)      all=true; shift ;;
      --json)     json=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_findings_usage; return 0 ;;
      -*)         print -u2 "bugbot findings: unknown option: $1"; bb_findings_usage; return 2 ;;
      *)          [[ -z $target ]] || { print -u2 "bugbot findings: give a single PR"; return 2 }
                  target=$1; shift ;;
    esac
  done
  [[ -n $target ]] || { bb_findings_usage; return 2 }
  [[ $show == true && $partial == true ]] && { print -u2 "bugbot findings: --show assembles nothing; drop --partial"; return 2 }

  source "$BUGBOT_HOME/lib/fetch/steps.sh"
  bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot findings: $REPLY"; return 2 }
  bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot findings: $REPLY"; return 2 }
  meta=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH/meta.json
  triage=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH/triage.json
  [[ -f $meta && -f $triage ]] || { print -u2 "bugbot findings: ${reply[1]}#$BB_PR_NUMBER has no fetch and triage yet"; return 1 }
  file=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["paths"]["findings"])' "$meta") || return 1

  bb_term_init $color
  if [[ $show == false ]]; then
    args=(assemble --meta "$meta" --triage "$triage" --max-lows "$BUGBOT_REVIEW_MAX_LOWS")
    [[ $partial == true ]] && args+=(--partial)
    out=$(python3 "$BUGBOT_HOME/lib/findings/findings.py" "${args[@]}" 2>&1) || {
      local rc=$?
      print -u2 -- "bugbot findings: ${out#findings: }"
      return $rc
    }
  fi
  [[ -f $file ]] || { print -u2 "bugbot findings: no findings.json yet — run without --show"; return 1 }

  if [[ $json == true ]]; then
    cat "$file"
    return 0
  fi

  local -a lines=("${(@f)$(python3 "$BUGBOT_HOME/lib/findings/findings.py" rows --file "$file")}")
  s=("${(@ps:\t:)lines[1]}")
  local -i kept=0 cut=0
  bb_say_open "bugbot findings"
  bb_say_bar
  bb_say_kv pr "${reply[1]}#$BB_PR_NUMBER  ${c_dim}${s[4]} · ${s[3]}${c_reset}"
  case ${s[2]} in
    REQUEST_CHANGES) bb_say_kv verdict "${c_red}request changes${c_reset}" ;;
    COMMENT)        bb_say_kv verdict "${c_yellow}comment${c_reset}" ;;
    PASS)           bb_say_kv verdict "${c_green}pass${c_reset}" ;;
    *)              bb_say_kv verdict "${s[2]}" ;;
  esac
  bb_say_bar
  for line in "${(@)lines[2,-1]}"; do
    f=("${(@ps:\t:)line}")
    if [[ ${f[1]} == keep ]]; then
      (( ++kept ))
      local sev=${f[3]}
      case $sev in
        Critical) sev="${c_red}Critical${c_reset}" ;;
        High) sev="${c_red}High${c_reset}    " ;;
        Medium) sev="${c_yellow}Medium${c_reset}  " ;;
        Low) sev="${c_dim}Low${c_reset}     " ;;
      esac
      bb_say_line "${f[2]}  $sev  ${f[6]}$([[ ${f[4]} == 1 ]] && print -n "  ${c_dim}pre-existing${c_reset}")$([[ ${f[5]} == PLAUSIBLE ]] && print -n "  ${c_dim}plausible${c_reset}")"
      bb_say_line "    ${f[7]}  ${c_dim}${f[8]}${c_reset}"
    else
      (( ++cut ))
      [[ $all == true ]] && bb_say_line "${c_dim}${f[2]}  cut  ${f[6]}  ${f[7]}  — ${f[9]}${c_reset}"
    fi
  done
  (( kept )) || bb_say_line "${c_dim}nothing kept${c_reset}"
  bb_say_bar
  bb_tilde "$file"
  bb_say_close "$kept kept · $cut cut$([[ $all == false ]] && (( cut )) && print -n ' (--all shows why)') · not posted · $REPLY"
  return 0
}

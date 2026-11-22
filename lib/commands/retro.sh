# bugbot retro — persist a retrospective report before the PR cache is cleaned.

bb_retro_usage() {
  print -u2 "usage: bugbot retro record <pr> [options]

Append a retrospective report from stdin to the permanent per-PR record.
The report is JSON: {\"summary\":\"...\",\"candidates\":[...]}. Used by
the bugbot-retro skill after a review; it does not apply proposed changes.

<pr> is the PR URL, owner/name#N, or the number with --repo.

Options:
  --repo <owner/name>  repository of the PR, when <pr> is just the number
  --trigger <kind>     auto_approve or manual
  -h, --help           this help"
}

bb_cmd_retro() {
  local operation='' target='' repo='' trigger=''
  while (( $# )); do
    case $1 in
      record)      [[ -z $operation ]] || { print -u2 "bugbot retro: give one operation"; return 2 }
                   operation=$1; shift ;;
      --repo)      [[ -n ${2:-} ]] || { bb_retro_usage; return 2 }; repo=$2; shift 2 ;;
      --trigger)   [[ ${2:-} == (auto_approve|manual) ]] || { print -u2 "bugbot retro: --trigger must be auto_approve or manual"; return 2 }
                   trigger=$2; shift 2 ;;
      -h|--help)   bb_retro_usage; return 0 ;;
      -*)          print -u2 "bugbot retro: unknown option: $1"; bb_retro_usage; return 2 ;;
      *)           [[ -z $target ]] || { print -u2 "bugbot retro: give one PR"; return 2 }
                   target=$1; shift ;;
    esac
  done
  [[ $operation == record && -n $target && -n $trigger ]] || { bb_retro_usage; return 2 }
  if [[ $trigger == auto_approve && $BUGBOT_RETRO_AUTO_ON_APPROVE != true ]]; then
    print -u2 "bugbot retro: automatic retrospective is disabled by retro.auto_on_approve"
    return 2
  fi

  source "$BUGBOT_HOME/lib/fetch/steps.sh"
  bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot retro: $REPLY"; return 2 }
  bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot retro: $REPLY"; return 2 }
  local findings=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH/findings.json
  [[ -f $findings ]] || { print -u2 "bugbot retro: no findings.json for this PR; cache is needed to record this round"; return 1 }
  python3 "$BUGBOT_HOME/lib/retro/record.py" --findings "$findings" \
    --retro-dir "$BUGBOT_RETRO_DIR" --trigger "$trigger"
}

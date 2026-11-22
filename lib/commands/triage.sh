# bugbot triage — tier, depth and scope of a PR, from facts only, before any model runs.
#
#   read PR state → compute (triage.py) → hooks → card → [reviewer confirms] → record decision
#
# Labels, draft, checks and reviews are read live on every run: they change without a new commit,
# so the fetch-time meta.json would be stale. Hooks from bugbot.json (draft, stale_days, exclude_teams,
# labels.skip, labels.trigger, reviewers.max, ci) decide whether the PR is reviewed at all, for every tier;
# --force goes on anyway.
#
# Writes triage.json next to the PR's meta.json. The PR's title, body and comments are never
# read here. The reviewer's answer (ok / quick / standard / deep / abort) is recorded per head,
# so `bugbot retro` can later compare suggestions and choices.

bb_triage_usage() {
  print -u2 "usage: bugbot triage <pr> [options]

Compute the PR's tier (low/medium/high), suggested depth (quick/standard/deep)
and scope from files, sizes and state only. Write triage.json to the PR's cache
and show the card for confirmation.

<pr> is the PR URL, owner/name#N, or the number with --repo.
Needs 'bugbot fetch <pr>' first. Repository rules: repos/<owner>/<name>.json.

Hooks (bugbot.json 'hooks', see 'bugbot config') can skip a PR or make it wait:
draft, stale_days, exclude_teams, labels.skip, labels.trigger, reviewers.max, ci.
They apply to every tier.
A skipped or waiting PR records no decision unless --force is given.

Options:
  --repo <owner/name>    repository of the PR, when <pr> is just the number
  --force                go on even when a hook says skip or wait
  --full                 review the whole diff this round, even past the round cap or with an
                         unchanged PR (rounds from review.delta_from_round on review only the delta)
  --depth <d>            answer the card without asking: ok, quick, standard, deep or abort
  --no-confirm           compute and show only, record no decision
  --assess <tier> <depth> --reason <text>
                         record the triage agent's assessment of the current head (keep, raise,
                         or lower one level, never below a sensitive floor) without recomputing;
                         the card then suggests that depth
  --json                 print triage.json to stdout
  --no-color             no colors
  -h, --help             this help"
}

# Live PR state for the hooks: labels, draft, state, every check and the required ones, as
# `gh pr checks --json` reports them. With --json gh exits 0 even when checks fail; it exits 1
# for "no required checks reported", which is a repository without branch protection, not an
# error. Any other failure leaves checks unread, and hooks.ci passed then waits.
bb_triage_step_state() {
  local view checks required err rc available=true read=true msg=''

  view=$(bb_gh pr view "$BB_TRIAGE_PR" --repo "$BB_TRIAGE_REPO" --json labels,isDraft,state,reviewRequests,commits 2>&1) \
    || { print -r -- "gh pr view failed: $view"; return 1 }

  checks=$(bb_gh pr checks "$BB_TRIAGE_PR" --repo "$BB_TRIAGE_REPO" --json name,bucket,state 2>"$BB_RUN_DIR/checks.err")
  rc=$?
  if (( rc != 0 )); then
    err=$(<"$BB_RUN_DIR/checks.err")
    if [[ $err == *"no checks reported"* ]]; then
      checks='[]'
    else
      read=false msg=$err checks='[]'
    fi
  fi

  required=$(bb_gh pr checks "$BB_TRIAGE_PR" --repo "$BB_TRIAGE_REPO" --required --json name,bucket,state 2>"$BB_RUN_DIR/required.err")
  rc=$?
  if (( rc != 0 )); then
    err=$(<"$BB_RUN_DIR/required.err")
    required='[]'
    if [[ $err == *"no required checks reported"* ]]; then
      available=false
    else
      read=false msg=${msg:-$err}
    fi
  fi

  # Reviews from REST, the only API that says whether a reviewer is a bot (user.type). Only
  # needed for reviewers.max; a failure here makes that hook unusable, so it is a hard error.
  if (( BB_TRIAGE_REVIEWERS_MAX > 0 )); then
    bb_gh api --paginate --slurp "repos/$BB_TRIAGE_REPO/pulls/$BB_TRIAGE_PR/reviews" \
      > "$BB_RUN_DIR/reviews.raw" 2>"$BB_RUN_DIR/reviews.err" \
      || { print -r -- "reading reviews failed: $(<"$BB_RUN_DIR/reviews.err")"; return 1 }
    python3 -c 'import json,sys; pages=json.load(sys.stdin); print(json.dumps([{"user": {"login": r["user"]["login"], "type": r["user"]["type"]}, "state": r["state"]} for page in pages for r in page if r.get("user")]))' \
      < "$BB_RUN_DIR/reviews.raw" > "$BB_RUN_DIR/reviews.json" || return 1
  fi

  # Who is running the bot and, for exclude_teams, which teams they are in (org/slug, like gh
  # reports review requests). A failure reading teams makes that hook unusable: hard error.
  if [[ -n $BB_APP_ACTIVE ]]; then
    print -r -- "{\"login\": \"$BUGBOT_REVIEW_AUTHOR\"}" > "$BB_RUN_DIR/me.json"   # the App's bot login
  elif (( BB_TRIAGE_REVIEWERS_MAX > 0 || ${#BUGBOT_HOOKS_EXCLUDE_TEAMS} )); then
    bb_gh api user --jq '{login: .login}' > "$BB_RUN_DIR/me.json" 2>/dev/null || print -r -- '{}' > "$BB_RUN_DIR/me.json"
  fi
  if (( ${#BUGBOT_HOOKS_EXCLUDE_TEAMS} )); then
    bb_gh api --paginate user/teams --jq '[.[] | .organization.login + "/" + .slug]' \
      > "$BB_RUN_DIR/teams.raw" 2>"$BB_RUN_DIR/teams.err" \
      || { print -r -- "reading your teams failed: $(<"$BB_RUN_DIR/teams.err")"; return 1 }
    python3 -c 'import json,sys; print(json.dumps([t for line in sys.stdin if line.strip() for t in json.loads(line)]))' \
      < "$BB_RUN_DIR/teams.raw" > "$BB_RUN_DIR/teams.json" || return 1
  fi

  print -r -- "$view" > "$BB_RUN_DIR/view.json"
  print -r -- "$checks" > "$BB_RUN_DIR/checks.json"
  print -r -- "$required" > "$BB_RUN_DIR/required.json"
  python3 "$BUGBOT_HOME/lib/triage/triage.py" state --dir "$BB_RUN_DIR" \
    --read "$read" --error "$msg" --available "$available" || return 1

  bb_note "$(python3 "$BUGBOT_HOME/lib/triage/triage.py" state-note --dir "$BB_RUN_DIR")"
  [[ $read == true ]] || return $BB_STEP_WARN
}

bb_triage_step_compute() {
  python3 "$BUGBOT_HOME/lib/triage/triage.py" compute \
    --meta "$BB_TRIAGE_META" --config "$BB_TRIAGE_CONFIG" --state "$BB_RUN_DIR/state.json" \
    --hooks "$BB_RUN_DIR/hooks.json" \
    --out "$BB_TRIAGE_FILE" --large-diff "$BUGBOT_TRIAGE_LARGE_DIFF" \
    --broad-change "$BUGBOT_TRIAGE_BROAD_CHANGE" \
    --delta-from-round "$BUGBOT_REVIEW_DELTA_FROM_ROUND" --max-rounds "$BUGBOT_REVIEW_MAX_ROUNDS" \
    $([[ $BB_TRIAGE_FULL == true ]] && print -- --full) || return 1
  local -a f=("${(@0)$(python3 "$BUGBOT_HOME/lib/triage/triage.py" card --out "$BB_TRIAGE_FILE")}")
  bb_note "${f[1]} · ${f[5]} file(s), ${f[6]} line(s)"
}

bb_triage_tier_color() {
  case $1 in
    high)   REPLY="${c_red}${c_bold}high${c_reset}" ;;
    medium) REPLY="${c_yellow}medium${c_reset}" ;;
    *)      REPLY="${c_green}low${c_reset}" ;;
  esac
}

bb_triage_card() {
  local -a f
  local -i i n
  f=("${(@0)$(python3 "$BUGBOT_HOME/lib/triage/triage.py" card --out "$BB_TRIAGE_FILE")}")
  # 1 tier 2 depth 3 floor 4 reasons 5 files 6 lines 7 round 8 prev 9 note 10 exec_ok 11 denied
  # 12 ci_state 13 ci_line 14 config 15 decided 16 hook_outcome 17 hook_rule 18 hook_reason
  # 19 labels 20 agent_outcome 21 agent_line 22 nscope, scope..., nwarn, warnings...

  bb_say_bar
  bb_triage_tier_color "${f[1]}"
  bb_say_kv tier "$REPLY  ${c_dim}${f[4]}${c_reset}"
  bb_say_kv depth "${f[2]}$([[ -n ${f[3]} ]] && print -n "  ${c_dim}(floor: ${f[3]})${c_reset}")"
  [[ -n ${f[20]} ]] && bb_say_kv agent "${f[20]}  ${c_dim}${f[21]}${c_reset}"
  bb_say_kv diff "${f[5]} file(s) · ${f[6]} reviewable line(s)"

  n=${f[22]}
  local -i w=0
  for (( i = 1; i <= n; i++ )); do
    local -a s=("${(@ps:\t:)f[22+i]}")
    (( ${#s[1]} > w )) && w=${#s[1]}
  done
  for (( i = 1; i <= n; i++ )); do
    local -a s=("${(@ps:\t:)f[22+i]}")
    if (( i == 1 )); then
      bb_say_kv scope "${(r:w:)s[1]}  ${s[2]} file(s), ${s[3]} line(s)"
    else
      bb_say_line "          ${(r:w:)s[1]}  ${s[2]} file(s), ${s[3]} line(s)"
    fi
  done

  if [[ ${f[10]} == 1 ]]; then
    bb_say_kv local "checks allowed"
  else
    bb_say_kv local "${c_yellow}checks blocked${c_reset}  ${c_dim}${f[11]}${c_reset}"
  fi
  case ${f[12]} in
    pass)       REPLY="${c_green}pass${c_reset}" ;;
    fail)       REPLY="${c_red}fail${c_reset}" ;;
    pending)    REPLY="${c_yellow}pending${c_reset}" ;;
    unreadable) REPLY="${c_red}unreadable${c_reset}" ;;
    *)          REPLY="${c_dim}none${c_reset}" ;;
  esac
  bb_say_kv ci "$REPLY  ${c_dim}${f[13]}${c_reset}"
  [[ -n ${f[19]} ]] && bb_say_kv labels "${f[19]}"
  case ${f[16]} in
    review) bb_say_kv hooks "${c_green}review${c_reset}${f[18]:+  ${c_dim}${f[18]}${c_reset}}" ;;
    skip)   bb_say_kv hooks "${c_yellow}skip${c_reset}  ${f[18]}  ${c_dim}(${f[17]})${c_reset}" ;;
    wait)   bb_say_kv hooks "${c_yellow}wait${c_reset}  ${f[18]}  ${c_dim}(${f[17]})${c_reset}" ;;
  esac
  local -a rs=("${(@ps:\t:)f[9]}")
  local scope=${rs[1]} rnote=${rs[2]:-}
  case $scope in
    delta)     scope="${c_cyan}delta${c_reset}" ;;
    unchanged) scope="${c_green}unchanged${c_reset}" ;;
    capped)    scope="${c_yellow}capped${c_reset}" ;;
  esac
  bb_say_kv round "${f[7]}  $scope  ${c_dim}${rnote}${c_reset}"
  [[ -n ${f[15]} ]] && bb_say_line "          ${c_dim}already decided for this sha: ${f[15]} (answering again replaces it)${c_reset}"
  typeset -g BB_TRIAGE_SCOPE=${rs[1]}

  w=$(( 23 + n ))
  for (( i = 1; i <= ${f[w]}; i++ )); do bb_say_warn "${f[w+i]}"; done
  bb_say_bar

  typeset -g BB_TRIAGE_SUGGESTED=${f[2]} BB_TRIAGE_FLOOR=${f[3]}
  typeset -g BB_TRIAGE_HOOK=${f[16]} BB_TRIAGE_HOOK_REASON=${f[18]}
}

# One keypress: enter or o accepts the suggestion; q/s/d choose a depth; a aborts. Anything
# else is ignored and asked again: a stray key must never record a decision.
bb_triage_ask() {
  local key
  bb_is_tty || return 2
  print -rn -u2 -- "${c_cyan}◆${c_reset}  Depth ${c_dim}[enter ok (${BB_TRIAGE_SUGGESTED}) · q quick · s standard · d deep · a abort]${c_reset} "
  bb_tty_quiet
  stty -icanon min 1 time 0 </dev/tty 2>/dev/null
  REPLY=''
  while [[ -z $REPLY ]]; do
    read -r -s -k 1 -u 0 key </dev/tty || { REPLY=abort; break }
    case $key in
      $'\n'|$'\r'|o|O) REPLY=$BB_TRIAGE_SUGGESTED ;;
      q|Q) REPLY=quick ;;
      s|S) REPLY=standard ;;
      d|D) REPLY=deep ;;
      a|A|$'\e') REPLY=abort ;;
      $'\x03') bb_tty_restore; print -u2 ''; bb_on_interrupt ;;
    esac
  done
  bb_tty_restore
  print -u2 -- "$REPLY"
}

bb_cmd_triage() {
  local target='' repo='' depth='' confirm=true json=false color=true force=false lock meta owner name
  typeset -g BB_TRIAGE_FULL=false
  local assess_tier='' assess_depth='' assess_reason='' 
  local -a order=(quick standard deep)

  while (( $# )); do
    case $1 in
      --repo)       [[ -n ${2:-} ]] || { bb_triage_usage; return 2 }; repo=$2; shift 2 ;;
      --depth)      [[ ${2:-} == (ok|quick|standard|deep|abort) ]] \
                      || { print -u2 "bugbot triage: --depth must be ok, quick, standard, deep or abort"; return 2 }
                    depth=$2; shift 2 ;;
      --no-confirm) confirm=false; shift ;;
      --assess)     [[ -n ${2:-} && -n ${3:-} ]] || { print -u2 "bugbot triage: --assess needs <tier> <depth>"; return 2 }
                    assess_tier=$2 assess_depth=$3; shift 3 ;;
      --reason)     [[ -n ${2:-} ]] || { print -u2 "bugbot triage: --reason needs a text"; return 2 }
                    assess_reason=$2; shift 2 ;;
      --force)      force=true; shift ;;
      --full)       typeset -g BB_TRIAGE_FULL=true; shift ;;
      --json)       json=true; shift ;;
      --no-color)   color=false; shift ;;
      -h|--help)    bb_triage_usage; return 0 ;;
      -*)           print -u2 "bugbot triage: unknown option: $1"; bb_triage_usage; return 2 ;;
      *)            [[ -z $target ]] || { print -u2 "bugbot triage: give a single PR"; return 2 }
                    target=$1; shift ;;
    esac
  done
  [[ -n $target ]] || { bb_triage_usage; return 2 }
  [[ $confirm == false && -n $depth ]] && { print -u2 "bugbot triage: --depth and --no-confirm are mutually exclusive"; return 2 }
  [[ -n $assess_tier && -z $assess_reason ]] && { print -u2 "bugbot triage: --assess needs --reason"; return 2 }
  [[ -z $assess_tier && -n $assess_reason ]] && { print -u2 "bugbot triage: --reason goes with --assess"; return 2 }
  [[ -n $assess_tier && ( -n $depth || $confirm == false ) ]] && { print -u2 "bugbot triage: --assess records only the assessment; decide in a second call"; return 2 }

  source "$BUGBOT_HOME/lib/fetch/steps.sh"
  bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot triage: $REPLY"; return 2 }
  bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot triage: $REPLY"; return 2 }

  typeset -g BB_TRIAGE_REPO=${reply[1]} BB_TRIAGE_PR=$BB_PR_NUMBER
  bb_app_use "$BB_TRIAGE_REPO" || { print -u2 "bugbot triage: cannot act as the GitHub App on $BB_TRIAGE_REPO"; return 1 }
  typeset -g BB_TRIAGE_META=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH/meta.json
  typeset -g BB_TRIAGE_FILE=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH/triage.json
  owner=${BB_PR_PATH%%/*} name=${${BB_PR_PATH#*/}%%/*}
  typeset -g BB_TRIAGE_CONFIG=$BUGBOT_HOME/repos/$owner/$name.json
  [[ -f $BB_TRIAGE_META ]] || {
    print -u2 "bugbot triage: $BB_TRIAGE_REPO#$BB_TRIAGE_PR has not been fetched — run 'bugbot fetch' first"
    return 1
  }

  lock=$BUGBOT_DATA_DIR/state/locks/$BB_PR_PATH
  bb_term_init $color
  bb_run_dir_new triage || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }

  bb_say_open "bugbot triage"
  bb_say_bar
  bb_say_kv pr "$BB_TRIAGE_REPO#$BB_TRIAGE_PR  ${c_dim}$(python3 -c 'import json,sys;m=json.load(open(sys.argv[1]));print(m.get("author") or "", "·", m["head"]["sha"][:9], "→", m["base"]["branch"])' "$BB_TRIAGE_META")${c_reset}"
  bb_tilde "$BB_TRIAGE_CONFIG"
  if [[ -f $BB_TRIAGE_CONFIG ]]; then
    bb_say_kv rules "$REPLY"
  else
    bb_say_kv rules "${c_yellow}no config${c_reset}  ${c_dim}($REPLY)${c_reset}"
  fi

  bb_lock "$lock" || { bb_say_close "${c_red}PR in use by another process${c_reset} (pid $REPLY)"; return 1 }

  # The agent's assessment applies to the triage it read: no recompute, only the current head.
  if [[ -n $assess_tier ]]; then
    local out err
    [[ -f $BB_TRIAGE_FILE ]] || { bb_unlock "$lock"; bb_say_close "${c_red}no triage for this PR yet${c_reset} — run 'bugbot triage' first"; return 1 }
    [[ $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["head"])' "$BB_TRIAGE_FILE") == \
       $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["head"]["sha"])' "$BB_TRIAGE_META") ]] \
      || { bb_unlock "$lock"; bb_say_close "${c_red}triage is for an older head${c_reset} — run 'bugbot triage' again"; return 1 }
    out=$(python3 "$BUGBOT_HOME/lib/triage/triage.py" assess --out "$BB_TRIAGE_FILE" \
            --tier "$assess_tier" --depth "$assess_depth" --reason "$assess_reason" 2>&1) \
      || { bb_unlock "$lock"; bb_say_close "${c_red}assessment refused${c_reset} · ${out#triage: }"; return 2 }
    bb_unlock "$lock"
    bb_tilde "$BB_TRIAGE_FILE"
    bb_say_kv agent "$out  ${c_dim}${assess_tier}/${assess_depth} · ${assess_reason}${c_reset}"
    bb_say_close "${c_green}assessment recorded${c_reset} · $REPLY"
    [[ $json == true ]] && cat "$BB_TRIAGE_FILE"
    return 0
  fi

  # Hooks are passed as data, from the settings already validated by settings.py.
  bb_hooks_json > "$BB_RUN_DIR/hooks.json" \
    || { bb_say_close "${c_red}failed to read hooks${c_reset}"; return 1 }

  bb_cursor_hide
  bb_board_init "Read PR state" "Compute tier and scope"
  # Effective reviewers.max: the repository's override when present, else the global setting.
  # Reviews are only read when it is above 0, so it must be known before the state step.
  typeset -g BB_TRIAGE_REVIEWERS_MAX=$(python3 "$BUGBOT_HOME/lib/triage/triage.py" reviewers-max \
    --config "$BB_TRIAGE_CONFIG" --global "$BUGBOT_HOOKS_REVIEWERS_MAX") \
    || { bb_say_close "${c_red}invalid repo config${c_reset}"; return 1 }
  bb_run_step 1 state   bb_triage_step_state
  bb_run_step 2 compute bb_triage_step_compute || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show

  bb_triage_card

  # A hook said no: show why and stop, unless the reviewer explicitly forces it.
  if [[ $BB_TRIAGE_HOOK != review && $force != true && $confirm == true ]]; then
    bb_unlock "$lock"
    bb_tilde "$BB_TRIAGE_FILE"
    if [[ $BB_TRIAGE_HOOK == wait ]]; then
      bb_say_close "${c_yellow}waiting${c_reset} · $BB_TRIAGE_HOOK_REASON · run again later, or --force · $REPLY"
    else
      bb_say_close "${c_yellow}skipped${c_reset} · $BB_TRIAGE_HOOK_REASON · --force to review anyway · $REPLY"
    fi
    [[ $json == true ]] && cat "$BB_TRIAGE_FILE"
    return 0
  fi

  if [[ $confirm == false ]]; then
    bb_unlock "$lock"
    bb_tilde "$BB_TRIAGE_FILE"
    bb_say_close "${c_dim}no decision recorded · $REPLY${c_reset}"
    [[ $json == true ]] && cat "$BB_TRIAGE_FILE"
    return 0
  fi

  if [[ -z $depth ]]; then
    bb_triage_ask || {
      bb_unlock "$lock"
      bb_say_close "${c_red}confirmation needs a terminal${c_reset} — use --depth or --no-confirm"
      return 2
    }
    depth=$REPLY
  fi
  [[ $depth == ok ]] && depth=$BB_TRIAGE_SUGGESTED

  python3 "$BUGBOT_HOME/lib/triage/triage.py" decide --out "$BB_TRIAGE_FILE" --depth "$depth" \
    || { bb_unlock "$lock"; bb_say_close "${c_red}failed to record the decision${c_reset}"; return 1 }
  bb_unlock "$lock"

  bb_tilde "$BB_TRIAGE_FILE"
  if [[ $depth == abort ]]; then
    bb_say_close "${c_dim}aborted and recorded · $REPLY${c_reset}"
  elif [[ -n $BB_TRIAGE_FLOOR ]] && (( ${order[(Ie)$depth]} < ${order[(Ie)$BB_TRIAGE_FLOOR]} )); then
    bb_say_close "${c_yellow}$depth is below the floor ($BB_TRIAGE_FLOOR)${c_reset} · recorded · $REPLY"
  elif [[ $depth != $BB_TRIAGE_SUGGESTED ]]; then
    bb_say_close "${c_green}$depth${c_reset} ${c_dim}(suggested: $BB_TRIAGE_SUGGESTED)${c_reset} · $REPLY"
  else
    bb_say_close "${c_green}$depth${c_reset} · $REPLY"
  fi

  [[ $json == true ]] && cat "$BB_TRIAGE_FILE"
  return 0
}

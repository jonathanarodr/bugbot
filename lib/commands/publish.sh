# bugbot publish — post a PR round's findings as one GitHub review, after you confirm.
#
#   findings.json (+ translate.json) → payload → secret gate → preview → confirm → POST review
#
# One review per round, as review.author (@me = the gh user). Its body carries the ledger and the
# marker <!-- bugbot:vN sha= round= verdict= --> that later rounds read. Inline comments only for
# findings with no thread yet, on lines GitHub accepts; the rest under "Outside the diff". The bot
# maps REQUEST_CHANGES/COMMENT/PASS to the matching GitHub review event. The previous bot review is
# minimized as OUTDATED only when neither it nor this one reviewed anything; inline threads remain.
# Rules for the payload: lib/publish/publish.py. Gate: lib/publish/gate.py.

bb_publish_usage() {
  print -u2 "usage: bugbot publish <pr> [options]

<pr> is the PR URL, owner/name#N, or the number with --repo.

Post the current round's findings.json (from 'bugbot findings') as one
GitHub review, as review.author. Shows the review first and asks; nothing is
posted without a yes. The review event is REQUEST_CHANGES, COMMENT or APPROVE, based on findings.

Options:
  --repo <owner/name>  repository of the PR, when <pr> is just the number
  --dry-run            build and show the review, post nothing
  --yes                post without asking (after the same checks)
  --tldr <text>        the review's summary paragraph (in review.language)
  --translation <file> translate.json with the findings' text in review.language
  --json               print the payload to stdout (with --dry-run)
  --no-color           no colors
  -h, --help           this help

Exit: 0 posted (or dry run), 1 failure, 2 usage or a precondition, 3 secret gate."
}

bb_cmd_publish() {
  local target='' repo='' dry=false yes=false tldr='' translation='' json=false color=true
  local meta findings dir payload out author me prev_id prev_body threads lang line event
  local -a args f

  while (( $# )); do
    case $1 in
      --repo)        [[ -n ${2:-} ]] || { bb_publish_usage; return 2 }; repo=$2; shift 2 ;;
      --dry-run)     dry=true; shift ;;
      --yes)         yes=true; shift ;;
      --tldr)        [[ -n ${2:-} ]] || { bb_publish_usage; return 2 }; tldr=$2; shift 2 ;;
      --translation) [[ -f ${2:-} ]] || { print -u2 "bugbot publish: --translation needs a file"; return 2 }; translation=$2; shift 2 ;;
      --json)        json=true; shift ;;
      --no-color)    color=false; shift ;;
      -h|--help)     bb_publish_usage; return 0 ;;
      -*)            print -u2 "bugbot publish: unknown option: $1"; bb_publish_usage; return 2 ;;
      *)             [[ -z $target ]] || { print -u2 "bugbot publish: give a single PR"; return 2 }
                     target=$1; shift ;;
    esac
  done
  [[ -n $target ]] || { bb_publish_usage; return 2 }
  [[ $json == true && $dry == false ]] && { print -u2 "bugbot publish: --json goes with --dry-run"; return 2 }

  source "$BUGBOT_HOME/lib/fetch/steps.sh"
  bb_fetch_parse_target "$target" "$repo" || { print -u2 "bugbot publish: $REPLY"; return 2 }
  bb_pr_names "${reply[1]}" "${reply[2]}" || { print -u2 "bugbot publish: $REPLY"; return 2 }
  repo=${reply[1]}
  bb_app_use "$repo" || { print -u2 "bugbot publish: cannot act as the GitHub App on $repo"; return 1 }
  dir=$BUGBOT_DATA_DIR/cache/$BB_PR_PATH
  meta=$dir/meta.json findings=$dir/findings.json
  [[ -f $meta ]] || { print -u2 "bugbot publish: $repo#$BB_PR_NUMBER has not been fetched"; return 2 }
  [[ -f $findings ]] || { print -u2 "bugbot publish: no findings.json for this round — run 'bugbot findings' first"; return 2 }

  bb_term_init $color
  bb_run_dir_new publish || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }
  local lock=$BUGBOT_DATA_DIR/state/locks/$BB_PR_PATH
  bb_lock "$lock" || { print -u2 "bugbot publish: PR in use by another process (pid $REPLY)"; return 1 }
  {
    # The PR must still be at the head that was reviewed: posting findings of an older head anchors
    # comments on lines that moved, and GitHub would attach them to the wrong commit.
    local live_head
    live_head=$(bb_gh pr view "$BB_PR_NUMBER" --repo "$repo" --json headRefOid,state --jq '.state + " " + .headRefOid' 2>&1) \
      || { print -u2 "bugbot publish: reading the PR failed: $live_head"; return 1 }
    [[ ${live_head%% *} == OPEN ]] || { print -u2 "bugbot publish: the PR is ${${live_head%% *}:l}"; return 2 }
    [[ ${live_head#* } == $(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["head"])' "$findings") ]] \
      || { print -u2 "bugbot publish: the PR moved to ${${live_head#* }[1,9]} since this round was reviewed — fetch, triage and review it again"; return 2 }

    # Who posts: review.author. @me is the gh user; on the App's repositories it is the App's bot
    # login (bb_app_use); any other bot login must be what gh acts as.
    bb_gh_identity || { print -u2 "bugbot publish: gh is not logged in: $REPLY"; return 1 }
    me=$REPLY
    author=$BUGBOT_REVIEW_AUTHOR
    [[ $author == @me ]] && author=$me
    [[ ${author:l} == ${me:l} ]] \
      || { print -u2 "bugbot publish: review.author is $author but gh acts as $me"; return 2 }
    [[ -z $BB_APP_ACTIVE && -n $BB_GH_ENV_TOKEN ]] && print -u2 "bugbot publish: note: gh uses \$$BB_GH_ENV_TOKEN, not the keyring login"

    # The previous bot review (its ledger) and the fingerprints already carried by inline threads.
    prev_body=$BB_RUN_DIR/previous-body.md threads=$BB_RUN_DIR/threads.txt
    out=$(BB_AUTHOR=$author BB_MARKER="<!-- bugbot:v$BUGBOT_MARKER_VERSION " bb_gh api "repos/$repo/pulls/$BB_PR_NUMBER/reviews" --paginate \
      --jq '[.[] | select((.user.login | ascii_downcase) == (env.BB_AUTHOR | ascii_downcase)) | select(.body | contains(env.BB_MARKER))] | last // empty | [(.id|tostring), .body] | @json' 2>&1) \
      || { print -u2 "bugbot publish: reading the PR's reviews failed: $out"; return 1 }
    prev_id=''
    if [[ -n $out ]]; then
      prev_id=$(python3 -c 'import json,sys;print(json.loads(sys.argv[1])[0])' "$out")
      python3 -c 'import json,sys;sys.stdout.write(json.loads(sys.argv[1])[1])' "$out" > "$prev_body"
    else
      : > "$prev_body"
    fi
    BB_AUTHOR=$author bb_gh api "repos/$repo/pulls/$BB_PR_NUMBER/comments" --paginate \
      --jq '.[] | select((.user.login | ascii_downcase) == (env.BB_AUTHOR | ascii_downcase)) | [.body | scan("bugbot:f=([0-9a-f,]+)") | .[0]] | join(",")' \
      > "$threads" 2>"$BB_RUN_DIR/threads.err" || { print -u2 "bugbot publish: reading the PR's comments failed: $(<"$BB_RUN_DIR/threads.err")"; return 1 }

    # Language: anything but English needs the translation file, or the review goes out in English.
    lang=$BUGBOT_REVIEW_LANGUAGE
    if [[ $lang != en* && -z $translation && -f $dir/translate.json ]]; then translation=$dir/translate.json; fi
    [[ $lang != en* && -z $translation ]] \
      && print -u2 "bugbot publish: note: review.language is $lang but there is no translate.json: findings and summary go out in English"

    payload=$BB_RUN_DIR/payload.json
    args=(build --findings "$findings" --meta "$meta" --marker-version "$BUGBOT_MARKER_VERSION"
          --previous-body "$prev_body" --threads "$threads" --language "$lang"
          --repo-config "$BUGBOT_HOME/repos/${BB_PR_PATH%%/*}/${${BB_PR_PATH#*/}%%/*}.json")
    [[ -n $translation ]] && args+=(--translation "$translation")
    [[ -n $tldr ]] && args+=(--tldr "$tldr")
    python3 "$BUGBOT_HOME/lib/publish/publish.py" "${args[@]}" > "$payload" 2> "$BB_RUN_DIR/counts" \
      || { print -u2 "bugbot publish: $(<"$BB_RUN_DIR/counts")"; return 1 }
    f=("${(@ps:\t:)$(<"$BB_RUN_DIR/counts")}")
    event=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["event"])' "$payload")
    # 1 verdict 2 inline 3 outside 4 already-threaded 5 fixed 6 cut 7 looked (0: nothing reviewed)

    local clone
    clone=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["paths"]["clone"])' "$meta")
    out=$(python3 "$BUGBOT_HOME/lib/publish/gate.py" scan --payload "$payload" --clone "$clone" 2>&1) || {
      print -u2 "bugbot publish: blocked by the secret gate ($out). Nothing was posted; do not edit around it."
      return 3
    }

    if [[ $json == true ]]; then cat "$payload"; return 0; fi

    bb_say_open "bugbot publish"
    bb_say_bar
    bb_say_kv pr "$repo#$BB_PR_NUMBER  ${c_dim}as $author · ${lang}${c_reset}"
    bb_say_kv review "${f[1]} · GitHub event: $event · ${f[2]} inline · ${f[3]} outside the diff · ${f[4]} already under discussion · ${f[5]} fixed · ${f[6]} cut"
    bb_say_bar
    python3 - "$payload" <<'PY' >&2
import json, re, sys
p = json.load(open(sys.argv[1]))
body = p["body"].split("<!-- bugbot:ledger", 1)[0].rstrip()
for line in body.splitlines():
    print("│  " + line)
for c in p.get("comments", []):
    print("│")
    print(f"│  ── inline · {c['path']}:{c['line']} ({c['side']})")
    # One comment can carry several findings on the same line, each ending in its own marker.
    for line in re.sub(r"\n?<!--\s*bugbot:f=[0-9a-f,]+\s*-->", "", c["body"]).splitlines():
        print("│  " + line)
PY
    bb_say_bar

    if [[ $dry == true ]]; then
      bb_say_close "${c_dim}dry run · nothing posted${c_reset}"
      return 0
    fi
    if [[ $yes != true ]]; then
      bb_confirm "Post this $event review to $repo#$BB_PR_NUMBER as $author?" || {
        local rc=$?
        bb_say_close "${c_dim}not posted${c_reset}"
        (( rc == 2 )) && { print -u2 "bugbot publish: confirmation needs a terminal — use --yes"; return 2 }
        return 0
      }
    fi

    out=$(bb_gh api -X POST "repos/$repo/pulls/$BB_PR_NUMBER/reviews" --input "$payload" --jq '.html_url' 2>&1) \
      || { bb_say_close "${c_red}posting failed${c_reset}: $out"; return 1 }
    local url=$out

    # Collapse the previous bot review only when neither it nor this one reviewed anything: a
    # review that looked at code stays as it was. The new review is already up, so a failed
    # collapse costs tidiness, never the review.
    local minimized=''
    if [[ -n $prev_id && ${f[7]} == 0 ]] && grep -qE 'bugbot:v[0-9]+ sha=[0-9a-f]+ round=[0-9]+ verdict=(SKIPPED|INVALID_INVOCATION) ' "$prev_body"; then
      local node
      node=$(bb_gh api "repos/$repo/pulls/$BB_PR_NUMBER/reviews/$prev_id" --jq .node_id 2>/dev/null)
      [[ -n $node ]] && bb_gh api graphql -f subject="$node" -f query='mutation($subject: ID!) {
        minimizeComment(input: {subjectId: $subject, classifier: OUTDATED}) { minimizedComment { isMinimized } } }' \
        --jq '.data.minimizeComment.minimizedComment.isMinimized' 2>/dev/null | grep -qx true && minimized=" · previous review minimized"
    fi
    bb_say_close "${c_green}posted${c_reset}${minimized} · $url"
    return 0
  } always {
    bb_unlock "$lock"
  }
}

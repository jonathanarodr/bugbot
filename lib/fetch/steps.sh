# fetch steps: resolve the target, read the PR, fetch refs, write the diff, prepare a worktree.
#
# Each step runs in a subshell (bb_run_step), so state is passed through files in the run
# directory, read back by the command once the step passes:
#   target  repo · clone path · remote · pr number
#   pr      the PR fields meta.py validated (NUL-separated)
#
# The canonical clone is never checked out: only refs are fetched into it, under
# refs/bugbot/<owner>/<name>/pull/<N>/ (see lib/core/pr.sh), so the branch and local
# changes of whoever works in it are untouched.

# Accepts https://github.com/owner/name/pull/N[/...], owner/name#N, or N with --repo.
bb_fetch_parse_target() {
  local target=$1 repo=$2

  REPLY='' reply=()
  if [[ $target =~ '^https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)/pull/([0-9]+)(/.*)?([?#].*)?$' ]]; then
    reply=("${match[1]}/${match[2]}" "${match[3]}")
  elif [[ $target =~ '^([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)#([0-9]+)$' ]]; then
    reply=("${match[1]}/${match[2]}" "${match[3]}")
  elif [[ $target =~ '^[0-9]+$' ]]; then
    [[ -n $repo ]] || { REPLY='give --repo owner/name with the PR number'; return 1 }
    [[ $repo =~ '^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$' ]] || { REPLY="invalid --repo: $repo"; return 1 }
    reply=("$repo" "$target")
  else
    REPLY="unrecognized target: $target (use the PR URL, owner/name#N or N --repo owner/name)"
    return 1
  fi

  if [[ -n $repo && ${(L)repo} != ${(L)reply[1]} ]]; then
    REPLY="--repo $repo does not match the PR ${reply[1]}#${reply[2]}"
    return 1
  fi
  bb_pr_valid "${reply[1]}" "${reply[2]}"
}

bb_fetch_step_resolve() {
  local out f
  local -a fields

  out=$(python3 "$BUGBOT_HOME/lib/core/workspace.py" lookup \
          --current "$BUGBOT_WORKSPACE_FILE" --repo "$BB_FETCH_REPO")
  case $? in
    0) ;;
    3) print -r -- "$BB_FETCH_REPO is not in workspace.json — run 'bugbot setup' and select it"
       return 1 ;;
    *) return 1 ;;
  esac

  fields=("${(@0)out}")
  [[ -d ${fields[2]}/.git ]] || { print -r -- "clone not found: ${fields[2]}"; return 1 }

  # The remote must still point at this repository: a clone re-pointed after setup would
  # otherwise fetch someone else's pull/N/head.
  f=$(bb_git "${fields[2]}" remote get-url "${fields[3]}" 2>/dev/null) \
    || { print -r -- "remote ${fields[3]} does not exist in ${fields[2]}"; return 1 }
  bb_github_slug "$f"
  if [[ ${(L)REPLY} != ${(L)fields[1]} ]]; then
    print -r -- "remote ${fields[3]} of ${fields[2]} points at ${REPLY:-$f}, not ${fields[1]}"
    return 1
  fi

  print -rn -- "${(pj:\0:)fields}" > "$BB_RUN_DIR/target"
  bb_tilde "${fields[2]}"
  bb_note "$REPLY"
}

bb_fetch_step_pr() {
  local json=$BB_RUN_DIR/pr.json err
  local -a f

  err=$(bb_gh pr view "$BB_FETCH_PR" --repo "$BB_FETCH_REPO" --json \
    number,url,title,body,state,isDraft,author,headRefOid,headRefName,headRepositoryOwner,isCrossRepository,baseRefName,baseRefOid,additions,deletions,changedFiles \
    2>&1 > "$json") || { print -r -- "gh pr view failed: $err"; return 1 }

  python3 "$BUGBOT_HOME/lib/fetch/meta.py" pr --json "$json" > "$BB_RUN_DIR/pr" || return 1
  f=("${(@0)$(<"$BB_RUN_DIR/pr")}")

  # number state draft head base base_sha cross head_owner files additions deletions author
  bb_note "${f[2]:l}$([[ ${f[3]} == true ]] && print -n ' · draft') · ${f[4][1,9]} · ${f[9]} file(s) +${f[10]}/-${f[11]} · ${f[12]}"
  [[ ${f[2]} == OPEN && ${f[3]} == false ]] || return $BB_STEP_WARN
  return 0
}

# Fetches into refs/bugbot/ only, never into a branch. The base is fetched every time: a
# stale base turns `base...head` into a diff that drags in commits from the base branch.
bb_fetch_step_refs() {
  local clone remote base head_sha got
  local -a t p

  t=("${(@0)$(<"$BB_RUN_DIR/target")}")
  p=("${(@0)$(<"$BB_RUN_DIR/pr")}")
  clone=${t[2]} remote=${t[3]} base=${p[5]} head_sha=${p[4]}

  print -r -- "git fetch $remote refs/heads/$base pull/$BB_FETCH_PR/head"
  bb_git "$clone" fetch --no-tags --no-write-fetch-head --force "$remote" \
    "+refs/heads/$base:$BB_FETCH_BASE_REF" \
    "+refs/pull/$BB_FETCH_PR/head:$BB_FETCH_HEAD_REF" || return 1

  got=$(bb_git "$clone" rev-parse --verify --quiet "$BB_FETCH_HEAD_REF^{commit}") || return 1
  if [[ $got != $head_sha ]]; then
    print -r -- "fetched head ($got) differs from the one GitHub reported ($head_sha): a push landed during the fetch, run it again"
    bb_note "head changed during the fetch"
    return 1
  fi

  bb_note "base ${$(bb_git "$clone" rev-parse --short "$BB_FETCH_BASE_REF")} · head ${got[1,9]}"
}

# Diff computed locally from the two refs, the same three-dot range GitHub shows.
bb_fetch_step_diff() {
  local clone remote dir=$BB_FETCH_DIR mb base_sha line
  local -a t p diffopts

  t=("${(@0)$(<"$BB_RUN_DIR/target")}")
  p=("${(@0)$(<"$BB_RUN_DIR/pr")}")
  clone=${t[2]} remote=${t[3]}
  mkdir -p "$dir" || return 1

  # A new head starts a new review round: the previous round's candidates, verdicts and preflight
  # plan no longer apply, and its findings.json is kept as previous-findings.json, for carrying
  # findings over. The agents' files carry no head: this is what keeps them current. A missing or
  # unreadable meta.json means the head they were written for is unknown, so they go too.
  local old_head new_head
  new_head=$(bb_git "$clone" rev-parse "$BB_FETCH_HEAD_REF") || return 1
  [[ -f $dir/meta.json ]] && \
    old_head=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["head"]["sha"])' "$dir/meta.json" 2>/dev/null)
  if [[ $old_head != $new_head ]]; then
    rm -rf -- "$dir/candidates" "$dir/verdicts" "$dir/preflight.json"
    [[ -f $dir/findings.json ]] && mv -f -- "$dir/findings.json" "$dir/previous-findings.json"
  fi
  mkdir -p "$dir/candidates" "$dir/verdicts" || return 1

  mb=$(bb_git "$clone" merge-base "$BB_FETCH_BASE_REF" "$BB_FETCH_HEAD_REF") \
    || { print -r -- "no merge base between base and head"; return 1 }
  base_sha=$(bb_git "$clone" rev-parse "$BB_FETCH_BASE_REF") || return 1

  diffopts=(--no-ext-diff --no-textconv --no-color --find-renames)
  bb_git "$clone" diff $diffopts "$mb" "$BB_FETCH_HEAD_REF" > "$dir/diff.patch.tmp" || return 1
  bb_git "$clone" diff $diffopts --name-status -z "$mb" "$BB_FETCH_HEAD_REF" > "$BB_RUN_DIR/name-status" || return 1
  bb_git "$clone" diff $diffopts --numstat -z "$mb" "$BB_FETCH_HEAD_REF" > "$BB_RUN_DIR/numstat" || return 1
  mv -f -- "$dir/diff.patch.tmp" "$dir/diff.patch"

  line=$(python3 "$BUGBOT_HOME/lib/fetch/meta.py" write \
    --json "$BB_RUN_DIR/pr.json" --repo "${t[1]}" --dir "$dir" --clone "$clone" \
    --name-status "$BB_RUN_DIR/name-status" --numstat "$BB_RUN_DIR/numstat" \
    --head-ref "$BB_FETCH_HEAD_REF" --base-ref "$BB_FETCH_BASE_REF" \
    --base-sha "$base_sha" --merge-base "$mb" \
    --worktree "${BB_FETCH_WORKTREE_PATH:-}") || return 1

  local -a s=("${(@ps:\t:)line}")
  bb_note "${s[1]} file(s) +${s[2]}/-${s[3]}${s[4]}"

  # Review rounds come from GitHub, not from local state: the last review by `review.author` that
  # carries the bot's marker says which head it reviewed and which round it was. The delta compares
  # the PR then and now (lib/fetch/delta.py).
  local rounds prev note=""
  rounds=$(python3 "$BUGBOT_HOME/lib/fetch/rounds.py" read --repo "${t[1]}" --pr "$BB_FETCH_PR" \
    --author "$BUGBOT_REVIEW_AUTHOR" --version "$BUGBOT_MARKER_VERSION" \
    --head "$(bb_git "$clone" rev-parse "$BB_FETCH_HEAD_REF")" --clone "$clone") \
    || { print -r -- "reading the PR's reviews failed"; return 1 }
  prev=$(python3 -c 'import json,sys;l=json.loads(sys.argv[1])["last"];print(l["head"] if l else "")' "$rounds")
  rm -f -- "$dir/delta.patch"
  if [[ -n $prev ]]; then
    # The reviewed head may be gone from the branch (force-push): fetch it by sha, then pin it,
    # so the comparison never depends on what the branch still contains.
    if ! bb_git "$clone" cat-file -e "$prev^{commit}" 2>/dev/null; then
      print -r -- "git fetch $remote $prev (last reviewed head)"
      bb_git "$clone" fetch --no-tags --no-write-fetch-head "$remote" "$prev" 2>/dev/null || true
    fi
    if bb_git "$clone" cat-file -e "$prev^{commit}" 2>/dev/null; then
      bb_git "$clone" update-ref "$BB_PR_REFS/reviewed" "$prev" || return 1
      local prev_mb delta
      prev_mb=$(bb_git "$clone" merge-base "$BB_FETCH_BASE_REF" "$prev") || prev_mb=$(bb_git "$clone" merge-base "$mb" "$prev")
      delta=$(python3 "$BUGBOT_HOME/lib/fetch/delta.py" compute --clone "$clone" \
        --prev-head "$prev" --prev-base "$prev_mb" \
        --head "$BB_FETCH_HEAD_REF" --base "$mb" --out "$dir/delta.patch") || return 1
    else
      delta='{"from_head": null, "files": [], "lines": 0, "changes": [], "patch": null, "unavailable": "last reviewed head is not fetchable"}'
    fi
    note=$(python3 "$BUGBOT_HOME/lib/fetch/meta.py" set-delta --dir "$dir" --delta "$delta" --rounds "$rounds") || return 1
  else
    note=$(python3 "$BUGBOT_HOME/lib/fetch/meta.py" set-delta --dir "$dir" --rounds "$rounds") || return 1
  fi
  bb_note "${s[1]} file(s) +${s[2]}/-${s[3]}${s[4]} · $note"
  [[ -z ${s[4]} ]] || return $BB_STEP_WARN
}

# A worktree for checks that need the files on disk. Reused across rounds: moved to the new
# head with reset --hard and never cleaned, so untracked build output and local.properties
# stay and the next build is incremental. Detached, so no branch is created in the clone.
bb_fetch_step_worktree() {
  local clone wt=$BB_FETCH_WORKTREE_PATH action
  local -a t

  t=("${(@0)$(<"$BB_RUN_DIR/target")}")
  clone=${t[2]}

  if [[ -e $wt/.git ]] && bb_git "$wt" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
     && [[ $(bb_git "$wt" rev-parse --git-common-dir 2>/dev/null) == ${clone}/.git ]]; then
    bb_git "$wt" reset --hard --quiet "$BB_FETCH_HEAD_REF" || return 1
    action=updated
  else
    if [[ -e $wt ]]; then
      print -r -- "directory exists but is not a worktree of this clone, recreating: $wt"
      bb_git "$clone" worktree remove --force "$wt" 2>/dev/null
      rm -rf -- "$wt"
    fi
    mkdir -p "${wt:h}"
    bb_git "$clone" worktree add --quiet --detach "$wt" "$BB_FETCH_HEAD_REF" || return 1
    action=created
  fi

  bb_tilde "$wt"
  bb_note "$action · $REPLY"
}

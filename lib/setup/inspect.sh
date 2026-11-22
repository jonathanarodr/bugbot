# Step: describe each clone from local git data only — no network.
#
# A clone qualifies when a remote points at github.com (or an ssh alias for it) under the
# configured organisation. Credentials embedded in a remote URL are dropped: only the
# normalised https URL is kept, so a token can never reach workspace.json.
#
# Output: $BB_RUN_DIR/candidates.tsv
#   key  path  clone_url  remote  default_branch  dirty(0|1)  fetched_epoch

bb_inspect_one() {
  local dir=$1 remote url default head fetched=0 dirty=0
  local -a remotes stamp

  remotes=(${(f)"$(git -C "$dir" remote 2>/dev/null)"})
  (( ${#remotes} )) || return 0
  (( ${remotes[(Ie)origin]} )) && remotes=(origin ${remotes:#origin})

  for remote in $remotes; do
    url=$(git -C "$dir" remote get-url "$remote" 2>/dev/null) || continue
    bb_github_slug "$url" || continue
    [[ -z $BB_SETUP_ORG || ${(L)REPLY%%/*} == ${(L)BB_SETUP_ORG} ]] || continue

    head=$(git -C "$dir" symbolic-ref --quiet --short "refs/remotes/$remote/HEAD" 2>/dev/null)
    default=${head#$remote/}

    [[ -n $(git -C "$dir" status --porcelain --untracked-files=no 2>/dev/null) ]] && dirty=1

    if zstat -A stamp +mtime "$dir/.git/FETCH_HEAD" 2>/dev/null; then
      fetched=${stamp[1]}
    fi

    print -r -- "${REPLY}"$'\t'"${dir}"$'\t'"https://github.com/${REPLY}.git"$'\t'"${remote}"$'\t'"${default}"$'\t'"${dirty}"$'\t'"${fetched}"
    return 0
  done
}

# Fills in a default branch the local clone does not know (origin/HEAD never set),
# but only when --remote allows the network.
bb_inspect_remote_default() {
  local file=$1 line
  local -a f
  local tmp=$file.tmp

  : > "$tmp"
  while IFS= read -r line; do
    f=("${(@ps:\t:)line}")
    if [[ -z ${f[5]} ]]; then
      f[5]=$(gh api "repos/${f[1]}" --jq .default_branch 2>/dev/null) || f[5]=''
    fi
    print -r -- "${(pj:\t:)f}"
  done < "$file" >> "$tmp"
  mv -f -- "$tmp" "$file"
}

bb_setup_inspect() {
  local dir out=$BB_RUN_DIR/candidates.tsv parts=$BB_RUN_DIR/inspect.d
  local -a clones rows
  local -i i=0 repos dupes unknown

  clones=(${(f)"$(<"$BB_RUN_DIR/clones.txt")"})
  mkdir -p "$parts"

  for dir in $clones; do
    (( ++i ))
    bb_inspect_one "$dir" > "$parts/$i.tsv" &
    (( i % BUGBOT_INSPECT_JOBS == 0 )) && wait
  done
  wait

  cat "$parts"/*.tsv(N) 2>/dev/null | sort -f -t $'\t' -k1,1 -k2,2 > "$out"
  rm -rf -- "$parts"

  [[ $BB_SETUP_REMOTE == true ]] && bb_inspect_remote_default "$out"

  rows=(${(f)"$(<"$out")"})
  repos=${#${(u)${(L)rows%%$'\t'*}}}
  dupes=$(( ${#rows} - repos ))
  unknown=${#${(M)rows:#*$'\t'$'\t'[01]$'\t'*}}

  local owner=${BB_SETUP_ORG:-GitHub}
  print -r -- "${#clones} clones inspected, ${#rows} from ${BB_SETUP_ORG:+org }$owner"
  (( repos > 0 )) || { bb_note "no $owner repos"; return 1 }

  bb_note "$repos $owner repos · $dupes duplicate clone(s)"
  if (( unknown > 0 )); then
    print -r -- "$unknown clone(s) without origin/HEAD: run with --remote or 'git remote set-head origin -a'"
    bb_note "$repos $owner repos · $dupes duplicate clone(s) · $unknown without default branch"
    return $BB_STEP_WARN
  fi
  return 0
}

# Step: who the gh CLI acts as. Comments will be posted as this user, so an ambient
# token silently overriding the keyring login is reported here, before anything writes.
bb_setup_identity() {
  local login var
  local -i warn=0

  for var in GH_TOKEN GITHUB_TOKEN; do
    if [[ -n ${(P)var:-} ]]; then
      print -r -- "$var is set in the environment and overrides the gh login"
      warn=1
    fi
  done

  login=$(gh api user --jq .login 2>&1) || {
    print -r -- "gh api user failed: $login"
    bb_note "gh not authenticated"
    return $BB_STEP_WARN
  }

  print -r -- "$login" > "$BB_RUN_DIR/gh-user"
  bb_note "$login"
  (( warn )) && return $BB_STEP_WARN
  return 0
}

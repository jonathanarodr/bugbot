# Removal of what the bot left behind. Shared by `clean` (merged PRs only) and `purge`
# (everything); purge sources this file.
#
# Nothing outside the bot's own footprint is touched: files are only removed under
# BUGBOT_DATA_DIR, refs only under refs/bugbot/, and worktree registrations only when they
# point into the bot's worktrees/ directory. `git worktree prune` is never run, because it
# would also drop registrations that other tools keep in the same clones.
#
# BUGBOT_RETRO_DIR is never removed here: `clean` keeps the retro record of merged PRs,
# since that is exactly what the retro reads. Only `purge --retro` deletes it.

# rm -rf restricted to the data directory; a symlink resolving elsewhere is refused.
bb_clean_rm() {
  local target=$1 root=${2:-$BUGBOT_DATA_DIR}
  root=${root:A}

  [[ -e $target || -L $target ]] || return 0
  if [[ ${target:A} != $root/?* || ${target:h:A} != $root(|/*) ]]; then
    print -r -- "refused, outside the bot directory: $target"
    return 1
  fi
  rm -rf -- "$target"
}

# Drops a registration whose directory is already gone — what prune would do, for one entry.
bb_clean_rm_admin() {
  local clone=$1 admin=$2 points

  [[ ${admin:h:A} == ${clone:A}/.git/worktrees ]] || { print -r -- "unexpected registration: $admin"; return 1 }
  points=$(<"$admin/gitdir" 2>/dev/null)
  [[ ${points:h} == ${BUGBOT_DATA_DIR}/worktrees/* || ${points:h:A} == ${BUGBOT_DATA_DIR:A}/worktrees/* ]] \
    || { print -r -- "registration does not point into the bot directory: $admin"; return 1 }
  rm -rf -- "$admin"
}

bb_clean_rm_empty_parents() {
  local dir=$1 stop=${BUGBOT_DATA_DIR:A}
  while [[ ${dir:A} == $stop/*/* ]] && rmdir -- "$dir" 2>/dev/null; do dir=${dir:h}; done
}

bb_clean_inventory_run() {
  python3 "$BUGBOT_HOME/lib/clean/inventory.py" \
    --data-dir "$BUGBOT_DATA_DIR" --workspace "$BUGBOT_WORKSPACE_FILE" > "$BB_RUN_DIR/inventory"
}

# Inventory → arrays: BB_CL_* one per PR, BB_CW_* one per bot worktree registration,
# BB_CC_* one per clone that has bot refs.
bb_clean_parse() {
  local -a f
  local -i i=1

  typeset -ga BB_CL_OWNER=() BB_CL_NAME=() BB_CL_PR=() BB_CL_SHOW=() BB_CL_CLONE=() \
              BB_CL_CACHE=() BB_CL_WTDIR=() BB_CL_WTADM=() BB_CL_REFS=() BB_CL_LOCK=()
  typeset -ga BB_CW_CLONE=() BB_CW_ADMIN=() BB_CW_PATH=() BB_CW_EXISTS=()
  typeset -ga BB_CC_CLONE=() BB_CC_SHOW=() BB_CC_REFS=()

  [[ -s $BB_RUN_DIR/inventory ]] || return 0
  f=("${(@0)$(<"$BB_RUN_DIR/inventory")}")

  while (( i <= ${#f} )); do
    case ${f[i]} in
      pr)
        BB_CL_OWNER+=("${f[i+1]}") BB_CL_NAME+=("${f[i+2]}") BB_CL_PR+=("${f[i+3]}")
        BB_CL_SHOW+=("${f[i+4]}") BB_CL_CLONE+=("${f[i+5]}") BB_CL_CACHE+=("${f[i+6]}")
        BB_CL_WTDIR+=("${f[i+7]}") BB_CL_WTADM+=("${f[i+8]}") BB_CL_REFS+=("${f[i+9]}")
        BB_CL_LOCK+=("${f[i+10]}")
        (( i += 11 )) ;;
      wt)
        BB_CW_CLONE+=("${f[i+1]}") BB_CW_ADMIN+=("${f[i+2]}") BB_CW_PATH+=("${f[i+3]}")
        BB_CW_EXISTS+=("${f[i+4]}")
        (( i += 5 )) ;;
      clone)
        BB_CC_CLONE+=("${f[i+1]}") BB_CC_SHOW+=("${f[i+2]}") BB_CC_REFS+=("${f[i+3]}")
        (( i += 4 )) ;;
      '') (( i++ )) ;;
      *)  print -u2 "bugbot: invalid inventory (field ${f[i]})"; return 1 ;;
    esac
  done
}

# Summary of what a PR has, for the list: "cache · worktree · 2 refs".
bb_clean_parts() {
  local -i i=$1
  local -a parts=()

  (( BB_CL_CACHE[i] )) && parts+=(cache)
  (( BB_CL_WTDIR[i] )) || [[ -n ${BB_CL_WTADM[i]} ]] && parts+=(worktree)
  (( BB_CL_REFS[i] )) && parts+=("${BB_CL_REFS[i]} ref(s)")
  (( BB_CL_LOCK[i] == 1 )) && parts+=("stale lock")
  REPLY=${(j: · :)parts}
}

# Removes one PR: worktree, refs, cache and lock. Names come from bb_pr_names only.
# Exit 3 when the PR is in use by another process (skipped, not an error).
bb_clean_remove_pr() {
  local owner=$1 name=$2 pr=$3 clone=$4 admin=$5 lock wt ref
  local -a refs

  bb_pr_names "$owner/$name" "$pr" || { print -r -- "$REPLY"; return 1 }
  lock=$BUGBOT_DATA_DIR/state/locks/$BB_PR_PATH
  wt=$BUGBOT_DATA_DIR/worktrees/$BB_PR_PATH

  if ! bb_lock "$lock"; then
    print -r -- "$owner/$name#$pr in use (pid $REPLY): kept"
    return 3
  fi

  if [[ -n $admin && -n $clone ]]; then
    if [[ -d $wt ]]; then
      bb_git "$clone" worktree remove --force "$wt" || return 1
    else
      bb_clean_rm_admin "$clone" "$admin" || return 1
    fi
  fi
  bb_clean_rm "$wt" || return 1

  if [[ -n $clone && -d $clone/.git ]]; then
    refs=(${(f)"$(bb_git "$clone" for-each-ref --format='%(refname)' "$BB_PR_REFS/")"})
    for ref in $refs; do bb_git "$clone" update-ref -d "$ref" || return 1; done
  fi

  bb_clean_rm "$BUGBOT_DATA_DIR/cache/$BB_PR_PATH" || return 1
  bb_unlock "$lock"

  bb_clean_rm_empty_parents "$BUGBOT_DATA_DIR/cache/$BB_PR_PATH:h"
  bb_clean_rm_empty_parents "$BUGBOT_DATA_DIR/worktrees/$BB_PR_PATH:h"
  bb_clean_rm_empty_parents "$BUGBOT_DATA_DIR/state/locks/$BB_PR_PATH:h"
  print -r -- "removed: $owner/$name#$pr${refs:+ (${#refs} ref(s))}"
}

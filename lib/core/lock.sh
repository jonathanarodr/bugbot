# Per-pull-request lock, so two sessions cannot move the same worktree at the same time.
#
# mkdir is atomic. The owner's pid is recorded inside, and a lock whose process no longer
# exists is taken over instead of blocking forever after a crash or a kill -9.

typeset -ga BB_LOCKS_HELD=()

bb_lock() {
  local dir=$1 owner

  mkdir -p "${dir:h}" || return 1
  if ! mkdir "$dir" 2>/dev/null; then
    owner=$(<"$dir/pid" 2>/dev/null)
    if [[ $owner == <-> ]] && kill -0 $owner 2>/dev/null; then
      REPLY=$owner
      return 1
    fi
    rm -rf -- "$dir"
    mkdir "$dir" 2>/dev/null || { REPLY=?; return 1 }
  fi

  print -r -- $$ > "$dir/pid"
  BB_LOCKS_HELD+=("$dir")
  return 0
}

bb_unlock() {
  local dir=$1
  [[ -d $dir && $(<"$dir/pid" 2>/dev/null) == $$ ]] && rm -rf -- "$dir"
  BB_LOCKS_HELD=(${BB_LOCKS_HELD:#$dir})
}

bb_unlock_all() {
  local dir
  for dir in "${BB_LOCKS_HELD[@]}"; do bb_unlock "$dir"; done
}

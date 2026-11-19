# Step: find every git clone under the configured roots.
#
# Only directories named .git count: linked worktrees and submodules have a .git *file*,
# so they are never mistaken for a clone. The directories in BUGBOT_SCAN_PRUNE, the bot's
# own data directory and (unless asked) hidden directories are pruned, which is what keeps
# a scan of the whole home directory to a couple of seconds.
#
# Output: $BB_RUN_DIR/clones.txt, one repository root per line.

bb_setup_scan() {
  local root name out=$BB_RUN_DIR/clones.txt
  local -a hidden prune
  local -i count

  prune=( -path "$BUGBOT_DATA_DIR" -o -path "$BUGBOT_RETRO_DIR" )
  for name in "${BUGBOT_SCAN_PRUNE[@]}"; do prune+=( -o -name "$name" ); done
  print -r -- "pruning: ${(j:, :)BUGBOT_SCAN_PRUNE}"

  : > "$out.raw"
  for root in "${BB_SETUP_ROOTS[@]}"; do
    [[ -d $root ]] || { print -r -- "root skipped (does not exist): $root"; continue }
    print -r -- "scanning $root (depth $BB_SETUP_DEPTH)"

    hidden=()
    [[ $BB_SETUP_HIDDEN == true ]] || hidden=( -o '(' -name '.*' -not -name .git -not -path "$root" ')' )

    find "$root" -maxdepth $BB_SETUP_DEPTH \
      '(' "${prune[@]}" "${hidden[@]}" ')' -prune \
      -o -type d -name .git -print -prune 2>/dev/null >> "$out.raw"
  done

  sed 's#/\.git$##' "$out.raw" | sort -u > "$out"
  rm -f -- "$out.raw"

  count=$(wc -l < "$out")
  bb_note "$count clones"
  (( count > 0 )) || { print -r -- "no clones found"; return 1 }
  return 0
}

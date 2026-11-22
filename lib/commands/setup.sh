# bugbot setup — discover the organization's local clones, record them in workspace.json and
# create a default review config for every selected repository.
#
#   scan → identity → inspect → select → preview → confirm → write → repo configs
#
# Nothing is written before the confirmation. --plan stops after inspect and prints what the
# selector would offer; --json prints the would-be workspace.json to stdout.
#
# Repo configs (repos/<owner>/<name>.json, schemas/repo.schema.json) are created only when the
# file does not exist, with just $schema, $metadata and repo (lib/setup/repo_config.py). Nothing
# is inferred from the repository; the optional review groups are added by hand.
# An existing config is never touched: `bugbot doctor` checks that it is still adequate.

bb_setup_usage() {
  local root
  local -a shown=()
  for root in "${BUGBOT_SCAN_ROOTS[@]}"; do bb_tilde "$root"; shown+=("$REPLY"); done
  print -u2 "usage: bugbot setup [options]

Find local clones of GitHub repositories (of github.org when it is set, else of any
owner), pick one canonical clone per repository,
write ${BUGBOT_WORKSPACE_FILE:t} and create a minimal review config in
repos/<owner>/<name>.json for each selected repository that has none yet.
Existing configs are never changed; 'bugbot doctor' checks them.

Options:
  --root <dir>      directory to scan (repeatable; default: ${(j:, :)shown})
  --depth <n>       maximum scan depth (default: $BUGBOT_SCAN_DEPTH)
  --org <name>      only clones of this GitHub owner (default: ${BUGBOT_ORG:-any owner})
  --hidden          include hidden directories in the scan
  --remote          ask GitHub for default branches the local clone does not know
  --all             select every repository without the selector (suggested clone)
  --yes             write without asking for confirmation
  --plan            show what would be offered, write nothing
  --json            print the proposed workspace.json to stdout, write nothing
  --no-color        no colors
  -h, --help        this help

Defaults come from bugbot.json (github.org, scan.*); see 'bugbot config'."
}

# Suggested clone for a repo: the one already configured, else a clean one, else the most
# recently fetched — the clone you work in is the one most likely kept up to date.
bb_setup_rank() {
  awk -F'\t' -v OFS='\t' -v configured_file="$BB_RUN_DIR/current.tsv" '
    FILENAME == configured_file { configured[tolower($1) "\t" $2] = 1; next }
    { print (configured[tolower($1) "\t" $2] ? 0 : 1), $6, -$7, $0 }
  ' "$BB_RUN_DIR/current.tsv" "$BB_RUN_DIR/candidates.tsv" \
    | sort -f -t $'\t' -k4,4 -k1,1n -k2,2n -k3,3n \
    | cut -f4- > "$BB_RUN_DIR/ranked.tsv"
}

bb_setup_age() {
  local -i fetched=$1 days

  if (( fetched == 0 )); then REPLY='never'; return; fi
  days=$(( (EPOCHSECONDS - fetched) / 86400 ))
  if (( days == 0 )); then REPLY='today'; else REPLY="${days}d ago"; fi
}

# Loads ranked.tsv into the selector: one item per repository, one option per clone.
bb_setup_offer() {
  local line key clone_path url remote default dirty fetched prev=''
  local -a opts f
  local -A configured
  local hint state on age

  while IFS= read -r line; do configured[${(L)line%%$'\t'*}]=1; done < "$BB_RUN_DIR/current.tsv"

  bb_select_reset
  typeset -ga BB_SETUP_KEYS BB_SETUP_PATHS
  BB_SETUP_KEYS=() BB_SETUP_PATHS=()

  _flush() {
    [[ -n $prev ]] || return 0
    on=0
    (( ${+configured[${(L)prev}]} )) && on=1
    [[ $BB_SETUP_ALL == true ]] && on=1
    hint="${#opts} clone$( (( ${#opts} > 1 )) && print -n s)  ·  ${BB_SETUP_DEFAULT[${(L)prev}]:-no default branch}"
    bb_select_add "$prev" "$hint" $on 1 "${opts[@]}"
    BB_SETUP_KEYS+=("$prev")
    BB_SETUP_PATHS+=("${(pj:\x1f:)BB_SETUP_OPTPATHS}")
  }

  typeset -gA BB_SETUP_DEFAULT
  typeset -ga BB_SETUP_OPTPATHS
  opts=() BB_SETUP_OPTPATHS=()

  # Split by hand: with IFS set to a tab, zsh collapses empty fields such as an
  # unknown default branch and shifts every column after it.
  while IFS= read -r line; do
    f=("${(@ps:\t:)line}")
    key=$f[1] clone_path=$f[2] url=$f[3] remote=$f[4] default=$f[5] dirty=$f[6] fetched=$f[7]
    if [[ ${(L)key} != ${(L)prev} ]]; then
      _flush
      prev=$key opts=() BB_SETUP_OPTPATHS=()
    fi
    [[ -n $default && -z ${BB_SETUP_DEFAULT[${(L)key}]:-} ]] && BB_SETUP_DEFAULT[${(L)key}]=$default
    (( dirty )) && state='local changes' || state='clean'
    bb_setup_age $fetched
    age=$REPLY
    bb_tilde "$clone_path"
    opts+=("$REPLY  ·  $state  ·  fetched $age")
    BB_SETUP_OPTPATHS+=("$clone_path")
  done < "$BB_RUN_DIR/ranked.tsv"
  _flush
  unfunction _flush
}

bb_setup_selection() {
  local out=$BB_RUN_DIR/selection.tsv
  local -a paths
  local -i i pick

  : > "$out"
  for (( i = 1; i <= ${#BB_SETUP_KEYS}; i++ )); do
    (( BB_SEL_ON[i] )) || continue
    paths=("${(@ps:\x1f:)BB_SETUP_PATHS[i]}")
    pick=${BB_SEL_PICK[i]:-1}
    if (( pick < 1 || pick > ${#paths} )); then
      print -u2 "bugbot: invalid clone choice for ${BB_SETUP_KEYS[i]} (option $pick of ${#paths})"
      return 1
    fi
    print -r -- "${BB_SETUP_KEYS[i]}"$'\t'"${paths[pick]}" >> "$out"
  done
}

bb_setup_plan() {
  local -a opts
  local -i i j

  bb_say_bar
  for (( i = 1; i <= ${#BB_SEL_LABEL}; i++ )); do
    (( BB_SEL_ON[i] )) && REPLY="${c_green}●${c_reset}" || REPLY="${c_dim}○${c_reset}"
    bb_say_line "$REPLY ${BB_SEL_LABEL[i]}  ${c_dim}${BB_SEL_HINT[i]}${c_reset}"
    opts=("${(@ps:\x1f:)BB_SEL_OPTS[i]}")
    for (( j = 1; j <= ${#opts}; j++ )); do
      if (( j == 1 )); then
        bb_say_line "    ${c_cyan}◉${c_reset} ${opts[j]}"
      else
        bb_say_note "    ○ ${opts[j]}"
      fi
    done
  done
}

bb_setup_preview() {
  local line
  local -a f
  local -i added=0 removed=0 changed=0 same=0

  python3 "$BUGBOT_HOME/lib/core/workspace.py" diff \
    --current "$BUGBOT_WORKSPACE_FILE" --proposed "$BB_RUN_DIR/workspace.json" \
    > "$BB_RUN_DIR/diff.tsv" || return 1

  bb_say_bar
  while IFS= read -r line; do
    f=("${(@ps:\t:)line}")
    bb_tilde "${f[3]}"
    case ${f[1]} in
      +) (( ++added ));   bb_say_line "${c_green}+${c_reset} ${f[2]}  ${c_dim}${REPLY}${c_reset}" ;;
      -) (( ++removed )); bb_say_line "${c_red}-${c_reset} ${f[2]}  ${c_dim}${REPLY}${c_reset}" ;;
      \~) (( ++changed )); bb_say_line "${c_yellow}~${c_reset} ${f[2]}  ${c_dim}${REPLY}${c_reset}" ;;
      *) (( ++same ));    bb_say_line "${c_dim}= ${f[2]}${c_reset}" ;;
    esac
  done < "$BB_RUN_DIR/diff.tsv"
  bb_say_bar

  REPLY="$added added · $changed changed · $removed removed · $same unchanged"
  (( added + removed + changed > 0 ))
}

# Step: one minimal review config per repository in workspace.json, only when missing.
# An existing file is left exactly as it is; `bugbot doctor` reports drift.
bb_setup_step_repo_configs() {
  local line repo clone_path branch remote owner name target out
  local -a f
  local -i created=0 existing=0 failed=0

  python3 "$BUGBOT_HOME/lib/core/workspace.py" list --current "$BUGBOT_WORKSPACE_FILE" \
    > "$BB_RUN_DIR/repos.tsv" || return 1

  while IFS= read -r line; do
    f=("${(@ps:\t:)line}")
    repo=$f[1] clone_path=$f[2] branch=$f[3] remote=$f[4]
    owner=${${repo%%/*}:l} name=${${repo#*/}:l}
    target=$BUGBOT_HOME/repos/$owner/$name.json

    if [[ -e $target ]]; then
      print -r -- "exists, left as is: $repo"
      (( ++existing ))
      continue
    fi
    mkdir -p "${target:h}" || return 1
    out=$(python3 "$BUGBOT_HOME/lib/setup/repo_config.py" template --repo "$repo" \
            --out-dir "${target:h}" 2>&1) || {
      print -r -- "template failed for $repo: $out"
      (( ++failed ))
      continue
    }
    print -r -- "$out" > "$target.tmp" && mv -f -- "$target.tmp" "$target"
    (( ++created )); print -r -- "created config: $repo"
  done < "$BB_RUN_DIR/repos.tsv"

  REPLY="$created created · $existing existing"
  (( failed )) && REPLY+=" · $failed failed"
  bb_note "$REPLY"
  (( failed )) && return $BB_STEP_WARN
  return 0
}

bb_cmd_setup() {
  typeset -ga BB_SETUP_ROOTS=()
  typeset -g  BB_SETUP_DEPTH=$BUGBOT_SCAN_DEPTH BB_SETUP_ORG=$BUGBOT_ORG
  typeset -g  BB_SETUP_HIDDEN=false BB_SETUP_REMOTE=false BB_SETUP_ALL=false
  local yes=false plan=false json=false color=true gh_user='' summary root wrote=false
  local -i rc

  while (( $# )); do
    case $1 in
      --root)     [[ -n ${2:-} ]] || { bb_setup_usage; return 2 }; BB_SETUP_ROOTS+=("${2:A}"); shift 2 ;;
      --depth)    [[ ${2:-} == <1-8> ]] || { print -u2 "bugbot: --depth must be between 1 and 8"; return 2 }
                  BB_SETUP_DEPTH=$2; shift 2 ;;
      --org)      [[ -n ${2:-} ]] || { bb_setup_usage; return 2 }; BB_SETUP_ORG=$2; shift 2 ;;
      --hidden)   BB_SETUP_HIDDEN=true; shift ;;
      --remote)   BB_SETUP_REMOTE=true; shift ;;
      --all)      BB_SETUP_ALL=true; shift ;;
      --yes|-y)   yes=true; shift ;;
      --plan)     plan=true; shift ;;
      --json)     json=true; shift ;;
      --no-color) color=false; shift ;;
      -h|--help)  bb_setup_usage; return 0 ;;
      *)          print -u2 "bugbot setup: unknown option: $1"; bb_setup_usage; return 2 ;;
    esac
  done

  if (( ! ${#BB_SETUP_ROOTS} )); then
    for root in "${BUGBOT_SCAN_ROOTS[@]}"; do BB_SETUP_ROOTS+=("${root:A}"); done
  fi
  BB_SETUP_ROOTS=(${(u)BB_SETUP_ROOTS})

  bb_term_init $color
  bb_run_dir_new setup || { print -u2 "bugbot: cannot create $BUGBOT_DATA_DIR"; return 1 }

  local -a shown=()
  for root in $BB_SETUP_ROOTS; do bb_tilde "$root"; shown+=("$REPLY"); done

  bb_say_open "bugbot setup"
  bb_say_bar
  bb_say_kv org "${BB_SETUP_ORG:-(any owner)}"
  bb_say_kv roots "${(j: · :)shown}"
  bb_tilde "$BUGBOT_WORKSPACE_FILE"
  bb_say_kv output "$REPLY"
  bb_say_bar

  bb_cursor_hide
  bb_board_init "Scan repositories" "Check gh identity" "Inspect remotes"
  bb_run_step 1 scan     bb_setup_scan     || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_run_step 2 identity bb_setup_identity
  bb_run_step 3 inspect  bb_setup_inspect  || { bb_say_close "${c_red}failed${c_reset}"; return 1 }
  bb_board_freeze
  bb_cursor_show

  [[ -s $BB_RUN_DIR/gh-user ]] && gh_user=$(<"$BB_RUN_DIR/gh-user")

  python3 "$BUGBOT_HOME/lib/core/workspace.py" current --current "$BUGBOT_WORKSPACE_FILE" \
    > "$BB_RUN_DIR/current.tsv" || { bb_say_close "${c_red}invalid workspace.json${c_reset}"; return 1 }

  bb_setup_rank
  bb_setup_offer

  if [[ $plan == true ]]; then
    bb_setup_plan
    bb_say_bar
    bb_say_close "${c_dim}${#BB_SEL_LABEL} repo(s) would be offered · nothing written${c_reset}"
    return 0
  fi

  if [[ $BB_SETUP_ALL != true ]]; then
    bb_say_bar
    if ! bb_is_tty; then
      bb_say_close "${c_red}the selector needs a terminal${c_reset} — use --all, --plan or --json"
      return 2
    fi
    bb_select_run "Select repositories"
    rc=$?
    if (( rc != 0 )); then
      bb_say_close "${c_dim}cancelled · nothing written${c_reset}"
      return 1
    fi
  fi

  bb_setup_selection || { bb_say_close "${c_red}invalid selection${c_reset} · nothing written"; return 1 }
  bb_select_count
  if (( REPLY == 0 )); then
    bb_say_close "${c_yellow}no repository selected${c_reset} · nothing written"
    return 1
  fi

  python3 "$BUGBOT_HOME/lib/core/workspace.py" build \
    --current "$BUGBOT_WORKSPACE_FILE" \
    --candidates "$BB_RUN_DIR/candidates.tsv" \
    --selection "$BB_RUN_DIR/selection.tsv" \
    --gh-user "$gh_user" --org "$BB_SETUP_ORG" \
    > "$BB_RUN_DIR/workspace.json" || { bb_say_close "${c_red}failed to build the workspace${c_reset}"; return 1 }

  if [[ $json == true ]]; then
    bb_say_close "${c_dim}proposed workspace on stdout · nothing written${c_reset}"
    cat "$BB_RUN_DIR/workspace.json"
    return 0
  fi

  bb_say_step "Review changes"
  if bb_setup_preview; then
    summary=$REPLY
    if [[ $yes != true ]]; then
      if ! bb_is_tty; then
        bb_say_close "${c_red}confirmation needs a terminal${c_reset} — use --yes"
        return 2
      fi
      bb_confirm "Write ${BUGBOT_WORKSPACE_FILE:t}? ${c_dim}${summary}${c_reset}" || {
        bb_say_close "${c_dim}nothing written${c_reset}"
        return 1
      }
    fi
    python3 "$BUGBOT_HOME/lib/core/workspace.py" write \
      --current "$BUGBOT_WORKSPACE_FILE" --proposed "$BB_RUN_DIR/workspace.json" \
      || { bb_say_close "${c_red}write failed${c_reset}"; return 1 }
    wrote=true
  else
    summary=$REPLY
    bb_say_note "${summary} · workspace unchanged"
  fi

  bb_cursor_hide
  bb_board_init "Create repo configs"
  bb_run_step 1 repo-configs bb_setup_step_repo_configs
  bb_board_freeze
  bb_cursor_show

  bb_say_bar
  if [[ $wrote == true ]]; then
    bb_say_close "${c_green}${BUGBOT_WORKSPACE_FILE:t} written${c_reset} · ${summary} · calibrate repos/<owner>/<name>.json"
  else
    bb_say_close "${c_green}done${c_reset} · calibrate repos/<owner>/<name>.json"
  fi
}

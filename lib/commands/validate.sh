# bugbot validate — check a JSON file the bot or an agent wrote against its schema.
#
# The schema comes from where the file is: candidates/<finder>.json → candidates,
# verdicts/<file>.json → verdicts, <name>.json → <name> (preflight, translate, findings, meta, ...).
# Agents run this before replying, so none has to know a schema's name or a Python one-liner.

bb_validate_usage() {
  print -u2 "usage: bugbot validate <file> [--schema <name>]

Validate a JSON file against its schema in schemas/. The schema is taken
from the file's place: candidates/*.json, verdicts/*.json, or <name>.json.
Prints 'ok' or every error, one per line.

Options:
  --schema <name>  schema to use, when the path does not say it
  -h, --help       this help"
}

bb_cmd_validate() {
  local file='' name=''
  while (( $# )); do
    case $1 in
      --schema)  [[ -n ${2:-} ]] || { bb_validate_usage; return 2 }; name=$2; shift 2 ;;
      --no-color) shift ;;
      -h|--help) bb_validate_usage; return 0 ;;
      -*)        print -u2 "bugbot validate: unknown option: $1"; bb_validate_usage; return 2 ;;
      *)         [[ -z $file ]] || { print -u2 "bugbot validate: give a single file"; return 2 }
                 file=$1; shift ;;
    esac
  done
  [[ -n $file ]] || { bb_validate_usage; return 2 }
  [[ -f $file ]] || { print -u2 "bugbot validate: $file: not found"; return 1 }

  if [[ -z $name ]]; then
    case ${file:A:h:t} in
      candidates|verdicts) name=${file:A:h:t} ;;
      *)                name=${${file:t}%.json} ;;
    esac
  fi
  [[ -f $BUGBOT_HOME/schemas/$name.schema.json ]] \
    || { print -u2 "bugbot validate: no schema '$name' for $file: pass --schema <name>"; return 2 }

  python3 - "$BUGBOT_HOME/lib/core" "$file" "$name" <<'EOF'
import sys
sys.path.insert(0, sys.argv[1])
import schema
try:
    schema.read_document(sys.argv[2], sys.argv[3])
except schema.ValidationError as error:
    print("\n".join(error.errors))
    sys.exit(1)
print(f"ok  {sys.argv[3]}")
EOF
}

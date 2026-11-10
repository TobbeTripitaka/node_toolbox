#!/usr/bin/env bash
# =============================================================================
# smartsolo_copy.sh
#
# Finds mounted USB drives whose name is exactly 9 digits (e.g. /Volumes/453021191),
# copies all data from each one into <out_path>/<drive_id>/ and prints basic QC
# (file count, total size, copy-vs-source match, count by file type).
#
# - Source drives are only read; nothing is ever deleted or modified on them.
# - If <out_path>/<drive_id> already exists, a new folder
#   <drive_id>_YYYYmmdd_HHMMSS is created instead (nothing is overwritten).
# - Drives are copied in parallel (default 4 at a time, see --jobs).
# - Works with the default macOS bash (3.2) and on Linux.
#
# out_path is taken from (first match wins):
#   1. --out PATH on the command line
#   2. out_path=... in ./smartsolo_copy.conf (or file given with --config)
#   otherwise the script stops with an error.
#
# Config file format (key=value, # for comments), all keys optional:
#   out_path=/Users/me/data/raw
#   csv_path=/Users/me/data/raw/qc_log.csv   # QC CSV is only written if set
#   jobs=4
#   volumes_root=/Volumes
# =============================================================================

set -o pipefail

VERSION="1.0"
SCRIPT_PATH="$0"
CONFIG_DEFAULT="./smartsolo_copy.conf"


# ----------------------------------------------------------------------------- helpers
die()  { echo "ERROR: $*" >&2; exit 1; }
warn() { echo "WARNING: $*" >&2; }

human() {  # bytes -> human readable
    awk -v b="${1:-0}" 'BEGIN{
        split("B KB MB GB TB PB",u," "); i=1;
        while (b>=1024 && i<6) { b/=1024; i++ }
        if (i==1) printf "%d %s", b, u[i]; else printf "%.2f %s", b, u[i] }'
}

expand_tilde() { case "$1" in "~"|"~/"*) echo "${HOME}${1#\~}" ;; *) echo "$1" ;; esac; }

# find expression that prunes the excluded names (used for counting)
# prints: "<files> <bytes>"
count_files_bytes() {
    find "$1" \( -name .Spotlight-V100 -o -name .fseventsd -o -name .Trashes \
                 -o -name .TemporaryItems -o -name .DocumentRevisions-V100 \
                 -o -name 'System Volume Information' \) -prune \
         -o -type f ! -name .DS_Store ! -name '._*' -ls 2>/dev/null \
    | awk '{n++; s+=$7} END{printf "%d %.0f\n", n, s}'
}

# prints e.g. "DLD:120;LOG:2;(none):1"
count_by_type() {
    find "$1" \( -name .Spotlight-V100 -o -name .fseventsd -o -name .Trashes \
                 -o -name .TemporaryItems -o -name .DocumentRevisions-V100 \
                 -o -name 'System Volume Information' \) -prune \
         -o -type f ! -name .DS_Store ! -name '._*' -print 2>/dev/null \
    | awk -F/ '{ b=$NF; if (b ~ /^[^.].*\./) { e=b; sub(/.*\./,"",e) } else e="(none)"; c[e]++ }
               END{ for (e in c) printf "%s:%d\n", e, c[e] }' \
    | sort | paste -sd ';' -
}

is_drive_id() { case "$1" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;; esac; return 1; }

usage() {
cat <<EOF
smartsolo_copy.sh v$VERSION - copy all 9-digit-named USB drives to out_path

Usage: $(basename "$SCRIPT_PATH") [options]

  -o, --out PATH          Output directory (overrides config file)
  -c, --config FILE       Config file (default: $CONFIG_DEFAULT)
      --csv PATH          Append one QC row per drive to this CSV (overrides config)
  -j, --jobs N            Drives copied in parallel (default 4; 1 = one at a time)
  -d, --drive ID          Copy only this drive (can be repeated)
  -n, --dry-run           Only list the drives found and where they would go
      --volumes-root DIR  Where drives are mounted (default: /Volumes on macOS,
                          /media/\$USER and /run/media/\$USER on Linux)
  -h, --help              Show this help
EOF
}

# ============================================================================= worker
# Copies one drive. Called by the main process via xargs (parallel).
# Writes one tab-separated result line to $RESULT_DIR/<id>.result
worker() {
    local id="$1"
    local src dest log start rc method
    src=$(cat "$RESULT_DIR/$id.src")
    dest=$(cat "$RESULT_DIR/$id.dest")
    log="$RESULT_DIR/$id.log"
    start=$SECONDS

    echo "[$(date +%H:%M:%S)] START  $id  ->  $dest"
    if ! mkdir -p "$dest"; then
        printf "%s\037%s\037%s\037FAIL\037mkdir failed\0370\0370\0370\0370\0370\037-\037\n" "$id" "$src" "$dest" > "$RESULT_DIR/$id.result"
        return 1
    fi

    if [ "$COPY_TOOL" = "rsync" ]; then
        method="rsync"
        rsync -a \
            --exclude='.Spotlight-V100' --exclude='.fseventsd' --exclude='.Trashes' \
            --exclude='.TemporaryItems' --exclude='.DocumentRevisions-V100' \
            --exclude='System Volume Information' --exclude='.DS_Store' --exclude='._*' \
            "$src/" "$dest/" > "$log" 2>&1
        rc=$?
    else
        method="cp"
        rc=0
        # copy each top-level entry, skipping OS junk
        for entry in "$src"/* "$src"/.[!.]* "$src"/..?*; do
            [ -e "$entry" ] || continue
            case "$(basename "$entry")" in
                .Spotlight-V100|.fseventsd|.Trashes|.TemporaryItems|.DocumentRevisions-V100|'System Volume Information'|.DS_Store|._*) continue ;;
            esac
            cp -Rp "$entry" "$dest/" >> "$log" 2>&1 || rc=$?
        done
    fi

    local s_n s_b d_n d_b types status note dur
    read -r s_n s_b <<EOF
$(count_files_bytes "$src")
EOF
    read -r d_n d_b <<EOF
$(count_files_bytes "$dest")
EOF
    types=$(count_by_type "$dest")
    dur=$((SECONDS - start))

    if [ "$rc" -ne 0 ]; then
        status="FAIL"; note="$method exit code $rc (see log)"
    elif [ "$s_n" != "$d_n" ] || [ "$s_b" != "$d_b" ]; then
        status="WARN"; note="source/copy mismatch"
    else
        status="OK"; note=""
    fi

    local US=$'\x1f'   # field separator (keeps empty fields intact)
    printf "%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s\n" \
        "$id" "$src" "$dest" "$status" "$note" "$s_n" "$d_n" "$s_b" "$d_b" "$dur" "$method" "$types" \
        > "$RESULT_DIR/$id.result"

    echo "[$(date +%H:%M:%S)] $status $( [ "$status" = OK ] && echo '   ' )$id  ($d_n files, $(human "$d_b"), ${dur}s)"
    [ "$status" = "FAIL" ] && return 1
    return 0
}

if [ "${1:-}" = "--_worker" ]; then
    worker "$2"
    exit $?
fi

# ============================================================================= main
OUT_PATH=""; CSV_PATH=""; JOBS=""; VOLUMES_ROOT=""; CONFIG="$CONFIG_DEFAULT"
DRY_RUN=0; ONLY_DRIVES=""; CONFIG_GIVEN=0

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--out)          [ -n "${2:-}" ] || die "$1 needs a path"; OUT_PATH="$2"; shift 2 ;;
        -c|--config)       [ -n "${2:-}" ] || die "$1 needs a file"; CONFIG="$2"; CONFIG_GIVEN=1; shift 2 ;;
        --csv)             [ -n "${2:-}" ] || die "$1 needs a path"; CSV_PATH="$2"; shift 2 ;;
        -j|--jobs)         [ -n "${2:-}" ] || die "$1 needs a number"; JOBS="$2"; shift 2 ;;
        -d|--drive)        is_drive_id "${2:-}" || die "--drive needs a 9-digit id"; ONLY_DRIVES="$ONLY_DRIVES $2"; shift 2 ;;
        -n|--dry-run)      DRY_RUN=1; shift ;;
        --volumes-root)    [ -n "${2:-}" ] || die "$1 needs a dir"; VOLUMES_ROOT="$2"; shift 2 ;;
        -h|--help)         usage; exit 0 ;;
        *)                 usage; die "unknown option: $1" ;;
    esac
done

# ---- config file (command line values win)
if [ -f "$CONFIG" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"                                   # strip comments
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [ -z "$line" ] && continue
        key=$(echo "${line%%=*}" | sed 's/[[:space:]]*$//')
        val=$(echo "${line#*=}"  | sed 's/^[[:space:]]*//;s/^"\(.*\)"$/\1/;s/^'"'"'\(.*\)'"'"'$/\1/')
        case "$key" in
            out_path)     [ -z "$OUT_PATH" ]     && OUT_PATH="$val" ;;
            csv_path)     [ -z "$CSV_PATH" ]     && CSV_PATH="$val" ;;
            jobs)         [ -z "$JOBS" ]         && JOBS="$val" ;;
            volumes_root) [ -z "$VOLUMES_ROOT" ] && VOLUMES_ROOT="$val" ;;
            *) warn "unknown key in $CONFIG: $key" ;;
        esac
    done < "$CONFIG"
elif [ "$CONFIG_GIVEN" -eq 1 ]; then
    die "config file not found: $CONFIG"
fi

[ -n "$OUT_PATH" ] || die "no output path. Use --out PATH or set out_path=... in $CONFIG"
OUT_PATH=$(expand_tilde "$OUT_PATH")
[ -n "$CSV_PATH" ] && CSV_PATH=$(expand_tilde "$CSV_PATH")
JOBS="${JOBS:-4}"
case "$JOBS" in ''|*[!0-9]*|0) die "--jobs must be a positive integer" ;; esac

# ---- where to look for drives
if [ -n "$VOLUMES_ROOT" ]; then
    ROOTS="$(expand_tilde "$VOLUMES_ROOT")"
elif [ "$(uname -s)" = "Darwin" ]; then
    ROOTS="/Volumes"
else
    ROOTS="/media/${USER:-$(id -un)} /run/media/${USER:-$(id -un)} /media /mnt"
fi

# ---- find drives
DRIVE_IDS=(); DRIVE_SRCS=()
for root in $ROOTS; do
    [ -d "$root" ] || continue
    for d in "$root"/*; do
        [ -d "$d" ] || continue
        id=$(basename "$d")
        is_drive_id "$id" || continue
        if [ -n "$ONLY_DRIVES" ]; then
            case " $ONLY_DRIVES " in *" $id "*) ;; *) continue ;; esac
        fi
        dup=0
        for x in ${DRIVE_IDS[@]+"${DRIVE_IDS[@]}"}; do [ "$x" = "$id" ] && dup=1; done
        [ $dup -eq 1 ] && continue
        DRIVE_IDS+=("$id"); DRIVE_SRCS+=("$d")
    done
done

echo "smartsolo_copy.sh v$VERSION   $(date '+%Y-%m-%d %H:%M:%S')"
echo "Looking in : $ROOTS"
echo "Output to  : $OUT_PATH"
[ -n "$CSV_PATH" ] && echo "QC CSV     : $CSV_PATH"

if [ ${#DRIVE_IDS[@]} -eq 0 ]; then
    echo "No drives with a 9-digit name found."
    [ -n "$ONLY_DRIVES" ] && echo "(asked for:$ONLY_DRIVES)"
    exit 0
fi

if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$OUT_PATH" || die "cannot create $OUT_PATH"
fi

# Safety: out_path must not be on one of the source drives
OUT_ABS=$( [ -d "$OUT_PATH" ] && cd "$OUT_PATH" && pwd -P || echo "$OUT_PATH" )
for s in "${DRIVE_SRCS[@]}"; do
    s_abs=$(cd "$s" && pwd -P)
    case "$OUT_ABS/" in "$s_abs/"*) die "out_path is inside source drive $s" ;; esac
done

# ---- free space (KB)
if [ -d "$OUT_PATH" ]; then
    FREE_KB=$(df -Pk "$OUT_PATH" | awk 'NR==2{print $4}')
else
    p="$OUT_PATH"; while [ ! -d "$p" ]; do p=$(dirname "$p"); done
    FREE_KB=$(df -Pk "$p" | awk 'NR==2{print $4}')
fi
MARGIN_KB=$((1024 * 1024))   # keep 1 GB free

RUN_TS=$(date +%Y%m%d_%H%M%S)
RESULT_DIR=$(mktemp -d "${TMPDIR:-/tmp}/smartsolo_copy.XXXXXX") || die "mktemp failed"
trap 'rm -rf "$RESULT_DIR"' EXIT

if command -v rsync >/dev/null 2>&1; then COPY_TOOL="rsync"; else COPY_TOOL="cp"; warn "rsync not found, using cp"; fi

echo
printf '%-11s %-12s %-10s %s\n' "DRIVE" "USED" "ACTION" "DESTINATION"
TODO=(); SKIPPED=()
need_total=0
i=0
while [ $i -lt ${#DRIVE_IDS[@]} ]; do
    id="${DRIVE_IDS[$i]}"; src="${DRIVE_SRCS[$i]}"; i=$((i+1))
    read -r _n used_b <<EOF2
$(count_files_bytes "$src")
EOF2
    used_kb=$(awk -v b="$used_b" 'BEGIN{printf "%.0f", b/1024 + 1}')
    dest="$OUT_PATH/$id"
    [ -e "$dest" ] && dest="$OUT_PATH/${id}_${RUN_TS}"
    action="copy"
    if [ $((need_total + used_kb + MARGIN_KB)) -gt "$FREE_KB" ]; then
        action="SKIP(space)"
        SKIPPED+=("$id")
    else
        need_total=$((need_total + used_kb))
        TODO+=("$id")
        echo "$src"  > "$RESULT_DIR/$id.src"
        echo "$dest" > "$RESULT_DIR/$id.dest"
    fi
    printf '%-11s %-12s %-10s %s\n' "$id" "$(human $((used_kb * 1024)))" "$action" "$dest"
done
echo "Total to copy: $(human $((need_total * 1024)))   Free at destination: $(human $((FREE_KB * 1024)))"

if [ "$DRY_RUN" -eq 1 ]; then
    echo; echo "Dry run - nothing copied."
    exit 0
fi
[ ${#TODO[@]} -eq 0 ] && { echo "Nothing to copy."; exit 1; }

# ---- copy (parallel)
echo
echo "Copying ${#TODO[@]} drive(s) with $COPY_TOOL, $JOBS at a time ..."
export RESULT_DIR COPY_TOOL
T0=$SECONDS
printf '%s\0' "${TODO[@]}" | xargs -0 -n 1 -P "$JOBS" "$SCRIPT_PATH" --_worker
T_TOTAL=$((SECONDS - T0))

# ---- QC summary
echo
echo "================================ QC SUMMARY ================================"
printf '%-11s %-6s %9s %9s %12s %12s  %s\n' "DRIVE" "STATUS" "SRC_FILES" "DST_FILES" "SRC_SIZE" "DST_SIZE" "FILE TYPES"
n_ok=0; n_warn=0; n_fail=0; tot_files=0; tot_bytes=0
if [ -n "$CSV_PATH" ]; then
    mkdir -p "$(dirname "$CSV_PATH")"
    [ -s "$CSV_PATH" ] || echo "run_time,drive_id,source,destination,status,note,src_files,dst_files,src_bytes,dst_bytes,duration_s,method,file_types" > "$CSV_PATH"
fi
for id in "${TODO[@]}"; do
    f="$RESULT_DIR/$id.result"
    if [ ! -f "$f" ]; then
        printf '%-11s %-6s  (worker did not finish)\n' "$id" "FAIL"; n_fail=$((n_fail+1)); continue
    fi
    IFS=$'\x1f' read -r r_id r_src r_dest r_status r_note s_n d_n s_b d_b dur method types < "$f"
    printf '%-11s %-6s %9s %9s %12s %12s  %s\n' "$r_id" "$r_status" "$s_n" "$d_n" "$(human "$s_b")" "$(human "$d_b")" "${types:--}"
    [ -n "$r_note" ] && echo "            -> $r_note"
    [ "$r_status" = "FAIL" ] && [ -s "$RESULT_DIR/$id.log" ] && sed 's/^/            | /' "$RESULT_DIR/$id.log" | tail -n 5
    case "$r_status" in OK) n_ok=$((n_ok+1)) ;; WARN) n_warn=$((n_warn+1)) ;; *) n_fail=$((n_fail+1)) ;; esac
    tot_files=$((tot_files + d_n)); tot_bytes=$(awk -v a="$tot_bytes" -v b="$d_b" 'BEGIN{printf "%.0f", a+b}')
    if [ -n "$CSV_PATH" ]; then
        q() { printf '"%s"' "$(echo "$1" | sed 's/"/""/g')"; }
        echo "$RUN_TS,$r_id,$(q "$r_src"),$(q "$r_dest"),$r_status,$(q "$r_note"),$s_n,$d_n,$s_b,$d_b,$dur,$method,$(q "$types")" >> "$CSV_PATH"
    fi
done
for id in ${SKIPPED[@]+"${SKIPPED[@]}"}; do
    printf '%-11s %-6s  not copied: not enough free space\n' "$id" "SKIP"
done
echo "----------------------------------------------------------------------------"
echo "OK: $n_ok   WARN: $n_warn   FAIL: $n_fail   SKIPPED: ${#SKIPPED[@]}"
echo "Copied $tot_files files, $(human "$tot_bytes") in ${T_TOTAL}s"
[ -n "$CSV_PATH" ] && echo "QC rows appended to $CSV_PATH"

[ $n_fail -gt 0 ] && exit 2
[ $n_warn -gt 0 ] || [ ${#SKIPPED[@]} -gt 0 ] && exit 1
exit 0

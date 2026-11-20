#!/usr/bin/env bash
# =============================================================================
# smartsolo_copy.sh  v2.1
#
# Copies SmartSolo nodes (USB drives named with their 9-digit serial, e.g. /Volumes/453021191)
# to <out_path>/<serial>/<harvest_time>/ and prints a QC summary.
#
# Layout (default, "dated"):
#   <out_path>/<serial>/20261116_173026/   first harvest: every file
#   <out_path>/<serial>/20261120_101500/   later harvest: only files that are new or changed
#   Every harvest folder has _harvest_manifest.tsv listing EVERY file that was on the node
#   (size, modification time, and the harvest folder that holds that version).
#   Nothing is overwritten. A log that grew is stored again in the new folder; the old
#   version stays where it was. Old-layout folders (<serial>/ with files, <serial>_<stamp>/)
#   count as earlier harvests.
#
# Safety
#   - Nodes are only read. Nodes the script mounts itself are mounted read-only.
#   - A harvest is copied into <stamp>.incomplete/ and renamed to <stamp>/ only after every
#     new file is verified. A failed harvest stays .incomplete and is never used as reference.
#   - The script does not start copying if fewer nodes are found than --expect N, if a
#     node given with --drive is missing, or if a node is attached but could not be
#     mounted. It prints a warning instead (override: --allow-missing).
#   - Only one run at a time per out_path (lock), a log of every run in out_path/_harvest_logs/.
#
# Works with the default macOS bash (3.2) and on Linux.
# See README.md (harvest) for documentation and examples.
# =============================================================================

set -o pipefail

VERSION="2.1"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd -P)/$(basename "$0")"
BASH_BIN="${BASH:-bash}"
CONFIG_DEFAULT="./smartsolo_copy.conf"
MANIFEST="_harvest_manifest.tsv"
INCOMPLETE=".incomplete"
OS="$(uname -s)"
TAB="$(printf '\t')"
US=$'\x1f'

# exit codes
EXIT_OK=0; EXIT_WARN=1; EXIT_FAIL=2; EXIT_NOT_STARTED=3; EXIT_LOCKED=4

# ----------------------------------------------------------------------------- helpers
die()  { echo "ERROR: $*" >&2; exit "$EXIT_FAIL"; }
warn() { echo "WARNING: $*" >&2; }

human() {  # bytes -> human readable
    awk -v b="${1:-0}" 'BEGIN{
        split("B KB MB GB TB PB",u," "); i=1;
        while (b>=1024 && i<6) { b/=1024; i++ }
        if (i==1) printf "%d %s", b, u[i]; else printf "%.2f %s", b, u[i] }'
}

expand_tilde() { case "$1" in "~"|"~/"*) echo "${HOME}${1#\~}" ;; *) echo "$1" ;; esac; }

is_drive_id() { case "$1" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;; esac; return 1; }
# harvest folder name: YYYYmmdd_HHMMSS, optionally _2, _3 ... (never *.incomplete)
is_stamp() {
    case "$1" in
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]) return 0 ;;
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]_[0-9]) return 0 ;;
        [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9]) return 0 ;;
    esac
    return 1
}

if [ "$OS" = "Darwin" ]; then
    size_mtime() { stat -f '%z %m' "$1" 2>/dev/null; }
else
    size_mtime() { stat -c '%s %Y' "$1" 2>/dev/null; }
fi

# all regular files below $1 (OS junk pruned), one path per line
list_files() {
    find "$1" \( -name .Spotlight-V100 -o -name .fseventsd -o -name .Trashes \
                 -o -name .TemporaryItems -o -name .DocumentRevisions-V100 \
                 -o -name 'System Volume Information' \) -prune \
         -o -type f ! -name .DS_Store ! -name '._*' ! -name "$MANIFEST" -print 2>/dev/null
}

# prints e.g. "DLD:120;LOG:2;(none):1" for a list of paths on stdin
count_by_type() {
    awk -F/ '{ b=$NF; if (b ~ /^[^.].*\./) { e=b; sub(/.*\./,"",e) } else e="(none)"; c[e]++ }
             END{ for (e in c) printf "%s:%d\n", e, c[e] }' | sort | paste -sd ';' -
}

eject_drive() {  # $1 = mount point
    local mp="$1" dev
    if [ "$OS" = "Darwin" ]; then
        diskutil eject "$mp" >/dev/null 2>&1
        return $?
    fi
    dev=$(df -P "$mp" 2>/dev/null | awk 'NR==2{print $1}')
    if command -v udisksctl >/dev/null 2>&1 && [ -b "$dev" ]; then
        udisksctl unmount -b "$dev" >/dev/null 2>&1 || return 1
        udisksctl power-off -b "$dev" >/dev/null 2>&1 || true
        return 0
    fi
    umount "$mp" >/dev/null 2>&1
}

usage() {
cat <<EOF
smartsolo_copy.sh v$VERSION - copy SmartSolo nodes to out_path/<serial>/<harvest_time>/

Usage: $(basename "$SCRIPT_PATH") [options]

  -o, --out PATH          Output directory (overrides config file)
  -c, --config FILE       Config file (default: $CONFIG_DEFAULT)
      --csv PATH          Append one QC row per node to this CSV (overrides config)
  -j, --jobs N            Nodes copied in parallel (default 4; 1 = one at a time)
  -d, --drive ID          Copy only this node (can be repeated). A missing one stops the run.
  -n, --dry-run           Show what was found, what is new and where it would go; copy nothing
  -e, --eject             Eject each node after its copy passed QC (status OK)
      --volumes-root DIR  Where drives are mounted (default: /Volumes on macOS,
                          /media/\$USER, /run/media/\$USER, /media, /mnt on Linux)

  Finding nodes (done before anything is copied):
      --expect N          Number of nodes you connected. Fewer found = not started.
      --allow-missing     Start anyway when nodes are missing (still warns)
      --wait SEC          Keep mounting and rescanning for at most SEC seconds (default 60)
      --no-scan           Do not mount or wait; use only what is already mounted
  -i, --interactive       Show what was found and wait: Enter = rescan (reconnect
                          missing nodes first), s = start, q = quit

  Copy:
      --checksum          Decide "unchanged" by comparing file contents (slower)
                          instead of size + modification time
      --legacy-layout     v1 behaviour: full copy to <serial>/ or <serial>_YYYYmmdd_HHMMSS/
  -h, --help              Show this help

Exit codes: 0 OK, 1 WARN/SKIP/eject failed, 2 FAIL, 3 not started (nodes missing), 4 locked
EOF
}

# ============================================================================= worker
# Copies the new/changed files of one node into <dest>.incomplete, verifies, renames to <dest>.
# Inputs: $RESULT_DIR/<id>.src, .dest, .new (relative paths), .plan (manifest rows)
# Writes one result line to $RESULT_DIR/<id>.result
worker() {
    local id="$1" src dest tmp log start rc method
    src=$(cat "$RESULT_DIR/$id.src"); dest=$(cat "$RESULT_DIR/$id.dest"); tmp="$dest$INCOMPLETE"
    log="$RESULT_DIR/$id.log"; start=$SECONDS; rc=0
    : > "$log"

    echo "[$(date +%H:%M:%S)] START  $id  ->  $dest"
    if [ -e "$dest" ] || [ -e "$tmp" ] || ! mkdir -p "$tmp"; then
        printf "%s${US}%s${US}%s${US}FAIL${US}cannot create a new folder %s${US}0${US}0${US}0${US}0${US}0${US}0${US}-${US}-\n" \
            "$id" "$src" "$dest" "$tmp" > "$RESULT_DIR/$id.result"
        echo "[$(date +%H:%M:%S)] FAIL   $id  (cannot create $tmp)"
        return 1
    fi

    if [ -s "$RESULT_DIR/$id.new" ]; then
        if [ "$COPY_TOOL" = "rsync" ]; then
            method="rsync"
            rsync -a --files-from="$RESULT_DIR/$id.new" "$src/" "$tmp/" >> "$log" 2>&1
            rc=$?
        else
            method="cp"
            while IFS= read -r rel; do
                mkdir -p "$tmp/$(dirname "$rel")" >> "$log" 2>&1 && cp -p "$src/$rel" "$tmp/$rel" >> "$log" 2>&1 || rc=$?
            done < "$RESULT_DIR/$id.new"
        fi
    else
        method="none"
    fi

    # verify: every new file must be in the copy with the size and time of the source
    local n_src n_new n_same b_src b_new bad=0 types status note dur rel s m state loc
    while IFS="$TAB" read -r rel s m state loc; do
        [ "$state" = "new" ] || continue
        if [ "$(size_mtime "$tmp/$rel")" != "$s $m" ]; then
            if [ "$CHECKSUM" -eq 1 ] && [ -f "$tmp/$rel" ] && cmp -s "$src/$rel" "$tmp/$rel"; then :; else
                bad=$((bad + 1)); echo "not verified: $rel" >> "$log"; fi
        elif [ "$CHECKSUM" -eq 1 ] && ! cmp -s "$src/$rel" "$tmp/$rel"; then
            bad=$((bad + 1)); echo "content differs: $rel" >> "$log"
        fi
    done < "$RESULT_DIR/$id.plan"
    n_src=$(wc -l < "$RESULT_DIR/$id.plan" | tr -d ' ')
    n_new=$(awk -F'\t' '$4=="new"' "$RESULT_DIR/$id.plan" | wc -l | tr -d ' ')
    n_same=$((n_src - n_new))
    b_src=$(awk -F'\t' '{s+=$2} END{printf "%.0f", s}' "$RESULT_DIR/$id.plan")
    b_new=$(awk -F'\t' '$4=="new"{s+=$2} END{printf "%.0f", s}' "$RESULT_DIR/$id.plan")
    types=$(cut -f1 "$RESULT_DIR/$id.plan" | count_by_type)

    # manifest: every file on the node and where that version is stored
    {
        echo "# smartsolo_copy.sh v$VERSION  harvest $(basename "$dest")  serial $id"
        echo "# source $src  host $(hostname 2>/dev/null)  compare $( [ "$CHECKSUM" -eq 1 ] && echo content || echo size+mtime )"
        printf 'path\tsize_bytes\tmtime_unix\tstate\tstored_in\n'
        cat "$RESULT_DIR/$id.plan"
    } > "$tmp/$MANIFEST" 2>> "$log" || rc=${rc:-1}

    if [ "$rc" -ne 0 ]; then
        status="FAIL"; note="$method exit code $rc (see log); kept as $(basename "$tmp"), not used as reference"
    elif [ "$bad" -gt 0 ]; then
        status="FAIL"; note="$bad new file(s) not verified; kept as $(basename "$tmp"), not used as reference"
    elif mv "$tmp" "$dest" 2>> "$log"; then
        status="OK"; [ "$n_new" -eq 0 ] && note="nothing new since the last harvest (manifest only)" || note=""
    else
        status="FAIL"; note="copied and verified, but renaming $(basename "$tmp") failed"
    fi
    dur=$((SECONDS - start))

    printf "%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s\n" \
        "$id" "$src" "$dest" "$status" "$note" "$n_src" "$n_new" "$n_same" "$b_src" "$b_new" "$dur" "$method" "$types" \
        > "$RESULT_DIR/$id.result"
    echo "[$(date +%H:%M:%S)] $status $( [ "$status" = OK ] && echo '   ' )$id  ($n_new new of $n_src files, $(human "$b_new"), ${dur}s)"
    [ "$status" = "OK" ]
}

if [ "${1:-}" = "--_worker" ]; then
    worker "$2"
    exit $?
fi

# ============================================================================= finding nodes
# One line per attached partition that may be a node: "<device>|<label>|<mount point>"
# A node is a FAT/exFAT partition on an external/hot-plug disk whose label is 9 digits,
# or whose label could not be read yet (empty).
list_candidates() {
    if [ "$OS" = "Darwin" ]; then
        diskutil list external 2>/dev/null | awk '/^ +[0-9]+:/{print $NF}' | while read -r ident; do
            info=$(diskutil info "$ident" 2>/dev/null) || continue
            label=$(echo "$info" | awk -F': *' '/^ *Volume Name:/{print $2; exit}' | sed 's/[[:space:]]*$//')
            mp=$(echo "$info"    | awk -F': *' '/^ *Mount Point:/{print $2; exit}' | sed 's/[[:space:]]*$//')
            fs=$(echo "$info"    | awk -F': *' '/^ *Type \(Bundle\):/{print $2; exit}' | sed 's/[[:space:]]*$//')
            case "$label" in "Not applicable"*|"(no name)"*|"NO NAME") label="" ;; esac
            if is_drive_id "$label"; then echo "/dev/$ident|$label|$mp"
            elif [ -z "$label" ] && [ -z "$mp" ]; then
                case "$fs" in msdos|exfat) echo "/dev/$ident||" ;; esac
            fi
        done
    elif command -v lsblk >/dev/null 2>&1; then
        lsblk -Ppno NAME,LABEL,MOUNTPOINT,FSTYPE,HOTPLUG 2>/dev/null | while IFS= read -r line; do
            name=$(echo "$line"  | sed -n 's/.*NAME="\([^"]*\)".*/\1/p')
            label=$(echo "$line" | sed -n 's/.*LABEL="\([^"]*\)".*/\1/p')
            mp=$(echo "$line"    | sed -n 's/.*MOUNTPOINT="\([^"]*\)".*/\1/p')
            fs=$(echo "$line"    | sed -n 's/.*FSTYPE="\([^"]*\)".*/\1/p')
            hp=$(echo "$line"    | sed -n 's/.*HOTPLUG="\([^"]*\)".*/\1/p')
            if is_drive_id "$label"; then echo "$name|$label|$mp"
            elif [ -z "$label" ] && [ -z "$mp" ] && [ "$hp" = "1" ]; then
                case "$fs" in vfat|exfat) echo "$name||" ;; esac
            fi
        done
    fi
}

# a file-system check (fsck) on the device is running - the system mounts it only afterwards
fsck_running() {  # $1 = /dev/diskNsM or /dev/sdX1
    local d="${1#/dev/}"
    ps -axo command= 2>/dev/null | grep -E "fsck[_.a-z]* .*r?${d}( |$)" | grep -v grep >/dev/null
}

# read-only mount; prints the reason on failure
mount_ro() {  # $1 = device
    local out
    if [ "$OS" = "Darwin" ]; then
        out=$(diskutil mount readOnly "$1" 2>&1) && return 0
    elif command -v udisksctl >/dev/null 2>&1; then
        out=$(udisksctl mount -b "$1" -o ro --no-user-interaction 2>&1) && return 0
    else
        echo "udisksctl not installed - cannot mount without sudo"; return 1
    fi
    echo "$out" | tail -n 1 | sed 's/^[[:space:]]*//'
    return 1
}

dev_mountpoint() {  # $1 = device -> current mount point (empty if not mounted)
    if [ "$OS" = "Darwin" ]; then
        diskutil info "$1" 2>/dev/null | awk -F': *' '/^ *Mount Point:/{print $2; exit}' | sed 's/[[:space:]]*$//'
    elif command -v findmnt >/dev/null 2>&1; then
        findmnt -no TARGET --source "$1" 2>/dev/null | head -n 1
    else
        lsblk -no MOUNTPOINT "$1" 2>/dev/null | head -n 1
    fi
}

unmount_dev() {  # $1 = device
    if [ "$OS" = "Darwin" ]; then diskutil unmount "$1" >/dev/null 2>&1
    else udisksctl unmount -b "$1" --no-user-interaction >/dev/null 2>&1; fi
}

# fills DRIVE_IDS / DRIVE_SRCS with the mounted 9-digit drives
find_drives() {
    DRIVE_IDS=(); DRIVE_SRCS=()
    local root d id x dup
    for root in $ROOTS; do
        [ -d "$root" ] || continue
        for d in "$root"/*; do
            [ -d "$d" ] || continue
            id=$(basename "$d")
            is_drive_id "$id" || continue
            if [ -n "$ONLY_DRIVES" ]; then
                case " $ONLY_DRIVES " in *" $id "*) ;; *) continue ;; esac
            fi
            [ -r "$d" ] || continue
            dup=0
            for x in ${DRIVE_IDS[@]+"${DRIVE_IDS[@]}"}; do [ "$x" = "$id" ] && dup=1; done
            [ $dup -eq 1 ] && continue
            DRIVE_IDS+=("$id"); DRIVE_SRCS+=("$d")
        done
    done
}

# One pass over attached candidates: mount the unmounted ones (read-only).
# Writes $SCAN_DIR/pending: "<device>|<label>|<reason>" for nodes still not mounted.
mount_pass() {
    local dev label mp reason newmp n_try
    : > "$SCAN_DIR/pending"
    list_candidates > "$SCAN_DIR/cand"
    while IFS='|' read -r dev label mp; do
        [ -n "$mp" ] && continue
        grep -qx "$dev" "$SCAN_DIR/not_nodes" 2>/dev/null && continue
        [ -n "$(dev_mountpoint "$dev")" ] && continue
        if [ -n "$ONLY_DRIVES" ] && [ -n "$label" ]; then
            case " $ONLY_DRIVES " in *" $label "*) ;; *) continue ;; esac
        fi
        if fsck_running "$dev"; then
            echo "$dev|$label|file-system check (fsck) running" >> "$SCAN_DIR/pending"; continue
        fi
        n_try=$(grep -cx "$dev" "$SCAN_DIR/tries" 2>/dev/null); echo "$dev" >> "$SCAN_DIR/tries"
        if reason=$(mount_ro "$dev"); then
            sleep 1
            newmp=$(dev_mountpoint "$dev")
            if [ -z "$label" ]; then
                # label was not readable: keep it only if it turns out to be a node
                if [ -n "$newmp" ] && is_drive_id "$(basename "$newmp")"; then
                    echo "  mounted (read-only)  $(basename "$newmp")  ($dev, attempt $((n_try + 1)))"
                else
                    unmount_dev "$dev"; echo "$dev" >> "$SCAN_DIR/not_nodes"
                    echo "  ignored $dev: not a node (name '$(basename "${newmp:-?}")'), unmounted again"
                fi
            else
                echo "  mounted (read-only)  $label  ($dev, attempt $((n_try + 1)))"
            fi
        else
            echo "$dev|$label|${reason:-mount failed}" >> "$SCAN_DIR/pending"
        fi
        sleep 1   # do not flood the disk arbitration with mount requests
    done < "$SCAN_DIR/cand"
}

pending_text() {  # human readable list of pending nodes
    awk -F'|' '{ n=($2==""?"(label not readable)":$2); printf "%s%s %s: %s", (NR>1?"\n":""), "   ", n" ("$1")", $3 }' "$SCAN_DIR/pending"
}

# Mount what is attached but not mounted, retrying, until all nodes are mounted and the
# count is stable, --expect N is reached, or --wait seconds have passed.
scan_media() {
    local t0=$SECONDS prev=-1 stable=0 n np
    echo "Finding nodes (mount attached ones read-only, retry for up to ${WAIT}s) ..."
    while :; do
        mount_pass
        find_drives; n=${#DRIVE_IDS[@]}
        np=$(wc -l < "$SCAN_DIR/pending" | tr -d ' ')
        printf '  [%3ss] mounted: %s%s%s\n' "$((SECONDS - t0))" "$n" "$( [ -n "$EXPECT" ] && echo " of $EXPECT expected" )" \
            "$( [ "$np" -gt 0 ] && echo ", not mounted yet: $np" )"
        if [ "$n" -eq "$prev" ]; then stable=$((stable + 1)); else stable=0; fi
        prev=$n
        if [ "$np" -eq 0 ]; then
            if [ -n "$EXPECT" ]; then [ "$n" -ge "$EXPECT" ] && [ "$stable" -ge 1 ] && break
            elif [ -n "$ONLY_DRIVES" ]; then all_requested_found && break
            else [ "$n" -gt 0 ] && [ "$stable" -ge 2 ] && break
            fi
        fi
        [ $((SECONDS - t0)) -ge "$WAIT" ] && break
        sleep 3
    done
}

all_requested_found() {
    local w x f
    for w in $ONLY_DRIVES; do
        f=0; for x in ${DRIVE_IDS[@]+"${DRIVE_IDS[@]}"}; do [ "$x" = "$w" ] && f=1; done
        [ $f -eq 1 ] || return 1
    done
    return 0
}

# Checks that everything expected is mounted. Prints the problems; returns 1 if any.
check_found() {
    local ok=0 w x f miss=""
    find_drives
    echo
    echo "Nodes mounted (${#DRIVE_IDS[@]}): $(printf '%s ' ${DRIVE_IDS[@]+"${DRIVE_IDS[@]}"})"
    if [ -n "$EXPECT" ] && [ "${#DRIVE_IDS[@]}" -lt "$EXPECT" ]; then
        warn "expected $EXPECT nodes, found ${#DRIVE_IDS[@]}"; ok=1
    fi
    if [ -n "$EXPECT" ] && [ "${#DRIVE_IDS[@]}" -gt "$EXPECT" ]; then
        warn "expected $EXPECT nodes, found ${#DRIVE_IDS[@]} (more than expected - check --expect)"; ok=1
    fi
    for w in $ONLY_DRIVES; do
        f=0; for x in ${DRIVE_IDS[@]+"${DRIVE_IDS[@]}"}; do [ "$x" = "$w" ] && f=1; done
        [ $f -eq 1 ] || miss="$miss $w"
    done
    [ -n "$miss" ] && { warn "asked for but not mounted:$miss"; ok=1; }
    if [ "$SCAN" -eq 1 ] && [ -s "$SCAN_DIR/pending" ]; then
        warn "attached but not mounted:"; pending_text >&2; echo >&2; ok=1
        grep -q fsck "$SCAN_DIR/pending" && \
            echo "   fsck: the system is checking the card; this can take minutes. Wait and rescan." >&2
    fi
    return $ok
}

hints_missing() {
cat >&2 <<EOF
   Nodes that do not show up at all are not seen by the computer (USB level):
     - reseat the node in the cable/cradle and wait 10-20 s before the next one
     - use a powered USB hub and fewer nodes per hub
     - macOS 13+ on Apple silicon: answer "Allow accessory to connect?", or set
       System Settings > Privacy & Security > Allow accessories to connect: Automatically when unlocked
     - check: diskutil list external (macOS) or lsblk (Linux)
EOF
}

interactive_loop() {
    local ans gate
    while :; do
        check_found; gate=$?
        [ $gate -ne 0 ] && hints_missing
        if [ $gate -eq 0 ]; then
            printf "All found. s = start, Enter = rescan, q = quit: "
        else
            printf "Not complete. Enter = rescan (reconnect missing nodes first), s = start anyway, q = quit: "
        fi
        read -r ans || ans=q
        case "$ans" in
            s|S) if [ $gate -ne 0 ]; then
                     printf "Start with %s node(s) although some are missing? (y/N): " "${#DRIVE_IDS[@]}"
                     read -r ans || ans=n
                     case "$ans" in y|Y|yes) GATE_OVERRIDDEN=1; return 0 ;; *) continue ;; esac
                 fi
                 return 0 ;;
            q|Q) echo "Stopped by user - nothing copied."; exit "$EXIT_NOT_STARTED" ;;
            *)   if [ "$SCAN" -eq 1 ]; then : > "$SCAN_DIR/tries"; scan_media; fi ;;
        esac
    done
}

# ============================================================================= planning
# Earlier harvests of a serial, newest first (one per line):
#   <out>/<serial>/<stamp>/,  <out>/<serial>_<stamp>/ (old layout),  <out>/<serial>/ itself if it
#   holds old-layout files. *.incomplete folders are never used.
previous_harvests() {
    local id="$1" d base own=0
    if [ -d "$OUT_ABS/$id" ]; then
        for d in "$OUT_ABS/$id"/*; do
            [ -d "$d" ] && is_stamp "$(basename "$d")" && echo "$d"
        done | sort -r
    fi
    for d in "$OUT_ABS/${id}"_*; do
        [ -d "$d" ] && is_stamp "${d##*/${id}_}" && echo "$d"
    done | sort -r
    if [ -d "$OUT_ABS/$id" ]; then
        for d in "$OUT_ABS/$id"/* "$OUT_ABS/$id"/.[!.]*; do
            [ -e "$d" ] || continue
            base=$(basename "$d")
            [ -d "$d" ] && is_stamp "$base" && continue
            case "$base" in *"$INCOMPLETE") continue ;; esac
            own=1; break
        done
        [ $own -eq 1 ] && echo "$OUT_ABS/$id"
    fi
}

# writes $RESULT_DIR/<id>.plan (path, size, mtime, new|unchanged, stored_in) and <id>.new;
# prints the number of earlier harvests
plan_drive() {
    local id="$1" src="$2" dest="$3" f rel sm p found
    local prevs=()
    if [ "$LAYOUT" = "dated" ]; then
        while IFS= read -r p; do [ -n "$p" ] && prevs+=("$p"); done <<EOF
$(previous_harvests "$id")
EOF
    fi
    : > "$RESULT_DIR/$id.plan"; : > "$RESULT_DIR/$id.new"
    list_files "$src" | LC_ALL=C sort | while IFS= read -r f; do
        rel="${f#"$src"/}"; sm=$(size_mtime "$f")
        [ -n "$sm" ] || { echo "cannot read: $rel" >> "$RESULT_DIR/$id.unreadable"; continue; }
        found=""
        for p in ${prevs[@]+"${prevs[@]}"}; do
            [ -f "$p/$rel" ] || continue
            if [ "$CHECKSUM" -eq 1 ]; then
                [ "$(size_mtime "$p/$rel" | cut -d' ' -f1)" = "${sm% *}" ] && cmp -s "$f" "$p/$rel" && { found="$p"; break; }
            else
                [ "$(size_mtime "$p/$rel")" = "$sm" ] && { found="$p"; break; }
            fi
        done
        if [ -n "$found" ]; then
            printf '%s\t%s\t%s\tunchanged\t%s\n' "$rel" "${sm% *}" "${sm#* }" "${found#"$OUT_ABS"/}" >> "$RESULT_DIR/$id.plan"
        else
            printf '%s\t%s\t%s\tnew\t%s\n' "$rel" "${sm% *}" "${sm#* }" "${dest#"$OUT_ABS"/}" >> "$RESULT_DIR/$id.plan"
            printf '%s\n' "$rel" >> "$RESULT_DIR/$id.new"
        fi
    done
    echo "${#prevs[@]}"
}

# ============================================================================= main
OUT_PATH=""; CSV_PATH=""; JOBS=""; VOLUMES_ROOT=""; CONFIG="$CONFIG_DEFAULT"
DRY_RUN=0; ONLY_DRIVES=""; CONFIG_GIVEN=0; EJECT=""
SCAN=""; WAIT=""; EXPECT=""; ALLOW_MISSING=""; INTERACTIVE=0; CHECKSUM=""; LAYOUT=""
GATE_OVERRIDDEN=0

while [ $# -gt 0 ]; do
    case "$1" in
        -o|--out)          [ -n "${2:-}" ] || die "$1 needs a path"; OUT_PATH="$2"; shift 2 ;;
        -c|--config)       [ -n "${2:-}" ] || die "$1 needs a file"; CONFIG="$2"; CONFIG_GIVEN=1; shift 2 ;;
        --csv)             [ -n "${2:-}" ] || die "$1 needs a path"; CSV_PATH="$2"; shift 2 ;;
        -j|--jobs)         [ -n "${2:-}" ] || die "$1 needs a number"; JOBS="$2"; shift 2 ;;
        -d|--drive)        is_drive_id "${2:-}" || die "--drive needs a 9-digit serial"; ONLY_DRIVES="$ONLY_DRIVES $2"; shift 2 ;;
        -n|--dry-run)      DRY_RUN=1; shift ;;
        -e|--eject)        EJECT=1; shift ;;
        --volumes-root)    [ -n "${2:-}" ] || die "$1 needs a dir"; VOLUMES_ROOT="$2"; shift 2 ;;
        --no-scan)         SCAN=0; shift ;;
        --wait)            [ -n "${2:-}" ] || die "$1 needs seconds"; WAIT="$2"; shift 2 ;;
        --expect)          [ -n "${2:-}" ] || die "$1 needs a number"; EXPECT="$2"; shift 2 ;;
        --allow-missing)   ALLOW_MISSING=1; shift ;;
        -i|--interactive)  INTERACTIVE=1; shift ;;
        --checksum)        CHECKSUM=1; shift ;;
        --legacy-layout)   LAYOUT="legacy"; shift ;;
        -h|--help)         usage; exit 0 ;;
        *)                 usage >&2; die "unknown option: $1" ;;
    esac
done

# ---- config file (command line values win)
yesno() { case "$1" in yes|YES|Yes|true|TRUE|1|on) echo 1 ;; no|NO|No|false|FALSE|0|off) echo 0 ;; *) echo "bad" ;; esac; }
if [ -f "$CONFIG" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%%#*}"
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        [ -z "$line" ] && continue
        case "$line" in *=*) ;; *) warn "ignoring line without '=' in $CONFIG: $line"; continue ;; esac
        key=$(echo "${line%%=*}" | sed 's/[[:space:]]*$//')
        val=$(echo "${line#*=}"  | sed 's/^[[:space:]]*//;s/^"\(.*\)"$/\1/;s/^'"'"'\(.*\)'"'"'$/\1/')
        case "$key" in
            out_path)      [ -z "$OUT_PATH" ]      && OUT_PATH="$val" ;;
            csv_path)      [ -z "$CSV_PATH" ]      && CSV_PATH="$val" ;;
            jobs)          [ -z "$JOBS" ]          && JOBS="$val" ;;
            volumes_root)  [ -z "$VOLUMES_ROOT" ]  && VOLUMES_ROOT="$val" ;;
            eject)         [ -z "$EJECT" ]         && EJECT=$(yesno "$val") ;;
            scan)          [ -z "$SCAN" ]          && SCAN=$(yesno "$val") ;;
            wait)          [ -z "$WAIT" ]          && WAIT="$val" ;;
            expect)        [ -z "$EXPECT" ]        && EXPECT="$val" ;;
            allow_missing) [ -z "$ALLOW_MISSING" ] && ALLOW_MISSING=$(yesno "$val") ;;
            checksum)      [ -z "$CHECKSUM" ]      && CHECKSUM=$(yesno "$val") ;;
            layout)        [ -z "$LAYOUT" ]        && LAYOUT="$val" ;;
            *) warn "unknown key in $CONFIG: $key" ;;
        esac
    done < "$CONFIG"
elif [ "$CONFIG_GIVEN" -eq 1 ]; then
    die "config file not found: $CONFIG"
fi

[ -n "$OUT_PATH" ] || die "no output path. Use --out PATH or set out_path=... in $CONFIG"
OUT_PATH=$(expand_tilde "$OUT_PATH")
[ -n "$CSV_PATH" ] && CSV_PATH=$(expand_tilde "$CSV_PATH")
JOBS="${JOBS:-4}"; EJECT="${EJECT:-0}"; SCAN="${SCAN:-1}"; WAIT="${WAIT:-60}"
ALLOW_MISSING="${ALLOW_MISSING:-0}"; CHECKSUM="${CHECKSUM:-0}"; LAYOUT="${LAYOUT:-dated}"
for v in EJECT SCAN ALLOW_MISSING CHECKSUM; do
    [ "$(eval echo \$$v)" = "bad" ] && die "$(echo $v | tr 'A-Z' 'a-z') in $CONFIG must be yes or no"
done
case "$JOBS" in ''|*[!0-9]*|0) die "jobs must be a positive integer" ;; esac
case "$WAIT" in ''|*[!0-9]*) die "wait must be a number of seconds" ;; esac
[ -z "$EXPECT" ] || case "$EXPECT" in *[!0-9]*|0) die "expect must be a positive number" ;; esac
case "$LAYOUT" in dated|legacy) ;; *) die "layout must be dated or legacy" ;; esac
[ "$INTERACTIVE" -eq 1 ] && [ ! -t 0 ] && { warn "--interactive needs a terminal; ignored"; INTERACTIVE=0; }

# ---- where to look for drives
if [ -n "$VOLUMES_ROOT" ]; then
    ROOTS="$(expand_tilde "$VOLUMES_ROOT")"
elif [ "$OS" = "Darwin" ]; then
    ROOTS="/Volumes"
else
    ROOTS="/media/${USER:-$(id -un)} /run/media/${USER:-$(id -un)} /media /mnt"
fi

RUN_TS=$(date +%Y%m%d_%H%M%S)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/smartsolo_copy.XXXXXX") || die "mktemp failed"
SCAN_DIR="$WORK/scan"; RESULT_DIR="$WORK/results"; mkdir -p "$SCAN_DIR" "$RESULT_DIR"
LOCK=""
cleanup() { [ -n "$LOCK" ] && rm -rf "$LOCK"; rm -rf "$WORK"; }
trap cleanup EXIT
trap 'echo; echo "Interrupted - harvest folders being written stay *.incomplete"; exit 130' INT TERM

# ---- real run: output folder, lock, run log
if [ "$DRY_RUN" -eq 0 ]; then
    mkdir -p "$OUT_PATH" || die "cannot create $OUT_PATH"
    [ -w "$OUT_PATH" ] || die "cannot write to $OUT_PATH"
    OUT_ABS=$(cd "$OUT_PATH" && pwd -P)
    LOCK_TRY="$OUT_ABS/.smartsolo_copy.lock"
    if ! mkdir "$LOCK_TRY" 2>/dev/null; then
        old_pid=$(cat "$LOCK_TRY/pid" 2>/dev/null)
        if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
            echo "ERROR: another smartsolo_copy.sh (pid $old_pid) is writing to $OUT_ABS - wait for it to finish." >&2
            exit "$EXIT_LOCKED"
        fi
        warn "removing a stale lock left by an earlier run (pid ${old_pid:-unknown})"
        rm -rf "$LOCK_TRY"; mkdir "$LOCK_TRY" 2>/dev/null || die "cannot create lock $LOCK_TRY"
    fi
    LOCK="$LOCK_TRY"; echo $$ > "$LOCK/pid"
    mkdir -p "$OUT_ABS/_harvest_logs"
    RUN_LOG="$OUT_ABS/_harvest_logs/harvest_${RUN_TS}.log"
    exec > >(tee -a "$RUN_LOG") 2>&1
else
    OUT_ABS=$( [ -d "$OUT_PATH" ] && cd "$OUT_PATH" && pwd -P || echo "$OUT_PATH" )
fi

echo "smartsolo_copy.sh v$VERSION   $(date '+%Y-%m-%d %H:%M:%S')   $( [ "$DRY_RUN" -eq 1 ] && echo 'DRY RUN' )"
echo "Looking in : $ROOTS"
echo "Output to  : $OUT_ABS   (layout: $LAYOUT$( [ "$LAYOUT" = dated ] && echo ', <serial>/<harvest_time>/, only new or changed files'))"
echo "Compare    : $( [ "$CHECKSUM" -eq 1 ] && echo 'file contents' || echo 'size + modification time')"
[ -n "$EXPECT" ]      && echo "Expect     : $EXPECT node(s)$( [ "$ALLOW_MISSING" -eq 1 ] && echo ', start even if some are missing')"
[ -n "$ONLY_DRIVES" ] && echo "Only       :$ONLY_DRIVES"
[ -n "$CSV_PATH" ]    && echo "QC CSV     : $CSV_PATH"
[ "$EJECT" -eq 1 ]    && echo "Eject      : yes (only nodes with QC status OK)"
[ "$DRY_RUN" -eq 0 ]  && echo "Run log    : $RUN_LOG"
n_inc=$(find "$OUT_ABS" -maxdepth 2 -type d -name "*$INCOMPLETE" 2>/dev/null | wc -l | tr -d ' ')
[ "$n_inc" -gt 0 ] && warn "$n_inc earlier harvest folder(s) *$INCOMPLETE in $OUT_ABS (failed or interrupted; not used as reference, kept for checking)"
echo

# ---- find nodes (mount what the system missed), then check against what is expected
[ "$SCAN" -eq 1 ] && scan_media
if [ "$INTERACTIVE" -eq 1 ]; then
    interactive_loop
elif ! check_found; then
    hints_missing
    if [ "$ALLOW_MISSING" -eq 1 ]; then
        warn "--allow-missing: starting with the ${#DRIVE_IDS[@]} node(s) found"; GATE_OVERRIDDEN=1
    elif [ "$DRY_RUN" -eq 1 ]; then
        echo "(a real run would stop here; showing the plan for the nodes found)"
        GATE_OVERRIDDEN=1
    else
        echo
        echo "NOT STARTED - nothing copied. Reconnect the missing nodes and run again,"
        echo "or use --interactive to rescan, or --allow-missing to copy what is found."
        exit "$EXIT_NOT_STARTED"
    fi
fi
find_drives

if [ ${#DRIVE_IDS[@]} -eq 0 ]; then
    echo "No nodes (drives with a 9-digit name) found - nothing copied."
    hints_missing
    exit "$EXIT_NOT_STARTED"
fi

for s in "${DRIVE_SRCS[@]}"; do
    s_abs=$(cd "$s" && pwd -P)
    case "$OUT_ABS/" in "$s_abs/"*) die "out_path is on node $s" ;; esac
done

# ---- free space (KB)
p="$OUT_PATH"; while [ ! -d "$p" ]; do p=$(dirname "$p"); done
FREE_KB=$(df -Pk "$p" | awk 'NR==2{print $4}')
MARGIN_KB=$((1024 * 1024))   # keep 1 GB free
if command -v rsync >/dev/null 2>&1; then COPY_TOOL="rsync"; else COPY_TOOL="cp"; warn "rsync not found, using cp"; fi

echo
printf '%-11s %6s %6s %11s %8s %-12s %s\n' "NODE" "FILES" "NEW" "NEW_SIZE" "EARLIER" "ACTION" "DESTINATION"
TODO=(); SKIPPED=(); need_total=0; need_bytes=0; i=0
while [ $i -lt ${#DRIVE_IDS[@]} ]; do
    id="${DRIVE_IDS[$i]}"; src="${DRIVE_SRCS[$i]}"; i=$((i+1))
    if [ "$LAYOUT" = "dated" ]; then
        dest="$OUT_ABS/$id/$RUN_TS"; k=2
        while [ -e "$dest" ] || [ -e "$dest$INCOMPLETE" ]; do dest="$OUT_ABS/$id/${RUN_TS}_$k"; k=$((k+1)); done
    else
        dest="$OUT_ABS/$id"; k=2
        if [ -e "$dest" ] || [ -e "$dest$INCOMPLETE" ]; then dest="$OUT_ABS/${id}_${RUN_TS}"; fi
        while [ -e "$dest" ] || [ -e "$dest$INCOMPLETE" ]; do dest="$OUT_ABS/${id}_${RUN_TS}_$k"; k=$((k+1)); done
    fi
    n_prev=$(plan_drive "$id" "$src" "$dest")
    n_all=$(wc -l < "$RESULT_DIR/$id.plan" | tr -d ' ')
    n_new=$(wc -l < "$RESULT_DIR/$id.new" | tr -d ' ')
    new_b=$(awk -F'\t' '$4=="new"{s+=$2} END{printf "%.0f", s}' "$RESULT_DIR/$id.plan")
    new_kb=$(awk -v b="$new_b" 'BEGIN{printf "%.0f", b/1024 + 1}')
    action="copy"; [ "$n_new" -eq 0 ] && action="manifest"
    if [ -s "$RESULT_DIR/$id.unreadable" ]; then
        action="SKIP(read)"; SKIPPED+=("$id:files on the node could not be read")
    elif [ "$n_all" -eq 0 ]; then
        action="SKIP(empty)"; SKIPPED+=("$id:no files on the node")
    elif [ $((need_total + new_kb + MARGIN_KB)) -gt "$FREE_KB" ]; then
        action="SKIP(space)"; SKIPPED+=("$id:not enough free space")
    else
        need_total=$((need_total + new_kb)); need_bytes=$(awk -v a="$need_bytes" -v b="$new_b" 'BEGIN{printf "%.0f", a+b}'); TODO+=("$id")
        echo "$src"  > "$RESULT_DIR/$id.src"; echo "$dest" > "$RESULT_DIR/$id.dest"
    fi
    printf '%-11s %6s %6s %11s %8s %-12s %s\n' "$id" "$n_all" "$n_new" "$(human "$new_b")" "$n_prev" "$action" "${dest#"$OUT_ABS"/}"
done
echo "Total new data: $(human "$need_bytes")   Free at destination: $(human $((FREE_KB * 1024)))"
echo "(EARLIER = earlier harvests of that node found; manifest = nothing new, only the file list is written)"

if [ "$DRY_RUN" -eq 1 ]; then
    echo; echo "Dry run - nothing copied."
    [ "$GATE_OVERRIDDEN" -eq 1 ] && [ "$ALLOW_MISSING" -eq 0 ] && exit "$EXIT_NOT_STARTED"
    exit 0
fi
if [ ${#TODO[@]} -eq 0 ]; then
    echo "Nothing to copy."
    for x in ${SKIPPED[@]+"${SKIPPED[@]}"}; do printf '%-11s SKIP    %s\n' "${x%%:*}" "${x#*:}"; done
    exit "$EXIT_WARN"
fi

# ---- copy (parallel)
echo
echo "Copying ${#TODO[@]} node(s) with $COPY_TOOL, $JOBS at a time ... (do not disconnect nodes)"
export RESULT_DIR COPY_TOOL CHECKSUM
T0=$SECONDS
printf '%s\0' "${TODO[@]}" | xargs -0 -n 1 -P "$JOBS" "$BASH_BIN" "$SCRIPT_PATH" --_worker
T_TOTAL=$((SECONDS - T0))

# ---- QC summary
echo
echo "==================================== QC SUMMARY ===================================="
printf '%-11s %-6s %7s %7s %9s %11s  %s\n' "NODE" "STATUS" "FILES" "NEW" "UNCHANGED" "NEW_SIZE" "FILE TYPES"
OK_SRCS=(); n_ok=0; n_fail=0; tot_new=0; tot_bytes=0
if [ -n "$CSV_PATH" ]; then
    mkdir -p "$(dirname "$CSV_PATH")"
    [ -s "$CSV_PATH" ] || echo "run_time,drive_id,source,destination,status,note,src_files,new_files,unchanged_files,src_bytes,new_bytes,duration_s,method,file_types" > "$CSV_PATH"
fi
q() { printf '"%s"' "$(echo "$1" | sed 's/"/""/g')"; }
for id in "${TODO[@]}"; do
    f="$RESULT_DIR/$id.result"
    if [ ! -f "$f" ]; then
        printf '%-11s %-6s  (copy did not finish)\n' "$id" "FAIL"; n_fail=$((n_fail+1)); continue
    fi
    IFS="$US" read -r r_id r_src r_dest r_status r_note n_src n_new n_same b_src b_new dur method types < "$f"
    printf '%-11s %-6s %7s %7s %9s %11s  %s\n' "$r_id" "$r_status" "$n_src" "$n_new" "$n_same" "$(human "$b_new")" "${types:--}"
    [ -n "$r_note" ] && echo "            -> $r_note"
    [ "$r_status" != "OK" ] && [ -s "$RESULT_DIR/$id.log" ] && sed 's/^/            | /' "$RESULT_DIR/$id.log" | tail -n 5
    if [ "$r_status" = "OK" ]; then OK_SRCS+=("$r_src"); n_ok=$((n_ok+1)); else n_fail=$((n_fail+1)); fi
    [ "$r_status" = "OK" ] && { tot_new=$((tot_new + n_new)); tot_bytes=$(awk -v a="$tot_bytes" -v b="$b_new" 'BEGIN{printf "%.0f", a+b}'); }
    [ -n "$CSV_PATH" ] && echo "$RUN_TS,$r_id,$(q "$r_src"),$(q "$r_dest"),$r_status,$(q "$r_note"),$n_src,$n_new,$n_same,$b_src,$b_new,$dur,$method,$(q "$types")" >> "$CSV_PATH"
done
for x in ${SKIPPED[@]+"${SKIPPED[@]}"}; do
    printf '%-11s %-6s  not copied: %s\n' "${x%%:*}" "SKIP" "${x#*:}"
    [ -n "$CSV_PATH" ] && echo "$RUN_TS,${x%%:*},,,SKIP,$(q "${x#*:}"),,,,,,," >> "$CSV_PATH"
done
echo "------------------------------------------------------------------------------------"
echo "OK: $n_ok   FAIL: $n_fail   SKIPPED: ${#SKIPPED[@]}$( [ "$GATE_OVERRIDDEN" -eq 1 ] && echo '   (started with nodes missing)')"
echo "Copied and verified: $tot_new new files, $(human "$tot_bytes") in ${T_TOTAL}s"
[ -n "$CSV_PATH" ] && echo "QC rows appended to $CSV_PATH"

# ---- eject nodes that copied OK
n_eject_fail=0
if [ "$EJECT" -eq 1 ]; then
    echo
    if [ ${#OK_SRCS[@]} -eq 0 ]; then
        echo "Eject: no node with status OK - nothing ejected."
    else
        sync
        for src in "${OK_SRCS[@]}"; do
            if eject_drive "$src"; then
                echo "Ejected      $(basename "$src")"
            else
                echo "EJECT FAILED $(basename "$src")  (still mounted - eject it by hand)"
                n_eject_fail=$((n_eject_fail+1))
            fi
        done
    fi
    n_left=$(( ${#DRIVE_IDS[@]} - ${#OK_SRCS[@]} ))
    [ $n_left -gt 0 ] && echo "$n_left node(s) not OK - left mounted for checking."
fi
echo "Run log: $RUN_LOG"

[ $n_fail -gt 0 ] && exit "$EXIT_FAIL"
{ [ ${#SKIPPED[@]} -gt 0 ] || [ $n_eject_fail -gt 0 ] || [ "$GATE_OVERRIDDEN" -eq 1 ]; } && exit "$EXIT_WARN"
exit "$EXIT_OK"

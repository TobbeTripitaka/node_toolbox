# Harvest: copying data off SmartSolo nodes

`smartsolo_copy.sh` (v2.1) copies the data from SmartSolo nodes connected over
USB into one backup folder and prints a quality check (QC). A node shows up as
a USB drive named with its 9-digit serial number, for example
`/Volumes/453021191`.

It is plain bash and runs on macOS (including the default bash 3.2) and on
Linux. It needs no Python, and no `sudo`.

## Quick start

```bash
cd harvest
cp smartsolo_copy.conf.example smartsolo_copy.conf    # once; set out_path
./smartsolo_copy.sh -n --expect 12                     # dry run: what is found, what is new
./smartsolo_copy.sh    --expect 12                     # copy
```

Always give `--expect N` with the number of nodes you connected. If fewer
are found, nothing is copied (see [Finding nodes](#finding-nodes)).

## Folder layout

```
out_path/
  453021191/
    20261116_173026/              first harvest: every file on the node
      seis00010.DLD ...
      DigiSolo.LOG
      _harvest_manifest.tsv
    20261120_101500/              later harvest: only files that are new or changed
      seis00011.DLD
      DigiSolo.LOG                (the log grew, so it is stored again)
      _harvest_manifest.tsv       lists ALL files on the node at that time
  453021636/
    ...
  _harvest_logs/
    harvest_20261120_101500.log   full screen output of every run
  qc_log.csv                      (if csv_path is set)
```

- A harvest folder is named after the time the run started
  (`YYYYmmdd_HHMMSS`). If the name already exists, `_2`, `_3` ... is added.
- A file counts as unchanged if an earlier harvest of the same node already
  has the same path with the same size and modification time. It is not
  copied again. With `--checksum` the contents are compared instead.
- New and changed files go into the new folder. Nothing is overwritten. When
  a log grows, both versions are kept.
- Every harvest folder has `_harvest_manifest.tsv`, with one row per file on
  the node: `path`, `size_bytes`, `mtime_unix`, `state` (`new` or
  `unchanged`), and `stored_in` (the folder that holds that version). The
  manifest is written even when nothing is new, so each harvest is fully
  documented.
- Old (v1) folders, `<serial>/` with files in it and `<serial>_YYYYmmdd_HHMMSS/`,
  count as earlier harvests. Nothing in them is moved or changed. For a node
  that only has an old folder, the first v2 harvest goes into a dated
  subfolder of `<serial>/`, next to the old files.
- To get the complete set of files of a node, take the manifest of its
  latest harvest: `stored_in` says where each file is. D43 notebook 01 reads
  this layout directly and keeps one copy of each file.
- `--legacy-layout` (or `layout=legacy`) gives the v1 behaviour: a full copy
  into `<serial>/`, or `<serial>_YYYYmmdd_HHMMSS/` if that exists.

## Safety

- **The nodes are only read.** Nothing on them is changed or deleted. Nodes
  that the script mounts itself are mounted read-only. There is no delete
  option.
- **Nothing in `out_path` is overwritten or deleted.** Every harvest gets a
  new folder.
- **No half-finished harvests in the archive.** A harvest is first copied into
  `<stamp>.incomplete/`. It is renamed to `<stamp>/` only after every new file
  has been checked against the node (size and modification time; with
  `--checksum`, the contents too).
  - If the copy fails, or the run is interrupted (Ctrl-C, cable pulled), the
    folder stays `.incomplete`. It is never used as an earlier harvest, so the
    next run copies those files again.
  - Every run warns about `.incomplete` folders. Delete them by hand once a
    later harvest of that node is OK.
- **It does not start with nodes missing.** See below.
- **One run at a time.** A lock (`out_path/.smartsolo_copy.lock`) stops a
  second run writing to the same `out_path`. A lock left by a crashed run is
  removed automatically.
- **Other checks before copying:**
  - The script stops if `out_path` is on one of the nodes.
  - It checks there is room for the new data, plus 1 GB to spare. Nodes that
    do not fit are skipped (SKIP).
  - Nodes with files that cannot be read, or with no files at all, are
    skipped too.
- **Ejecting is off by default.** With `--eject`, only nodes whose copy passed
  QC are ejected.
- **Bad settings stop the run** before anything happens: an unknown option, or
  a bad value in the config file (for example `eject=maybe`).
- Keep the data on the nodes until the QC says OK and the backup disk has been
  copied again.

## Finding nodes

Before anything is copied, the script makes sure it sees every node.

1. **Mount what the system missed.** It lists attached FAT/exFAT partitions on
   external drives whose label is a 9-digit serial, or whose label cannot be
   read yet. It mounts those read-only: `diskutil mount readOnly` on macOS,
   `udisksctl mount -o ro` on Linux.
   - A partition whose label was unreadable is kept only if it turns out to be
     a node. Other USB sticks are unmounted again and left alone.
2. **Retry.** A mount that fails is tried again every few seconds, for up to
   `--wait` seconds (default 60). Some nodes only mount after several
   attempts; the screen shows `attempt N` when they do.
   - If macOS is still running a file-system check (`fsck`) on a node, the
     script waits for it rather than interrupting it. This can take minutes for
     a full card that was not ejected cleanly.
3. **Check.** The script then compares what is mounted with what you said:
   - with `--expect N`: exactly N nodes must be mounted;
   - with `--drive ID`: every node you asked for must be mounted;
   - always: no node may be attached but still unmounted.

   If any check fails, the script prints what is missing and why, prints
   `NOT STARTED - nothing copied`, and stops with exit code 3. Reconnect the
   missing nodes and run again, or:
   - `--interactive` (`-i`): fix it without restarting. Reconnect the nodes,
     then press Enter to rescan, `s` to start, or `q` to quit. Starting with
     nodes still missing asks for confirmation.
   - `--allow-missing`: copy what was found anyway. It still warns, and exits
     with code 1.

`--no-scan` skips the mounting and waiting, and uses only what is already
mounted. The check against `--expect` and `--drive` still applies. A dry run
(`-n`) also mounts nodes (read-only) and shows the plan even when a check
fails, so you can see what is wrong.

### Nodes that never show up

If a node is not listed at all by `diskutil list external` (macOS) or
`lsblk` (Linux), the computer does not see it on the USB bus, and no script
can mount it. What usually helps:

- Reseat the node in its cable or cradle, and wait 10 to 20 s before
  connecting the next one.
- Use a powered USB hub, with fewer nodes per hub. Try `-j 1` if the hub drops
  nodes while copying.
- On macOS 13 or later on Apple silicon laptops, each new USB device must be
  allowed: answer "Allow accessory to connect?", or set System Settings >
  Privacy & Security > Allow accessories to connect to "Automatically when
  unlocked".
- Do not use the Finder on a node while it is mounting.

## Options

| Option | Meaning |
|---|---|
| `-o, --out PATH` | Output folder. Overrides the config file. |
| `-c, --config FILE` | Config file. Default: `./smartsolo_copy.conf`. |
| `--csv PATH` | Add one QC row per node to this CSV. |
| `-j, --jobs N` | Nodes copied at the same time. Default 4. |
| `-d, --drive ID` | Copy only this node. Can be repeated. If it is missing, the run does not start. |
| `-n, --dry-run` | Show the nodes, what is new and where it would go. Copies nothing and writes nothing to `out_path`. |
| `-e, --eject` | Eject each node whose copy passed QC. |
| `--volumes-root DIR` | Where drives are mounted. Default `/Volumes` (macOS); `/media/$USER`, `/run/media/$USER`, `/media`, `/mnt` (Linux). |
| `--expect N` | Number of nodes connected. If a different number is found, the run does not start. |
| `--allow-missing` | Start even if nodes are missing (warns, exit code 1). |
| `--wait SEC` | Keep mounting and rescanning for at most SEC seconds. Default 60. |
| `--no-scan` | Do not mount or wait. |
| `-i, --interactive` | Rescan by hand before starting: Enter = rescan, `s` = start, `q` = quit. |
| `--checksum` | Compare file contents instead of size and modification time. Slower. |
| `--legacy-layout` | v1 behaviour: full copy to `<serial>/` or `<serial>_YYYYmmdd_HHMMSS/`. |
| `-h, --help` | Help. |

## Config file

Put the settings you always use in `smartsolo_copy.conf`, in the folder you
run the script from. Command-line options override it.

```ini
out_path=/Volumes/LaCie/MINIS/smart_solo_backup
csv_path=/Volumes/LaCie/MINIS/smart_solo_backup/qc_log.csv
jobs=4
eject=no
scan=yes
wait=60
#expect=12          # usually better given on the command line for each batch
allow_missing=no
checksum=no
layout=dated
#volumes_root=/Volumes
```

`~` is expanded in paths. `yes`/`no` keys also accept `true`/`false` and
`1`/`0`.

## Examples

A batch of 12 nodes, ejecting the ones that copied OK:

```bash
./smartsolo_copy.sh --expect 12 --eject
```

Nodes that are hard to mount: wait longer, and rescan by hand after
reseating the missing ones:

```bash
./smartsolo_copy.sh --expect 12 --wait 120 -i
```

Two particular nodes, one at a time, comparing file contents:

```bash
./smartsolo_copy.sh -d 453021191 -d 453021636 -j 1 --checksum
```

In another script, stopping if anything went wrong:

```bash
./smartsolo_copy.sh --expect 12 || { echo "check the output (exit $?)"; exit 1; }
```

## Output

```
Finding nodes (mount attached ones read-only, retry for up to 60s) ...
  mounted (read-only)  453021191  (/dev/disk4s1, attempt 1)
  [  7s] mounted: 11 of 12 expected, not mounted yet: 1
  mounted (read-only)  453021636  (/dev/disk9s1, attempt 3)
  [ 20s] mounted: 12 of 12 expected

NODE         FILES    NEW    NEW_SIZE  EARLIER ACTION       DESTINATION
453021191      214      3    48.20 MB        1 copy         453021191/20261120_101500
453021636      214      0         0 B        1 manifest     453021636/20261120_101500
...
==================================== QC SUMMARY ====================================
NODE        STATUS   FILES     NEW UNCHANGED    NEW_SIZE  FILE TYPES
453021191   OK         214       3       211    48.20 MB  DLD:212;LOG:1;ini:1
453021636   OK         214       0       214         0 B  DLD:212;LOG:1;ini:1
            -> nothing new since the last harvest (manifest only)
```

(The numbers are illustrative.) `EARLIER` is the number of earlier harvests
of that node that were found. `manifest` means nothing is new, so only the
file list is written.

### QC status

| Status | Meaning |
|---|---|
| `OK` | Every new file was copied and checked. The folder is final (`<stamp>/`). |
| `FAIL` | The copy failed or could not be checked. The folder stays `<stamp>.incomplete/`, and the last error lines are shown. |
| `SKIP` | Not copied: not enough free space, unreadable files, or an empty node. |

Files the operating system puts on USB drives (`.Spotlight-V100`,
`.fseventsd`, `.Trashes`, `.TemporaryItems`, `System Volume Information`,
`.DS_Store`, `._*`) are neither copied nor counted.

### QC CSV

One row per node and run:

```
run_time,drive_id,source,destination,status,note,src_files,new_files,unchanged_files,src_bytes,new_bytes,duration_s,method,file_types
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | All nodes OK (and ejected, if asked). |
| 1 | Some nodes skipped, an eject failed, or started with `--allow-missing` while nodes were missing. |
| 2 | At least one FAIL, or an error in the settings. |
| 3 | Not started: nodes missing or not mounted, or none found. Nothing copied. |
| 4 | Another run is writing to the same `out_path`. |

## Notes

- **Timestamps.** FAT/exFAT cards store local time. If the computer's time
  zone changed since the last harvest, files can look changed. They are then
  copied again, which is safe but wastes space. Use `--checksum`, or keep
  the harvest computer on one time zone (UTC).
- **Copy tools.** Copying uses `rsync -a --files-from` (never `--delete`),
  or `cp -p` if `rsync` is missing.
- **Mounting.** Uses `diskutil` on macOS and `lsblk`/`udisksctl` on Linux.
  Without `udisksctl`, Linux nodes must be mounted by the desktop or by hand.
- **Ejecting.** Uses `diskutil eject` on macOS; `udisksctl` unmount and
  power-off, or `umount`, on Linux.

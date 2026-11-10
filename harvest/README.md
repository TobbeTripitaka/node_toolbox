# Harvest: copying data off SmartSolo nodes

`smartsolo_copy.sh` copies all data from SmartSolo nodes connected over USB
into one backup folder, one subfolder per node, and prints a short quality
check (QC).

A node shows up as a USB drive named with its 9-digit serial number, for
example `/Volumes/453021191`. The script finds every mounted drive with a
9-digit name and copies it to `<out_path>/<serial>/`.

It is plain bash and runs on macOS (including the old default bash 3.2) and
Linux. It does not need Python or the rest of the toolbox.

## Safety

- The nodes are only read. Nothing on them is changed or deleted, and there
  is no `--delete` option.
- Nothing at the destination is overwritten. If `<out_path>/453021191`
  already exists, the copy goes to `453021191_YYYYmmdd_HHMMSS` instead.
- The script stops if `out_path` is on one of the nodes being copied.
- Before copying it checks that the destination has room for the data
  (plus 1 GB to spare). Nodes that do not fit are skipped.
- Ejecting is off by default. With `--eject`, only nodes whose copy passed
  QC are ejected; the others stay mounted so you can check them.
- Keep the data on the nodes until the QC says OK and the copy is backed up.

## Quick start

```bash
cd node_toolbox/harvest
./smartsolo_copy.sh -n --out /Volumes/LaCie/MINIS/smart_solo_backup   # dry run: what would be copied
./smartsolo_copy.sh    --out /Volumes/LaCie/MINIS/smart_solo_backup   # copy
```

If the script is not executable, run it with `bash ./smartsolo_copy.sh ...`
or make it executable once with `chmod +x smartsolo_copy.sh`. `sudo` is not
needed.

## Options

| Option | Meaning |
|---|---|
| `-o, --out PATH` | Output folder. Overrides the config file. |
| `-c, --config FILE` | Config file. Default: `./smartsolo_copy.conf` |
| `--csv PATH` | Add one QC row per node to this CSV. No CSV unless this or `csv_path` is set. |
| `-j, --jobs N` | Nodes copied at the same time. Default 4; `1` = one after another. |
| `-d, --drive ID` | Copy only this node. Can be repeated. |
| `-n, --dry-run` | List the nodes found, their size and destination; copy nothing. |
| `-e, --eject` | Eject each node after its copy passed QC. |
| `--volumes-root DIR` | Where drives are mounted. Default `/Volumes` (macOS); `/media/$USER`, `/run/media/$USER`, `/media`, `/mnt` (Linux). |
| `-h, --help` | Help. |

## Config file

Instead of typing the options every time, put them in `smartsolo_copy.conf`
in the folder you run the script from. Start from the example:

```bash
cp smartsolo_copy.conf.example smartsolo_copy.conf
```

```ini
out_path=/Volumes/LaCie/MINIS/smart_solo_backup
csv_path=/Volumes/LaCie/MINIS/smart_solo_backup/qc_log.csv
jobs=4
eject=no
#volumes_root=/Volumes
```

Order of precedence: command line, then config file. If neither gives an
output path, the script stops with an error. `~` is expanded in paths.

## Examples

Copy every connected node, using the settings in `./smartsolo_copy.conf`:

```bash
./smartsolo_copy.sh
```

Copy to a given folder and eject the nodes that copied OK:

```bash
./smartsolo_copy.sh --out /Volumes/LaCie/MINIS/smart_solo_backup --eject
```

Copy two particular nodes only, one at a time, and log the QC to a CSV:

```bash
./smartsolo_copy.sh -d 453021191 -d 453021636 -j 1 --csv ~/harvest_qc.csv
```

Use another config file (e.g. one per field season):

```bash
./smartsolo_copy.sh -c ~/configs/season_2026.conf
```

Use it in another script; stop if anything went wrong:

```bash
./smartsolo_copy.sh --eject || { echo "check the QC output"; exit 1; }
```

## Output

```
smartsolo_copy.sh v1.2   2026-11-10 17:30:26
Looking in : /Volumes
Output to  : /Volumes/LaCie/MINIS/smart_solo_backup

DRIVE       USED         ACTION     DESTINATION
453021191   562.00 KB    copy       /Volumes/LaCie/MINIS/smart_solo_backup/453021191
453021636   47.02 MB     copy       /Volumes/LaCie/MINIS/smart_solo_backup/453021636
Total to copy: 47.57 MB   Free at destination: 1.82 TB

Copying 2 drive(s) with rsync, 4 at a time ...
[17:30:26] START  453021191  ->  /Volumes/LaCie/MINIS/smart_solo_backup/453021191
[17:30:26] START  453021636  ->  /Volumes/LaCie/MINIS/smart_solo_backup/453021636
...

================================ QC SUMMARY ================================
DRIVE       STATUS SRC_FILES DST_FILES     SRC_SIZE     DST_SIZE  FILE TYPES
453021191   OK             5         5    560.12 KB    560.12 KB  DLD:1;LOG:1;ini:1;...
453021636   OK            12        12     47.01 MB     47.01 MB  DLD:3;LOG:1;...
----------------------------------------------------------------------------
OK: 2   WARN: 0   FAIL: 0   SKIPPED: 0
```

(The numbers above are illustrative.)

### QC status

| Status | Meaning |
|---|---|
| `OK` | Copy finished, and the file count and total size match the node. |
| `WARN` | Copy finished, but the counts or sizes differ. Check the node before reusing it. |
| `FAIL` | The copy failed. The last lines of the error are shown. |
| `SKIP` | Not copied: not enough free space at the destination. |

The QC compares file counts and sizes. It does not compute checksums, so it
catches missing or cut-off files but not a file with the right size and
corrupted content.

Files that the operating system puts on USB drives (`.Spotlight-V100`,
`.fseventsd`, `.Trashes`, `.TemporaryItems`, `System Volume Information`,
`.DS_Store`, `._*`) are neither copied nor counted.

### QC CSV

With `--csv` or `csv_path`, one row per node is added on every run:

```
run_time,drive_id,source,destination,status,note,src_files,dst_files,src_bytes,dst_bytes,duration_s,method,file_types
20261110_173026,453021191,"/Volumes/453021191","/Volumes/LaCie/.../453021191",OK,"",5,5,573563,573563,1,rsync,"DLD:1;LOG:1;..."
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | All nodes OK (and ejected, if asked) |
| 1 | At least one WARN, SKIP or failed eject, or nothing to copy |
| 2 | At least one FAIL |

## How it works

- Copies with `rsync -a` (no `--delete`); falls back to `cp -Rp` if `rsync`
  is not installed.
- Copies several nodes at once (`--jobs`, default 4). Each USB node is slow
  compared with the destination disk, so copying in parallel is usually much
  faster. Use `-j 1` if a USB hub becomes unreliable.
- Ejects with `diskutil eject` on macOS; with `udisksctl` (unmount, then
  power off) or `umount` on Linux.

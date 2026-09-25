# Loading your ROM and harvesting rooms

Everything the VR pipeline builds starts from room dumps (`.tmcr` files) that
the game writes about itself while it runs. This page covers getting from a
fresh clone and your own ROM to a folder of dumps. What happens after that is
in [README.md](README.md#pipeline).

Nothing on this page produces anything you may commit or share. The ROM,
the dumps and everything made from them stay on your machine, and
`.gitignore` already excludes all of it.

## 1. The ROM

You need your own dump of *The Legend of Zelda: The Minish Cap*. The
pipeline was developed against the **USA** version, and the harvest tools
default to it.

| Version | File name | SHA-1 |
|---|---|---|
| USA | `baserom.gba` | `b4bd50e4131b027c334547b4524e2dbbd4227130` |
| EU | `baserom_eu.gba` | `cff199b36ff173fb6faf152653d1bccf87c26fb7` |

Check yours before going further; a bad dump fails in confusing ways later:

```sh
sha1sum baserom.gba
```

Put it in the repository root as `baserom.gba`. (`build.py` will also find a
matching `.gba` in the folder above the repository or in `~/Downloads`, check
its SHA-1 and copy it into the root for you.)

## 2. Build and stage the game

```sh
python3 build.py --usa
```

This extracts the game's assets from your ROM and stages a runnable game in
`dist/USA/`: `tmc_pc`, `assets/` and `sounds.json`. The ROM itself is not
copied there; `tmc_pc` finds it at `../../baserom.gba`, which from
`dist/USA/` is the repository root. Copying `baserom.gba` into `dist/USA/`
works too.

Check the game runs before adding anything:

```sh
cd dist/USA && ./tmc_pc
```

## 3. Rebuild with the VR capture code

A normal build leaves the capture code out. Wire in the `vr` option first
(see [Setup](README.md#setup); it is not committed yet), then:

```sh
xmake f -y --game_version=USA --vr=y
xmake build -y tmc_pc
cp build/pc/tmc_pc dist/USA/
```

Confirm the capture code is actually in the binary. If this prints nothing,
`TMC_VR` was not defined and a harvest will write no dumps:

```sh
strings dist/USA/tmc_pc | grep TMC_ROOMCAP_TMCR
python3 tools/apply_roomcap_hook.py --check
```

Run `python3 tools/apply_roomcap_hook.py` (no flag) if the check says the
hook is missing or out of date, then rebuild.

## 4. Capture one area first

```sh
python3 tools/harvest_rooms.py --out vrdump --area 0
```

That captures every room of area 0 (Minish Woods), each in its own headless
run of the game: boot a fresh save, warp to the room, wait for it to settle,
write `vrdump/room_AA_RR.tmcr`, exit. Look at one to be sure it is real:

```sh
python3 tools/extract_art.py room vrdump/room_00_00.tmcr --out room.png
```

`room.png` should show the room as the game draws it.

## 5. Capture everything

```sh
python3 tools/harvest_rooms.py --out vrdump --jobs 8
```

- `--jobs` is how many copies of the game run at once (default: half your
  CPUs). Each dump is about 200 KB; the whole game is about 100 MB.
- It resumes: rooms already in `vrdump/` are skipped. `--redo` captures them
  again.
- It takes a while. The last full run took about an hour and a half.
- **Not every room captures, and that is expected.** Out of 842 slots the
  last run captured 562. Unused slots are refused by the warp; some rooms
  only exist after story events; a few warps land in a neighbouring room
  (exit code 6), in which case nothing is written rather than the wrong
  room. The summary at the end lists every failure with its exit code.
- Three areas always load as a different area on a fresh save (Hyrule Town
  as Festival Town, for example). They are reported as *redirected*, not
  failed; the area that loads instead is captured through its own entry in
  the room table. Step 6 captures the real Hyrule Town.

`--area N` (repeatable) limits the run, `--list` prints the room table,
`--binary dist/EU/tmc_pc` uses an EU build.

## 6. Capture later story states (optional)

Some rooms change as the story moves on: torches lit, chests opened, Hyrule
Town after the festival. To capture the world after 0, 1, … 6 dungeons:

```sh
bash tools/harvest_states.sh
```

This installs the capture hook, builds, and harvests into `states/p0` …
`states/p6`, about 20 minutes to 1.5 hours per state, skipping any state
folder that already has dumps. `tools/voxelate_all.sh` uses `states/p0` in
preference to `vrdump/` when it exists.

To capture a single state by hand, set `TMC_ROOMCAP_PROGRESS`:

```sh
TMC_ROOMCAP_PROGRESS=3 python3 tools/harvest_rooms.py --out states/p3
```

## 7. What you have now

| Folder | Contents |
|---|---|
| `vrdump/` | one `.tmcr` per captured room |
| `states/pN/` | the same, after N dungeons (if you ran step 6) |

All of it is made from your ROM and is gitignored. It takes hours to
recreate, so back it up somewhere outside the repository if you might
delete your checkout. Next: `bash tools/voxelate_all.sh` (see
[Pipeline](README.md#pipeline)).

## Troubleshooting

| Symptom | Cause |
|---|---|
| `binary not found: …/dist/USA/tmc_pc` | Step 2 has not been run, or you passed a different `--binary`. |
| Every room fails and `vrdump/` stays empty | The binary has no capture code: redo step 3 and its `strings` check. |
| The game says it could not load `baserom.gba` | The ROM is not in the repository root or `dist/USA/`, or its name or SHA-1 is wrong (step 1). |
| `rc=6` for a room | The warp landed in another room. Try `--spawn x,y` with a point inside the room. |
| `rc=-1` or `rc=3` | Timeout. Raise `--timeout` (seconds) or `--settle` (frames). |
| `harvest_night.sh` writes no sprite frames | Known: the current hook no longer reads `TMC_ROOMCAP_SPRITERANGE`. See [What is not finished](README.md#what-is-not-finished). |

# 11 — Upstream 0.9.6: merging it, and what the VR work can use

999sian/tmc master is 45 commits ahead of `vr` (release 0.9.6,
`d377558d9`). Four of them are the VR backports already in `vr`:
- the room tours (6380866bb);
- `voxel_shapes.json` (366d51a5b, e70144327);
- the wall fade (6b13a73de).

## Merging

A trial merge (`git merge --no-commit sian/master`) conflicts in three
files.

**`build.py`.** Upstream adds `_assets_fingerprint` and
`_invalidate_region_caches`, which the code that calls them already
uses. Take upstream's side.

**`port/port_voxel.cpp`.** One hunk: the wall fade now lifts actors on
the upper collision layer by `kTopLayerLift`. Take upstream's side.

**`port/port_repro_roomcap.c`.** Upstream reviewed and hardened our tour
code:
- a tour plan is validated and refused if malformed;
- `TMC_ROOMCAP_TOUR_START` is range-checked;
- a failed capture exits with status 2;
- a view that never arrives fails the run instead of being skipped.

Take upstream's side, then:
- check that the managed `TMC_VR` block is intact
  (`tools/apply_roomcap_hook.py`);
- update `tools/room_capture.py` (VR-only; upstream has no copy) to
  resume after a failed view with `TMC_ROOMCAP_TOUR_START` rather than
  expect a skip line.

Merged into `vr` on 2026-10-20, resolved as above. Checks run:
- upstream's `tools/roomcap_regression_test.py` and
  `tools/voxel_regression_test.py` pass against the merged file, with the
  TMC_VR block reinstalled by `tools/apply_roomcap_hook.py`;
- `port/port_repro_roomcap.c` compiles with and without `TMC_VR`
  (`gcc -fsyntax-only`, the build's defines).

`tools/room_capture.py` now reads `failed` (formerly `skip`) and `capture
failed`, and resumes after either.

## Cues the VR work can use

**Actors on the upper layer.** An entity's `collisionLayer == 2` means it
stands on the upper layer, which upstream draws `kTopLayerLift` (16 px)
up. That is the game's own answer to "is Link on the raised level". The
per-room level map (doc 09, sprint 2) should use it with the dump's
entity `layer` field, not only the tile heights.

**Entities across the whole 3D view** (0b33c3640). While the 3D view
draws, sprites are produced up to 256 px past the GBA screen, and pieces
OAM can't hold keep their true position in a voxel tag. The VR runtime
needs actors well outside the 240×160 window, and this is how to get
them.

**No 2D flashes while scrolling** (e4135be8b). The test of how much of
the map is on screen now scores the camera tile and its 8 neighbours.
The same issue applies when the VR view decides whether a room is
loaded.

**Save-independent scenes** (`tools/render_scene_check.py`,
`render_scenes.tsv`). Named scenes start a fresh transient save and warp
with the room capture harness (`TMC_ROOMCAP_WARP=area,room,x,y,layer`),
with environment and settings pinned. This is the way to give vox's
reference scenes, and the early-game story rooms, a reproducible
capture: add rows for Link's house, the town on festival day and the
castle rooms.

**Source-level regression tests** (`tools/voxel_regression_test.py`,
`tools/roomcap_regression_test.py`). These lift one function out of the
C source and test it without the game or ROM. The same pattern suits
vox's pure functions:
- the cap profile;
- the 45° height solve;
- the classifier's colour families.

**Asset fingerprint.** `build.py` now hashes the extraction inputs and
reuses `build/<version>/assets` on code-only rebuilds. `voxelate_all.sh`
could key its geometry cache the same way, so meshes rebuild only when
the dumps or the tools change.

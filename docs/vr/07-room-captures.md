# 07 — Room captures: building from what the game shows

A room dump (`.tmcr`, see [05](05-rom-and-harvest.md)) holds the room's tile
layers. Much of what makes a room look right is not in them: the game draws
it with **sprites**. Link's house shows the problem at once:

- each doorway's wooden **arch** is an ARCHWAY object's sprite
  (`src/object/archway.c`). The tiles under it draw red "kabe" (wall)
  marker plates and a dark opening two cells wide; the arch covers the
  plates and narrows the opening to about 22 px;
- the **shelf of plates** and the **water barrel** are furniture objects,
  sprites standing on cells the tiles draw as floor;
- the front door's **mat** is a sprite.

So the pipeline also captures each room from the running game, and builds
from what the player actually sees.

## The process

```
tmc_pc (port_repro_roomcap.c tour)  ──►  one capture per view, camera logged
                │
tools/room_capture.py  ──►  vr/captures/room_AA_RR_shown.png    the room as shown
                            vr/captures/room_AA_RR_sprite.png   what sprites draw
                            vr/captures/room_AA_RR_capture.json views, matches
                │
tools/tilevox.py  (reads vr/captures/, or $TMC_CAPTURES)
```

Everything in `vr/captures/` is made from your ROM and stays local: the
folder is ignored.

### 1. A game that runs here

`room_capture.py` drives a built `tmc_pc` (see AGENTS.md for the build). In
a cloud container without SDL's X11 and GL packages, install them from the
distribution rather than letting xmake fetch them (its source downloads may
be blocked by the network policy):

```bash
apt-get install -y libegl-dev libgl-dev libgles-dev libx11-dev libxext-dev \
    libxrandr-dev libxcursor-dev libxi-dev libxss-dev libxfixes-dev \
    libxkbcommon-dev libwayland-dev libdrm-dev libgbm-dev libxtst-dev \
    libcurl4-openssl-dev libpng-dev zlib1g-dev nlohmann-json3-dev
```

Then, in the game folder's `config.json`, turn the port's GBA-LCD colour
correction off, so the screen shows the tile art's own colours:

```json
"color_correction": false
```

(With it on, the tool learns the correction back from the capture, less
exactly.)

### 2. The tour

```bash
python3 tools/room_capture.py DUMPS 34_16,03_06 --game dist/USA
python3 tools/room_capture.py DUMPS 34 --game dist/USA      # a whole area
python3 tools/room_capture.py DUMPS all --game dist/USA     # every room
```

The tool plans views over each room — screen-sized, overlapping, the last
flush with the room's far edges — with Link standing on walkable ground in
each. It writes the plan and runs the game **once**:
`TMC_ROOMCAP_TOUR=<plan>` makes the capture harness
(`port/port_repro_roomcap.c`) warp through every view in one session, as
999sian's voxel tour (`port/port_repro_npc_talk.c`) walks every room.
About three seconds a view, against seven for a game started per capture.

In the tour the harness keeps the room alone on screen:

| What | How |
|---|---|
| the HUD | the game's own `gHUD.hideFlags = HUD_HIDE_ALL` (`include/ui.h`) |
| Link | not drawn (`gPlayerEntity.base.spriteSettings.draw = 0`) |
| dialogue, cutscenes | A pressed while the player has no control, as the voxel tour does |
| the prologue | the new game starts past it (`TMC_ROOMCAP_PROGRESS`, one dungeon cleared): the flags, and what the game hands out by then — each dungeon's element (without it, an area change takes the clear back, `gameUtils.c`) and the kinstone bag (without it, the town plays Ezlo's kinstone scene) |
| a scene | a view is taken only with the camera on Link; one a scene holds elsewhere is skipped (`TMC_ROOMCAP_TOUR_DEBUG=1` logs the scripts running, to find it) |
| the area-name box | the settle time (160 frames) outlasts it: `enterRoomTextboxManager.c` shows it for 120 |

Each capture is logged with the room, where the camera was
(`scroll - origin`, room pixels) and where Link stood.

The debug warp takes the room's **own** coordinates; above 0x3ff they mean
"keep the position". World coordinates land Link anywhere.

### 3. Assembly

For each room, `room_capture.py`:

1. places each capture where the game's camera was — or where it best
   matches within `ALIGN` (8 px) of that, as the camera can trail the
   picture — and drops it if it is another room (an exit was stepped on)
   or matches the room's tile art less than `MATCH_MIN` (70%): a story
   scene took over, or an overlay (the Minish Woods fog) covers it. Where
   the dump has no art (magenta, `NO_ART`) or no tile layer draws (the
   backdrop: Cloud Tops' sky), the capture is taken as is;
2. fills what no view saw with the tile art;
3. takes out what **moves**: overlapping views are taken at different
   moments, so where two disagree, something animates (water, foam,
   swaying flowers) or wanders (a creature) — the tile art there;
4. takes out **passing shade**: the overworld's cloud shadows are a layer
   blended over the ground, `out = s·art + t` per channel, learned from the
   capture;
5. marks as **sprite** what still differs from both tile layers, less
   specks (animation the overlap test missed).

`capture.json` records each view: where Link stood, the camera, the match,
why any view was dropped, and the share of the room seen.

## What tilevox takes from a capture

Where a room has one:

- its faces wear the shown picture: an arch stands on the wall around its
  doorway, covering the plates the tiles draw;
- a doorway opens only where the game shows dark between its arch's posts;
  whether it opens onto the dark (an unlit space behind it, with steps
  only if drawn) is still the room's own tile art's call;
- object sprites (a shelf, a barrel) are built as furniture, their drawing
  from the picture.

## Known gaps

- A part of a room only one view sees (a waterfall at a room's edge) is
  not checked for movement; its animation can read as a sprite.
- Views are planned on walkable ground; a room with none (a pure backdrop)
  is not captured.
- A room whose dump drew some tiles from the wrong tileset (garbage where
  the capture shows the room) fails the match there; it needs harvesting
  again, not capturing.
- A dark room shows only the light around Link; an overlay layer (the
  Minish Woods fog, a canopy's shade) covers its views. Both fail the
  match and keep the tile art.
- The progress flags are the same for every room: a room that only exists
  in another story state (or only before the prologue ends) shows that
  state's version, and is dropped if it does not match its dump.

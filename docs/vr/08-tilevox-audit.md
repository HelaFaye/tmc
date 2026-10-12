# 08 — Audit of the tile stage (tilevox, tileid)

An audit of every rule `tools/tilevox.py` and `tools/tileid.py` use to turn the
game's drawing into solids. It asks whether the approach itself is right, and
proposes a better one. Written after a run of fixes in Hyrule Town, the castle
garden and Hyrule Field, where each fix was checked in one room and several of
them broke something else.

## What the owner pointed out (October 2026)

| Seen | What is wrong underneath |
|---|---|
| Roofs are not all one height. The blue house is a single floor, others are taller, and the stone wall left of it is taller still. | Height is chosen per *class* (wall 16, building `BUILDING_HB`), not measured per object. |
| A wood plank joins the level above to the blue house's roof. | Nothing builds a part that spans two objects. The plank is drawn over the roof and is lost in it. |
| Two objects in one tile must be told apart. | The unit of everything is the 16 px cell. A cell holding a roof edge and a plank, or a bush and a ledge, is one thing to every rule. |
| Steps are not sized, spaced or placed by their shading, and lose their own colours. The bottom step of most flights looks like a wall. | Steps are one solid scaled to the flight. The drawn bands are stretched to fit. The bottom band's lit rows are often 1–2 px, so the bottom step is built as a riser alone: a wall. |
| Door textures are misplaced. The blue house's door is yellow and tan, its frame brown. The frame starts where the colour changes, stands proud of the door by a few pixels, and has its own grain. | Doors are a recess of one material, cut by column. There is no notion of a door leaf and a frame. |
| The arch is doubled and too thin. The front shading gives its silhouette and the lit band its thickness. The gold sigil is the Triforce with wings. It rests on two large sloped stone blocks either side of the steps, with writing on their fronts. Its footprint is two squares. | The arch is a slab of fixed thickness (`ARCH_T = 6`). The pillars are raised cells, not sloped blocks. The sigil is part of the arch's pixels. "Doubled" comes from the depth model (see below). |
| Smeared textures; hedges wearing the path; guards in the texture. | Faces that the game never shows are filled by stretching or by guessing from the composited picture. |

## The diagnosis

### 1. The unit is wrong: cells, not objects or parts

tilevox decides everything per 16 px cell:
- the cell's class from collision;
- its family from the cell's pixels;
- a height per cell;
- one tile model per drawing.

The game's objects do not respect cells. A house, a gate, a hedge or a flight of steps spans many cells, and many cells hold parts of two things.

Each special case so far grew a pass that gathers cells back into an object: `find_buildings`, `find_uprights`, `hedge_cells`, `stair_levels`, `standing_arches`, `furniture_prisms`, `room_shell`. Each pass gathers its own way. None can split a cell between two objects, and each pass then hands its result back to the cell grid.

### 2. There are two depth models, and they disagree

The game's camera rule (doc 06) says a point at height `y` over ground `z` is drawn at row `z − y`. So a thing drawn at row `R`, standing `h` up, is at depth `R + h`.

tilevox instead puts every cell's geometry at its drawn row, `z = R`, and its height on top. That is right for flat ground and wrong for everything raised:
- a raised plateau stands `h` px too far north;
- its front face is built where the face is drawn, *and* its top wears the face's drawing too.

This one disagreement is behind:
- the doubled arch;
- the doubled ledge;
- the cliff tops (now patched by `under`/`TEX_SRC`);
- the tops of raised wall cells wearing their front;
- the extra raised-deck texture strip.

The hedge builder already uses the right rule internally: its top wears row `z − h`. That is why it matched the game when nothing else did.

Entities have the same problem. Link on the plateau is at drawn row `R`; in a world built at `R + h` he must stand at `R + h` too. The entity stage has to use the same model.

### 3. Hidden faces are guessed from the composite

Faces the game shows are textured by projecting the captured picture. Faces it never shows (backs, flanks, the ground under a moved object) are filled by whatever is cheapest: stretching a row, mirroring, taking the nearest pixel. Some of those guesses read wrong:
- the smears on built faces;
- the stone path on the hedges;
- the guards in the texture;
- the arch drawn on the ground under it.

A part that knows what it is (a door leaf, a stone block, a hedge) could give its hidden faces its own material, as the cohesive-backs change did for props.

### 4. 105 tuning constants, tuned one room at a time

tilevox has 105 module-level constants and tileid 27. Most were tuned on the room where a problem was seen. Recent regressions came from exactly that:
- **Cliff raise:** raising "the cliff between two levels" caught the bushes beside the gate stairs, which stood up as 30 px pillars.
- **Arch test:** the first arch test matched tree canopies, roofs and a garden hedge in 29 places across just four rooms.
- **Texture substitution:** texturing a cell from the cell its model came from put bush leaves in the blue house's doorway.

Each was caught only because the owner or a render happened to look there. There is no fixed set of scenes rendered before and after every change.

### 5. The stage does not use what the game already knows

The dump and Picori already say a lot:
- the collision type and tile type (`act`), with names such as stairs, ledge and door;
- the layer and priority of every tile;
- the entity list with kinds and positions (now used to drop actors);
- each room's tileset and metatile indices;
- which object sprites the game draws where (the captures).

The stage reads some of this. Much of it is used only as a hint before the pixels decide. For example, a door is found from collision and act, and is then shaped by pixel columns that ignore the door's own drawing.

## Rule by rule

What each rule does, then a verdict:
- **Keep:** sound as it is.
- **Revise:** the idea is right, the method is not.
- **Replace:** the wrong approach.

### Identify (cells)

| Rule | Verdict | Why |
|---|---|---|
| `room_roles`, `despeckle_walls` | Keep | Collision-led cell roles are a sound first label. |
| `terrace_levels`, `undrawn_void`, `sprite_floor` | Keep | Narrow and correct. |
| `interior_walls` | Revise | Builds walls from drawn bands. These should become parts (see "a better method"). |
| `tileid.families` | Revise | Pixel families per cell. They should label *regions* of pixels, not cells, so a cell can hold two things. |
| `GREEN_SHARE`, `HEDGE_*`, `PETALS` and other colour thresholds | Revise | Absolute colour thresholds tuned on a few palettes. Make them relative to the room's palette and the region's outline. |

### Heights

| Rule | Verdict | Why |
|---|---|---|
| Class heights (wall 16) | Replace | A height should come from the object's drawn front band, by the 45° rule: the town ledge is drawn 29 px tall, the hedge 12. The class height is a last resort. |
| `stair_levels` | Revise | Raising whole landings by flood fill is sound. These are patches: shared rise per level pair, the cliff raise, lowest flight first. Levels should be solved once, with every flight, ledge and building measuring the same steps. |
| `cliff_bands` | Keep | Reads the face from the drawing. Should feed the level solver. |

### Stairs

| Rule | Verdict | Why |
|---|---|---|
| `find_stairs` bands (lit/shaded rows) | Keep | The right cue, as the owner said: lit = tread, shaded = riser. |
| `stair_quads` | Replace | One flight is built and its drawing stretched over it. Instead, each step should be its own part: its tread the lit band, its riser the shaded band, at their drawn sizes, wearing their own pixels. The bottom step's tread is often 1–2 px drawn because the ground hides it; it should take the step's usual depth instead of collapsing to a wall. |
| Steps as deep as the flight's cells | Revise | Steps should stand where the 45° rule puts them. A flight cut into a wall can sit in its notch; a flight in the open stands in front of the face. |

### Doors

| Rule | Verdict | Why |
|---|---|---|
| `find_doors` | Keep | Collision/act-led. |
| `door_quads`, `doorway_recesses` | Replace | A door is a leaf (its colours) inside a frame (the colour change around it). The frame stands a few px proud, with its own grain. Build both as parts from the colour regions. |

### Buildings

| Rule | Verdict | Why |
|---|---|---|
| `find_buildings` anchored on doors | Revise | Anchoring is good. One height from `BUILDING_HB` and one style (`dome`/gable) per house is not. |
| `building_quads` | Replace | A house is parts: roof (its slopes, from the shading of each face), walls, door, frame, windows, chimney, and planks or stairs to other levels. Each part gets its own measured size. The roof's height comes from its drawn eaves and ridge, so a single-floor house and a taller one come out different. |

### Overhead layer

| Rule | Verdict | Why |
|---|---|---|
| `room_overlay` decks | Revise | Flat plates are right for canopies Link walks under. They are wrong for anything standing. |
| `standing_arches` | Revise | Right that the gate's arch stands upright. These are wrong: the fixed thickness, the sigil folded into the arch, the plain pillars. Rebuild the gate from parts: two sloped stone blocks (the footprint the owner describes, writing on their fronts), the arch (silhouette from its front shading, thickness from its lit band), and the winged Triforce as its own part. |
| Deck texture strip | Keep, as a stopgap | Removed once the depth model is right. |

### Props, foliage, hedges

| Rule | Verdict | Why |
|---|---|---|
| `stand_quads`, `mound_quads`, cohesive backs | Keep | Already part-like: fitted to the drawing by the camera rule, with hidden faces wearing the thing's own surface. |
| `off_ground` | Keep | |
| `hedge_cells` | Keep | It uses the right depth model, and matches the game from its own camera. The two-tone test should become a region test (outline-bounded). |
| `foliage_hmaps` | Revise | Tree canopies as heightmaps over cells. Make them parts (crowns), so a trunk and a crown in one cell separate. |
| `drawn_upright`, `find_uprights` (fences, signs, posts) | Keep | |

### Indoors

| Rule | Verdict | Why |
|---|---|---|
| `room_shell`, `box_walls`, `fit_wall_foot`, `dewarp_outer` | Keep | Enclosed rooms are a solved shape: a box. |
| `furniture_heights`, `furniture_prisms` | Revise | Per-cell pieces merge separate things (the stools into Link's table). Should be per region. |

### Texturing

| Rule | Verdict | Why |
|---|---|---|
| Projecting the captured picture | Keep for shown faces | |
| Stretching or mirroring for unseen faces | Replace | A part's unseen faces wear its own material. |
| `under`, `TEX_SRC`, `hide_under_arches` | Remove | Patches for the depth model. |
| Dropping actor sprites (`ACTOR_KINDS`) | Keep | |

### Checking

| Rule | Verdict | Why |
|---|---|---|
| `capture_check.py` (game camera vs the capture) | Keep, extend | It can only judge what the game shows. Add turned-view checks: nothing below ground, symmetric things symmetric, no floating plates. Add a fixed set of reference scenes rendered before and after every change: the town gate, blue house, west ledge, castle garden, Link's house inside and out, a field, a dungeon room. |

## A better method

The owner's suggestion is the right shape: break objects into their parts by shape and shading, then build each part from a small set of primitives.

### Step 1: one depth model

Everything stands where the 45° rule puts it: a thing drawn at row `R` and `h` px tall is at depth `R + h`.
- **Terrain:** raised levels move south by their height, and their front faces stand at the drawn foot.
- **Entities:** they use the same mapping (the stage that places Link and enemies must add the ground height under them).
- **Collision:** the game's checks stay in drawn coordinates. Only the 3D view uses world depth.

From the game's camera the build then matches the picture by construction, so the doubling disappears. This comes first because every other rule depends on it.

### Step 2: regions, not cells

Split each room's picture into regions: connected pixels bounded by the outline colour or by a colour-family change. Then label each region:
- its **shading class**: lit, mid or shaded (lit = facing up, shaded = facing the camera);
- its **colour family**: stone, wood, leaf, roof tile, metal, gold;
- its **layer**;
- the **collision** under it;
- any **entity** standing there.

A cell can hold several regions, so a cell holding a roof edge and a plank holds two.

### Step 3: parts from regions

Group adjacent regions into parts by rules about shading and colour:
- a lit region directly above a shaded one, sharing an edge, is the top and front of one box. The shaded height is the box's height and the lit depth its depth;
- a door leaf is a region of one colour family enclosed by a frame region;
- a frame is a region surrounding a leaf, built a few px proud;
- a roof face is a shading-uniform region above a wall; its slope comes from how its shading changes and how far it overhangs;
- a chimney is a small box region rising out of a roof;
- a window is a small dark region in a wall, built as an inset;
- a plank is a narrow wood region spanning two levels;
- a step is a lit band over a shaded band.

Each part is fitted as one primitive: box, sloped block, slab, arch, plank, dome, cylinder, gable or hip roof, or step. Its dimensions are measured from its own regions, never from a class default.

### Step 4: objects and kits

Group parts into objects: house, gate, flight, hedge, bush, table, chair.

The game repeats objects much more than it repeats tiles. Across 562 rooms there are 250,878 cells. Single drawings repeat poorly: 34,315 unique, with the most common 1,000 covering only 52% of cells. Whole objects (the same house front, the same gate, the same table) recur far more.

So an object recognised once (by its arrangement of metatiles and colours) becomes a *kit*: its parts and sizes, stored as data. That data is coordinates and indices only, no art. A kit is reviewed once on the review page and then applied everywhere the object appears. The owner's corrections become data in the kit instead of new rules.

### Step 5: check every change against fixed scenes

Before any commit, render a fixed set of reference scenes and set the result beside the previous build:
- from the game's camera (must match the capture);
- from two turned views (no doubling, no floating, symmetric where drawn symmetric).

The review page already lets the owner flag wrong objects. Those flags should become the regression list.

## What changes first

1. **Depth model** (Step 1), with the entity stage told the same rule. This removes the doubling everywhere, and retires `under`, `TEX_SRC`, the deck strip and the cliff-top patch.
2. **Regions and shading classes** (Step 2), shown on the review page as an overlay, so the owner can check the split before anything is built from it.
3. **Parts for the three objects the owner named:**
   - steps (each step its own drawn tread and riser, colours kept, a bottom step that is a step);
   - doors (leaf, proud frame, grain);
   - the town gate (two sloped stone blocks with writing, an arch with its drawn silhouette and thickness, the winged Triforce on top).
4. **Houses as parts:** roof height and slopes measured per house, chimneys, windows, and the plank to the upper level.
5. **Kits,** starting with the most repeated objects. The cell rules then remain only as the fallback for what no kit covers.
6. **Reference scenes** in `tools/`, run before every commit.

## On the work so far

Several decisions were reasonable at the time and should be undone now:
- **Per-cell tile models:** chosen because they share well across an area, but they make two-objects-per-cell impossible.
- **Class heights:** chosen because collision gives them for free, but they ignore what the drawing says.
- **Fixes in place:** each one cured a symptom where it was seen, so the depth error got patched in four places instead of fixed once.

The parts of the stage that held up are the ones that obey the camera rule directly and fit a whole object to its drawing:
- the hedge boxes;
- the mound and stand props;
- the room shell;
- the drawn-upright fences.

That is the evidence for the method above.

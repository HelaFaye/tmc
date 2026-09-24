# Part III — Stabilized camera and dual-scale anchoring

Companion to `tmc-vr-voxel-blueprint.md` (where to hook) and
`tmc-vr-part2-implementation.md` (what to build). This specifies the camera
system that serves both the tabletop diorama and the 1:1 human scale from one
implementation.

---

## 1. The invariant

Everything below follows from one rule:

> **The VR camera never moves as a consequence of gameplay.**
>
> It moves only when (a) the player physically moves their head, (b) the player
> deliberately requests a reposition, or (c) a room transition occurs, covered by
> a fade.

Scale becomes a parameter rather than an architecture. Diorama and life scale are
the same system with different numbers, and the player can switch between them at
runtime.

This also inverts the usual comfort problem. Ordinarily you fight the game for
control of the camera. Here the game keeps its camera — `gRoomControls.scroll_x/y`
runs untouched, exactly as Part I requires, because scripts busy-wait on it — and
the VR camera simply ignores it.

---

## 2. What is already immune, and why

A pleasant discovery from reading `src/scroll.c`: rendering from `gMapTop`/
`gMapBottom` in world space excludes most of the engine's camera violence **by
construction**, with no suppression code at all.

`UpdateScreenShake()` (`src/scroll.c:892`) writes to exactly four places:

```c
gMapBottom.bgSettings->xOffset = screenShakeOffset[0] + roomOffsetX;
gMapBottom.bgSettings->yOffset = screenShakeOffset[1] + roomOffsetY;
gMapTop.bgSettings->xOffset    = ...;
gMapTop.bgSettings->yOffset    = ...;
gRoomControls.aff_x = screenShakeOffset[0];
gRoomControls.aff_y = screenShakeOffset[1];
```

It never touches `scroll_x` or `scroll_y`. Shake is a **BG scroll-register
offset**, i.e. a purely screen-space effect applied at PPU composite time. If you
never read `bgSettings->xOffset/yOffset` — and the world-space renderer has no
reason to — shake cannot reach the camera.

The same holds for the affine effects. `aff_x`/`aff_y` and the per-scanline affine
latches in `port/ppu/src/mode1.c` drive the Deepwood rolling-barrel rotation and
similar tricks. World-space rendering never consults them.

| Source | Reaches camera? | Why |
|---|---|---|
| `InitScreenShake` / `UpdateScreenShake` | **No** | writes `bgSettings->xOffset/yOffset`, `aff_x/aff_y` only |
| BG affine (barrel, warps) | **No** | per-scanline PPU state, not world state |
| `gRoomControls.oam_offset_x/y` | **No** | screen-space OAM nudge |
| `Scroll0`–`Scroll5` follow camera | **Only if you read `scroll_x/y`** | so don't — see §4 |
| `camera_target` retargeting (cutscenes) | **Only if you read `scroll_x/y`** | same |
| Room transition (`DoExitTransition`) | **Yes, legitimately** | the world itself is replaced — §6 |

So the suppression list is short: **do not read `scroll_x`, `scroll_y`,
`bgSettings->xOffset/yOffset`, `aff_x/aff_y`, or `oam_offset_x/y` anywhere in the
VR render path.** Enforce it with a code review rule, or by not exposing them in
the `vr_world` snapshot struct in the first place.

Note the useful corollary: `scroll_x/y` remains a valid *streaming hint*. It tells
you which part of the world the engine guarantees is loaded and which entities are
being updated. Read it for residency decisions; never for a view matrix.

---

## 3. The anchor model

```c
/* port/vr/vr_anchor.h */

typedef enum {
    VR_FOLLOW_STATIC,   /* anchor fixed for the room             — diorama default */
    VR_FOLLOW_BLINK,    /* fade-snap when Link exits a dead zone — life-scale default */
    VR_FOLLOW_ATTACHED, /* continuous follow                     — opt-in, comfort risk */
} VrFollowMode;

typedef struct {
    float        worldOrigin[3]; /* game world-space point mapped to play-space origin */
    float        metresPerPixel; /* the scale parameter */
    float        yaw;            /* world rotation about play-space Y; player-set only */
    float        deadZonePx;     /* BLINK: radius in world pixels before a resnap */
    VrFollowMode follow;
} VrAnchor;
```

The render transform is

```
worldToPlaySpace = translate(-worldOrigin) · rotateY(yaw) · scale(metresPerPixel)
view[i]          = eyePose[i]⁻¹ · worldToPlaySpace
```

`eyePose[i]` comes from `xrLocateViews` and is the only per-frame-varying term.
`VrAnchor` changes on discrete events only. That is the whole stabilization
mechanism — it is small, and its smallness is the point.

Filling this into the `VrViewSet` from Part II §2.2 means the flat-3D build
(`count = 1`) and the VR build (`count = 2`) share the anchor system unchanged,
so you can tune and test stabilization on a monitor in Phase 2, months before a
headset is involved.

---

## 4. Follow policies

### 4.1 `VR_FOLLOW_STATIC` — diorama

Anchor at the room's centroid, recomputed only on room change:

```c
anchor.worldOrigin[0] = gRoomControls.origin_x + gRoomControls.width  * 0.5f;
anchor.worldOrigin[2] = gRoomControls.origin_y + gRoomControls.height * 0.5f;
anchor.worldOrigin[1] = 0.0f;
```

`origin_x`/`origin_y` are stable for the lifetime of a room; `scroll_x`/`scroll_y`
are not. Using origin means the diorama is a fixed model on a fixed table and Link
walks around inside it. Nothing slides under the player, ever.

There is a nice accident here worth knowing: `Scroll1` clamps scroll to
`[origin_x, origin_x + width - 0xF0]` and `[origin_y, origin_y + height - 160]`.
For any room at or below 240×160 px the engine camera **never moves at all**, so
a single-screen room is already perfectly stable even under naive scroll-following.
The dungeon rooms and interiors this covers are a large fraction of the game.

### 4.2 `VR_FOLLOW_BLINK` — life scale

At 0.10 m/px a maximum-size room is over 100 m across. The player cannot see Link
from the far corner, and continuous following would be the classic vection
failure. The answer is a **blink**: when Link leaves a dead-zone radius, fade out
over ~80 ms, move the anchor so he is recentred, fade in over ~80 ms.

```c
float dx = linkX - anchor.worldOrigin[0];
float dz = linkY - anchor.worldOrigin[2];
if (dx*dx + dz*dz > anchor.deadZonePx * anchor.deadZonePx) {
    VrFade_Begin(&anchor, linkX, linkY);   /* fade → snap → fade */
}
```

Blink teleportation is the most comfort-safe transition known, precisely because
it removes optic flow rather than smoothing it. It also reads as intentional
rather than broken, which matters: players forgive a cut and resent a drift.

Recommended `deadZonePx` is roughly 0.4 × the play-space comfortable viewing
radius in pixels — tune, but start around 120 px at life scale, which is half a
GBA screen. Add hysteresis so a player oscillating on the boundary doesn't blink
repeatedly.

### 4.3 `VR_FOLLOW_ATTACHED` — offer it, default it off

Continuous follow. Some players genuinely prefer it and have no sensitivity.
Ship it behind a setting with an honest label, never as the default, and never as
the first thing a new player experiences.

---

## 5. Scale presets

| Preset | m/px | Link height | GBA screen | Max room (64×64 metatiles) | Feel |
|---|---|---|---|---|---|
| **Tabletop** | 0.005 | 8 cm | 1.2 × 0.8 m | 5.1 × 5.1 m | a model on a desk; you lean in |
| **Diorama** | 0.010 | 16 cm | 2.4 × 1.6 m | 10.2 × 10.2 m | a model on the floor; you walk around it |
| **Life** | 0.100 | 1.6 m | 24 × 16 m | 102 × 102 m | you are standing in Hyrule |

Tabletop is the one I'd ship as the default. A 1.2 × 0.8 m screen sits entirely
within reach from a seated position, which means a whole GBA screen is visible
without moving at all — the strongest possible version of a stabilized camera. The
0.010 preset is better for the big outdoor areas where you want to walk the
perimeter.

**One calibration warning.** `entity->z` is the game's height axis, but TMC's z
units are not necessarily visually 1:1 with x/y — the engine folds z into screen Y
at a 1:1 ratio (`port_draw.c:735`) for *drawing*, which is a 2D convention, not a
statement about physical proportion. A 16 px hop at 0.10 m/px would be a 1.6 m
vertical leap. Introduce a separate `zScale` multiplier, start it at 1.0, and tune
it by eye against Link's jump and the Roc's Cape. Do this in Phase 2, on a
monitor.

---

## 6. Room transitions — the one legitimate anchor move

`LoadRoomTileSet` (`src/playerUtils.c:4046`) clears and reloads both map layers
wholesale. Adjacent rooms are not resident. So the world genuinely is replaced,
and the anchor genuinely must move.

Use the engine's own cover. TMC already fades for transitions
(`DoExitTransition`, `SetRoomTransitionTypeFor*`, `UpdateDoorTransition`), and
`gFadeControl` drives it. Hook the fade, move the anchor at the black point, and
the transition is invisible and comfortable for free.

For same-area scrolling transitions (`gRoomControls.unk_18` tracks progress), the
engine slides rather than fades. **Do not slide the anchor to match.** Blink
instead, or hold the old anchor until the new room's data lands and then blink.
A slide here is the single worst thing you could do — a whole world translating
past a stationary head is maximal vection.

---

## 7. Cutscene cameras

`gRoomControls.camera_target` is an `Entity*`, and cutscenes retarget it to show
the player something — a door opening, a boss entering. The 2D game moves the
camera because it has no other way to direct attention.

In VR you cannot move the player's head, and you shouldn't try. Replace camera
direction with **attention direction**:

- A soft vignette or desaturation everywhere except the subject.
- A diegetic light or particle cue at the subject.
- For the diorama scales, nothing at all is often fine — the whole room is already
  in view, so the player simply sees it happen.
- Only at life scale, where the subject may be genuinely off-view, consider a
  blink to a framing position, then blink back. Never a pan.

Since `camera_target` still drives `Scroll1` for the engine's own purposes, you
get the subject's identity for free: read `camera_target` to know *what* the
cutscene wants to show, then present it your own way.

---

## 8. Re-expressing the effects you excluded

Screen shake, knockback flash and the barrel rotation carried real information.
Dropping them silently loses feedback; re-routing them keeps it.

| Original | VR replacement |
|---|---|
| `InitScreenShake(time, magnitude)` | Controller haptic pulse scaled by `magnitude`; optional sub-degree jitter on **world objects**, never on the camera |
| Deepwood rolling barrel (BG affine) | An actual rotating voxel object. The player's frame stays fixed while the barrel turns — which is both more correct and more comfortable than the original |
| Link damage flash (palette) | Keep as-is; palette effects are already world-space per-object |
| Water ripple (per-scanline `BGxVOFS`) | Vertex-displaced water surface |
| `oam_offset_x/y` nudges | Ignore |

The haptics substitution is worth doing properly. `InitScreenShake` is called from
`src/player.c:659`, `src/player.c:3103`, `src/roomInit.c:1032` and
`src/script.c:1542`, so there are four call sites and a clean signature to
intercept.

---

## 9. Comfort details that are easy to get wrong

**The HUD should not be fully head-locked.** Part I suggested a head-locked ortho
quad, which is right for *legibility* and wrong for comfort — a rigidly
head-locked panel is a fixed retinal stimulus that fights the vestibular system.
Use a **delayed-follow / body-locked** panel: it lags head rotation by ~150 ms and
recentres only past a threshold. Or, better at tabletop scale, put the hearts and
inventory on a physical-feeling panel beside the diorama, where the player can
glance at them.

**Give the player a rest frame.** A static reference the eyes can trust: at
tabletop and diorama scales, render the plinth or table the room sits on. At life
scale there is no obvious one — this is another reason life scale is the harder
mode and shouldn't be the default.

**Yaw is player-controlled only.** Nothing in TMC rotates the world, so
`anchor.yaw` should only ever change via an explicit snap-turn input. If you add
smooth turning, gate it behind a setting alongside `VR_FOLLOW_ATTACHED`.

**Interpolation is not optional here.** Part II flags it for Phase 4; at these
scales it matters more. At tabletop scale a 60 Hz simulation shown at 90 Hz makes
Link visibly stutter across a surface the player is staring at from 40 cm. The
decoupled present path in `port/port_bios.c:742` already re-presents identical
frames — its own comment says "identical until interpolation lands."

---

## 10. Runtime switching

Because scale is a single float and the anchor is a single struct, switching
presets is cheap — but the **mesh caches are not scale-invariant** if you bake
scale into vertices. Don't. Keep all meshes in world-pixel units and apply
`metresPerPixel` in the model matrix. Then switching Tabletop → Life is one
uniform update and a blink, with zero remeshing.

Suggested UX: switching scale is itself a blink, with the anchor recomputed for
the new preset's follow mode (`STATIC` for tabletop/diorama, `BLINK` for life).

---

## 11. Validation checklist

Run these before calling stabilization done. All are testable in Phase 2 on a
monitor with `count = 1`, except the last two.

1. Trigger `InitScreenShake(60, 7)` from the debug console. The camera must not
   move by a single pixel.
2. Walk Link to every corner of a 64×64 room at each preset. `STATIC` must not
   move the anchor; `BLINK` must blink and never drift.
3. Enter the Deepwood barrel. The world frame must stay level while the barrel
   turns.
4. Take a room-scroll transition. Confirm a blink, not a slide.
5. Trigger a cutscene that retargets `camera_target`. Confirm the anchor holds.
6. Switch scale presets mid-room. Confirm no remesh, no hitch.
7. *(VR)* Have a motion-sensitive tester play 20 minutes at Tabletop with
   `STATIC`. This is the shipping bar.
8. *(VR)* The same tester at Life with `BLINK`. If this fails, life scale ships
   behind a warning, not as an equal option.

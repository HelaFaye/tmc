# Part II — Implementation plan
## PC → Quest, 3D → VR, voxelized character models

Companion to `tmc-vr-voxel-blueprint.md`. That document establishes *where* to
hook. This one specifies *what to build*, in what order, given the four
decisions.

---

## 0. What the four choices lock in

| Decision | Consequence |
|---|---|
| **PC first, Quest second** | One Vulkan path targeting **Vulkan 1.1 core**, not 1.3. Quest 2 caps at 1.1. Every desktop-only convenience you reach for in Phase 2 is a Quest rewrite later. |
| **No ray tracing** (implied) | Drop `RayTracingPipeline.cpp`, `DenoisePipeline.cpp` and `shaders/*.rgen|.rchit|.rmiss` from the vk_rt_experiment salvage. Keep `Engine` and `RenderLayerManager`. This also un-blocks Apple Silicon later, should you want it. |
| **3D first, VR second** | The camera must be an **array from day one**. Flat 3D runs it with `count = 1`; VR sets `count = 2`. Get this wrong and Phase 4 is a rewrite instead of a substitution. |
| **Voxelized models** | This is now the **critical path**, not terrain. It is also the highest-uncertainty item in the project. Phase 0 exists to de-risk it before anything else is built. |

---

## 1. Character voxelization — the technique

### 1.1 What the ROM actually gives you

Per animation frame, the engine holds an **OBJ piece list**. `RenderSpritePieces`
(`port/port_draw.c:347`) decodes it: a count byte, then N five-byte pieces:

```c
s8  xoff;        /* data[0] — signed offset from the frame origin */
s8  yoff;        /* data[1] */
u8  shapeInfo;   /* data[2] — 0xC0 = OBJ shape, 0x3C = size + flip, bit0 = palette-clear */
u8  tileLow;     /* data[3] */
u8  tileHigh;    /* data[4] */
```

Shape and size resolve through `sSizeTable[240]`, a 240-byte table read straight
out of ROM at an overlay offset (`Port_LoadOverlayData`, `port_draw.c:79`), where
each 4-byte entry is `{anchorX, anchorY, width, height}`.

Tile pixels come from the extracted asset blobs — `port/port_asset_index.c` is a
generated index of **9,968 entries**, raw `.bin`, 4bpp GBA layout (each byte is
two pixels, **low nibble = left**). The vk_rt_experiment's `main.cpp` already
contains a working 4bpp tilesheet decoder with that exact comment, so you are not
writing it from scratch.

So: **any `(spriteIndex, frameIndex)` pair can be rasterized offline to an RGBA
bitmap with a known origin.** That bitmap is the atom of everything below.

### 1.2 The directional structure you exploit

`include/entity.h`:

```c
typedef enum {
    IdleNorth = 0x0,
    IdleEast  = 0x2,
    IdleSouth = 0x4,
    IdleWest  = 0x6,
} AnimationState;
```

Four directions, distinct animations. But West is very probably East mirrored —
per-frame horizontal flip lives in `Frame.spriteSettings.b.hFlip` (`include/sprite.h`),
and `ResolveEntitySpriteParams` combines it with the entity's own flip:

```c
u8 flipBits = (frameSS ^ ssRaw) & 0xC0;   /* port_draw.c:771 */
```

That XOR only makes sense if the art relies on mirroring. **Verify empirically in
Phase 0** — dump the four direction frames of Link's walk cycle and compare
East against mirrored West pixel-for-pixel. If it holds, you have **three genuine
orthogonal views per pose**: South (front), North (back), East (side).

### 1.3 Visual hull carving

Three orthogonal silhouettes is exactly the input for a classic visual hull. For
a pose at frame index *k*:

```
Let S(x, y) = front silhouette   (from IdleSouth  frame k)
Let E(z, y) = side  silhouette   (from IdleEast   frame k)
Let N(x, y) = back  silhouette   (from IdleNorth  frame k)

occupied(x, y, z)  ⟺  (S(x,y) ∨ N(W-1-x, y))  ∧  E(z, y)
```

Colour assignment, in priority order:

1. Voxel on the −Z face (front-most at that `(x,y)`) → sample `S(x,y)`.
2. Voxel on the +Z face → sample `N(W-1-x, y)`.
3. Voxel on the ±X face → sample `E(z,y)`, mirrored for the −X side.
4. Interior → nearest exposed neighbour, or a darkened average.

This produces a genuinely three-dimensional Link — not a slab — from art the ROM
already contains, automatically, at zero authoring cost.

### 1.4 The four ways this breaks, and what to do

**Frame-index correspondence.** The hull assumes walk-frame *k* in South is the
same pose as walk-frame *k* in East. Nothing in the data guarantees it. TMC's
art is consistent enough that it will hold for most cycles, but it will fail
somewhere. **Detect it**: compare silhouette heights and areas across the three
views per frame; flag frames where they diverge beyond a threshold. Failed frames
fall back to **single-view extrusion** (front silhouette, fixed depth) — the
PotatoVoxel approach for props. A slightly flat Link for three frames of an
obscure animation is an acceptable loss.

**Vertical registration.** The three views must be aligned in Y or the hull
shears. Use the **piece-list bounding box top** as the datum, computed from
`yoff - sizeTab[sizeIdx + 1]` across all pieces in the frame. Do not use the
sprite's nominal 16×16 box; multi-piece frames overflow it constantly.

**Phantom volume.** A visual hull is always an over-estimate. Link holding a sword
out to the east: the front view shows the blade at `(x, y)`, the side view shows
his *body* at that `y`, and their intersection fills the space between blade and
body with solid voxels. This is the single most visible artifact of the technique.
Mitigations, in order of cost:

- Per-sprite authored **carve masks** — the direct analogue of PotatoVoxel's
  `data/voxel_heights.lua` pins, and you should expect to need a comparable
  volume of them for major characters.
- Connected-component analysis in the hull: discard components not connected to
  the torso seed.
- Accept it for minor enemies. A Chuchu has no protruding limbs.

**Semi-transparent / blended sprites.** `entity->spriteRendering.alphaBlend` and
the GBA's OBJ blend modes drive effects (Ezlo's glow, the Gust Jar cone) that have
no silhouette to carve. Route these to billboards or particle systems, not hulls.
Detect via `spriteRendering.alphaBlend != 0` and `mode1_oam_mode()`.

### 1.5 Scale, and why this must be fully automatic

Measured from `data/gfx/sprite_frames.s` (USA):

- ~138 sprite sheet labels; 38 carry address annotations.
- Address deltas across those 38 give **6,520 `SpriteFrame` entries** in that span
  alone, largest single sheet **1,194 frames**, Link (`gSpriteFrames_1`) **253 frames**.
- Separately, ~**7,951** `gSpriteAnimations_*` labels across
  `data/animations/{enemy,npc,object,projectile}/`, plus ~530 Link animations.

Order of magnitude: **5,000–10,000 distinct frames.** Hand-authoring is out of the
question. The pipeline is automatic; authored input is exceptions only.

### 1.6 Budget and residency

At one voxel per pixel, a 16×16 sprite hull is at most 16³ = 4,096 cells,
greedy-meshing to roughly 100–400 quads. Ten thousand frames resident is not
happening.

**Exploit the fact that the engine already solves this.** TMC DMAs only the frames
it needs into OBJ VRAM. `SpriteFrame{numTiles, unk_1, firstTileIndex}` plus
`entity->spriteVramOffset` tell you precisely which frames are live. Build hulls
for resident frames only, cache keyed on `(spriteIndex, frameIndex)`, evict on
room change alongside the terrain mesh cache.

Expect a warm-up cost on first encounter with each enemy type. PotatoVoxel ships
a user-facing **PREBUILD CACHE** step for exactly this reason, and its 1.6–1.9
changelogs are almost entirely cache engineering (`MeshCache`, `CacheIdentity`,
`GeometryStream`, `BuildBudget`, `WorkerPool`, `geometry_worker.lua`). Plan the
same shape from the start: coroutine-or-thread sliced builds against a per-frame
millisecond budget, never a synchronous build on the render thread.

---

## 2. Renderer architecture — one path, two targets

### 2.1 Non-negotiables from the Quest requirement

| Constraint | Why | Cost of ignoring |
|---|---|---|
| **Vulkan 1.1 core** | Quest 2 (Adreno 650) caps there. Quest 3 (Adreno 740) does 1.3 but you want one binary shape. | Phase 5 rewrite |
| **`VK_KHR_multiview`** | One draw call, two layers. Effectively mandatory on mobile tiled GPUs; a free win on PC. | ~2× draw cost, blown frame budget |
| **Zero GPU readback** | Tiled deferred renderers stall hard on `vkCmdCopyImageToBuffer`. | Frame time cliff |
| **Greedy meshing** | Quest 3 tolerates roughly 1M tris/frame at 90 Hz with multiview; Quest 2 about half. Naive per-voxel quads blow this in one room. | Unshippable |
| **Single-cascade shadows** | PotatoVoxel's `ShadowMap.lua` is 1,068 lines of multi-cascade sun rig. Port the idea, not the scope. | Fill-rate death |

Corollary: **the shm framebuffer publisher must be off in the VR path.** It exists
to feed an external consumer and costs a full extra BG/OBJ re-render pass
(`port_ppu.cpp:1387`). Useful in Phase 0 only.

### 2.2 The camera abstraction that makes Phase 4 a substitution

Write this in Phase 2, before any rendering exists:

```c
/* port/vr/vr_camera.h */
#define VR_MAX_VIEWS 2

typedef struct {
    float view[16];         /* world -> eye */
    float proj[16];         /* eye -> clip; asymmetric frustum in VR */
    float position[3];      /* world-space eye origin, for LOD + billboard facing */
} VrView;

typedef struct {
    VrView   views[VR_MAX_VIEWS];
    uint32_t count;         /* 1 = flat 3D, 2 = stereo */
    uint32_t multiviewMask; /* 0b01 flat, 0b11 stereo */
} VrViewSet;
```

Every render pass takes a `const VrViewSet*` and nothing else. In Phase 2 a
free-fly or over-the-shoulder camera fills `views[0]` and sets `count = 1`. In
Phase 4 `xrLocateViews` fills both and sets `count = 2`. The render graph,
culling, shaders and pipeline layouts do not change — the multiview render pass
is created with `viewMask = multiviewMask` and the vertex shader reads
`gl_ViewIndex` to select its matrix. That is the whole delta.

Two rules that make it work:

- **Never compute a single "the camera" position.** Billboard facing, LOD
  selection and frustum culling all take the view set. In stereo, cull against
  the union frustum, not either eye.
- **Never write a fullscreen post pass that assumes one image.** Any post
  effect is per-layer or it is deleted.

### 2.3 What to salvage, what to discard

From `port/vk_rt_experiment/`:

| File | Action |
|---|---|
| `Engine.{h,cpp}` | **Keep.** Vulkan instance/device/swapchain. Strip swapchain creation into a seam — OpenXR allocates swapchain images in Phase 4, you don't. |
| `RenderLayerManager.{h,cpp}` | **Keep** the batching and the persistent-staging pattern (`kMaxQuads`, mapped staging, one memcpy + one `vkCmdCopyBuffer`). Discard the acceleration-structure buffer usage flags and the 5-layer Z table. |
| `FrameSource.{h,cpp}` | **Phase 0 only.** Delete after. |
| `RayTracingPipeline`, `DenoisePipeline`, `shaders/*.rgen|.rchit|.rahit|.rmiss|.comp` | **Discard.** |

Its README is candid that none of it has been executed on hardware, and that
BLAS/TLAS/SBT setup needs Renderdoc and validation-layer iteration. By dropping
ray tracing you drop precisely the parts that warning applies to.

---

## 3. Quest-specific delta (Phase 5)

Picori's Android build already exists and works — `xmake.lua:526` switches to
`set_kind("shared")`, `set_basename("main")`, per-ABI `build/android/arm64-v8a`,
with the 16 KB page-alignment flag Android 15 requires. RetroAchievements is
already auto-disabled there (`xmake.lua:216`, no libcurl in the NDK). Gradle
consumes the `.so` as jniLibs.

What changes:

**`android/app/build.gradle`** — `minSdk 21` → **29** (Quest 2 is Android 10).
Keep `targetSdk 34`. Add the loader:

```gradle
dependencies {
    implementation 'org.khronos.openxr:openxr_loader_for_android:1.1.43'
}
```

**`android/app/src/main/AndroidManifest.xml`** — currently declares
`<uses-feature android:glEsVersion="0x00020000" />` and
`android:screenOrientation="sensorLandscape"`. Both are wrong for a headset.
Replace with:

```xml
<uses-feature android:name="android.hardware.vr.headtracking"
              android:required="true" android:version="1" />
<uses-permission android:name="com.oculus.permission.USE_ANCHOR_API" />
<!-- on the activity: -->
<intent-filter>
    <action android:name="android.intent.action.MAIN" />
    <category android:name="com.oculus.intent.category.VR" />
    <category android:name="android.intent.category.LAUNCHER" />
</intent-filter>
```

Drop `screenOrientation` entirely.

**Loader bootstrap.** On Android the OpenXR loader needs initializing before
`xrCreateInstance`, with the JavaVM and Activity:

```c
PFN_xrInitializeLoaderKHR xrInitializeLoaderKHR = NULL;
xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
                      (PFN_xrVoidFunction*)&xrInitializeLoaderKHR);
XrLoaderInitInfoAndroidKHR init = {
    .type = XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR,
    .applicationVM      = vm,        /* from SDL_GetAndroidJNIEnv / JNI_OnLoad */
    .applicationActivity = activity, /* SDL_GetAndroidActivity() */
};
xrInitializeLoaderKHR((const XrLoaderInitInfoBaseHeaderKHR*)&init);
```

SDL3 provides both handles, so SDL's Java glue stays useful even though it no
longer owns the display.

**SDL's role shrinks.** On Quest, SDL3 keeps audio, timing, file I/O and the JNI
bridge. It does **not** own the display surface — OpenXR does. Controller input
comes through OpenXR action sets, not `SDL_GameController`. Expect the SDL window
to exist but never be presented to; validate that SDL3's Android backend tolerates
this (it does, but confirm early rather than at Phase 5).

**ROM location.** `Android/data/dev.picori.tmc/files/` — the path the dual-screen
fork already documents in its README. No change needed.

**Foveation.** `XR_FB_foveation` + `XR_FB_foveation_configuration` +
`XR_FB_swapchain_update_state`. Cheap, large win, add once the frame is stable.

---

## 4. Milestone plan

Revised for 3D-before-VR and models-before-terrain. Each phase produces something
runnable; no phase ships a stub. Durations assume one experienced engineer.

### Phase 0 — Offline hull prototype · 2–3 weeks · **de-risks the whole project**

A standalone CLI tool. No game integration, no Vulkan, no OpenXR.

1. Decode extracted 4bpp tile blobs + palettes (port the decoder from
   `vk_rt_experiment/main.cpp`).
2. Walk `gFrameObjLists` piece lists; rasterize `(sprite, frame)` → RGBA bitmap
   with origin, using `sSizeTable` for shape/size.
3. **Verify the mirroring hypothesis**: is West == mirrored East?
4. **Verify frame correspondence**: do S/N/E frame indices describe the same pose?
   Emit a divergence report across Link's full 253 frames.
5. Carve hulls per §1.3. Export `.vox` and `.obj`.
6. Eyeball Link idle, Link walk (4 directions), a Chuchu, a Moblin, a Business
   Scrub.

**Exit criterion:** a voxel Link you'd be happy to see in a headset, produced
automatically. If the hull technique fails here it fails cheaply, and you switch
to extrusion-plus-authored-depth before building anything on top of it.

### Phase 1 — World model extraction · 2 weeks

Fork Picori. `port/vr/vr_world.c`. On `LoadRoomTileSet` (`src/playerUtils.c:4046`),
snapshot `gMapTop`/`gMapBottom`. ImGui debug view showing `mapData`,
`collisionData`, `tileTypes`, `actTiles` as a top-down grid. Verify it tracks
live `SetTile` edits via `gUpdateVisibleTiles`. Walk `gEntityLists`, table-dump
`kind`/`id`/`type`/`x`/`y`/`z`/`direction`/`frameIndex`.

**Exit criterion:** you can see the whole room in world space, live, and the
entity table matches what's on screen — including `z` rising when Link jumps.

### Phase 2 — Flat 3D · 4–6 weeks

Own Vulkan 1.1 device (salvaged `Engine`). `VrViewSet` with `count = 1`. Terrain
as simple boxes from `collisionData` height classes — **not** voxelized yet.
Characters as Phase 0 hulls, streamed by residency. Tileset atlas from the
extractor output. Suppress `Port_PresentOnce`. Free camera.

**Exit criterion:** the game is playable in flat 3D with voxel characters on
blocky terrain. This is the point where the project becomes visibly real, and
where you find out whether the hull cache keeps up with combat.

### Phase 3 — Terrain voxelization · 8–12 weeks · **the long pole**

Port the `lib/Structures.lua` algorithm: region flood-fill, ground-seeded
background detection, sprite-like vs. volume classification, repeat-aware art
height measurement, 8px band folding on side faces. Build the authored-pin table
(your `voxel_heights.lua` equivalent, keyed on area + tileset + tileIndex). Async
chunk meshing on a budget. Greedy mesher.

**Exit criterion:** the diorama. Flat 3D, but beautiful.

### Phase 4 — PCVR · 3–4 weeks

OpenXR session on the existing Vulkan device. `count = 2`, multiview render pass,
`gl_ViewIndex` in the vertex shader. `xrWaitFrame` predicted display time replaces
the decoupled present grid in `VBlankIntrWait` (`port/port_bios.c:742` — the
decoupled path already exists and is on by default, `sDecoupleRender = true`).
UI on a head-locked ortho quad; reuse the published message-box rect
(`virtuappu_mode1_ws_msg_x0/x1/y0/y1`) to place the textbox panel. Widen
`CheckOnScreen` (`port_draw.c:619`) via `Port_Widescreen_EffectiveViewWidth()`
and fix the fallout.

**Entity interpolation lands here, not later.** The decoupled pacing comment in
`port_bios.c` says re-presented frames are "identical until interpolation lands."
On a monitor that's invisible; at 90 Hz in a headset, a 60 Hz simulation without
interpolation is judder you will feel.

**Exit criterion:** playable in a headset on PC.

### Phase 5 — Quest · 4–6 weeks

Manifest, gradle, loader bootstrap per §3. Foveation. Aggressive LOD and mesh
budget cuts — expect to need a POTATO-equivalent quality ladder; PotatoVoxel ships
one (OFF/HIGH/MEDIUM/LOW/POTATO with render scale 100/75/50/33%) for exactly this
class of hardware.

### Phase 6 — Content · open-ended

Per-area pin authoring. Carve masks for major characters. HDMA effect
substitutions (Deepwood barrel → rotating voxel object, water ripple →
vertex-displaced surface). 144 areas, 842 rooms.

**Strongly consider a vertical slice** — Minish Woods or Hyrule Town — to prove
the whole pipeline at a tenth of the content cost before committing to the full
world.

---

## 5. Total, and the honest shape of it

Phases 0–4 to a playable PCVR build: roughly **20–27 weeks**. Phase 5 adds 4–6.
Phase 6 is as long as you want it to be.

The distribution is lopsided and worth internalizing: **getting into a headset is
not the hard part.** Phases 0, 1, 2 and 4 together are about 11–15 weeks and end
with a working VR game. Phase 3 alone is 8–12 weeks and Phase 6 is unbounded.
The project is a graphics-content project wearing a VR project's clothes.

---

## 6. Decisions still open

1. **Voxel resolution for characters.** One voxel per pixel gives a 16-tall Link
   — chunky in the 3D Dot Game Heroes idiom, which is probably what you want, and
   matches PotatoVoxel's choice ("One MagicaVoxel voxel is one world pixel").
   Supersampling to 2× per pixel quadruples the hull cost and starts to look like
   a smooth model rather than a voxel one. Recommend 1:1.

2. **Where Link's `z` goes.** `entity->z` is the game's height axis. World Y is
   the obvious mapping, but `collisionLayer` (`LAYER_BOTTOM`/`LAYER_TOP`) is a
   *separate* floor concept. You need a convention for how a top-layer entity at
   `z = 0` relates to a bottom-layer entity at `z = 0`. Suggest: world Y =
   `z.HALF.HI + (collisionLayer == LAYER_TOP ? FLOOR_HEIGHT : 0)`, with
   `FLOOR_HEIGHT` tuned per area.

3. **Scale.** One world pixel = how many metres? A 16px-tall Link at 1 px = 1 cm
   makes him 16 cm — a diorama you look down on. At 1 px = 10 cm he's 1.6 m and
   you're standing in Hyrule. These are completely different games and the choice
   affects comfort, locomotion and the entire terrain budget. **Decide before
   Phase 2**, because it's baked into every mesh you cache.

4. **Locomotion.** The game moves Link; you move your head. If scale is
   "diorama", this is a non-issue — you're a giant looking at a table. If scale is
   "1:1 human", Link's movement becomes forced camera motion, which is the single
   most reliable way to make people sick. The diorama framing sidesteps an entire
   category of VR comfort problems, and it's also what PotatoVoxel converged on.

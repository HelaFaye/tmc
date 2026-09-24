# VR Voxel Port of The Legend of Zelda: The Minish Cap
## Revised engineering blueprint, derived from the actual source

This replaces the original project brief. Every claim below is traced to a file
in the five uploaded archives. Where the original brief was wrong, the
correction is marked **[CORRECTION]**.

---

## 0. Executive summary

The goal — a voxelized, stereoscopic, head-tracked Minish Cap — is achievable,
but not by the route the brief describes. Three of its load-bearing premises
don't hold:

1. Gen1Recomp is a hand-written Lua reimplementation in LÖVE2D, not a native
   decompilation port. No code transfers. Only the *idea* transfers.
2. PotatoVoxel does not primarily map tile IDs to `.vox` files. It runs a
   procedural, art-driven voxelizer. 28 `.vox` files exist in the entire mod.
3. There is no VR precedent to mirror. `potato_voxel/lib/VR.lua` is a 65-line
   stub; `supported()` returns `false` unconditionally.

What *is* true, and better than the brief assumes: Project Picori
(`tmc-master_1_.zip`) is a mature native port that already contains a 2D→3D
quad scaffold, a shared-memory frame/OAM/layer publisher, a GPU-resident PPU,
and a render loop already decoupled from the game tick. The work is real but
the runway is far shorter than starting from the brief's Phase 1.

The single biggest architectural change: **do not hook the PPU. Hook the
engine.** The decompilation keeps a complete, world-space, collision-annotated
map in RAM and a 72-slot entity list with true 3D coordinates. The PPU only
ever sees a scrolling 240×160 window of screen-space fragments.

---

## 1. What the five archives actually are

| Archive | Identity | Role |
|---|---|---|
| `tmc-master.zip` | `zeldaret/tmc`, Feb 2026 | Matching GBA decompilation. Builds a **ROM**. Reference only. |
| `tmc-master_1_.zip` | **Project Picori** (`999sian/tmc`), Sep 2026 | **The fork base.** SDL3 native port. |
| `tmc-android-dual-screen-native.zip` | `samyost1/tmc-android`, Aug 2026 | A fork *of Picori*, ~1 month stale. Not independent. |
| `gen1recomp-dev.zip` | LÖVE2D Lua engine | Conceptual reference only. |
| `potato_voxel-main.zip` | Lua mod for the above | **Algorithm reference.** The valuable part. |

**[CORRECTION]** The brief treats `tmc-master_1_` and `tmc-master` as two copies
of the decomp, and cites Project Picori as a third repo to go and clone. It is
already in hand — it is `tmc-master_1_`, identified by
`docs/picori-logo.png` and the README title "Project Picori — Minish Cap PC Port".

**[CORRECTION]** The brief cites `zelda-tmc-3ds` and `tmc-android` as
independent demonstrations of "extracting the live PPU layout layers." The
uploaded `tmc-android` is a Picori fork whose second screen
(`port/port_second_screen_worldmap.c`) does something different and more
interesting: it decodes the pause menu's map artwork **from ROM constants**,
explicitly avoiding live engine state. Its header says so directly — it "never
reads live engine state (gSave/gArea/gRoomControls/gPaletteBuffer/VRAM)". It is
not a PPU-interception technique.

### Build system

**[CORRECTION]** The brief offers a `CMakeLists.txt` template. Picori uses
**xmake** (`xmake.lua`, 1,566 lines; `build.py` is the driver). There is no
CMake anywhere in the tree.

---

## 2. The correct interception layer

### 2.1 Why the PPU is the wrong place

The brief's Phase 1 proposes hooking `virtuappu_mode1_render_text_bg_line` to
sniff tile IDs and coordinates. Reading the actual implementation
(`port/ppu/src/mode1.c:423`) shows why this fails:

- The fetch is **per-pixel, per-scanline**, not per-tile. Tile IDs are re-read
  inside the `MODE1_BG_PIXEL` macro with a one-entry cache keyed on
  `(tile_col << 1) | use_shadow`.
- The source is a **32-tile screenblock** that wraps every 256 px of world.
  `src/screenTileMap.c` shows the engine rolling this window column-by-column
  via `DmaSet` as the camera scrolls. Anything the camera can't see isn't there.
- Coordinates are **post-scroll, post-camera, screen-space**.
- The 8×8 subtile is the wrong granularity. Collision, tile types and the
  game's own semantics all operate on 16×16 metatiles.

### 2.2 Where the world model actually lives

`include/map.h` defines two live `MapLayer` structs, `gMapTop` and
`gMapBottom`. Each is 0xC004 bytes and contains:

| Field | Size | What it gives you |
|---|---|---|
| `mapData[4096]` | u16 | 64×64 grid of 16×16 metatile indices, **world space** |
| `collisionData[4096]` | u8 | per-tile collision, **world space** |
| `tileTypes[2048]` | u16 | tileIndex → semantic tileType |
| `tileIndices[2048]` | u16 | inverse of the above |
| `subTiles[8192]` | u16 | tileIndex → its four 8×8 subtiles + flip/palette attrs |
| `actTiles[4096]` | u8 | secondary behaviour class (holes, wall-jumps) |

Two layers = two floors (`LAYER_BOTTOM = 1`, `LAYER_TOP = 2`). That is your
voxel world, already resident, already annotated, already stable under camera
motion. Nothing needs to be sniffed.

Additionally, `include/tileMap.h` exposes `gMapDataTopSpecial[0x4000]` and
`gMapDataBottomSpecial[0x4000]` — 16,384 halfwords each, i.e. the **full room
at 8×8 subtile resolution** (128×128 subtiles = 64×64 metatiles). This is the
texture-resolution view of the same world, and it is what Picori's widescreen
shadow tilemap already reads from. `RenderMapLayerToSubTileMap(u16*, MapLayer*)`
(declared in `port/port_level_editor.cpp`) is the engine function that produces
it — mirror this, don't reinvent it.

### 2.3 Entities, and the one line that matters most

`include/entity.h` gives 72 entity slots (`gEntities[MAX_ENTITIES]`, plus
`gAuxPlayerEntities[7]`), each carrying:

```c
/*0x2c*/ union SplitWord x;   /* Q16.16 world X */
/*0x30*/ union SplitWord y;   /* Q16.16 world Y */
/*0x34*/ union SplitWord z;   /* Q16.16 world Z — real height */
/*0x08*/ u8 kind;             /* PLAYER / ENEMY / PROJECTILE / OBJECT / NPC / ... */
/*0x09*/ u8 id;
/*0x0a*/ u8 type;
/*0x15*/ u8 direction;        /* 8-way, DirectionNorth..DirectionNorthWest */
/*0x14*/ u8 animationState;
/*0x1e*/ u8 frameIndex;
/*0x38*/ u8 collisionLayer;   /* which floor */
```

Now the critical detail. `port/port_draw.c:735`, `ResolveEntitySpriteParams`:

```c
s32 x = (s32)entity->x.HALF.HI + (s32)(s8)entity->spriteOffsetX;
s32 y = (s32)entity->y.HALF.HI + (s32)entity->z.HALF.HI + (s32)entity->spriteOffsetY;
if ((ip & 3) != 2) {
    x -= (s16)gOAMControls._4;   /* camera scroll X */
    y -= (s16)gOAMControls._6;   /* camera scroll Y */
}
```

**The game folds height into screen Y.** If you sniff OAM — as the brief
proposes — you receive the folded value, and every jump, every thrown pot,
every hovering Keaton becomes a *translation northward* instead of a rise.
Reading the entity list directly and keeping `z` on its own axis is not an
optimization; it is the difference between a 3D scene and a broken one.

### 2.4 Summary of hook points

| Purpose | Hook | File |
|---|---|---|
| World geometry | `gMapTop`, `gMapBottom` | `include/map.h` |
| World texture source | `gMapDataTopSpecial/BottomSpecial` | `include/tileMap.h` |
| Rebuild trigger | `LoadRoomTileSet()`, `LoadRoomGfx()` | `src/playerUtils.c:4046`, `:4095` |
| Live tile edits | `SetTile()`, `gUpdateVisibleTiles` | `port/port_level_editor.cpp` decls |
| Entity enumeration | `gEntityLists[9]`, `gEntities[72]` | `include/entity.h` |
| Per-entity draw | `ProcessEntityForDraw`, `DrawEntitySprites` | `port/port_draw.c:895`, `:804` |
| Sprite piece decode | `RenderSpritePieces` | `port/port_draw.c:347` |
| Camera state | `gRoomControls` (`scroll_x/y`, `origin_x/y`, `width`, `height`) | `include/room.h` |
| Frame boundary | `VBlankIntrWait()` | `port/port_bios.c:742` |
| Present | `Port_PresentOnce()` | `port/port_ppu.cpp` |

---

## 3. Two viable architectures

Because the target hardware question is still open, both are specified. They
are not mutually exclusive — A is a fast prototype that de-risks B.

### Architecture A — out-of-process, over shared memory

Picori already ships the bridge. `port/port_shm_framebuffer.c` publishes to
`/dev/shm/tmc_framebuffer` at magic `TMCF`, **version 4**:

```
0..3    u32 magic = 0x46434D54 ("TMCF")
4..7    u32 version = 4
8..15   u32 width, height
16..19  u32 frameCount   (monotonic; consumers poll)
20..23  u32 oamCount = 128
24..PIX u8  pixels[w*h*4]          composite RGBA8
PIX..   u16 oam[512]               raw GBA OAM
then    3 × plane[w*h*4]           BG1, BG2, sprite-only (alpha-masked)
```

Activated by `TMC_PUBLISH_FRAMEBUFFER=1`. The per-plane re-render lives in
`port/port_ppu.cpp:1387` and runs the same scanline functions into dedicated
buffers at a pinned native 240×160 regardless of widescreen settings.

The consumer already exists too: `port/vk_rt_experiment/FrameSource.h`
declares `ShmFrameSource` and `ParsedOam`, and
`port/vk_rt_experiment/RenderLayerManager.h` implements the 2D→3D quad
translation with a per-layer Z table:

```c
UI 0.0 | Sprite 0.2 | BG0 0.4 | BG1 0.6 | BG2 0.8
```

That is literally the brief's Phase 2 "Layer-to-Z-Axis Transform", already
written, with a 24-byte vertex (`position[3]`, `uv[2]`, `emissive`) and a
256-quad persistent buffer.

**Pros:** you can have a stereo diorama running in days. Zero changes to the
game build. Crash isolation. Free choice of graphics API — and you need that,
see §5.

**Cons:** Linux only (the file guards on `__linux__ && !__ANDROID__`). One frame
of latency. Tearing is possible — its own header admits "single-writer,
single-reader, tearing is possible but unlikely at 60 Hz". And critically, it
publishes **rendered planes, not world data** — you get a flat BG1 image, not
`gMapTop.mapData`. Good enough for layered-quad depth; not good enough for true
voxelization.

**Verdict:** build it first as a stereo smoke test. Do not ship it.

### Architecture B — in-process fork (recommended)

Fork Picori. Add a `port/vr/` module that:

1. Reads `gMapTop`/`gMapBottom` on room load, builds a voxel chunk mesh.
2. Walks `gEntityLists` each tick, emits billboards/models at `(x, y, z)`.
3. Owns its own Vulkan device (see §5) and OpenXR session.
4. Renders two eye passes from headset matrices, plus a UI quad.
5. Suppresses the 2D present path.

This is the only architecture that can reach the actual goal.

---

## 4. The voxelization algorithm, adapted from PotatoVoxel

**[CORRECTION]** The brief's asset pipeline — "GBA Tile ID 0x02F4 →
`assets/voxels/hyrule_tree.vox`" — is not what PotatoVoxel does, and at TMC's
scale it is not feasible. TMC has 144 areas, 842 rooms, and tilesets of up to
2,048 entries each (`TILESET_SIZE`). Hand-authoring per-tile-ID models is a
five-figure asset count.

What PotatoVoxel actually does, per `lib/Structures.lua` and
`data/voxel_heights.lua`, is a 3dSen-style procedural voxelizer:

1. **Flood-fill** every connected region of solid, unauthored tiles — a house
   with its mailbox, a fence row, a stretch of border forest.

2. **Identify background pixels.** Tileset art carries no alpha and white is a
   real paint colour (window frames, wall stripes), so whiteness alone proves
   nothing. The map does: background is the white that *connects to walkable
   ground* in the assembled scene. Seeding a flood from surrounding ground eats
   the air around a fence post but cannot reach an interior wall's stripes
   sealed behind dark trim.

3. **Sprite-like tiles** (art >20% background, `TILE_BG_RATIO = 0.20`) become
   per-pixel voxel objects at the art's real drawn height, with thin depth
   (`OBJECT_DEPTH = 6`), standing on synthesized ground. Capped at
   `OBJECT_MAX_ROWS = 6` (48 px of drawing) and `OBJECT_MAX_QUADS = 4096`.

4. **Everything else** becomes a volume column raised to the height it is
   *drawn* at — repeat-aware, so a 6-row house is 48 px but a 40-row border
   forest is rows of 16 px trees rather than a monolith. The south face folds
   the 2D artwork upright in 8 px bands, band *k* sampling the map row *k*
   tiles north. Side faces are never stretched.

5. **Authored pins override everything.** `data/voxel_heights.lua` is 6,469
   lines of hand-tuned exceptions keyed by tileset and tile group, with classes
   `ground / water / void / ledge / roof / bed / wall / tree / fence / sign /
   counter / table / desk / stair_e / stair_w / stair_down_e / stair_down_w /
   relief / bookcase`.

6. Only where the detector genuinely can't win do `.vox` assets appear — 28
   files, covering ledges, a few trees, and bollards. The loader is
   `lib/MagicaVoxel.lua` (VOX 150 + greedy face mesher, MagicaVoxel
   `+X/+Y-depth/+Z-up` remapped to world `+X east / +Y up / +Z south`).

### Porting this to TMC

The fallback chain in PotatoVoxel resolves class from **collision**, not art:
water cell → water, walkable cell → ground, remainder → wall. TMC gives you a
far richer version of exactly that signal in `MapLayer.collisionData` and
`MapLayer.actTiles`, already per-tile, already world-space.

So the TMC classifier should be:

```
1. authored pin for (area, tileset, tileIndex)      — your voxel_heights equivalent
2. actTiles[pos]   → holes, wall-jump faces, special behaviour
3. collisionData[pos] → solid / walkable / water / ledge
4. tileTypes[tileIndex] → semantic refinement where named
5. procedural art-height detection → volume or object
```

**Caveat on step 4.** `include/tiles.h` is 1,784 lines of
`TILE_TYPE_N = 0xN` with roughly a dozen human-meaningful comments
(`CUT_BUSH`, `CUT_GRASS`, `CUT_SIGNPOST`, `CUT_TREE`). Semantic classification
from tile types is a research task, not a lookup. Lean on collision first.

**Texture source.** PotatoVoxel samples a 128×48 tileset atlas rather than a
rendered map canvas, explicitly to avoid ~5 MB per-map canvases with up to five
maps live. Do the same: Picori's asset extractor
(`tools/src/assets_extractor/`, linked in-process via
`assets_extractor_api.cpp`) already produces the extracted graphics under
`assets/<region>/`. Build one atlas per area tileset at room load.

**Caching is mandatory.** PotatoVoxel's entire 1.6–1.9 changelog is cache
engineering: `MeshCache`, `CachePrebuild`, `CacheIdentity`, `GeometryStream`,
`BuildBudget` (coroutine-suspended slicing), a `WorkerPool` and a
`geometry_worker.lua`. Its README front-loads "PREBUILD CACHE" as a user-facing
step that can take a long time. On a 2D game at 60 Hz that's a stutter. In VR
at 72–90 Hz it is an instant comfort failure. Budget for async chunk meshing
from day one; do not plan to add it later.

---

## 5. OpenXR integration — the hard constraint

**[CORRECTION]** The brief says to "link an OpenXR loader package to the
application's main update loop" and initialize "OpenGL 4.5+ or Vulkan via
SDL3". This underestimates the problem substantially.

Picori renders through **SDL_GPU**, SDL3's backend-abstracted GPU API
(`port/port_gpu_renderer.cpp`: `SDL_CreateGPUDevice` with
`SDL_GPU_SHADERFORMAT_MSL` tried first on Apple, `SDL_GPU_SHADERFORMAT_SPIRV`
otherwise). There is also a GLES 3.1 compute fallback
(`port/port_gpu_raster_gl.cpp`) for Linux/Android devices without usable Vulkan.

OpenXR's Vulkan binding requires the application to:

- call `xrGetVulkanInstanceExtensionsKHR` / `xrGetVulkanDeviceExtensionsKHR`
  **before** instance and device creation, and enable what it returns;
- select the physical device OpenXR names via `xrGetVulkanGraphicsDeviceKHR`;
- hand OpenXR the `VkInstance`, `VkPhysicalDevice`, `VkDevice`, queue family
  index and queue index in `XrGraphicsBindingVulkanKHR`;
- render into swapchain images **OpenXR allocates**, not ones you created.

SDL_GPU exposes none of these hooks. It creates the device on your behalf,
with extensions of its choosing, and owns its swapchain.

**Therefore: the VR renderer must own its own Vulkan device.** This is not
optional and it is not a small refactor of `port_ppu.cpp`.

The good news: that device already exists in the tree.
`port/vk_rt_experiment/Engine.{h,cpp}` is a Vulkan 1.3 instance/device/swapchain
implementation, ~886 lines, written precisely because SDL_GPU wasn't enough for
the RT path. **That is your starting point, not `port_ppu.cpp`.**

Its README's caveats are important and honest:

> "Foundational architecture only. Compiles against the Vulkan SDK + SDL3 +
> glslang. **Has not been executed on real hardware**; the BLAS/TLAS, SBT, and
> pipeline setup are spec-conformant per the headers, but per-iteration
> debugging via Renderdoc / validation layers is still needed before any of it
> is trusted."

and

> "**Will not run on Apple Silicon** (Vulkan RT not available on Metal)."

Plan to strip the ray-tracing pipeline (`RayTracingPipeline.cpp`,
`DenoisePipeline.cpp`, `shaders/*.rgen|.rchit|.rmiss`) and keep
`Engine` + `RenderLayerManager` as a conventional rasterizer. Ray tracing is a
separate, later, desktop-only question; do not let it gate the VR milestone.

### Frame pacing — already solved

`port/port_bios.c` (`VBlankIntrWait`) implements a **decoupled** pacing mode,
enabled by default (`port_runtime_config.cpp:98`, `sDecoupleRender = true`):

> "the engine ticks on a fixed grid (`Port_Config_TickTimeNs` — game speed
> never follows the FPS cap) and presents run on their own wall-clock grid…
> Above the tick rate that means re-presenting the same game frame"

This is exactly the VR requirement: a 60 Hz simulation driving a 72/90/120 Hz
display. The mechanism exists; you replace the present grid with OpenXR's
`xrWaitFrame` predicted display time. Note the comment "identical until
interpolation lands" — entity position interpolation between game ticks is
*not* implemented, and in VR the judder will be visible. Budget for it.

---

## 6. Camera decoupling

The brief says to "replace the static horizontal tracking parameters inside
tmc's engine loop with independent 3D vectors." **Do not do this.**
`gRoomControls.scroll_x/scroll_y` is not merely a view transform — it drives:

- entity culling (`CheckOnScreen`, `port/port_draw.c:619`)
- room-edge transition triggers
- the screenblock rolling DMA (`src/screenTileMap.c`)
- script synchronisation. `port/port_widescreen.h` warns that
  `WaitForCameraTouchRoomBorder` "busy-waits for `scroll_x` **EQUALITY**: any
  drift between producers and that check deadlocks a cutscene."

Instead: **leave `gRoomControls` running the 2D camera untouched**, and add an
independent VR camera in the renderer. The game keeps thinking it is showing a
240×160 window; you render the room from wherever the player's head is. The
2D camera becomes a *streaming hint* — it tells you which part of the world the
engine guarantees is loaded — not a view matrix.

Picori's widescreen work already established the correct pattern:
`Port_Widescreen_CameraRestX(int target_x)` is documented as "THE single
formula for where the camera stops", used by the follow camera, the snap
cameras and the script wait. Any VR camera work must route through that
abstraction rather than poking `scroll_x`.

---

## 7. Revised risk register

The brief's three risks are real but incomplete, and two have known answers in
the tree already.

| Risk | Severity | Evidence | Mitigation |
|---|---|---|---|
| **Entity culling at the viewport edge** | **Critical** | `CheckOnScreen` culls at `view_width + 0x7E` horizontally and `0x11E` vertically. Off-screen entities are not drawn and, worse, some are not updated. In VR the player looks past those bounds constantly. | Widen the cull bounds via `Port_Widescreen_EffectiveViewWidth()` — the abstraction exists. But read the warning below. |
| **Engine parks sprites off-screen** | **Critical** | `docs/widescreen-phase2-design.md`: "Step C-2 (OAM viewport extension) is the hard one… the engine parking convention assumes y=0xA0 disable-bit-set but also scatter-positions some active entities at x≥240 that we can't easily distinguish from 'real' widescreen sprites without rewriting the engine's entity culling." | Render from the **entity list**, not OAM. Parked OAM slots are a rendering artifact; `entity->x/y/z` stays truthful. This is the second reason to abandon OAM sniffing. |
| **Vertical extension is blocked** | **High** | Same doc, Step C-3: extending `MODE1_GBA_HEIGHT` to 180 failed because "the engine's main loop fundamentally assumes 160-line frames for timing and BG preload, so the extra 20 lines pull in undefined buffer content." `port_widescreen.h` hard-codes `PORT_VIEW_HEIGHT 160` with a comment explaining that only the horizontal extent generalises. | Irrelevant if you render from `gMapTop`/`gMapBottom` rather than the PPU. This risk only exists on the PPU path — another reason to leave it. |
| **Room streaming boundary** | High | `LoadRoomTileSet` (`src/playerUtils.c:4046`) clears and reloads both maps wholesale on room change. Adjacent rooms are not resident. | A room is your chunk unit. On transition, cross-fade or use the engine's own `TRANSITION_FADE_*` cover. Do not attempt to render two rooms simultaneously without first proving the data is there. |
| **Widescreen is capped at 384 px** | Medium | `xmake.lua` `widescreen_width` option; release builds use 384. Content-width scanning (`Port_WidescreenScanContentPx`) falls back to native 240 when content is narrower. | 384 is 1.6× native. VR needs far more. This tells you widescreen is *not* a path to VR FOV — it confirms the engine-level render is the only way. |
| **Mod system is asset-only** | Medium | `port/port_mods.cpp` is "Tier 1 mod loader: asset overrides from `<exe>/mods/`" — filesystem asset substitution, no code hooks. | Unlike PotatoVoxel, this cannot ship as a mod. It is a fork. Plan the rebase strategy accordingly; Picori is actively developed. |
| **170 functions still in ARM assembly** | Medium | `asm/src/*.s` includes `DrawEntity`, `CheckOnScreen`, `GravityUpdate`, and the entire `arm_*` collision block (`arm_GetTileAtEntityPos`, `arm_CollideAll`, …). | Picori reimplemented the drawing ones in C (`port_draw.c` header lists `ram_DrawDirect`, `ram_DrawEntities`, `DrawEntity`, `CheckOnScreen`). **Always work against Picori's C, never upstream's asm.** |
| **Per-scanline DMA effects** | Medium | Water ripple `BGxVOFS`, `BLDY` fades, the Deepwood rolling-barrel affine, window animation. `port/port_hdma.c`, and the affine `#132` latch handling in `mode1.c`. | These are screen-space effects with no 3D meaning. Detect and substitute: the barrel becomes a rotating voxel object, the ripple becomes a vertex-displaced water surface. Enumerate them per-area; there are not many. |
| **Layer priority vs. map layer** | Medium | The brief conflates GBA BG priority (0–3) with `LAYER_BOTTOM`/`LAYER_TOP`. | They are unrelated. BG priority is compositing order; `collisionLayer` is which *floor* an entity is on. Map the latter to world Y. Mapping BG priority to Z breaks every bridge and rooftop. |
| **Mesh build stutter** | Medium | PotatoVoxel's entire cache subsystem exists for this. | Async from day one. Port the `BuildBudget` + worker-pool shape. |
| **UI in stereo** | Low | The brief's solution (floating ortho quad) is correct. | `port/port_imgui_menu.cpp` and the HUD both live on BG0. Picori already publishes a message-box rect (`virtuappu_mode1_ws_msg_x0/x1/y0/y1`) for widescreen centering — reuse it to locate the textbox for a world-space panel. |
| **Apple Silicon** | Low | Vulkan RT unavailable on Metal (vk_rt README). OpenXR on macOS is effectively nonexistent. | Declare macOS out of scope. Picori supports it; the VR build will not. |

---

## 8. Phased plan

Each phase ends with something runnable. No phase ships a stub — this mirrors
Picori's own convention in `docs/gpu-rasterizer-design.md`.

**Phase 0 — Stereo smoke test (Architecture A).** ~1–2 weeks.
Build `vk_rt_experiment` standalone. Strip the RT pipeline; render
`RenderLayerManager` quads with a conventional rasterizer. Consume
`ShmFrameSource` with `TMC_PUBLISH_FRAMEBUFFER=1`. Add OpenXR, render two eye
passes of the five layered quads. Deliverable: Minish Cap as a floating
parallax diorama in a headset. No voxels. This proves the OpenXR + Vulkan +
SDL3 stack on your actual hardware, which is the highest-uncertainty item.

**Phase 1 — World model extraction.** ~2–3 weeks.
In a Picori fork, add `port/vr/vr_world.c`. On `LoadRoomTileSet`, snapshot
`gMapTop`/`gMapBottom` and build a debug ImGui view showing `mapData`,
`collisionData`, `tileTypes` and `actTiles` as a top-down grid. Verify it
tracks `SetTile` edits via `gUpdateVisibleTiles`. No rendering yet. Deliverable:
proof you can see the whole room, in world space, live.

**Phase 2 — Flat 3D.** ~3–4 weeks.
Build the tileset atlas from the extractor output. Emit one textured quad per
metatile at its collision-derived height. Walk `gEntityLists`, emit camera-facing
billboards at `(x.HI, y.HI, z.HI)` — with `z` on the **vertical** axis. Render
in-process through your own Vulkan device; suppress `Port_PresentOnce`.
Deliverable: the game playable in flat 3D with a free camera.

**Phase 3 — VR.** ~2–3 weeks.
OpenXR session, `xrWaitFrame`-driven present grid replacing the decoupled render
grid. Two eye passes. UI on a head-locked ortho quad. Widen `CheckOnScreen` and
fix the fallout. Deliverable: playable in a headset.

**Phase 4 — Voxelization.** ~8–12 weeks, and this is the long pole.
Port the Structures algorithm: flood-fill regions, ground-seeded background
detection, sprite-like vs. volume classification, art-height measurement with
repeat capping, 8 px band folding on side faces. Build the authored-pin table.
Async chunk meshing with a per-frame budget. Deliverable: the voxel diorama.

**Phase 5 — Content pass.** Open-ended.
Per-area pin authoring, `.vox` assets for the cases the detector loses, HDMA
effect substitutions, entity models to replace billboards for major characters.
PotatoVoxel's 6,469-line pin file for a much simpler game is the honest scale
indicator here.

---

## 9. Build integration sketch (xmake, not CMake)

The VR module is a new target that links its own Vulkan, kept out of `tmc_pc`'s
SDL_GPU path. Following the existing `option()` convention at `xmake.lua:67`:

```lua
option("vr")
    set_default(false)
    set_showmenu(true)
    set_description("Compile the OpenXR VR renderer (own Vulkan device; Linux/Windows only)")
option_end()
```

Inside `target("tmc_pc")`, alongside the existing `has_config("gpu_renderer")`
block at `xmake.lua:781`:

```lua
if has_config("vr") then
    add_defines("TMC_VR=1")
    add_requires("openxr_loader", "vulkansdk")
    add_packages("openxr_loader", "vulkansdk")
    add_includedirs("port/vr")
    add_files("port/vr/*.cpp", "port/vr/*.c")
    -- Engine + layer manager salvaged from the RT scaffold, RT pipeline dropped
    add_files("port/vk_rt_experiment/Engine.cpp")
    add_files("port/vk_rt_experiment/RenderLayerManager.cpp")
    -- Reuse the committed-SPIR-V convention from the gpu_renderer block
    add_rules("utils.bin2c", {extensions = {".spv"}, nozeroend = true})
    add_files("port/vr/shaders/build/*.spv")
end
```

Shader compilation follows `port/shaders/build.sh`: `glslangValidator` offline,
`.spv` committed, so the build never requires the Vulkan SDK on a user's
machine. Extend that script rather than adding a second one.

---

## 10. Open questions, ranked

1. **Target hardware.** PCVR (SteamVR/Monado) or standalone Quest? Quest means
   the Android build path (`set_kind("shared")`, `libmain.so`, `arm64-v8a`
   target dir), Android Vulkan or the GLES 3.1 compute fallback, and a hard
   ban on the ray-traced path. It also means the voxel budget shrinks by
   roughly an order of magnitude — PotatoVoxel's POTATO preset (33% render
   scale) exists for exactly this class of device.

2. **Voxel scale.** One voxel per world pixel (PotatoVoxel's choice, per
   `MagicaVoxel.lua`: "One MagicaVoxel voxel is one world pixel") gives a
   16×16×N voxel metatile. At 64×64 metatiles × 2 layers that is up to
   2 million voxel cells per room before meshing. Greedy meshing is mandatory;
   PotatoVoxel ships one.

3. **Entity representation.** Billboards for everything (PotatoVoxel's answer,
   `lib/SpriteBillboards.lua` + `BattleBillboard.lua`) or voxelized sprites for
   major characters? Billboards in VR at close range read as flat cardboard in
   a way they do not on a monitor. This is a comfort question, not a fidelity
   one.

4. **Scope.** 144 areas and 842 rooms. A vertical slice — Minish Woods, or
   Hyrule Town — proves the pipeline at a tenth of the content cost.

---

## Appendix — file reference index

```
Project Picori (tmc-master_1_/tmc-master/)
  xmake.lua                            1566 lines, build definition
  build.py                             build driver
  port/ppu/src/mode1.c                 software GBA PPU (VirtuaPPU-derived)
  port/ppu/include/cpu/mode1.h         PPU interface, widescreen shadow decls
  port/port_ppu.cpp                    PPU bridge, memory bind (:928), shm publish (:1387)
  port/port_draw.c                     DrawEntity/CheckOnScreen/sprite pieces in C
  port/port_bios.c                     VBlankIntrWait, decoupled frame pacing (:742)
  port/port_shm_framebuffer.c          shm v4 publisher
  port/port_widescreen.h               viewport-width single source of truth
  port/port_level_editor.cpp           live map editing, engine fn declarations
  port/port_mods.cpp                   asset-override mod loader (no code hooks)
  port/vk_rt_experiment/Engine.{h,cpp} standalone Vulkan 1.3 device  <- VR base
  port/vk_rt_experiment/RenderLayerManager.h   2D->3D quads, layer Z table
  port/vk_rt_experiment/FrameSource.h  ShmFrameSource, ParsedOam
  docs/gpu-rasterizer-design.md        GPU PPU design + parity model
  docs/widescreen-phase2-design.md     646 lines; the culling/parking lessons

Decompilation (shared by all tmc forks)
  include/map.h                        MapLayer, gMapTop, gMapBottom
  include/tileMap.h                    gMapData{Top,Bottom}Special
  include/entity.h                     Entity (x/y/z), gEntities, gEntityLists
  include/room.h                       RoomControls, RoomVars
  include/tiles.h                      1784 lines of mostly-unnamed TileTypes
  src/playerUtils.c:4046               LoadRoomTileSet
  src/screenTileMap.c                  screenblock rolling DMA
  asm/src/*.s                          ~170 functions still in ARM asm

PotatoVoxel (potato_voxel-main/)
  lib/Structures.lua                   3186 lines; the voxelizer
  lib/ChunkMesher.lua                  1098 lines; tile layer -> static mesh
  lib/Voxel3D.lua                      1668 lines; shader, depth, camera
  lib/MagicaVoxel.lua                  .vox reader + greedy mesher
  lib/MeshCache.lua / CachePrebuild.lua  async cache subsystem
  lib/VR.lua                           65-line stub; supported() == false
  data/voxel_heights.lua               6469 lines of authored pins
  assets/vox/                          28 .vox files, total
```

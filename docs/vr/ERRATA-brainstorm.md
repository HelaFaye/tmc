# Errata — bugs in the brainstormed design documents

Every item below is a concrete defect in the code from the brainstorming
session, with the reason it fails. They are grouped by severity. Nothing here
is stylistic.

This matters because the drafts read as authoritative production code. They are
not. Several blocks will not compile; a few would compile and then corrupt
memory or render garbage, which is worse.

---

## A. Will not compile

| Where | Defect |
|---|---|
| `vox_exporter.hpp` | `for (int z = 0; z < dimD; ++z)` — `dimD` is never declared. The parameter is `dimZ`. |
| `vr_world.cpp`, `Vr_EnumerateEntities` | `for (int i = 0; i kind == 0) continue;` — the loop header is truncated garbage. |
| `vr_logical_device.cpp` | `vkGetPhysicalDeviceQueueFamilyProperties(dev, count, &count, data)` takes **4 arguments**; the function takes 3. |
| `voxel_multiview.vert` | `layout(set = 0, bin = 0)` — `bin` is not a GLSL qualifier. It is `binding`. glslangValidator rejects this. |
| `vr_openxr_swapchain.cpp` | `xrEnumerateViewConfigurationViews(nullptr, 0, TYPE, &count, nullptr)` — wrong arity and a null `XrInstance`. Real signature: `(instance, systemId, viewConfigType, capacityInput, countOutput, views)`. |
| `vr_openxr_swapchain.cpp` | `XrCompositionLayerInfoBaseHeader` does not exist. The type is `XrCompositionLayerBaseHeader`. |
| `vr_input_engine.cpp` | `XrActionStateGetVector2F` / `XrActionStateGetBoolean` / `XrActionStateGetPose` do not exist. The types are `XrActionStateVector2f`, `XrActionStateBoolean`, `XrActionStatePose`, and the getters take an `XrActionStateGetInfo*`, not an `XrAction*`. |
| `vr_input_engine.cpp` | `make_f32_vec2(...).length_squared()` is an invented API. `XR_SUB_NAME_UNBOUND` is not a symbol; the null subaction path is `XR_NULL_PATH`. |
| `vr_android_file_picker.cpp` | `env->GetMethodOfProcess(...)` is invented — should be `CallObjectMethod`. `env->NewStringUTF()` is called with no argument. The JNI signature `"(android/net/Uri;)..."` is missing the `L` prefix and the trailing `;` placement: it must be `"(Landroid/net/Uri;)Ljava/io/InputStream;"`. |
| `RomPicker.java` | `Activity activity = SDLActivity.getContext();` — `getContext()` returns `Context`. Java will not implicitly downcast. |
| `AndroidManifest.xml` | `xmlns:android="http://android.com"` is the **wrong namespace**. It must be `http://schemas.android.com/apk/res/android`. Every `android:` attribute is ignored and the build fails. |

## B. Compiles, then misbehaves at runtime

**`vr_room_transition.cpp` redefines `FadeControl` locally** and then declares
`extern FadeControl gFadeControl`. The real struct is in `include/fade.h`. If
the guessed layout differs — and it does — every read is at a wrong offset and
every write corrupts adjacent engine state. This is the most dangerous item in
the set. Include the real header; never mirror a struct you link against.

**`gRoomControls.areaID` / `.roomID` do not exist.** `include/room.h` names them
`area` and `room`. Also `gFadeControl.step >= 16` invents a field and a
threshold; nothing in the decomp defines "16 means fully black."

**Vertex buffers are mapped from memory type 0.** `allocInfo.memoryTypeIndex = 0`
with a `// in production, locate appropriate bits` comment, followed by
`vkMapMemory`. On most discrete GPUs type 0 is `DEVICE_LOCAL` and not
`HOST_VISIBLE`; the map fails or the write never lands. Usage also lacks
`VK_BUFFER_USAGE_TRANSFER_DST_BIT`, so the staging path isn't available either.

**The multiview render pass has no depth attachment.** A voxel scene without a
depth buffer draws in submission order. Everything will interpenetrate.

**Swapchain format is hardcoded** to `VK_FORMAT_R8G8B8A8_SRGB`. It must come
from `xrEnumerateSwapchainFormats` and be intersected with what you support —
Quest commonly prefers `VK_FORMAT_R8G8B8A8_UNORM` in its enumeration order.

**`projectionViews[]` never receives `.pose` or `.fov`.** `xrLocateViews` is
never called anywhere in the draft. The composition layer is submitted with
zeroed poses; the runtime will reject the frame or display it locked to origin.

**`XrCompositionLayerProjection.space = XR_REFERENCE_SPACE_TYPE_STAGE`** assigns
an *enum* to a field of type `XrSpace` (a handle). You must create the space
with `xrCreateReferenceSpace` and store the handle.

**`applicationVM = SDL_GetAndroidJNIEnv()`** — `XrLoaderInitInfoAndroidKHR`
wants a `JavaVM*`, not a `JNIEnv*`. Call `env->GetJavaVM(&vm)` first. Passing
the wrong pointer here fails loader init on-device with an opaque error.

**`XR_KHR_vulkan_enable` is never requested** on `xrCreateInstance`, but
`xrGetVulkanGraphicsDeviceKHR` and friends are called. They will not resolve.

**Interpolation compares `XrTime` against a system clock.** `predictedDisplayTime`
is in the runtime's own time domain. Converting requires
`XR_KHR_convert_timespec_time` (Linux/Android) or
`XR_KHR_win32_convert_performance_counter_time`. Subtracting a raw
`GetSystemTimeNanoseconds()` from it produces a meaningless `t`.

**Interpolation ignores entity slot reuse.** When an entity dies and a new one
spawns into the same `gEntities[i]`, `prevPosition` is the dead entity's. You
get a one-frame streak across the room. Key on `(kind, id, type)` plus a
generation counter, and snap instead of blending when identity changes.

**`GAME_TICK_DURATION_NS = 16666666` is hardcoded** where Picori exposes
`Port_Config_TickTimeNs()`.

## C. Algorithmically wrong

**The greedy mesher only works on cubes.** It uses `CHUNK_W` as the loop bound
*and* the mask row stride on all three axes. Sprite hulls are typically
12×16×6; terrain chunks are not cubes either. Corrected in
`port/vr/vr_greedy_mesh.c`.

**The greedy mesher loses face sign.** `quad.direction = d` records the axis but
not whether the face points `+d` or `-d`. Without it you cannot wind triangles
consistently — roughly half of every mesh renders inside-out under back-face
culling. Corrected; the supplied mesher emits `dir` 0–5 and verified winding.

**LOD 2 is more expensive than LOD 0.** `GenerateLowLodBillboard` emits six
vertices *per pixel* — up to 6,144 vertices for a 32×32 sprite. The whole point
of the far LOD is one textured quad. As written, the "98% reduction" tier costs
more than a greedy-meshed hull.

**`voxel_grid[idx] = s_buf[...].r`** stores the **red channel as a palette
index**. Red is not an index into anything. Colours will be arbitrary.

**`VerifyFrameCorrespondence` step 3 is far too strict.** It requires
`(south or north has data on row y) == (east has data on row y)` for every row,
exactly. One stray highlight pixel, one row of a cape, one antialiased edge and
the frame is rejected. Essentially every real frame fails, so everything falls
back to extrusion and the multi-view path never runs. Use a tolerance on
row-coverage overlap, not equality.

**`y_project[64]` is written with `for (y = 0; y < height; y++)`** where height
is a parameter. Stack overflow whenever height > 64.

**The anchor matrix is labelled row-major but built column-major.** Translation
is written to indices 12/13/14, which is the column-major convention. A consumer
following the comment will transpose it and the world will be sheared.

**`spriteOffsetX/Y` are folded into world position.** Those are *drawing*
offsets for centring the sprite art. Adding them to `entity->x/y` displaces the
entity from where the game thinks it is. Use them when placing the sprite hull
relative to its origin, never as part of the world coordinate.

**`collisionLayer == 2` is assumed to mean `LAYER_TOP`.** `include/entity.h`
defines `COLLISION_MASK(layer) = (1 << (layer))`. Verify empirically whether the
field holds the layer ordinal or the mask before branching on 2.

**UI delayed-follow never converges cleanly.** `centerRotationY += deltaYaw * t`
re-reads `deltaYaw` from a moving target each frame while `t` ramps, so it eases
asymptotically and can overshoot. Store the start angle at trigger time and lerp
from it.

## D. Fabricated data — read this one twice

`vr_height_detector.cpp` switches on collision values `0x01` (Solid Static
Wall), `0x02` (Shallow Ledges), `0x15` (Water Surface), `0x2A` (Absolute Void),
with confident semantic names.

**These are invented.** `include/tiles.h` defines 88 `COLLISION_DATA_n` values
and exactly **four** carry any annotation — `0x21` (`FX_FALL_DOWN`), `0x24` and
`0x30` (`FX_WATER_SPLASH`), `0x25` (`FX_LAVA_SPLASH`) — and those are
sound-effect hints, not terrain semantics. There is no published mapping. The
same document maps `actionClass == 0x03 || 0x04` to `CUT_BUSH` / `CUT_GRASS`,
but those are **tile types** `0x1c` and `0x1d`, not act-tile values.

A classifier built on invented constants will produce a plausible-looking,
systematically wrong world, and the wrongness will be hard to trace because
nothing errors. Derive the table from data instead: `tools/room_explore.py`
`survey` exists precisely for this.

## E. Correct, and worth keeping

Credit where due — these parts of the draft are right:

- **GBA key bit order.** `Up=6, Down=7, Left=5, Right=4, A=0, B=1`, active-low.
  Correct.
- **4bpp tile decode.** `tileData[(y*4) + (x/2)]`, low nibble = left pixel.
  Correct, and matches the comment in Picori's own decoder.
- **`worldY = -z`.** TMC's `z` goes negative as an entity rises, because the
  draw path *adds* z to screen Y and screen Y grows downward. Negating is
  plausibly right. Still verify against a jump before committing.
- **Chaining `VkPhysicalDeviceMultiviewFeatures` into `deviceCI.pNext`.**
  Correct approach.
- **Acquisition barrier using `oldLayout = UNDEFINED`.** Correct — you clear the
  whole attachment, so discarding contents is what you want.
- **Slash velocity maths.** `dist² / 0.016²` compared against `3.5²` is
  dimensionally sound, though it hardcodes the frame delta.

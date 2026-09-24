-- xmake-vr.lua — VR option and tool targets for a Project Picori fork.
--
-- Picori's own xmake.lua is 1,566 lines and its tmc_pc target lists sources
-- explicitly. Do NOT replace it. Add one line near the top of the real file:
--
--     includes("xmake-vr.lua")
--
-- then add the block marked TMC_VR BLOCK below into target("tmc_pc").
--
-- Note this is xmake, not CMake. There is no CMakeLists.txt anywhere in Picori.

option("vr")
    set_default(false)
    set_showmenu(true)
    set_category("Graphics")
    set_description("Build the OpenXR VR renderer (own Vulkan 1.1 device)")
option_end()

-- Phase 0/1 host tools. No graphics, no OpenXR, no game loop — these exist to
-- be run before any renderer work starts.
target("vr_meshtest")
    set_kind("binary")
    set_default(false)
    add_files("port/vr/vr_greedy_mesh.c")
    add_files("tools/src/vr_meshtest/main.c")
    add_includedirs("port/vr")
    set_languages("c11")
target_end()

--------------------------------------------------------------------------------
-- TMC_VR BLOCK — paste inside target("tmc_pc"), next to the existing
-- has_config("gpu_renderer") block.
--------------------------------------------------------------------------------
--
-- if has_config("vr") then
--     add_defines("TMC_VR=1")
--     add_requires("openxr_loader", "vulkansdk")
--     add_packages("openxr_loader", "vulkansdk")
--     add_includedirs("port/vr")
--
--     -- The port/vr/ modules:
--     add_files("port/vr/vr_world.c")
--     add_files("port/vr/vr_anchor.c")
--     add_files("port/vr/vr_greedy_mesh.c")
--     add_files("port/vr/vr_interp.c")
--     add_files("port/vr/vr_spritedump.c")
--
--     -- Salvage from the ray-tracing scaffold. Keep the device and the batcher,
--     -- drop RayTracingPipeline.cpp / DenoisePipeline.cpp and every shaders/*.rgen
--     -- — ray tracing is not available on Quest and gates nothing you need.
--     add_files("port/vk_rt_experiment/Engine.cpp")
--     add_files("port/vk_rt_experiment/RenderLayerManager.cpp")
--
--     -- Follow Picori's committed-SPIR-V convention (port/shaders/build.sh):
--     -- compile offline with glslangValidator, commit the .spv, embed via bin2c,
--     -- so users never need the Vulkan SDK installed.
--     add_rules("utils.bin2c", {extensions = {".spv"}, nozeroend = true})
--     add_files("port/vr/shaders/build/*.spv")
-- end
--
--------------------------------------------------------------------------------
-- Android / Quest notes (Phase 5, not yet)
--------------------------------------------------------------------------------
-- Picori already has a working Android path: is_plat("android") switches to
-- set_kind("shared") / set_basename("main") / arm64-v8a, and already passes
-- -Wl,-z,max-page-size=16384 for the Android 15 alignment rule. Do not add a
-- second android target; extend the existing one.
--
-- What actually changes for Quest:
--   * minSdk 21 -> 29 (Quest 2 is Android 10) in android/app/build.gradle
--   * implementation 'org.khronos.openxr:openxr_loader_for_android:<ver>'
--   * manifest: uses-feature android.hardware.vr.headtracking (required),
--     category com.oculus.intent.category.VR, drop screenOrientation
--   * xrInitializeLoaderKHR with a JavaVM* (NOT the JNIEnv*) before
--     xrCreateInstance
--   * do NOT hand-write -target aarch64-none-linux-android29 in cxflags; the
--     xmake ndk toolchain already sets the triple and you will get it twice

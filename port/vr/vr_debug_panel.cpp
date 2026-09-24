/*
 * vr_debug_panel.cpp — flat-PC integration glue. No Vulkan, no OpenXR.
 *
 * This is the single call site that brings the kit online inside Picori so you
 * can produce and inspect data before any renderer exists. Everything here runs
 * in the normal 2D build.
 *
 * Wiring — three edits total:
 *
 *   1. port/port_bios.c, inside VBlankIntrWait(), after the engine's tick work:
 *          #ifdef TMC_VR
 *          extern "C" void VrDebug_Tick(void);
 *          VrDebug_Tick();
 *          #endif
 *
 *   2. port/port_imgui_menu.cpp, inside the menu draw:
 *          #ifdef TMC_VR
 *          extern "C" void VrDebug_DrawPanel(void);
 *          VrDebug_DrawPanel();
 *          #endif
 *
 *   3. port/port_draw.c — expose the size table for the sprite dumper:
 *          const u8* Port_GetSizeTable(void) {
 *              return sSizeTableLoaded ? sSizeTable : NULL;
 *          }
 *
 * xmake: add_files("port/vr/vr_debug_panel.cpp") in the has_config("vr") block.
 */

#include "vr_world.h"
#include "vr_anchor.h"
#include "vr_interp.h"

extern "C" {
#include "entity.h"
#include "room.h"
/* NOT player.h: it declares `SetItemAnim(ItemBehavior* this, ...)` and `this`
 * is a reserved word in C++, so the header is unusable from a .cpp. We only
 * need the player's Entity, and PlayerEntity begins with `Entity base` at
 * offset 0 (player.h:8), so declaring the symbol as an Entity reads that
 * member. Safe because the declaration has C linkage: the linker matches on
 * name alone and the layout at offset 0 is identical. */
extern Entity gPlayerEntity;
}

#include "imgui.h"
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

extern "C" void VrDump_SpriteRange(const char* outDir, u16 spriteIndex,
                                   u8 first, u8 last, u32 paletteBank,
                                   const char* tag);

extern RoomControls gRoomControls;
/* gEntities is GenericEntity[MAX_ENTITIES], declared by entity.h.
 * GenericEntity begins with `Entity base`. */
#define VR_ENT(i) (&gEntities[(i)].base)

/* Link is NOT gEntities[0]. gEntities is the pool of non-player entities;
 * the player lives in its own global (player.h:648), and PlayerEntity also
 * begins with `Entity base`. Reading slot 0 as Link put the marker on
 * whatever enemy or object happened to occupy that slot. */
#define VR_LINK() (&gPlayerEntity)

/* ---- state --------------------------------------------------------------- */

static char sOutDir[256] = "vrdump";
static int  sAutoDumpRooms = 0;
static u8   sLastDumpedArea = 0xFF, sLastDumpedRoom = 0xFF;
static int  sDumpedCount = 0;

/* Sprite dump driver. The dumper reads OBJ VRAM, so it only sees frames the
 * engine has DMA'd in. Driving frameIndex on a live entity is what makes the
 * rest resident. Without this, most frames come out blank. */
static int  sSpriteDumpActive = 0;
static int  sSpriteSlot = 0;        /* gEntities index to drive; 0 is Link */
static int  sSpriteFrameFirst = 0, sSpriteFrameLast = 0;
static int  sSpriteFrameCur = 0;
static int  sSpriteSettleFrames = 0;
static char sSpriteTag[32] = "south";

/* Directions map to AnimationState: IdleNorth 0, IdleEast 2, IdleSouth 4,
 * IdleWest 6. Dump one tag at a time and carve from the set. */
static const char* kDirTags[4] = { "north", "east", "south", "west" };
static const u8    kDirStates[4] = { 0, 2, 4, 6 };

static void EnsureOutDir(void) {
#if defined(_WIN32)
    _mkdir(sOutDir);
#else
    mkdir(sOutDir, 0755);
#endif
}

/* ---- per-tick ------------------------------------------------------------ */

extern "C" void VrDebug_Tick(void) {
    VrWorld_Tick();

    /* Pass 0 for the XrTime stamp: no session in the flat build, so the
     * interpolator falls back to snapping. Interpolation only matters once a
     * headset is driving the present rate. */
    VrInterp_OnTick(0);

    if (gVrWorld.valid) {
        float linkX = (float)VR_LINK()->x.HALF.HI;
        float linkY = (float)VR_LINK()->y.HALF.HI;
        VrAnchor_Tick(linkX, linkY, 1.0f / 60.0f);
    }

    /* Only latch the ids once a dump of a REAL room has succeeded. Latching on
     * a zero-size snapshot is what wrote 0x0 files and then never retried. */
    if (sAutoDumpRooms && gVrWorld.valid &&
        gVrWorld.cellsW > 0 && gVrWorld.cellsH > 0) {
        if (gVrWorld.area != sLastDumpedArea || gVrWorld.room != sLastDumpedRoom) {
            EnsureOutDir();
            if (VrWorld_Dump(sOutDir)) {
                sDumpedCount++;
                sLastDumpedArea = gVrWorld.area;
                sLastDumpedRoom = gVrWorld.room;
            }
        }
    }

    /* Step the sprite dump one frame per tick, letting the engine load each
     * frame's tiles before we rasterize it. */
    if (sSpriteDumpActive) {
        Entity* e = VR_ENT(sSpriteSlot);
        if (e->kind == 0) {
            sSpriteDumpActive = 0;
        } else if (sSpriteSettleFrames > 0) {
            sSpriteSettleFrames--;
        } else {
            e->frameIndex = (u8)sSpriteFrameCur;
            EnsureOutDir();
            VrDump_SpriteRange(sOutDir, e->spriteIndex,
                               (u8)sSpriteFrameCur, (u8)sSpriteFrameCur,
                               0, sSpriteTag);
            sSpriteFrameCur++;
            sSpriteSettleFrames = 2;   /* let the DMA land before the next */
            if (sSpriteFrameCur > sSpriteFrameLast) sSpriteDumpActive = 0;
        }
    }
}

/* ---- panel --------------------------------------------------------------- */

extern "C" void VrDebug_DrawPanel(void) {
    if (!ImGui::Begin("VR Kit (flat)")) { ImGui::End(); return; }

    ImGui::InputText("output dir", sOutDir, sizeof(sOutDir));

    /* ---- world ---- */
    if (ImGui::CollapsingHeader("World", ImGuiTreeNodeFlags_DefaultOpen)) {
        if (!gVrWorld.valid) {
            ImGui::TextUnformatted("no snapshot yet");
        } else {
            ImGui::Text("area %u  room %u  rev %u",
                        gVrWorld.area, gVrWorld.room, gVrWorld.revision);
            ImGui::Text("origin %u,%u   %ux%u px   %ux%u cells",
                        gVrWorld.originX, gVrWorld.originY,
                        gVrWorld.width, gVrWorld.height,
                        gVrWorld.cellsW, gVrWorld.cellsH);
            ImGui::Text("layers: bottom %s  top %s",
                        gVrWorld.layer[0].present ? "yes" : "no",
                        gVrWorld.layer[1].present ? "yes" : "no");

            /* A room <= 240x160 never scrolls, because Scroll1 clamps to
             * [origin, origin + width - 0xF0]. Those rooms are already
             * perfectly stable even under naive scroll-following. */
            if (gVrWorld.width <= 240 && gVrWorld.height <= 160)
                ImGui::TextUnformatted("single screen: engine camera never scrolls");

            if (ImGui::Button("Dump this room")) {
                EnsureOutDir();
                if (VrWorld_Dump(sOutDir)) sDumpedCount++;
            }
            ImGui::SameLine();
            bool autoDump = sAutoDumpRooms != 0;
            if (ImGui::Checkbox("auto-dump on room change", &autoDump))
                sAutoDumpRooms = autoDump ? 1 : 0;
            ImGui::Text("rooms dumped: %d", sDumpedCount);
        }
    }

    /* ---- top-down grid ---- */
    if (gVrWorld.valid && ImGui::CollapsingHeader("Collision grid")) {
        /* Many interiors put the playfield on LAYER_TOP and leave the bottom
         * layer as under-floor, which reads as a sea of "unclassified" with a
         * void hole where the room actually is. Follow Link by default. */
        static int layer = -1;
        int linkLayer = (VR_LINK()->collisionLayer == COL_LAYER_TOP) ? 1 : 0;
        if (layer < 0) layer = linkLayer;
        ImGui::RadioButton("bottom", &layer, 0); ImGui::SameLine();
        ImGui::RadioButton("top", &layer, 1);   ImGui::SameLine();
        if (ImGui::SmallButton("follow Link")) layer = linkLayer;
        ImGui::Text("Link is on %s (collisionLayer = 0x%02X)",
                    linkLayer ? "TOP" : "BOTTOM",
                    (unsigned)VR_LINK()->collisionLayer);

        /* Emptiness is a property of the LAYER, not of a cell. A walkable cell
         * whose metatile index happens to be 0 is floor; only a layer that is
         * empty everywhere is void. Testing per-cell painted Link's carpet
         * black in area 32 room 0. */
        int nonEmpty = 0;
        for (int cy = 0; cy < gVrWorld.cellsH && !nonEmpty; cy++)
            for (int cx = 0; cx < gVrWorld.cellsW; cx++)
                if (VrWorld_TileIndex(layer, cx, cy) || VrWorld_Collision(layer, cx, cy)) {
                    nonEmpty = 1; break;
                }
        const int layerEmpty = !nonEmpty;
        if (layerEmpty) ImGui::TextUnformatted("this layer is empty");

        const float cell = 7.0f;
        ImDrawList* dl = ImGui::GetWindowDrawList();
        ImVec2 p0 = ImGui::GetCursorScreenPos();

        for (int cy = 0; cy < gVrWorld.cellsH; cy++) {
            for (int cx = 0; cx < gVrWorld.cellsW; cx++) {
                u8 c = VrWorld_Collision(layer, cx, cy);
                ImU32 col;
                if (layerEmpty)            col = IM_COL32(24, 24, 28, 255);
                else if (c == 0x00)        col = IM_COL32(110, 160, 90, 255);
                else if (c == 0x0F)        col = IM_COL32(150, 130, 110, 255);
                else if (c == 0x21)        col = IM_COL32(18, 18, 20, 255);
                else if (c == 0x24 || c == 0x25 || c == 0x30)
                                           col = IM_COL32(60, 110, 190, 255);
                else                       col = IM_COL32(150, 130, 110, 255);
                dl->AddRectFilled(
                    ImVec2(p0.x + cx * cell, p0.y + cy * cell),
                    ImVec2(p0.x + (cx + 1) * cell - 1, p0.y + (cy + 1) * cell - 1),
                    col);
            }
        }
        /* Link's cell, so you can confirm the grid is oriented correctly. */
        int lx = (VR_LINK()->x.HALF.HI - gVrWorld.originX) / 16;
        int ly = (VR_LINK()->y.HALF.HI - gVrWorld.originY) / 16;
        if (lx >= 0 && ly >= 0 && lx < gVrWorld.cellsW && ly < gVrWorld.cellsH)
            dl->AddRect(ImVec2(p0.x + lx * cell - 1, p0.y + ly * cell - 1),
                        ImVec2(p0.x + (lx + 1) * cell, p0.y + (ly + 1) * cell),
                        IM_COL32(255, 80, 80, 255), 0.0f, 0, 2.0f);

        ImGui::Dummy(ImVec2(gVrWorld.cellsW * cell, gVrWorld.cellsH * cell));
        ImGui::TextUnformatted("green walkable, brown solid(0x0F), blue water, grey unclassified, "
                               "red box = Link");
    }

    /* ---- entities: the z check ---- */
    if (ImGui::CollapsingHeader("Entities")) {
        ImGui::TextUnformatted("Jump and watch z. It should go NEGATIVE as Link "
                               "rises: the draw path adds z to screen Y and "
                               "screen Y grows downward. Confirm before "
                               "committing to worldY = -z.");
        if (ImGui::BeginTable("ents", 8, ImGuiTableFlags_Borders |
                                          ImGuiTableFlags_ScrollY,
                              ImVec2(0, 200))) {
            ImGui::TableSetupColumn("#");   ImGui::TableSetupColumn("kind");
            ImGui::TableSetupColumn("id");  ImGui::TableSetupColumn("type");
            ImGui::TableSetupColumn("x");   ImGui::TableSetupColumn("y");
            ImGui::TableSetupColumn("z");   ImGui::TableSetupColumn("layer");
            ImGui::TableHeadersRow();
            {   /* Link first, labelled P — he is not in gEntities. */
                Entity* e = VR_LINK();
                ImGui::TableNextRow();
                ImGui::TableNextColumn(); ImGui::TextUnformatted("P");
                ImGui::TableNextColumn(); ImGui::Text("%u", e->kind);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->id);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->type);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->x.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->y.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->z.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->collisionLayer);
            }
            for (int i = 0; i < VR_MAX_TRACKED; i++) {
                Entity* e = VR_ENT(i);
                if (e->kind == 0) continue;
                ImGui::TableNextRow();
                ImGui::TableNextColumn(); ImGui::Text("%d", i);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->kind);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->id);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->type);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->x.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->y.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%d", e->z.HALF.HI);
                ImGui::TableNextColumn(); ImGui::Text("%u", e->collisionLayer);
            }
            ImGui::EndTable();
        }
    }

    /* ---- sprite dump ---- */
    if (ImGui::CollapsingHeader("Sprite dump (Phase 0)")) {
        ImGui::SliderInt("entity slot", &sSpriteSlot, 0, VR_MAX_TRACKED - 1);
        Entity* e = VR_ENT(sSpriteSlot);
        if (e->kind == 0) {
            ImGui::TextUnformatted("slot empty");
        } else {
            ImGui::Text("spriteIndex %u   animState %u   frameIndex %u",
                        e->spriteIndex, e->animationState, e->frameIndex);
            ImGui::InputInt("first frame", &sSpriteFrameFirst);
            ImGui::InputInt("last frame", &sSpriteFrameLast);

            for (int d = 0; d < 4; d++) {
                if (d) ImGui::SameLine();
                char label[32];
                snprintf(label, sizeof(label), "dump %s", kDirTags[d]);
                if (ImGui::Button(label)) {
                    e->animationState = kDirStates[d];
                    snprintf(sSpriteTag, sizeof(sSpriteTag), "%s", kDirTags[d]);
                    sSpriteFrameCur = sSpriteFrameFirst;
                    sSpriteSettleFrames = 4;
                    sSpriteDumpActive = 1;
                }
            }
            if (sSpriteDumpActive)
                ImGui::Text("dumping %s frame %d / %d",
                            sSpriteTag, sSpriteFrameCur, sSpriteFrameLast);
            ImGui::TextUnformatted("Dump north, east and south, then run:\n"
                                   "  hull_carve.py verify <dir> --sprite N");
        }
    }

    /* ---- anchor ---- */
    if (ImGui::CollapsingHeader("Anchor (stabilized camera)")) {
        static int preset = 0;
        const char* names[] = { "Tabletop 0.005", "Diorama 0.010", "Life 0.100" };
        const float scales[] = { VR_SCALE_TABLETOP, VR_SCALE_DIORAMA, VR_SCALE_LIFE };
        if (ImGui::Combo("scale", &preset, names, 3))
            VrAnchor_SetScale(scales[preset]);

        ImGui::Text("origin  %.1f, %.1f", gVrAnchor.worldOrigin[0],
                                          gVrAnchor.worldOrigin[2]);
        ImGui::Text("follow  %s",
                    gVrAnchor.follow == VR_FOLLOW_STATIC   ? "STATIC"   :
                    gVrAnchor.follow == VR_FOLLOW_BLINK    ? "BLINK"    : "ATTACHED");
        ImGui::Text("fade    %.2f", VrAnchor_FadeAmount());
        if (ImGui::Button("centre on room")) VrAnchor_CentreOnRoom();

        ImGui::TextUnformatted(
            "Checklist items 1-6 in docs/03 are all testable here, flat.\n"
            "Trigger InitScreenShake and confirm origin does not move: shake\n"
            "writes only to bgSettings->xOffset/yOffset, never to scroll.");
    }

    ImGui::End();
}

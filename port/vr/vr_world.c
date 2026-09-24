/*
 * vr_world.c — Phase 1 world-model capture.
 *
 * Add to the tmc_pc target in xmake.lua:
 *     add_files("port/vr/vr_world.c")
 * and call VrWorld_Tick() once per tick, after UpdateScroll().
 *
 * Dump format ("TMCR", version 1, little-endian):
 *
 *   char   magic[4]      "TMCR"
 *   u32    version       1
 *   u8     area, room
 *   u16    originX, originY, width, height, cellsW, cellsH
 *   u8     layerPresent[2]
 *   -- per layer, both always written (zeroed when absent):
 *   u16    tileIndex[4096]
 *   u8     collision[4096]
 *   u8     actTile[4096]
 *   u16    tileType[2048]
 *   u16    subTile[8192]       (v2+; TL,TR,BL,BR per tile)
 *   u16    bgControl          (v3+; BGCNT — char_base lives in bits 2-3)
 * then, v3+ only, after the layer blocks:
 *   u16    subTileMap[2][0x4000]   gMapData{Bottom,Top}Special: the engine's
 *                                  own 128x128 position -> tilemap entry map
 *
 * Version 2 appends three sections. Geometry alone gives you grey boxes and an
 * empty world; these are what make it Hyrule:
 *
 *   u16    entityCount
 *   per entity (18 bytes):
 *     u8   kind, id, type, direction, collisionLayer, frameIndex
 *     s32  x, y, z            (Q16.16 world coords, NOT screen-folded)
 *   u16    bgPalette[256]     (gBgPltt — the LIVE hardware palette, POST-fade)
 * then, v4+ only, at the very end:
 *   u16    srcPalette[256]    (gPaletteBuffer — the fade SOURCE, unfaded)
 *   u8     fadeActive         (gFadeControl.active at capture time; non-zero
 *                              means gBgPltt was mid-fade and is unreliable)
 *   u32    vramBytes          (0x10000)
 *   u8     bgVram[0x10000]    (BG character data: the tileset art itself)
 *
 * ~107 KB per room, ~90 MB for the whole game. A v1 reader can stop after the
 * layer blocks and still be correct.
 */

#include "vr_world.h"
#include "map.h"
#include "room.h"
#include "entity.h"
#include "port_gba_mem.h"   /* gVram, gBgPltt */

/* player.h is C-only (it declares a parameter named `this`), but this file is
 * C, so including it is fine. */
#include "player.h"
#include "tileMap.h"       /* gMapData{Top,Bottom}Special */
#include "screen.h"        /* BgSettings.control */
#include "fade.h"          /* gFadeControl */
#include "main.h"          /* gPaletteBuffer */
#include <stdio.h>
#include <string.h>

extern RoomControls gRoomControls;
extern MapLayer gMapTop;
extern MapLayer gMapBottom;
extern u8 gUpdateVisibleTiles;   /* include/menu.h */

VrWorld gVrWorld;

static u8 sLastArea = 0xFF, sLastRoom = 0xFF;

/* Ticks to wait after a room change before snapshotting. The map arrays are
 * DMA'd in over several frames; capturing on the first tick can catch a
 * half-populated grid even when width/height are already set. */
#define VR_ROOM_SETTLE_TICKS 8
static int sSettle = 0;

static void CaptureLayer(VrMapLayer* dst, const MapLayer* src) {
    /* bgSettings == NULL is the engine's own "this layer is unused" signal —
     * UpdateScreenShake tests exactly this before touching a layer. */
    if (!src || src->bgSettings == NULL) {
        memset(dst, 0, sizeof(*dst));
        dst->present = 0;
        return;
    }
    memcpy(dst->tileIndex, src->mapData, sizeof(dst->tileIndex));
    memcpy(dst->collision, src->collisionData, sizeof(dst->collision));
    memcpy(dst->actTile, src->actTiles, sizeof(dst->actTile));
    memcpy(dst->tileType, src->tileTypes, sizeof(dst->tileType));
    memcpy(dst->subTile, src->subTiles, sizeof(dst->subTile));
    dst->bgControl = src->bgSettings->control;
    dst->present = 1;
}

void VrWorld_Capture(void) {
    /* Room dimensions are zero for a few ticks after the area/room ids change:
     * the engine bumps the ids first and only fills gRoomControls.width/height
     * once the room definition has loaded. Capturing in that window yields a
     * 0x0 snapshot with stale map arrays — 7 of the first 17 field dumps were
     * this. Refuse, and let VrWorld_Tick retry on a later tick. */
    if (gRoomControls.width == 0 || gRoomControls.height == 0)
        return;

    gVrWorld.area    = gRoomControls.area;
    gVrWorld.room    = gRoomControls.room;
    gVrWorld.originX = gRoomControls.origin_x;
    gVrWorld.originY = gRoomControls.origin_y;
    gVrWorld.width   = gRoomControls.width;
    gVrWorld.height  = gRoomControls.height;

    /* The array is always 64x64, but only width/16 x height/16 is live. Meshing
     * the whole grid wastes most of it — rooms are commonly far smaller. */
    gVrWorld.cellsW = gRoomControls.width  / 16;
    gVrWorld.cellsH = gRoomControls.height / 16;
    if (gVrWorld.cellsW > VR_MAP_DIM) gVrWorld.cellsW = VR_MAP_DIM;
    if (gVrWorld.cellsH > VR_MAP_DIM) gVrWorld.cellsH = VR_MAP_DIM;

    CaptureLayer(&gVrWorld.layer[0], &gMapBottom);
    CaptureLayer(&gVrWorld.layer[1], &gMapTop);

    /* Take the engine's own expanded maps rather than reimplementing
     * RenderMapLayerToSubTileMap + GetSafeTileSetIndex + the special-tile
     * path. gMapDataBottomSpecial is MAY_ALIAS and doubles as
     * gFileSelectState, but by the time a room is loaded it holds the map. */
    memcpy(gVrWorld.subTileMap[0], gMapDataBottomSpecial,
           sizeof(gVrWorld.subTileMap[0]));
    memcpy(gVrWorld.subTileMap[1], gMapDataTopSpecial,
           sizeof(gVrWorld.subTileMap[1]));

    gVrWorld.revision++;
    gVrWorld.valid = 1;
}

void VrWorld_Tick(void) {
    int roomChanged = (gRoomControls.area != sLastArea) ||
                      (gRoomControls.room != sLastRoom);

    /* gUpdateVisibleTiles is the engine's own "tiles changed, redraw" flag,
     * set by SetTile/SetTileType and by Scroll1 when the screenblock rolls.
     * We read it without clearing it — the engine owns the clear. */
    if (roomChanged) {
        /* Restart the settle countdown; don't latch the ids until a capture
         * actually succeeds, so a refused capture is retried next tick. */
        sSettle = VR_ROOM_SETTLE_TICKS;
    }

    if (sSettle > 0) {
        sSettle--;
        return;
    }

    if (roomChanged || gUpdateVisibleTiles || !gVrWorld.valid) {
        u32 before = gVrWorld.revision;
        VrWorld_Capture();
        if (gVrWorld.revision != before) {
            sLastArea = gRoomControls.area;
            sLastRoom = gRoomControls.room;
        }
    }
}

u16 VrWorld_TileIndex(int layer, int cx, int cy) {
    if (layer < 0 || layer >= VR_LAYERS) return 0;
    if (cx < 0 || cy < 0 || cx >= VR_MAP_DIM || cy >= VR_MAP_DIM) return 0;
    return gVrWorld.layer[layer].tileIndex[cy * VR_MAP_DIM + cx];
}

u8 VrWorld_Collision(int layer, int cx, int cy) {
    if (layer < 0 || layer >= VR_LAYERS) return 0;
    if (cx < 0 || cy < 0 || cx >= VR_MAP_DIM || cy >= VR_MAP_DIM) return 0;
    return gVrWorld.layer[layer].collision[cy * VR_MAP_DIM + cx];
}

u16 VrWorld_TileType(int layer, int cx, int cy) {
    u16 idx = VrWorld_TileIndex(layer, cx, cy);
    /* Indices >= 0x4000 are "special" tiles handled through a separate table
     * (see FillActTileForLayer, src/scroll.c:876). Treat as untyped here and
     * let the classifier fall back to collision. */
    if (idx >= 0x4000 || idx >= VR_TILESET) return 0;
    if (layer < 0 || layer >= VR_LAYERS) return 0;
    return gVrWorld.layer[layer].tileType[idx];
}

void VrWorld_CellCentre(int cx, int cy, float* outX, float* outY) {
    if (outX) *outX = (float)gVrWorld.originX + (float)cx * 16.0f + 8.0f;
    if (outY) *outY = (float)gVrWorld.originY + (float)cy * 16.0f + 8.0f;
}

/* ---- dump ---------------------------------------------------------------- */

static void PutU16(FILE* f, u16 v) { fputc(v & 0xFF, f); fputc((v >> 8) & 0xFF, f); }
static void PutU32(FILE* f, u32 v) {
    fputc(v & 0xFF, f); fputc((v >> 8) & 0xFF, f);
    fputc((v >> 16) & 0xFF, f); fputc((v >> 24) & 0xFF, f);
}

int VrWorld_Dump(const char* dir) {
    if (!gVrWorld.valid) return 0;

    char path[512];
    snprintf(path, sizeof(path), "%s/room_%02u_%02u.tmcr",
             dir, (unsigned)gVrWorld.area, (unsigned)gVrWorld.room);
    FILE* f = fopen(path, "wb");
    if (!f) return 0;

    fwrite("TMCR", 1, 4, f);
    PutU32(f, 4);
    fputc(gVrWorld.area, f);
    fputc(gVrWorld.room, f);
    PutU16(f, gVrWorld.originX);
    PutU16(f, gVrWorld.originY);
    PutU16(f, gVrWorld.width);
    PutU16(f, gVrWorld.height);
    PutU16(f, gVrWorld.cellsW);
    PutU16(f, gVrWorld.cellsH);
    fputc((u8)gVrWorld.layer[0].present, f);
    fputc((u8)gVrWorld.layer[1].present, f);

    for (int L = 0; L < VR_LAYERS; L++) {
        const VrMapLayer* l = &gVrWorld.layer[L];
        for (int i = 0; i < VR_MAP_CELLS; i++) PutU16(f, l->tileIndex[i]);
        fwrite(l->collision, 1, VR_MAP_CELLS, f);
        fwrite(l->actTile,   1, VR_MAP_CELLS, f);
        for (int i = 0; i < VR_TILESET; i++) PutU16(f, l->tileType[i]);
        for (int i = 0; i < VR_TILESET * 4; i++) PutU16(f, l->subTile[i]);
        PutU16(f, l->bgControl);
    }

    /* the engine's own subtile maps */
    for (int L = 0; L < 2; L++)
        for (int i = 0; i < VR_SUBTILEMAP; i++)
            PutU16(f, gVrWorld.subTileMap[L][i]);

    /* ---- v2: entities ---------------------------------------------------
     * From the entity list, not OAM: world-space, with identity and with z on
     * its own axis. gPlayerEntity is written first, as index 0. */
    {
        u16 count = 1;   /* Link */
        for (int i = 0; i < MAX_ENTITIES; i++)
            if (gEntities[i].base.kind != 0) count++;
        PutU16(f, count);

        const Entity* list[MAX_ENTITIES + 1];
        int n = 0;
        list[n++] = &gPlayerEntity.base;
        for (int i = 0; i < MAX_ENTITIES; i++)
            if (gEntities[i].base.kind != 0) list[n++] = &gEntities[i].base;

        for (int i = 0; i < n; i++) {
            const Entity* e = list[i];
            fputc(e->kind, f);
            fputc(e->id, f);
            fputc(e->type, f);
            fputc(e->direction, f);
            fputc(e->collisionLayer, f);
            fputc(e->frameIndex, f);
            PutU32(f, (u32)e->x.WORD);
            PutU32(f, (u32)e->y.WORD);
            PutU32(f, (u32)e->z.WORD);
        }
    }

    /* ---- v2: BG palettes + BG character data ----------------------------
     * The tileset art, exactly as the room has it loaded. Without this the
     * voxel pass has geometry but nothing to texture it with, and the
     * procedural height detector has no pixels to measure. */
    for (int i = 0; i < 256; i++) PutU16(f, gBgPltt[i]);
    PutU32(f, 0x10000u);
    fwrite(gVram, 1, 0x10000u, f);

    /* ---- v4: the UNFADED palette + the fade state ------------------------
     * gBgPltt is PAL_RAM: the fade's DESTINATION (src/fade.c:146 writes
     * gPaletteBuffer -> PAL_RAM through the active fade function). Capturing
     * it mid-fade yields colours offset toward the fade colour — measured as
     * a uniform +2 per 5-bit channel against a reference capture. Write the
     * fade SOURCE as well, so a decoder always has true colours, plus the
     * fade state so it knows which to trust. */
    for (int i = 0; i < 256; i++) PutU16(f, gPaletteBuffer[i]);
    fputc((u8)gFadeControl.active, f);

    fclose(f);
    fprintf(stderr, "vr_world: dumped area %u room %u (%ux%u cells)%s -> %s\n",
            (unsigned)gVrWorld.area, (unsigned)gVrWorld.room,
            (unsigned)gVrWorld.cellsW, (unsigned)gVrWorld.cellsH,
            gFadeControl.active ? " [MID-FADE]" : "", path);
    return 1;
}

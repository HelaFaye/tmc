/*
 * vr_world.h — Phase 1 world-model capture.
 *
 * The world you voxelize is gMapTop and gMapBottom, not the PPU. Each is a
 * 64x64 grid of 16x16 metatiles in WORLD space, with collision and act-tile
 * annotations, resident in RAM, on two floors. The PPU only ever holds a
 * scrolling 240px screenblock that wraps every 256px of world.
 *
 * This module snapshots that model, tracks live edits, and dumps rooms to disk
 * so the classifier can be developed offline against real data (see
 * tools/room_explore.py). Only 4 of TMC's 88 collision values and ~20 of its
 * ~1700 tile types carry human-meaningful names, so the classifier has to be
 * derived from observation rather than read off an enum.
 */

#ifndef VR_WORLD_H
#define VR_WORLD_H

#include "global.h"

#ifdef __cplusplus
extern "C" {
#endif

#define VR_MAP_DIM   64                      /* metatiles per side */
#define VR_MAP_CELLS (VR_MAP_DIM * VR_MAP_DIM)
#define VR_TILESET   2048                    /* tileTypes entries per layer */
#define VR_LAYERS    2                       /* 0 = bottom, 1 = top */

typedef struct {
    u16 tileIndex[VR_MAP_CELLS];  /* from MapLayer.mapData */
    u8  collision[VR_MAP_CELLS];  /* from MapLayer.collisionData */
    u8  actTile[VR_MAP_CELLS];    /* from MapLayer.actTiles */
    u16 tileType[VR_TILESET];     /* tileIndex -> semantic type */
    /* tileIndex*4 + {0,1,2,3} = TL,TR,BL,BR as BG tilemap entries:
     * bits 0-9 char index, bit10 hFlip, bit11 vFlip, bits12-15 palette bank.
     * Without this the BG VRAM cannot be assembled into 16x16 metatiles. */
    u16 subTile[VR_TILESET * 4];
    /* BGCNT for this layer, from BgSettings.control (screen.h:16). The PPU
     * derives char_base = ((bgcnt >> 2) & 3) * 0x4000 (mode1.c:426) and the
     * colour mode from bit 7. Without it a decoder is guessing which 16 KB
     * block the character data lives in. */
    u16 bgControl;
    int present;                  /* 0 when the layer has no bgSettings */
} VrMapLayer;

#define VR_SUBTILEMAP 0x4000   /* 128x128 8x8 subtiles = the whole room */

typedef struct {
    u8  area, room;
    u16 originX, originY;         /* world-space room origin, in px */
    u16 width, height;            /* room extent, in px */
    u16 cellsW, cellsH;           /* width/16, height/16 — the live sub-rect */
    VrMapLayer layer[VR_LAYERS];

    /* The engine's OWN expanded subtile maps (gMapData{Top,Bottom}Special),
     * produced by RenderMapLayerToSubTileMap. This is the authoritative
     * position -> BG tilemap entry mapping: it has already resolved
     * GetSafeTileSetIndex, including the >= 0x4000 special-tile path that
     * needs mapDataOriginal. Rendering from this matches the game exactly,
     * with no reimplementation of the lookup. */
    u16 subTileMap[2][VR_SUBTILEMAP];

    u32 revision;                 /* bumps on every capture; mesh-cache key */
    int valid;
} VrWorld;

/* The live snapshot. Read-only for everyone outside vr_world.c. */
extern VrWorld gVrWorld;

/* Call once per game tick, after UpdateScroll(). Re-captures when the room
 * changed or gUpdateVisibleTiles fired; cheap no-op otherwise. */
void VrWorld_Tick(void);

/* Force a capture regardless of change detection. */
void VrWorld_Capture(void);

/* Write the current snapshot to <dir>/room_<area>_<room>.tmcr.
 * Format is documented at the top of vr_world.c and parsed by room_explore.py. */
int VrWorld_Dump(const char* dir);

/* Convenience accessors. cx/cy are metatile coords, 0..cellsW/H-1.
 * Out-of-range returns 0, which is a real tile index — check bounds yourself
 * where it matters. */
u16 VrWorld_TileIndex(int layer, int cx, int cy);
u8  VrWorld_Collision(int layer, int cx, int cy);
u16 VrWorld_TileType(int layer, int cx, int cy);

/* World-space centre of a metatile cell, in game pixels. */
void VrWorld_CellCentre(int cx, int cy, float* outX, float* outY);

#ifdef __cplusplus
}
#endif

#endif /* VR_WORLD_H */

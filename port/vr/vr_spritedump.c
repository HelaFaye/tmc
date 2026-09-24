/*
 * vr_spritedump.c — Phase 0 sprite frame dumper.
 *
 * Drop into port/vr/ in a Project Picori fork. Add to the tmc_pc target:
 *     add_files("port/vr/vr_spritedump.c")
 *
 * Purpose
 * -------
 * Rasterize any (spriteIndex, frameIndex) pair to a PNG plus a JSON sidecar
 * carrying the frame's origin and tight bounding box. Those PNGs are the input
 * to tools/hull_carve.py, which answers the two questions Phase 0 exists to
 * answer:
 *
 *   1. Is West just mirrored East?
 *   2. Do S/N/E frame indices describe the same pose?
 *
 * Why instrument the port instead of writing a standalone ROM reader: Picori
 * already resolves gFrameObjLists, the 240-byte size table and the OBJ palette
 * per region at runtime (port_rom.c:1517, port_draw.c:79). Re-deriving those
 * offsets offline is a week of work that buys nothing.
 *
 * Coordinate convention
 * ---------------------
 * Pieces carry signed offsets from a nominal frame origin. We rasterize into a
 * canvas large enough for any frame, keep the origin at canvas centre, and
 * report the tight bbox in the sidecar. The carver registers views by bbox TOP,
 * not by canvas or by the nominal 16x16 box — multi-piece frames overflow that
 * box constantly.
 */

#include "global.h"
#include <stdbool.h>   /* port_rom.h uses bool but does not include this */
#include "port_rom.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

/* Picori/engine symbols we borrow. */
extern u32 gFrameObjLists[];
extern u16 gObjPltt[];      /* 256 entries, BGR555 */
extern u8  gVram[];         /* OBJ char data lives at 0x10000 */

u32 Port_FrameObjListsSizeForRegion(void);
u32 Port_FrameObjCountForRegion(void);

/* The 240-byte size/clip table is static inside port_draw.c. Expose it there:
 *     const u8* Port_GetSizeTable(void) { return sSizeTableLoaded ? sSizeTable : NULL; }
 * and declare it in port_draw.h. Three lines, no behaviour change. */
const u8* Port_GetSizeTable(void);

#define DUMP_CANVAS   128           /* px; origin at (64,64) */
#define DUMP_ORIGIN   (DUMP_CANVAS / 2)
#define OBJ_VRAM_BASE 0x10000       /* byte offset into gVram of OBJ char data */

/* GBA OBJ shape/size -> {w,h}. shape = attr0[15:14], size = attr1[15:14]. */
static const u8 kObjDims[3][4][2] = {
    /* square     */ { {  8,  8 }, { 16, 16 }, { 32, 32 }, { 64, 64 } },
    /* horizontal */ { { 16,  8 }, { 32,  8 }, { 32, 16 }, { 64, 32 } },
    /* vertical   */ { {  8, 16 }, {  8, 32 }, { 16, 32 }, { 32, 64 } },
};

typedef struct {
    u8 r, g, b, a;
} Rgba;

/* BGR555 -> RGBA8. Index 0 of any OBJ palette bank is transparent. */
static Rgba DecodeColor(u16 bgr555) {
    Rgba c;
    c.r = (u8)(((bgr555 >>  0) & 0x1F) * 255 / 31);
    c.g = (u8)(((bgr555 >>  5) & 0x1F) * 255 / 31);
    c.b = (u8)(((bgr555 >> 10) & 0x1F) * 255 / 31);
    c.a = 255;
    return c;
}

/* Mirrors LookupFrameData in port_draw.c. Self-relative offset chain:
 *   off1 = gFrameObjLists[spriteIndex]
 *   off2 = u32 at (base + off1 + frameIndex*4)
 *   data = base + off2
 * All bounds checks retained — a truncated ROM will otherwise walk off. */
static const u8* LookupFrame(u16 spriteIndex, u8 frameIndex) {
    const size_t size = Port_FrameObjListsSizeForRegion();
    const u8* base = (const u8*)gFrameObjLists;

    if ((u32)spriteIndex >= Port_FrameObjCountForRegion()) return NULL;

    u32 off1 = gFrameObjLists[spriteIndex];
    if ((size_t)off1 > size - sizeof(u32)) return NULL;

    size_t entry = (size_t)off1 + (size_t)frameIndex * sizeof(u32);
    if (entry > size - sizeof(u32)) return NULL;

    u32 off2;
    memcpy(&off2, base + entry, sizeof(off2));
    if ((size_t)off2 >= size) return NULL;

    return base + off2;
}

/* Blit one OBJ piece. Tiles are 4bpp, 32 bytes each, 1D-mapped.
 * GBA nibble order: the LOW nibble is the LEFT pixel. */
static void BlitPiece(Rgba* canvas,
                      s32 px, s32 py,         /* top-left in canvas space */
                      u32 tileBase,
                      u32 w, u32 h,
                      u32 paletteBank,
                      int hFlip, int vFlip) {
    const u32 tilesWide = w / 8;
    const u8* chars = &gVram[OBJ_VRAM_BASE];

    for (u32 row = 0; row < h; row++) {
        for (u32 col = 0; col < w; col++) {
            u32 sx = hFlip ? (w - 1 - col) : col;
            u32 sy = vFlip ? (h - 1 - row) : row;

            u32 tile   = tileBase + (sy / 8) * tilesWide + (sx / 8);
            u32 inX    = sx & 7, inY = sy & 7;
            u32 byteIx = tile * 32u + inY * 4u + (inX >> 1);

            if (byteIx >= 0x8000u) continue;           /* OBJ VRAM is 32 KB */

            u8 packed = chars[byteIx];
            u8 idx    = (inX & 1) ? (packed >> 4) : (packed & 0x0F);
            if (idx == 0) continue;                    /* transparent */

            s32 dx = px + (s32)col, dy = py + (s32)row;
            if (dx < 0 || dy < 0 || dx >= DUMP_CANVAS || dy >= DUMP_CANVAS) continue;

            canvas[dy * DUMP_CANVAS + dx] =
                DecodeColor(gObjPltt[paletteBank * 16u + idx]);
        }
    }
}

/* Rasterize a frame. Returns piece count, or -1 if the frame is absent.
 * `bbox` receives {minX, minY, maxX, maxY} in canvas space, or all -1 if empty.
 *
 * Note we deliberately do NOT apply the screen-space clipping that
 * RenderSpritePieces performs (the `y >= 160` / `x >= viewWidth` rejects).
 * Those exist to keep the GBA's 128 OAM slots fed; offline we want the whole
 * frame regardless of where it would have landed on screen. */
int VrDump_RasterizeFrame(u16 spriteIndex, u8 frameIndex,
                          u32 paletteBank, Rgba* canvas, s32 bbox[4]) {
    memset(canvas, 0, sizeof(Rgba) * DUMP_CANVAS * DUMP_CANVAS);
    bbox[0] = bbox[1] = bbox[2] = bbox[3] = -1;

    const u8* data = LookupFrame(spriteIndex, frameIndex);
    if (!data) return -1;

    const u8* sizeTab = Port_GetSizeTable();
    if (!sizeTab) {
        fprintf(stderr, "vr_spritedump: size table not loaded\n");
        return -1;
    }

    u8 count = *data++;
    if (count == 0) return 0;

    /* Clamp against the end of the frame-obj blob, as RenderSpritePieces does. */
    {
        const u8* end = (const u8*)gFrameObjLists + Port_FrameObjListsSizeForRegion();
        size_t maxPieces = (size_t)(end - data) / 5u;
        if ((size_t)count > maxPieces) count = (u8)maxPieces;
    }

    s32 minX = DUMP_CANVAS, minY = DUMP_CANVAS, maxX = -1, maxY = -1;

    for (int i = 0; i < count; i++) {
        s8  xoff      = (s8)data[0];
        s8  yoff      = (s8)data[1];
        u8  shapeInfo = data[2];
        u8  tileLow   = data[3];
        u8  tileHigh  = data[4];
        data += 5;

        u32 shape = (shapeInfo & 0xC0) >> 6;
        u32 size  = (shapeInfo & 0x30) >> 4;
        int hFlip = (shapeInfo & 0x08) != 0;
        int vFlip = (shapeInfo & 0x04) != 0;
        if (shape > 2) continue;                       /* prohibited encoding */

        u32 w = kObjDims[shape][size][0];
        u32 h = kObjDims[shape][size][1];

        /* Anchor subtraction, matching RenderSpritePieces. Normal (non-affine)
         * mode only — offline we never rasterize the affine sub-tables. */
        u32 sizeIdx = (u32)(shapeInfo & 0xF0) >> 2;
        if (sizeIdx + 3 >= 240u) continue;
        s32 ax = (s32)sizeTab[sizeIdx + 0];
        s32 ay = (s32)sizeTab[sizeIdx + 1];

        s32 px = DUMP_ORIGIN + (s32)xoff - ax;
        s32 py = DUMP_ORIGIN + (s32)yoff - ay;

        u32 tileBase = (u32)tileLow | ((u32)tileHigh << 8);
        tileBase &= 0x3FF;

        BlitPiece(canvas, px, py, tileBase, w, h, paletteBank, hFlip, vFlip);

        if (px < minX) minX = px;
        if (py < minY) minY = py;
        if (px + (s32)w - 1 > maxX) maxX = px + (s32)w - 1;
        if (py + (s32)h - 1 > maxY) maxY = py + (s32)h - 1;
    }

    /* Tighten the bbox to actually-opaque pixels; piece extents are generous. */
    {
        s32 tMinX = DUMP_CANVAS, tMinY = DUMP_CANVAS, tMaxX = -1, tMaxY = -1;
        for (s32 y = 0; y < DUMP_CANVAS; y++)
            for (s32 x = 0; x < DUMP_CANVAS; x++)
                if (canvas[y * DUMP_CANVAS + x].a) {
                    if (x < tMinX) tMinX = x;
                    if (y < tMinY) tMinY = y;
                    if (x > tMaxX) tMaxX = x;
                    if (y > tMaxY) tMaxY = y;
                }
        if (tMaxX >= 0) { minX = tMinX; minY = tMinY; maxX = tMaxX; maxY = tMaxY; }
        else            { minX = minY = maxX = maxY = -1; }
    }

    bbox[0] = minX; bbox[1] = minY; bbox[2] = maxX; bbox[3] = maxY;
    return count;
}

/* Minimal uncompressed-PNG writer (stored deflate blocks). Avoids pulling a
 * dependency into the port for a debug tool. Not fast; doesn't need to be. */
static u32 Crc32(const u8* p, size_t n, u32 crc) {
    static u32 tab[256]; static int init = 0;
    if (!init) {
        for (u32 i = 0; i < 256; i++) {
            u32 c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            tab[i] = c;
        }
        init = 1;
    }
    crc = ~crc;
    while (n--) crc = tab[(crc ^ *p++) & 0xFF] ^ (crc >> 8);
    return ~crc;
}

static void PutBE32(FILE* f, u32 v) {
    fputc((v >> 24) & 0xFF, f); fputc((v >> 16) & 0xFF, f);
    fputc((v >>  8) & 0xFF, f); fputc((v >>  0) & 0xFF, f);
}

static void WriteChunk(FILE* f, const char* type, const u8* data, u32 len) {
    PutBE32(f, len);
    u32 crc = Crc32((const u8*)type, 4, 0);
    if (len) crc = Crc32(data, len, crc);
    fwrite(type, 1, 4, f);
    if (len) fwrite(data, 1, len, f);
    PutBE32(f, crc);
}

static int WritePng(const char* path, const Rgba* px, u32 w, u32 h) {
    FILE* f = fopen(path, "wb");
    if (!f) return 0;

    fwrite("\x89PNG\r\n\x1a\n", 1, 8, f);

    u8 ihdr[13];
    ihdr[0] = (w >> 24) & 0xFF; ihdr[1] = (w >> 16) & 0xFF;
    ihdr[2] = (w >>  8) & 0xFF; ihdr[3] = w & 0xFF;
    ihdr[4] = (h >> 24) & 0xFF; ihdr[5] = (h >> 16) & 0xFF;
    ihdr[6] = (h >>  8) & 0xFF; ihdr[7] = h & 0xFF;
    ihdr[8] = 8; ihdr[9] = 6; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    WriteChunk(f, "IHDR", ihdr, 13);

    /* raw scanlines, filter byte 0 each */
    size_t rawLen = (size_t)h * (1 + (size_t)w * 4);
    u8* raw = (u8*)malloc(rawLen);
    if (!raw) { fclose(f); return 0; }
    for (u32 y = 0; y < h; y++) {
        u8* row = raw + (size_t)y * (1 + (size_t)w * 4);
        row[0] = 0;
        memcpy(row + 1, &px[(size_t)y * w], (size_t)w * 4);
    }

    /* zlib stream: 0x78 0x01, stored deflate blocks, adler32 */
    size_t zcap = rawLen + (rawLen / 65535 + 1) * 5 + 64;
    u8* z = (u8*)malloc(zcap);
    if (!z) { free(raw); fclose(f); return 0; }
    size_t zi = 0;
    z[zi++] = 0x78; z[zi++] = 0x01;
    size_t off = 0;
    while (off < rawLen) {
        size_t n = rawLen - off; if (n > 65535) n = 65535;
        int last = (off + n >= rawLen);
        z[zi++] = (u8)last;
        z[zi++] = (u8)(n & 0xFF);       z[zi++] = (u8)((n >> 8) & 0xFF);
        z[zi++] = (u8)(~n & 0xFF);      z[zi++] = (u8)((~n >> 8) & 0xFF);
        memcpy(z + zi, raw + off, n); zi += n; off += n;
    }
    u32 a = 1, b = 0;
    for (size_t i = 0; i < rawLen; i++) { a = (a + raw[i]) % 65521; b = (b + a) % 65521; }
    u32 adler = (b << 16) | a;
    z[zi++] = (adler >> 24) & 0xFF; z[zi++] = (adler >> 16) & 0xFF;
    z[zi++] = (adler >> 8) & 0xFF;  z[zi++] = adler & 0xFF;

    WriteChunk(f, "IDAT", z, (u32)zi);
    WriteChunk(f, "IEND", NULL, 0);

    free(z); free(raw); fclose(f);
    return 1;
}

/* Dump a contiguous frame range for one sprite.
 *
 * IMPORTANT: this reads OBJ VRAM, so it only sees frames whose graphics the
 * engine has actually DMA'd in. Call it with the relevant entity live and its
 * animation driven across the range — a debug ImGui panel that steps
 * entity->frameIndex and calls this each tick is the least-effort driver.
 * Frames whose tiles aren't resident come out blank; the carver flags them. */
void VrDump_SpriteRange(const char* outDir, u16 spriteIndex,
                        u8 first, u8 last, u32 paletteBank, const char* tag) {
    Rgba* canvas = (Rgba*)malloc(sizeof(Rgba) * DUMP_CANVAS * DUMP_CANVAS);
    if (!canvas) return;

    char manifestPath[512];
    snprintf(manifestPath, sizeof(manifestPath), "%s/sprite_%u_%s.json",
             outDir, (unsigned)spriteIndex, tag ? tag : "x");
    FILE* mf = fopen(manifestPath, "w");
    if (!mf) { free(canvas); return; }

    fprintf(mf, "{\n  \"sprite\": %u,\n  \"tag\": \"%s\",\n"
                "  \"canvas\": %d,\n  \"origin\": %d,\n  \"frames\": [\n",
            (unsigned)spriteIndex, tag ? tag : "x", DUMP_CANVAS, DUMP_ORIGIN);

    int wrote = 0;
    for (u32 fi = first; fi <= last; fi++) {
        s32 bbox[4];
        int pieces = VrDump_RasterizeFrame(spriteIndex, (u8)fi, paletteBank, canvas, bbox);
        if (pieces < 0) continue;

        char png[512];
        snprintf(png, sizeof(png), "%s/sprite_%u_%s_%03u.png",
                 outDir, (unsigned)spriteIndex, tag ? tag : "x", (unsigned)fi);
        if (!WritePng(png, canvas, DUMP_CANVAS, DUMP_CANVAS)) continue;

        if (wrote++) fprintf(mf, ",\n");
        fprintf(mf,
            "    { \"frame\": %u, \"pieces\": %d, \"file\": \"sprite_%u_%s_%03u.png\", "
            "\"bbox\": [%d, %d, %d, %d] }",
            (unsigned)fi, pieces, (unsigned)spriteIndex, tag ? tag : "x", (unsigned)fi,
            bbox[0], bbox[1], bbox[2], bbox[3]);
    }

    fprintf(mf, "\n  ]\n}\n");
    fclose(mf);
    free(canvas);

    fprintf(stderr, "vr_spritedump: sprite %u [%s] frames %u..%u -> %d PNGs in %s\n",
            (unsigned)spriteIndex, tag ? tag : "x",
            (unsigned)first, (unsigned)last, wrote, outDir);
}

/* ---- headless auto-driver ------------------------------------------------
 *
 * The dumper reads OBJ VRAM, so it only ever sees frames the engine has
 * already DMA'd in. Dumping a range cold yields mostly blanks. This drives a
 * live entity through the range, one frame per tick with a settle gap, so the
 * engine loads each frame's tiles before we rasterize it.
 *
 * Used by the ImGui panel interactively and by port_repro_roomcap.c headlessly
 * (TMC_ROOMCAP_SPRITES), so Phase 0 can run without a human at the keyboard.
 */

#include "player.h"

/* AnimationState direction bases: IdleNorth/East/South/West. */
static const u8 kVrDirStates[4] = { 0, 2, 4, 6 };
static const char* const kVrDirTags[4] = { "north", "east", "south", "west" };

static struct {
    int      active;
    int      dirSlot;        /* 0..3 index into kVrDirStates */
    int      dirsRemaining;  /* bitmask of directions still to do */
    u32      frame, first, last;
    int      settle;
    char     outDir[256];
    Entity*  target;
    u32      dumped;
    /* The driver owns its manifest. VrDump_SpriteRange opens the manifest
     * with "w", so calling it once per frame (as this driver did) truncated
     * the file every tick and left a manifest describing only the LAST frame
     * — 164 PNGs on disk, one entry listed, and hull_carve.py reporting the
     * dumps as missing. */
    FILE*    mf;
    int      wroteEntry;
    u16      spriteIndex;
} sAuto;

static void VrDump_AutoCloseManifest(void) {
    if (!sAuto.mf) return;
    fprintf(sAuto.mf, "\n  ]\n}\n");
    fclose(sAuto.mf);
    sAuto.mf = NULL;
}

static void VrDump_AutoOpenManifest(int dirSlot) {
    VrDump_AutoCloseManifest();
    char path[512];
    snprintf(path, sizeof(path), "%s/sprite_%u_%s.json",
             sAuto.outDir, (unsigned)sAuto.spriteIndex, kVrDirTags[dirSlot]);
    sAuto.mf = fopen(path, "w");
    sAuto.wroteEntry = 0;
    if (sAuto.mf) {
        fprintf(sAuto.mf,
                "{\n  \"sprite\": %u,\n  \"tag\": \"%s\",\n"
                "  \"canvas\": %d,\n  \"origin\": %d,\n  \"frames\": [\n",
                (unsigned)sAuto.spriteIndex, kVrDirTags[dirSlot],
                DUMP_CANVAS, DUMP_ORIGIN);
    }
}

void VrDump_StartAuto(const char* outDir, Entity* target,
                      u32 first, u32 last, int dirMask) {
    if (!outDir || !target) return;
    snprintf(sAuto.outDir, sizeof(sAuto.outDir), "%s", outDir);
    sAuto.target = target;
    sAuto.first = first;
    sAuto.last = last;
    sAuto.frame = first;
    sAuto.dirsRemaining = dirMask ? dirMask : 0xF;
    sAuto.dirSlot = -1;
    sAuto.settle = 8;
    sAuto.dumped = 0;
    sAuto.mf = NULL;
    sAuto.spriteIndex = (u16)target->spriteIndex;
    sAuto.active = 1;
}

int VrDump_AutoBusy(void) { return sAuto.active; }
u32 VrDump_AutoCount(void) { return sAuto.dumped; }

/* Call once per game tick. Returns 1 while still working. */
int VrDump_AutoTick(void) {
    if (!sAuto.active) return 0;
    Entity* e = sAuto.target;
    if (!e || e->kind == 0) { sAuto.active = 0; return 0; }

    if (sAuto.dirSlot < 0 || sAuto.frame > sAuto.last) {
        /* advance to the next requested direction */
        int next = -1;
        for (int d = (sAuto.dirSlot < 0 ? 0 : sAuto.dirSlot + 1); d < 4; d++)
            if (sAuto.dirsRemaining & (1 << d)) { next = d; break; }
        if (next < 0) { VrDump_AutoCloseManifest(); sAuto.active = 0; return 0; }
        sAuto.dirSlot = next;
        VrDump_AutoOpenManifest(next);
        e->animationState = kVrDirStates[next];
        sAuto.frame = sAuto.first;
        sAuto.settle = 8;
        return 1;
    }

    if (sAuto.settle > 0) { sAuto.settle--; return 1; }

    e->frameIndex = (u8)sAuto.frame;
    {
        /* Rasterize + write the PNG + append ONE manifest entry. */
        Rgba* canvas = (Rgba*)malloc(sizeof(Rgba) * DUMP_CANVAS * DUMP_CANVAS);
        if (canvas) {
            s32 bbox[4];
            int pieces = VrDump_RasterizeFrame(sAuto.spriteIndex, (u8)sAuto.frame,
                                               0, canvas, bbox);
            if (pieces >= 0) {
                char png[512];
                const char* tag = kVrDirTags[sAuto.dirSlot];
                snprintf(png, sizeof(png), "%s/sprite_%u_%s_%03u.png",
                         sAuto.outDir, (unsigned)sAuto.spriteIndex, tag,
                         (unsigned)sAuto.frame);
                if (WritePng(png, canvas, DUMP_CANVAS, DUMP_CANVAS) && sAuto.mf) {
                    if (sAuto.wroteEntry) fprintf(sAuto.mf, ",\n");
                    fprintf(sAuto.mf,
                            "    { \"frame\": %u, \"pieces\": %d, "
                            "\"file\": \"sprite_%u_%s_%03u.png\", "
                            "\"bbox\": [%d, %d, %d, %d] }",
                            (unsigned)sAuto.frame, pieces,
                            (unsigned)sAuto.spriteIndex, tag, (unsigned)sAuto.frame,
                            bbox[0], bbox[1], bbox[2], bbox[3]);
                    sAuto.wroteEntry = 1;
                    sAuto.dumped++;
                }
            }
            free(canvas);
        }
    }
    sAuto.frame++;
    sAuto.settle = 2;
    return 1;
}

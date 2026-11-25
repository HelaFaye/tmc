#ifndef PORT_TARGET_H
#define PORT_TARGET_H

/*
 * Build-target properties for the native ports, derived from the target
 * macro the build sets. PC builds define none of these.
 *
 *   TMC_EMBEDDED  a 32-bit target without SDL, threads or OpenMP; ROM data
 *                 comes from the ROM image or the pager, not the PC asset tree
 *   TMC_BE        a big-endian CPU: GBA little-endian data needs swapping
 *   TMC_ROM_WINDOW the ROM appears at a fixed address and the linker places
 *                 ROM-resident symbols (gMapData, the const ROM tables) in
 *                 it, so they are neither copied to RAM nor compiled in
 *
 * Targets: TMC_FXCG (Casio fx-CG50, SH-4A) and TMC_N64 (the earlier N64
 * experiment). N64-only bus quirks stay under TMC_N64.
 */
#if defined(TMC_FXCG) || defined(TMC_N64)
#ifndef TMC_EMBEDDED
#define TMC_EMBEDDED 1
#endif
#ifndef TMC_BE
#define TMC_BE 1
#endif
#endif

#if defined(TMC_FXCG) && !defined(TMC_ROM_WINDOW)
#define TMC_ROM_WINDOW 1
#endif

/* Tilemap entries -- VRAM screen blocks and the gBG*Buffer copies the game
 * DMAs there -- keep the GBA's little-endian bytes on every target, since
 * ROM tilemaps are copied in byte for byte and the PPU reads them that way.
 * Game code that stores or loads one entry as a u16 converts with
 * TILE_LE16 (its own inverse; nothing on little-endian targets). */
#ifdef TMC_BE
#define TILE_LE16(v) ((unsigned short)__builtin_bswap16((unsigned short)(v)))
#else
#define TILE_LE16(v) ((unsigned short)(v))
#endif

/* Records the game reads in place from the ROM (room headers, entity
 * lists, exits): GCC then loads and stores their integer fields in the
 * GBA's little-endian order on any host. Taking the address of such a
 * field is a compile error, which keeps native-order pointers out. */
#ifdef TMC_BE
#define GBA_LE_STRUCT __attribute__((scalar_storage_order("little-endian")))
#else
#define GBA_LE_STRUCT
#endif

/* Two adjacent u8 fields the GBA code reads as one u16 (*(u16*)&lo). */
#ifdef TMC_BE
#define GBA_U16_PAIR(lo, hi) ((unsigned short)((unsigned char)(lo) | ((unsigned char)(hi) << 8)))
#else
#define GBA_U16_PAIR(lo, hi) (*(unsigned short*)&(lo))
#endif

#endif /* PORT_TARGET_H */

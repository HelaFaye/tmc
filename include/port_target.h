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

#endif /* PORT_TARGET_H */

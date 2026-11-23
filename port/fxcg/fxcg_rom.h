/* fx-CG50: the game's view of the 16 MB ROM (docs/fx-cg50-d1-pager.md).
 *
 * With TMC_FXCG_D1_PAGER, gRomData points at the 16 MB window at 0x20000000
 * and fxcg_tlbh.S maps its pages on demand from a page cache filled from
 * Picori.pak. Without it, Fxcg_RomInit() fails closed. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Open the page pak and point gRomData/gRomSize at the window. False on
 * failure, with a reason in Fxcg_RomError(). */
bool Fxcg_RomInit(void);
const char* Fxcg_RomError(void);

#ifdef TMC_FXCG_D1_PAGER
/* Written by the TLB-miss handler before an upcall. */
struct FxcgRomSave {
    uint32_t spc, ssr, page;
    uint32_t r[8]; /* r1-r7, then r0 */
    uint32_t pr, mach, macl;
};

typedef struct {
    uint32_t fetches, evictions, invalidations, upcalls;
} FxcgRomStats;

bool Fxcg_RomOpenPak(const char* path);
/* Bring ROM page `page` into a frame (pinned frames are never evicted);
 * returns the frame or NULL with Fxcg_RomError() set. */
uint8_t* Fxcg_RomFetch(uint32_t page, bool pin);
void Fxcg_RomGetStats(FxcgRomStats* out);
/* `frames` (even, 0 = all) limits the cache, for tests. */
bool Fxcg_RomInitWith(unsigned frames);
#ifdef FXCG_ROM_HOST
bool Fxcg_RomInitHost(const char* pakPath, unsigned frames);
#endif
#endif

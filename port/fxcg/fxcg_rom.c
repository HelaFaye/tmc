/* fx-CG50 ROM window (docs/fx-cg50-d1-pager.md).
 *
 * Without TMC_FXCG_D1_PAGER this file keeps the fail-closed placeholder the
 * hardware-tested startup path expects. With it:
 *
 * gRomData = 0x20000000, a 16 MB window with nothing mapped. The first touch
 * of a page misses in the UTLB; fxcg_tlbh.S maps it with OS syscall 0x09 if
 * fxcg_rom_phys[page] names a resident frame, and otherwise upcalls
 * Fxcg_RomServiceFault() in normal context, which reads the page from
 * Picori.pak in storage memory, inflates it into a frame and enters it.
 *
 * Frames are split by page parity: the SH-4A operand cache is virtually
 * indexed, so a frame written through its P1 address and read through the
 * window must share cache index bit 12. The pak's baseline pages are loaded
 * at start and never evicted; the rest rotate first-in first-out per parity.
 * Evicting a frame drops its old window page from the UTLB (fxcg_p2_tlb.S,
 * run from P2) before the frame is reused.
 *
 * FXCG_ROM_HOST builds only the pak reader and cache for the host test;
 * FXCG_ROM_SYNTHETIC replaces the pak with generated pages for the
 * D1-PAGER-PROBE hardware test. */
#include "fxcg_rom.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef TMC_FXCG_D1_PAGER

static const char* sError = "";

bool Fxcg_RomInit(void) {
    sError = "ROM window (plan D1) not built yet.";
    return false;
}

const char* Fxcg_RomError(void) {
    return sError;
}

#else /* TMC_FXCG_D1_PAGER */

#include <cglink/inflate.h>

#define PAGE_SHIFT 12
#define PAGE_SIZE (1u << PAGE_SHIFT)
#define ROM_PAGES 4096u
#define NO_PAGE 0xFFFFu
#define FXCG_ROM_WINDOW 0x20000000u /* fxcg_tlbh.S and fxcg.ld */

#ifndef FXCG_ROM_FRAMES
#define FXCG_ROM_FRAMES 352 /* 1.38 MB; fxcg.ld asserts sysram still fits */
#endif
#ifndef FXCG_ROM_PAK
#define FXCG_ROM_PAK "/Picori.pak"
#endif

extern unsigned char* gRomData;
extern unsigned int gRomSize;

/* Read by fxcg_tlbh.S from P1 inside the TLB-miss handler. */
uint32_t fxcg_rom_phys[ROM_PAGES] __attribute__((aligned(32)));
struct FxcgRomSave fxcg_rom_save __attribute__((aligned(32)));

static const char* sError = "";
static char sErrorBuf[64];

/* ---- page source: Picori.pak (PCPK v1, scripts/fxcg/build_page_pak.py) ---- */
static FILE* sPak;
static uint32_t sRomSize = ROM_PAGES * PAGE_SIZE;
static uint32_t sDataStart;
static uint16_t sSlot[ROM_PAGES]; /* ROM page -> pak slot, or NO_PAGE */
static uint32_t* sOffset;
static uint16_t* sStored;
static uint16_t* sBaseline;
static uint32_t sBaselineCount;
static uint8_t sPacked[PAGE_SIZE];

static uint32_t Le32(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint16_t Le16(const uint8_t* p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

static bool Fail(const char* message) {
    sError = message;
    return false;
}

bool Fxcg_RomOpenPak(const char* path) {
    uint8_t h[28];
    sPak = fopen(path, "rb");
    if (!sPak)
        return Fail("Picori.pak not found in storage memory.");
    if (fread(h, 1, sizeof(h), sPak) != sizeof(h) || memcmp(h, "PCPK", 4) != 0 || Le16(h + 4) != 1 ||
        h[6] != 1 || h[7] != PAGE_SHIFT)
        return Fail("Picori.pak is not a PCPK v1 page pak.");
    sRomSize = Le32(h + 8);
    uint32_t pages = Le32(h + 12), rooms = Le32(h + 16), baseline = Le32(h + 20), manifests = Le32(h + 24);
    if (sRomSize > ROM_PAGES * PAGE_SIZE || pages > ROM_PAGES || baseline > pages)
        return Fail("Picori.pak is larger than a 16 MB ROM.");
    sOffset = malloc(pages * sizeof(uint32_t));
    sStored = malloc(pages * sizeof(uint16_t));
    sBaseline = malloc((baseline ? baseline : 1) * sizeof(uint16_t));
    if (!sOffset || !sStored || !sBaseline)
        return Fail("Out of memory for the pak index.");
    memset(sSlot, 0xFF, sizeof(sSlot));
    for (uint32_t i = 0; i < pages; i++) {
        uint8_t e[8];
        if (fread(e, 1, 8, sPak) != 8)
            return Fail("Picori.pak is truncated.");
        uint16_t romPage = Le16(e);
        if (romPage >= ROM_PAGES || Le16(e + 2) > PAGE_SIZE)
            return Fail("Picori.pak has a bad page entry.");
        sSlot[romPage] = (uint16_t)i;
        sStored[i] = Le16(e + 2);
        sOffset[i] = Le32(e + 4);
    }
    for (uint32_t i = 0; i < baseline; i++) {
        uint8_t e[2];
        if (fread(e, 1, 2, sPak) != 2 || Le16(e) >= pages)
            return Fail("Picori.pak has a bad baseline.");
        sBaseline[i] = Le16(e);
    }
    sBaselineCount = baseline;
    sDataStart = 28 + pages * 8 + baseline * 2 + rooms * 8 + manifests * 2;
    return true;
}

/* Fill `out` with ROM page `page`. */
static bool ReadPage(uint32_t page, uint8_t* out) {
#ifdef FXCG_ROM_SYNTHETIC
    /* D1-PAGER-PROBE: every word names its page and offset. */
    uint32_t* w = (uint32_t*)out;
    for (uint32_t i = 0; i < PAGE_SIZE / 4; i++)
        w[i] = (page << 16) ^ (i * 4u) ^ 0x5A000000u;
    return true;
#else
    uint16_t slot = page < ROM_PAGES ? sSlot[page] : NO_PAGE;
    if (slot == NO_PAGE) {
        snprintf(sErrorBuf, sizeof(sErrorBuf), "ROM page %u (0x%06X) is not in Picori.pak.", (unsigned)page,
                 (unsigned)(page << PAGE_SHIFT));
        return Fail(sErrorBuf);
    }
    uint16_t stored = sStored[slot];
    if (fseek(sPak, (long)(sDataStart + sOffset[slot]), SEEK_SET) != 0 ||
        fread(stored == PAGE_SIZE ? out : sPacked, 1, stored, sPak) != stored)
        return Fail("Reading Picori.pak failed.");
    if (stored != PAGE_SIZE && cglink_inflate(sPacked, stored, out, PAGE_SIZE) != (long)PAGE_SIZE)
        return Fail("A page in Picori.pak does not decompress.");
    return true;
#endif
}

/* ---- the page cache ---- */
static uint8_t sFrames[FXCG_ROM_FRAMES][PAGE_SIZE] __attribute__((aligned(2 * PAGE_SIZE)));
static uint16_t sFramePage[FXCG_ROM_FRAMES];
static uint8_t sFramePinned[FXCG_ROM_FRAMES];
static unsigned sFrameLimit = FXCG_ROM_FRAMES; /* the probe shrinks it */
static unsigned sNext[2];
static FxcgRomStats sStats;

static void InvalidatePage(uint32_t page);

static void ResetCache(void) {
    memset(fxcg_rom_phys, 0, sizeof(fxcg_rom_phys));
    for (unsigned f = 0; f < FXCG_ROM_FRAMES; f++) {
        sFramePage[f] = NO_PAGE;
        sFramePinned[f] = 0;
    }
    sNext[0] = sNext[1] = 0;
    memset(&sStats, 0, sizeof(sStats));
}

static uint32_t PhysOf(const void* p) {
    return (uint32_t)(uintptr_t)p & 0x1FFFF000u; /* P1 -> physical page */
}

/* Pick a frame for `page`: frames alternate parity, so frame f has
 * address bit 12 == (f & 1). Skips pinned frames. */
static int PickFrame(uint32_t page) {
    unsigned parity = page & 1u, perParity = sFrameLimit / 2;
    for (unsigned tries = 0; tries < perParity; tries++) {
        unsigned f = 2 * sNext[parity] + parity;
        sNext[parity] = (sNext[parity] + 1) % perParity;
        if (!sFramePinned[f])
            return (int)f;
    }
    return -1;
}

uint8_t* Fxcg_RomFetch(uint32_t page, bool pin) {
    if (page >= ROM_PAGES) {
        Fail("ROM access past 16 MB.");
        return NULL;
    }
    if (fxcg_rom_phys[page]) { /* already resident */
        for (unsigned f = 0; f < sFrameLimit; f++)
            if (sFramePage[f] == page) {
                sFramePinned[f] |= pin;
                return sFrames[f];
            }
    }
    int f = PickFrame(page);
    if (f < 0) {
        Fail("Every ROM cache frame is pinned.");
        return NULL;
    }
    if (sFramePage[f] != NO_PAGE) {
        fxcg_rom_phys[sFramePage[f]] = 0;
        InvalidatePage(sFramePage[f]);
        sStats.evictions++;
    }
    sFramePage[f] = NO_PAGE;
    if (!ReadPage(page, sFrames[f]))
        return NULL;
    sFramePage[f] = (uint16_t)page;
    sFramePinned[f] = pin;
    fxcg_rom_phys[page] = PhysOf(sFrames[f]);
    sStats.fetches++;
    return sFrames[f];
}

void Fxcg_RomGetStats(FxcgRomStats* out) {
    *out = sStats;
}

const char* Fxcg_RomError(void) {
    return sError;
}

#ifdef FXCG_ROM_HOST

static void InvalidatePage(uint32_t page) {
    (void)page;
}
bool Fxcg_RomInitHost(const char* pakPath, unsigned frames) {
    ResetCache();
    if (frames && frames <= FXCG_ROM_FRAMES)
        sFrameLimit = frames & ~1u;
    return Fxcg_RomOpenPak(pakPath);
}

#else /* the calculator */

extern void Fxcg_ShowMessage(const char* line1, const char* line2);
extern const uint8_t FxcgP2InvalTemplate[], FxcgP2InvalTemplateEnd[];

/* P2 copy of fxcg_p2_tlb.S. The buffer is in sysram; it is written and
 * executed through its uncached P2 alias. */
static uint8_t sP2Code[128] __attribute__((aligned(32)));
static void (*sP2Inval)(uint32_t pageVa);

static bool InstallP2(void) {
    size_t size = (size_t)(FxcgP2InvalTemplateEnd - FxcgP2InvalTemplate);
    uintptr_t p1 = (uintptr_t)sP2Code;
    if (size > sizeof(sP2Code) || (p1 >> 29) != 4)
        return Fail("P2 helper does not fit, or sysram is not P1.");
    volatile uint8_t* p2 = (volatile uint8_t*)(p1 | 0x20000000u);
    /* Drop any cached copy, write through P2, then sync both caches. */
    for (uintptr_t a = p1; a < p1 + sizeof(sP2Code); a += 32)
        __asm__ volatile("ocbp @%0" ::"r"(a) : "memory");
    for (size_t i = 0; i < size; i++)
        p2[i] = FxcgP2InvalTemplate[i];
    __asm__ volatile("synco" ::: "memory");
    for (uintptr_t a = p1 | 0x20000000u; a < (p1 | 0x20000000u) + sizeof(sP2Code); a += 32)
        __asm__ volatile("icbi @%0" ::"r"(a) : "memory");
    for (size_t i = 0; i < size; i++)
        if (p2[i] != FxcgP2InvalTemplate[i])
            return Fail("P2 helper copy does not read back.");
    sP2Inval = (void (*)(uint32_t))(p1 | 0x20000000u);
    return true;
}

static void InvalidatePage(uint32_t page) {
    uint32_t sr;
    __asm__ volatile("stc sr, %0" : "=r"(sr));
    __asm__ volatile("ldc %0, sr" ::"r"(sr | 0xF0u) : "memory"); /* IMASK = 15 */
    sP2Inval(FXCG_ROM_WINDOW + (page << PAGE_SHIFT));
    __asm__ volatile("ldc %0, sr" ::"r"(sr) : "memory");
    sStats.invalidations++;
}

/* Called by fxcg_tlbh.S's upcall, in normal context. */
void Fxcg_RomServiceFault(void) {
    uint32_t page = fxcg_rom_save.page;
    sStats.upcalls++;
    if (!Fxcg_RomFetch(page, false)) {
        Fxcg_ShowMessage("Picori: cannot read the ROM.", sError);
        exit(1);
    }
}

bool Fxcg_RomInitWith(unsigned frames) {
    ResetCache();
    if (frames && frames <= FXCG_ROM_FRAMES)
        sFrameLimit = frames & ~1u;
    if (!InstallP2())
        return false;
#ifndef FXCG_ROM_SYNTHETIC
    if (!Fxcg_RomOpenPak(FXCG_ROM_PAK))
        return false;
    /* Every room needs the baseline: load it now and keep it, leaving at
     * least a quarter of each parity's frames free for room pages. */
    unsigned pinned[2] = { 0, 0 }, pinLimit = (sFrameLimit / 2) * 3 / 4;
    for (uint32_t i = 0; i < sBaselineCount; i++) {
        uint32_t page = ROM_PAGES;
        for (uint32_t p = 0; p < ROM_PAGES; p++)
            if (sSlot[p] == sBaseline[i]) {
                page = p;
                break;
            }
        if (page == ROM_PAGES || pinned[page & 1] >= pinLimit)
            continue;
        if (!Fxcg_RomFetch(page, true))
            return false;
        pinned[page & 1]++;
    }
#endif
    gRomData = (unsigned char*)(uintptr_t)FXCG_ROM_WINDOW;
    gRomSize = sRomSize;
    return true;
}

bool Fxcg_RomInit(void) {
    return Fxcg_RomInitWith(0);
}

#endif /* FXCG_ROM_HOST */
#endif /* TMC_FXCG_D1_PAGER */

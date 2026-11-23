/* D1-PAGER-PROBE: staged hardware test of the D1 ROM-window pager.
 *
 * Same fxcg_tlbh.S (the replacement TLB-miss handler), fxcg_p2_tlb.S and
 * fxcg_rom.c as Picori built with TMC_FXCG_D1_PAGER, with generated pages
 * instead of Picori.pak (FXCG_ROM_SYNTHETIC: every word of ROM page p at
 * offset o is (p << 16) ^ o ^ 0x5A000000) and an 8-frame cache so eviction
 * starts early. Writes /D1-PAGER.TXT after every stage, so after a crash the
 * file shows the last stage that completed. See docs/fx-cg50-d1-pager.md. */
#include "fxcg_rom.h"

#include <gint/display.h>
#include <gint/gint.h>
#include <gint/keyboard.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define REPORT_PATH "/D1-PAGER.TXT"
#define WINDOW 0x20000000u
#define FRAMES 8

unsigned char* gRomData;
unsigned int gRomSize;
extern char rxbss[], sxbss[];
extern const uint8_t gint_tlbh_start[];
extern char gint_tlbh_size[];
extern uint32_t d1_regs_check(uint32_t addr, uint32_t* loaded);

static char sReport[4096];
static size_t sReportSize;
static int sFailures;
static int sSaveError;

static void Report(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(sReport + sReportSize, sizeof(sReport) - sReportSize, fmt, ap);
    va_end(ap);
    if (n > 0)
        sReportSize += (size_t)n < sizeof(sReport) - sReportSize ? (size_t)n : sizeof(sReport) - sReportSize - 1;
}

static int WriteReportOs(void* unused) {
    (void)unused;
    FILE* f = fopen(REPORT_PATH, "w");
    if (!f)
        return errno ? errno : EIO;
    int bad = fwrite(sReport, 1, sReportSize, f) != sReportSize;
    bad |= fclose(f) != 0;
    return bad ? (errno ? errno : EIO) : 0;
}

static void Save(void) {
    sSaveError = gint_world_switch(GINT_CALL(WriteReportOs, NULL));
}

/* Called by fxcg_rom.c when a page cannot be served. */
void Fxcg_ShowMessage(const char* line1, const char* line2) {
    Report("FATAL=%s / %s\n", line1, line2 ? line2 : "");
    Save();
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, line1);
    if (line2)
        dtext(4, 20, C_WHITE, line2);
    dupdate();
    getkey();
}

static uint32_t Expected(uint32_t page, uint32_t offset) {
    return (page << 16) ^ offset ^ 0x5A000000u;
}

static uint32_t ReadWindow(uint32_t page, uint32_t offset) {
    return *(volatile uint32_t*)(uintptr_t)(WINDOW + (page << 12) + offset);
}

static void Check(const char* what, uint32_t page, uint32_t offset, uint32_t got) {
    if (got != Expected(page, offset)) {
        Report("MISMATCH %s page=%u off=%03X got=%08X want=%08X\n", what, (unsigned)page, (unsigned)offset,
               (unsigned)got, (unsigned)Expected(page, offset));
        sFailures++;
    }
}

static void Stage(const char* name, int failuresBefore) {
    FxcgRomStats st;
    Fxcg_RomGetStats(&st);
    Report("STAGE %s %s fetches=%u evictions=%u invalidations=%u upcalls=%u\n", name,
           sFailures == failuresBefore ? "PASS" : "FAIL", (unsigned)st.fetches, (unsigned)st.evictions,
           (unsigned)st.invalidations, (unsigned)st.upcalls);
    Save();
}

/* Code-region data spread over many add-in pages, for stage 6. */
#define BIG_WORDS (48 * 1024) /* 192 KiB: about 48 add-in pages */
static const uint32_t sBig[BIG_WORDS] = { [0] = 0xB16B00B5u, [BIG_WORDS - 1] = 0x600DF00Du };

static void Run(void) {
    Report("== FXCG50 D1 PAGER PROBE ==\nFORMAT=1\nTARGET_OS_EXPECTED=3.20.0202\n");
    uint32_t vbr;
    __asm__ volatile("stc vbr, %0" : "=r"(vbr));
    size_t tlbhSize = (size_t)(uintptr_t)gint_tlbh_size;
    int installed = memcmp((const void*)(uintptr_t)(vbr + 0x400), gint_tlbh_start, tlbhSize) == 0;
    Report("VBR=%08X TLBH_SIZE=%u TLBH_INSTALLED=%d FRAMES=%d\n", (unsigned)vbr, (unsigned)tlbhSize, installed,
           FRAMES);
    if (!installed || tlbhSize > 0x200) {
        Report("RESULT=FAIL (handler not installed at VBR+0x400)\n");
        sFailures++;
        Save();
        return;
    }
    if (!Fxcg_RomInitWith(FRAMES)) {
        Report("RESULT=FAIL init: %s\n", Fxcg_RomError());
        sFailures++;
        Save();
        return;
    }
    Report("INIT=PASS gRomData=%08X\n", (unsigned)(uintptr_t)gRomData);
    Save();

    /* 1. Resident page: the handler maps it with syscall 0x09, no upcall. */
    int f0 = sFailures;
    if (!Fxcg_RomFetch(0, true))
        sFailures++;
    Check("resident", 0, 0x10, ReadWindow(0, 0x10));
    Stage("1_RESIDENT_MAP", f0);

    /* 2. Absent page: upcall, fetch, retry. */
    f0 = sFailures;
    Check("upcall", 1, 0x14, ReadWindow(1, 0x14));
    Stage("2_UPCALL", f0);

    /* 3. Registers across an upcall (page 2 is not resident yet). */
    f0 = sFailures;
    uint32_t loaded = 0, mask = d1_regs_check(WINDOW + (2u << 12) + 0x20, &loaded);
    Check("regs", 2, 0x20, loaded);
    Report("REGS_MISMATCH_MASK=%04X\n", (unsigned)mask);
    if (mask)
        sFailures++;
    Stage("3_REGISTERS", f0);

    /* 4. Page boundary: last word of page 3, first word of page 4. */
    f0 = sFailures;
    Check("boundary_end", 3, 0xFFC, ReadWindow(3, 0xFFC));
    Check("boundary_start", 4, 0x000, ReadWindow(4, 0x000));
    Stage("4_BOUNDARY", f0);

    /* 5. Eviction: 64 pages through 8 frames, forward then back. A stale
     *    UTLB entry after eviction would return another page's data. */
    f0 = sFailures;
    for (uint32_t p = 16; p < 80; p++)
        Check("evict_fwd", p, (p * 4) & 0xFFC, ReadWindow(p, (p * 4) & 0xFFC));
    for (uint32_t p = 80; p-- > 16;)
        Check("evict_back", p, (p * 8) & 0xFFC, ReadWindow(p, (p * 8) & 0xFFC));
    Stage("5_EVICTION", f0);

    /* 6. Interleave with add-in data misses (stock 0x0C path). */
    f0 = sFailures;
    for (uint32_t i = 0; i < 48; i++) {
        volatile uint32_t w = sBig[(i * 1024 + 7) % BIG_WORDS];
        if (w != 0) {
            Report("MISMATCH big[%u]=%08X\n", (unsigned)i, (unsigned)w);
            sFailures++;
        }
        uint32_t p = 100 + (i % 20);
        Check("interleave", p, i * 4, ReadWindow(p, i * 4));
    }
    if (sBig[0] != 0xB16B00B5u || sBig[BIG_WORDS - 1] != 0x600DF00Du)
        sFailures++;
    Stage("6_INTERLEAVE", f0);

    /* 7. Stress: 3000 pseudo-random reads over 256 pages. */
    f0 = sFailures;
    uint32_t x = 12345;
    for (int i = 0; i < 3000 && sFailures - f0 < 8; i++) {
        x = x * 1103515245u + 12345u;
        uint32_t p = 200 + ((x >> 8) & 255), off = (x >> 4) & 0xFFC;
        Check("stress", p, off, ReadWindow(p, off));
    }
    Stage("7_STRESS", f0);

    /* 8. The pinned page 0 survived everything. */
    f0 = sFailures;
    Check("pinned", 0, 0x40, ReadWindow(0, 0x40));
    Stage("8_PINNED", f0);

    Report("RESULT=%s FAILURES=%d\n", sFailures ? "FAIL" : "PASS", sFailures);
    Save();
}

int main(void) {
    memset(rxbss, 0, (size_t)sxbss);
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "D1 pager probe running...");
    dupdate();
    Run();
    dclear(C_BLACK);
    dtext(4, 2, C_WHITE, "D1 pager probe");
    if (sSaveError)
        dprint(4, 28, C_RED, "Could not save report: %d", sSaveError);
    dtext(4, 46, sFailures ? C_RED : C_GREEN, sFailures ? "FAIL: see D1-PAGER.TXT" : "PASS: D1-PAGER.TXT saved");
    dtext(4, 214, C_WHITE, "Press any key to exit");
    dupdate();
    getkey();
    return 0;
}

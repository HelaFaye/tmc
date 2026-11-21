/*
 * Read-only fx-CG50 SH-4A UTLB/ITLB snapshot.
 *
 * The memory-mapped TLB arrays are accessed only by the copied P2 template
 * in tlb_state_probe.S. That helper saves its P1 stack state before touching
 * the arrays, performs every array access and result-buffer write in P2, then
 * executes SYNCO + ICBI in P2 before returning to ordinary add-in code.
 *
 * This probe does not write MMU registers or TLB entries, install a custom
 * TLB handler, or map the planned ROM window. Gint's normal handler remains
 * active and may naturally refill add-in code pages while the app runs.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <gint/bfile.h>
#include <gint/cpu.h>
#include <gint/display.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <gint/keyboard.h>
#include <gint/keycodes.h>
#include <gint/mpu/mmu.h>

#define UTLB_COUNT 64u
#define ITLB_COUNT 4u
#define ENTRIES_PER_PAGE 16u

/* Both addresses are aliases of the previously tested upper-DRAM range. */
#define P2_SNAPSHOT_CODE ((uintptr_t)0xac7ff000u)
#define P2_SNAPSHOT_BUFFER ((uintptr_t)0xac7fe000u)
#define P2_TEST_PAGE_END ((uintptr_t)0xac800000u)
#define P2_SNAPSHOT_CODE_MAX 0x200u

/* UTLB pairs first, followed by ITLB pairs: (64 + 4) * 2 words. */
#define SNAPSHOT_WORDS ((UTLB_COUNT + ITLB_COUNT) * 2u)

#ifdef TMC_FXCG_TLB_SLOT_SWEEP
/* Absolute linker symbols for the add-in's complete mapped ROM image. */
extern char brom;
extern char srom;

static uintptr_t sSweepStart;
static uintptr_t sSweepBytes;
static uintptr_t sSweepLastPage;
static uint32_t sSweepPages;
static uint32_t sSweepChecksum;
static bool sSweepOk;

/* Cause ordinary read-side TLB misses for every mapped add-in ROM page.
 * Gint's stock handler and the OS page mapper remain in control. This code
 * never writes MMUCR, PTEH, PTEL, UTLB, ITLB, or the exception vector. */
static bool sweep_addin_rom_pages(void)
{
    const uintptr_t start = (uintptr_t)&brom;
    const uintptr_t bytes = (uintptr_t)&srom;
    const uintptr_t end = start + bytes;

    if (start != 0x00300000u || bytes == 0u || bytes > 0x00200000u ||
        end < start || end > 0x00500000u)
        return false;

    sSweepStart = start;
    sSweepBytes = bytes;
    sSweepLastPage = 0;
    sSweepPages = 0;
    sSweepChecksum = 0x544c4253u; /* "TLBS" */

    for (uintptr_t offset = 0; offset < bytes; offset += 0x1000u) {
        const uintptr_t address = start + offset;
        const volatile uint32_t *page_word =
            (const volatile uint32_t *)address;
        const uint32_t value = *page_word;

        sSweepChecksum = (sSweepChecksum << 5) ^
            (sSweepChecksum >> 27) ^ value ^ (uint32_t)address;
        sSweepLastPage = address;
        sSweepPages++;
    }

    return sSweepPages != 0u;
}
#endif

extern const uint8_t P2TlbSnapshotTemplate[];
extern const uint8_t P2TlbSnapshotTemplateEnd[];

typedef void (*P2TlbSnapshotFunction)(uint32_t *p2_output);

static uint32_t sUtlbAddr[UTLB_COUNT];
static uint32_t sUtlbData[UTLB_COUNT];
static uint32_t sItlbAddr[ITLB_COUNT];
static uint32_t sItlbData[ITLB_COUNT];
static uint32_t sMmucr;
static uint32_t sPteh;
static uint32_t sPtel;
static uint32_t sVbr;

/* Plain-text diagnostics are written to storage memory after the P2 reader
 * has returned to the normal add-in context. */
#define LOG_CAPACITY 4096u
static char sLog[LOG_CAPACITY];
static size_t sLogLength;
static bool sLogOverflow;
static int sLogResult;

static void log_emit(const char *text)
{
    size_t length = strlen(text);
    if (length > LOG_CAPACITY - sLogLength) {
        sLogOverflow = true;
        return;
    }
    memcpy(sLog + sLogLength, text, length);
    sLogLength += length;
}

static void log_emitf(const char *format, ...)
{
    char line[128];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(line, sizeof(line), format, args);
    va_end(args);

    if (length < 0 || (size_t)length >= sizeof(line)) {
        sLogOverflow = true;
        return;
    }
    log_emit(line);
}

static void build_log(bool snapshot_ok)
{
    sLogLength = 0;
    sLogOverflow = false;

#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    log_emit("== FXCG50 D1 TLB SLOT SWEEP ==\n");
#else
    log_emit("== FXCG50 D1 TLB SNAPSHOT ==\n");
#endif
    log_emit("FORMAT=1\n");
    log_emit(snapshot_ok ? "STATUS=OK\n" : "STATUS=FAIL\n");
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    log_emit(sSweepOk ? "SWEEP_STATUS=OK\n" : "SWEEP_STATUS=FAIL\n");
    log_emitf("SWEEP_START=%08lX SWEEP_BYTES=%08lX PAGES=%lu LAST=%08lX CHECKSUM=%08lX\n",
        (unsigned long)sSweepStart, (unsigned long)sSweepBytes,
        (unsigned long)sSweepPages, (unsigned long)sSweepLastPage,
        (unsigned long)sSweepChecksum);
#endif
    log_emitf("MMUCR=%08lX PTEH=%08lX PTEL=%08lX VBR=%08lX\n",
        (unsigned long)sMmucr, (unsigned long)sPteh,
        (unsigned long)sPtel, (unsigned long)sVbr);
    log_emitf("URB=%lu URC=%lu AT=%lu SV=%lu ASID=%02lX\n",
        (unsigned long)((sMmucr >> 18) & 0x3fu),
        (unsigned long)((sMmucr >> 10) & 0x3fu),
        (unsigned long)(sMmucr & 1u),
        (unsigned long)((sMmucr >> 8) & 1u),
        (unsigned long)(sPteh & 0xffu));

    if (snapshot_ok) {
        log_emit("[UTLB] index address data valid\n");
        for (unsigned i = 0; i < UTLB_COUNT; i++) {
            unsigned valid = ((sUtlbAddr[i] & 0x100u) &&
                              (sUtlbData[i] & 0x100u)) ? 1u : 0u;
            log_emitf("%02u %08lX %08lX %u\n", i,
                (unsigned long)sUtlbAddr[i],
                (unsigned long)sUtlbData[i], valid);
        }

        log_emit("[ITLB] index address data valid\n");
        for (unsigned i = 0; i < ITLB_COUNT; i++) {
            unsigned valid = ((sItlbAddr[i] & 0x100u) &&
                              (sItlbData[i] & 0x100u)) ? 1u : 0u;
            log_emitf("%u %08lX %08lX %u\n", i,
                (unsigned long)sItlbAddr[i],
                (unsigned long)sItlbData[i], valid);
        }
    } else {
        log_emit("DETAIL=P2 helper copy/size/readback validation failed\n");
    }

    if (!sLogOverflow && (sLogLength & 1u)) {
        if (sLogLength < LOG_CAPACITY)
            sLog[sLogLength++] = '\n'; /* BFile_Write requires an even byte count. */
        else
            sLogOverflow = true;
    }
}

static int save_log_file(void)
{
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    uint16_t const *path = u"\\\\fls0\\TLBSLOT.TXT";
#else
    uint16_t const *path = u"\\\\fls0\\TLBSNAP.TXT";
#endif
    if (sLogOverflow || sLogLength == 0 || sLogLength > (size_t)0x7fff)
        return -1000;

    /* Replacing a previous diagnostic is intentional; failure to remove an
     * existing file will be reported by BFile_Create instead of overwriting. */
    (void)BFile_Remove(path);

    int size = (int)sLogLength;
    int rc = BFile_Create(path, BFile_File, &size);
    if (rc < 0)
        return rc;

    int fd = BFile_Open(path, BFile_WriteOnly);
    if (fd < 0)
        return fd;

    rc = BFile_Write(fd, sLog, (int)sLogLength);
    int close_rc = BFile_Close(fd);
    if (rc < 0)
        return rc;
    if (close_rc < 0)
        return close_rc;
    return rc;
}

static bool snapshot(void)
{
    const size_t code_size = (size_t)(
        (uintptr_t)P2TlbSnapshotTemplateEnd -
        (uintptr_t)P2TlbSnapshotTemplate);

    if (code_size == 0 || code_size > P2_SNAPSHOT_CODE_MAX ||
        P2_SNAPSHOT_CODE + code_size > P2_TEST_PAGE_END ||
        P2_SNAPSHOT_BUFFER + SNAPSHOT_WORDS * sizeof(uint32_t) >
            P2_SNAPSHOT_CODE)
        return false;

    volatile uint8_t *destination =
        (volatile uint8_t *)P2_SNAPSHOT_CODE;

    for (size_t i = 0; i < code_size; i++)
        destination[i] = P2TlbSnapshotTemplate[i];

    /* Publish the copied template and invalidate every affected I-cache line. */
    __asm__ volatile("synco" ::: "memory");
    for (uintptr_t line = P2_SNAPSHOT_CODE;
         line < P2_SNAPSHOT_CODE + code_size; line += 32u)
        __asm__ volatile("icbi @%0" :: "r"(line) : "memory");

    for (size_t i = 0; i < code_size; i++) {
        if (destination[i] != P2TlbSnapshotTemplate[i])
            return false;
    }

    /* These control registers are read normally; only the TLB arrays need P2. */
    sMmucr = SH7305_MMU.MMUCR.lword;
    sPteh = SH7305_MMU.PTEH.lword;
    sPtel = SH7305_MMU.PTEL.lword;
    sVbr = cpu_getVBR();

    P2TlbSnapshotFunction read_arrays =
        (P2TlbSnapshotFunction)P2_SNAPSHOT_CODE;
    read_arrays((uint32_t *)P2_SNAPSHOT_BUFFER);

    /* The helper has already executed the required P2-to-non-P2 ICBI barrier. */
    __asm__ volatile("synco" ::: "memory");
    volatile uint32_t *words = (volatile uint32_t *)P2_SNAPSHOT_BUFFER;

    for (unsigned i = 0; i < UTLB_COUNT; i++) {
        sUtlbAddr[i] = words[2u * i];
        sUtlbData[i] = words[2u * i + 1u];
    }

    const unsigned itlb_offset = UTLB_COUNT * 2u;
    for (unsigned i = 0; i < ITLB_COUNT; i++) {
        sItlbAddr[i] = words[itlb_offset + 2u * i];
        sItlbData[i] = words[itlb_offset + 2u * i + 1u];
    }

    return true;
}

static void draw_utlb_page(unsigned page)
{
    const unsigned first = page * ENTRIES_PER_PAGE;

    dclear(C_BLACK);
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    dtext(4, 2, C_WHITE, "fx-CG50 TLB slot sweep (P2 read)");
#else
    dtext(4, 2, C_WHITE, "fx-CG50 UTLB snapshot (P2 read)");
#endif
    dprint(4, 14, C_WHITE, "MMUCR=%08X PTEH=%08X", sMmucr, sPteh);
    dprint(4, 25, C_WHITE, "PTEL=%08X VBR=%08X", sPtel, sVbr);
    dprint(4, 36, C_WHITE,
        "URB=%02u URC=%02u AT=%u SV=%u ASID=%02X PAGE=%u/4",
        (unsigned)((sMmucr >> 18) & 0x3f),
        (unsigned)((sMmucr >> 10) & 0x3f),
        (unsigned)(sMmucr & 1u),
        (unsigned)((sMmucr >> 8) & 1u),
        (unsigned)(sPteh & 0xffu), page + 1u);

    for (unsigned row = 0; row < ENTRIES_PER_PAGE; row++) {
        const unsigned index = first + row;
        const bool valid = (sUtlbAddr[index] & 0x100u) &&
                           (sUtlbData[index] & 0x100u);
        dprint(4, 49 + (int)row * 10, valid ? C_GREEN : C_WHITE,
            "%02u A=%08X D=%08X", index,
            sUtlbAddr[index], sUtlbData[index]);
    }

    if (sLogResult >= 0) {
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
        dtext(4, 214, C_GREEN, "FILE: TLBSLOT.TXT SAVED");
#else
        dtext(4, 214, C_GREEN, "FILE: TLBSNAP.TXT SAVED");
#endif
    } else {
        dprint(4, 214, C_RED, "FILE WRITE FAILED rc=%d", sLogResult);
    }
    dupdate();
}

static void draw_itlb_page(void)
{
    dclear(C_BLACK);
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    dtext(4, 2, C_WHITE, "fx-CG50 ITLB slot sweep (P2 read)");
#else
    dtext(4, 2, C_WHITE, "fx-CG50 ITLB snapshot (P2 read)");
#endif
    dprint(4, 14, C_WHITE, "MMUCR=%08X PTEH=%08X", sMmucr, sPteh);
    dprint(4, 25, C_WHITE, "PTEL=%08X VBR=%08X", sPtel, sVbr);
    dtext(4, 43, C_WHITE, "Instruction TLB: 4 entries");

    for (unsigned i = 0; i < ITLB_COUNT; i++) {
        const bool valid = (sItlbAddr[i] & 0x100u) &&
                           (sItlbData[i] & 0x100u);
        dprint(4, 64 + (int)i * 20, valid ? C_GREEN : C_WHITE,
            "%u A=%08X D=%08X", i, sItlbAddr[i], sItlbData[i]);
    }

    if (sLogResult >= 0) {
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
        dtext(4, 214, C_GREEN, "FILE: TLBSLOT.TXT SAVED");
#else
        dtext(4, 214, C_GREEN, "FILE: TLBSNAP.TXT SAVED");
#endif
    } else {
        dprint(4, 214, C_RED, "FILE WRITE FAILED rc=%d", sLogResult);
    }
    dupdate();
}

int main(int isappli, int optnum)
{
    (void)isappli;
    (void)optnum;

#ifdef TMC_FXCG_TLB_SLOT_SWEEP
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "fx-CG50 TLB slot sweep");
    dtext(4, 24, C_WHITE, "Reading each mapped add-in ROM page...");
    dtext(4, 44, C_WHITE, "Gint handles normal page faults");
    dupdate();
    sSweepOk = sweep_addin_rom_pages();
#endif

    bool snapshot_ok = snapshot();
    build_log(snapshot_ok);
    sLogResult = save_log_file();

    if (!snapshot_ok) {
        dclear(C_BLACK);
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
        dtext(4, 4, C_WHITE, "P2 TLB slot snapshot: FAIL");
#else
        dtext(4, 4, C_WHITE, "P2 TLB snapshot: FAIL");
#endif
        dtext(4, 24, C_WHITE, "Template copy/size check failed");
        if (sLogResult >= 0) {
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
            dtext(4, 48, C_GREEN, "Failure log saved: TLBSLOT.TXT");
#else
            dtext(4, 48, C_GREEN, "Failure log saved: TLBSNAP.TXT");
#endif
        } else {
            dprint(4, 48, C_RED, "Log write failed: rc=%d", sLogResult);
        }
#ifdef TMC_FXCG_TLB_SLOT_SWEEP
        dtext(4, 72, C_WHITE, "Normal page faults may alter TLB state");
#else
        dtext(4, 72, C_WHITE, "No MMU or TLB state was changed");
#endif
        dtext(4, 88, C_WHITE, "Press EXIT to close");
        dupdate();
        getkey();
        return 1;
    }

    unsigned page = 0;
    for (;;) {
        if (page < 4u)
            draw_utlb_page(page);
        else
            draw_itlb_page();

        key_event_t event = getkey_opt(GETKEY_NONE, NULL);
        if (event.type != KEYEV_DOWN)
            continue;

        if (event.key == KEY_EXIT)
            break;
        if (event.key == KEY_LEFT && page > 0u)
            page--;
        if (event.key == KEY_RIGHT && page < 4u)
            page++;
    }

    return 0;
}

/*
 * Controlled cold data-TLB miss/refill through the existing OS/Gint path.
 *
 * This diagnostic does NOT install an exception handler or call syscall 0x0c
 * or 0x09 directly. It selects a valid add-in page-table entry which is not
 * resident in the UTLB, then performs exactly one ordinary MOV.L data read
 * through that add-in virtual address. Gint's stock .gint.tlbh remains active.
 */
#include <gint/cpu.h>
#include <gint/display.h>
#include <gint/gint.h>
#include <gint/keyboard.h>
#include <gint/keycodes.h>
#include <gint/mpu/mmu.h>

#include <errno.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#define UTLB_COUNT              64u
#define PAGE_SIZE               4096u
#define PAGE_TABLE_SCAN_WORDS   256u
#define OS_PAGE_TABLE_ADDR      0x8c04cf0cu
#define OS_PAGE_COUNTER_ADDR    0x8c04d70cu
#define OS_ADDIN_MAPPED_ADDR    0x8c04cee1u
#define ADDIN_CODE_BASE         0x00300000u
#define OS_MAP_SYSCALL          0x00000009u
#define OS_TLBH_SYSCALL         0x0000000cu
#define OS_SYSCALL_DISPATCHER   0x80020070u
#define OS_FLASH_P1_BASE        0x80000000u
#define OS_FLASH_P1_LIMIT       0x82000000u
#define OS_MAP_SLOT_COUNT       55u
#define FLASH_PHYSICAL_END      0x02000000u
#define READ_OFFSET             0x00000100u
#define TLB_READ_MISS_EXPEVT    0x00000040u

#define P2_SNAPSHOT_CODE        ((uintptr_t)0xac7ff000u)
#define P2_SNAPSHOT_BUFFER      ((uintptr_t)0xac7fe000u)
#define P2_SNAPSHOT_PAGE_END    ((uintptr_t)0xac800000u)
#define P2_SNAPSHOT_CODE_MAX    0x200u
#define P2_SNAPSHOT_WORDS       ((UTLB_COUNT + 4u) * 2u)

#define REPORT_CAPACITY         3072u
#define REPORT_PATH             "/COLD-DATA-TLB-MISS.TXT"

extern char rxbss[], sxbss[];
extern char srom[]; /* Linker absolute symbol: mapped add-in image size. */
extern const uint8_t P2TlbSnapshotTemplate[];
extern const uint8_t P2TlbSnapshotTemplateEnd[];

struct ColdDataTlbCapture {
    uint32_t counter_before;
    uint32_t loaded_value;
    uint32_t counter_after;
    uint32_t expevt;
    uint32_t tea;
    uint32_t pteh;
};

extern void cold_data_tlb_load(uint32_t virtual_address,
                               struct ColdDataTlbCapture *capture);

typedef void (*P2TlbSnapshotFunction)(uint32_t *p2_output);

static uint32_t sUtlbAddr[UTLB_COUNT];
static uint32_t sUtlbData[UTLB_COUNT];
static int sP2SnapshotInstalled;
static struct ColdDataTlbCapture sCapture;
static uint32_t sMmucr;
static uint32_t sPtehBefore;
static uint32_t sSyscallTable;
static uint32_t sSyscall0cTarget;
static uint32_t sSyscall09Target;
static uint16_t sSyscall0cWords[4];
static uint32_t sAddinMapped;
static uintptr_t sImageBytes;
static uint32_t sCandidateRaw;
static uint32_t sCandidatePhysical;
static uintptr_t sCandidateVa;
static uintptr_t sAccessVa;
static uintptr_t sP1Alias;
static uint32_t sExpectedValue;
static uint32_t sCounterBefore;
static uint32_t sCounterAfterSnapshot;
static uint32_t sUtlbAddressMatch;
static uint32_t sUtlbDataMatch;
static int sCandidateIndex = -1;
static int sFoundSlot = -1;
static int sPreflightError;
static int sProbeError;
static int sSnapshotAfterOk;
static bool sColdBeforeRead;
static int sSaveError;
static char sReport[REPORT_CAPACITY];
static size_t sReportSize;

static uint32_t read_u32(uintptr_t address)
{
    return *(volatile const uint32_t *)address;
}

static uint16_t read_u16(uintptr_t address)
{
    return *(volatile const uint16_t *)address;
}

static uint8_t read_u8(uintptr_t address)
{
    return *(volatile const uint8_t *)address;
}

/* Read all P4 UTLB array entries only inside the copied privileged P2 helper.
 * Mask interrupts across the helper so an interrupt cannot branch out of P2
 * between the memory-mapped reads and the required SYNCO/ICBI transition. */
static int snapshot_utlb(void)
{
    if (!sP2SnapshotInstalled) {
        size_t code_size = (size_t)(
            (uintptr_t)P2TlbSnapshotTemplateEnd -
            (uintptr_t)P2TlbSnapshotTemplate);

        if (code_size == 0u || code_size > P2_SNAPSHOT_CODE_MAX ||
            P2_SNAPSHOT_CODE + code_size > P2_SNAPSHOT_PAGE_END ||
            P2_SNAPSHOT_BUFFER + P2_SNAPSHOT_WORDS * sizeof(uint32_t) >
                P2_SNAPSHOT_CODE)
            return -1;

        volatile uint8_t *destination = (volatile uint8_t *)P2_SNAPSHOT_CODE;
        for (size_t i = 0; i < code_size; i++)
            destination[i] = P2TlbSnapshotTemplate[i];

        __asm__ volatile("synco" ::: "memory");
        for (uintptr_t line = P2_SNAPSHOT_CODE;
             line < P2_SNAPSHOT_CODE + code_size; line += 32u)
            __asm__ volatile("icbi @%0" :: "r"(line) : "memory");

        for (size_t i = 0; i < code_size; i++) {
            if (destination[i] != P2TlbSnapshotTemplate[i])
                return -1;
        }
        sP2SnapshotInstalled = 1;
    }

    P2TlbSnapshotFunction read_arrays =
        (P2TlbSnapshotFunction)P2_SNAPSHOT_CODE;
    uint32_t original_sr;
    __asm__ volatile("stc sr,%0" : "=r"(original_sr));
    uint32_t masked_sr = original_sr | 0x000000f0u;
    __asm__ volatile("ldc %0,sr\n\tnop" :: "r"(masked_sr) : "memory");
    read_arrays((uint32_t *)P2_SNAPSHOT_BUFFER);
    __asm__ volatile("ldc %0,sr\n\tnop" :: "r"(original_sr) : "memory");

    __asm__ volatile("synco" ::: "memory");
    volatile const uint32_t *words =
        (volatile const uint32_t *)P2_SNAPSHOT_BUFFER;
    for (unsigned i = 0; i < UTLB_COUNT; i++) {
        sUtlbAddr[i] = words[2u * i];
        sUtlbData[i] = words[2u * i + 1u];
    }
    return 0;
}

static unsigned utlb_page_size(uint32_t data)
{
    unsigned code = (((data >> 7) & 1u) << 1) | ((data >> 4) & 1u);
    static const unsigned sizes[4] = {
        1024u, 4096u, 65536u, 1048576u
    };
    return sizes[code];
}

/* Conservatively treat any valid translation covering the VA as resident,
 * regardless of ASID. This may skip a usable page but cannot create a false
 * cold-page candidate. */
static bool utlb_covers(uintptr_t virtual_address)
{
    for (unsigned slot = 0; slot < UTLB_COUNT; slot++) {
        uint32_t address = sUtlbAddr[slot];
        uint32_t data = sUtlbData[slot];
        if (((address & 0x100u) == 0u) || ((data & 0x100u) == 0u))
            continue;

        unsigned size = utlb_page_size(data);
        uintptr_t base = (uintptr_t)(address & 0xfffffc00u);
        base &= ~((uintptr_t)size - 1u);
        if (virtual_address >= base && virtual_address - base < size)
            return true;
    }
    return false;
}

static bool find_candidate(void)
{
    uintptr_t page_limit = (sImageBytes + PAGE_SIZE - 1u) / PAGE_SIZE;
    if (page_limit > PAGE_TABLE_SCAN_WORDS)
        page_limit = PAGE_TABLE_SCAN_WORDS;

    for (unsigned index = 0; index < page_limit; index++) {
        uint32_t raw = read_u32(OS_PAGE_TABLE_ADDR + (uintptr_t)index * 4u);
        if (raw == 0u)
            break;

        uint32_t physical = raw & 0x1fffffffu;
        if ((physical & (PAGE_SIZE - 1u)) != 0u ||
            physical >= FLASH_PHYSICAL_END)
            return false;
        if (physical == 0u)
            continue;

        uintptr_t page_offset = (uintptr_t)index * PAGE_SIZE;
        if (page_offset + READ_OFFSET + sizeof(uint32_t) > sImageBytes)
            continue;
        uintptr_t page_va = ADDIN_CODE_BASE + page_offset;
        uintptr_t access_va = page_va + READ_OFFSET;
        if (utlb_covers(access_va))
            continue;

        sCandidateIndex = (int)index;
        sCandidateRaw = raw;
        sCandidatePhysical = physical;
        sCandidateVa = page_va;
        sAccessVa = access_va;
        sP1Alias = (uintptr_t)(OS_FLASH_P1_BASE | physical) + READ_OFFSET;
        sExpectedValue = read_u32(sP1Alias);
        return true;
    }
    return false;
}

static int preflight(void)
{
    sMmucr = SH7305_MMU.MMUCR.lword;
    sPtehBefore = SH7305_MMU.PTEH.lword;
    sAddinMapped = read_u8(OS_ADDIN_MAPPED_ADDR);

    if ((sMmucr & 1u) == 0u)
        return 1; /* MMU translation disabled. */
    if (((sMmucr >> 8) & 1u) == 0u)
        return 2; /* Supervisor bit disabled. */
    if (((sMmucr >> 18) & 0x3fu) != 63u)
        return 3; /* Expected add-in UTLB split not observed. */
    if (sAddinMapped != 1u)
        return 4;
    sImageBytes = (uintptr_t)&srom;
    if (sImageBytes == 0u || sImageBytes > 0x00200000u)
        return 10;
    if (snapshot_utlb() != 0)
        return 5;

    /* Decode the live syscall dispatcher's PC-relative table pointer. This
     * reads the 0x0c and 0x09 entries but does not call either entry. */
    uint16_t dispatch_mov = read_u16(OS_SYSCALL_DISPATCHER);
    uint16_t dispatch_shll = read_u16(OS_SYSCALL_DISPATCHER + 2u);
    if ((dispatch_mov & 0xff00u) != 0xd200u || dispatch_shll != 0x4008u)
        return 6;

    uintptr_t literal_address = ((OS_SYSCALL_DISPATCHER + 4u) & ~3u) +
                                (uintptr_t)(dispatch_mov & 0xffu) * 4u;
    sSyscallTable = read_u32(literal_address);
    if ((sSyscallTable & 3u) != 0u ||
        sSyscallTable < OS_FLASH_P1_BASE ||
        sSyscallTable >= OS_FLASH_P1_LIMIT)
        return 7;

    sSyscall0cTarget = read_u32(
        sSyscallTable + OS_TLBH_SYSCALL * sizeof(uint32_t));
    sSyscall09Target = read_u32(
        sSyscallTable + OS_MAP_SYSCALL * sizeof(uint32_t));
    if ((sSyscall0cTarget & 1u) != 0u ||
        sSyscall0cTarget < OS_FLASH_P1_BASE ||
        sSyscall0cTarget >= OS_FLASH_P1_LIMIT)
        return 8;
    if ((sSyscall09Target & 1u) != 0u ||
        sSyscall09Target < OS_FLASH_P1_BASE ||
        sSyscall09Target >= OS_FLASH_P1_LIMIT)
        return 9;

    for (unsigned i = 0; i < 4u; i++)
        sSyscall0cWords[i] = read_u16(sSyscall0cTarget + i * 2u);

    if (!find_candidate())
        return 11;

    return 0;
}

static int run_cold_read(void)
{
    int result = 0;
    cpu_atomic_start();

    /* Establish a fresh cold-state snapshot with maskable interrupts disabled.
     * Any add-in page fault while preparing this snapshot changes the counter
     * and invalidates the controlled-miss experiment. */
    uint32_t counter_before_snapshot = read_u32(OS_PAGE_COUNTER_ADDR);
    if (counter_before_snapshot >= OS_MAP_SLOT_COUNT) {
        sProbeError = 1;
        result = -1;
        goto done;
    }
    if (snapshot_utlb() != 0) {
        sProbeError = 2;
        result = -1;
        goto done;
    }
    sCounterBefore = read_u32(OS_PAGE_COUNTER_ADDR);
    if (sCounterBefore != counter_before_snapshot) {
        sProbeError = 3;
        result = -1;
        goto done;
    }
    if (utlb_covers(sAccessVa)) {
        sProbeError = 4;
        result = -1;
        goto done;
    }
    sColdBeforeRead = true;

    memset(&sCapture, 0, sizeof(sCapture));
    cold_data_tlb_load((uint32_t)sAccessVa, &sCapture);

    /* The assembly helper samples the counter before its sole MOV.L and again
     * immediately after the retried load. No unrelated add-in page refill may
     * occur between the caller's baseline and this first assembly sample. */
    uint32_t expected_after = (sCounterBefore + 1u) % OS_MAP_SLOT_COUNT;
    if (sCapture.counter_before != sCounterBefore)
        sProbeError = 5;
    else if (sCapture.counter_after != expected_after)
        sProbeError = 6;
    else if (sCapture.expevt != TLB_READ_MISS_EXPEVT)
        sProbeError = 7;
    else if (sCapture.tea != (uint32_t)sAccessVa)
        sProbeError = 8;
    else if (sCapture.loaded_value != sExpectedValue)
        sProbeError = 9;

    /* Verify the resulting UTLB mapping with the existing P2 reader while
     * still atomic. A second code-page miss here changes the counter and fails. */
    sSnapshotAfterOk = snapshot_utlb() == 0;
    sCounterAfterSnapshot = read_u32(OS_PAGE_COUNTER_ADDR);
    if (!sSnapshotAfterOk) {
        if (sProbeError == 0)
            sProbeError = 10;
    } else {
        if (sCounterAfterSnapshot != sCapture.counter_after && sProbeError == 0)
            sProbeError = 11;

        for (unsigned slot = 0; slot < UTLB_COUNT; slot++) {
            uint32_t address = sUtlbAddr[slot];
            uint32_t data = sUtlbData[slot];
            if ((address & 0x100u) == 0u || (data & 0x100u) == 0u)
                continue;
            if ((address & 0xfffff000u) != (uint32_t)sCandidateVa)
                continue;
            sFoundSlot = (int)slot;
            sUtlbAddressMatch = address;
            sUtlbDataMatch = data;
            break;
        }

        unsigned page_code = (((sUtlbDataMatch >> 7) & 1u) << 1) |
                             ((sUtlbDataMatch >> 4) & 1u);
        if (sFoundSlot < 0) {
            if (sProbeError == 0)
                sProbeError = 12;
        } else if ((sUtlbAddressMatch & 0xfffff000u) !=
                       (uint32_t)sCandidateVa ||
                   (sUtlbDataMatch & 0x1ffff000u) != sCandidatePhysical ||
                   page_code != 1u ||
                   (sUtlbAddressMatch & 0x100u) == 0u ||
                   (sUtlbDataMatch & 0x100u) == 0u) {
            if (sProbeError == 0)
                sProbeError = 13;
        } else if ((unsigned)sFoundSlot != sCounterBefore) {
            if (sProbeError == 0)
                sProbeError = 14;
        }
    }

done:
    cpu_atomic_end();
    return result;
}

static int appendf(size_t *used, const char *format, ...)
    __attribute__((format(printf, 2, 3)));

static int appendf(size_t *used, const char *format, ...)
{
    if (*used >= sizeof(sReport))
        return -1;

    va_list args;
    va_start(args, format);
    int count = vsnprintf(sReport + *used, sizeof(sReport) - *used,
                          format, args);
    va_end(args);
    if (count < 0 || (size_t)count >= sizeof(sReport) - *used)
        return -1;
    *used += (size_t)count;
    return 0;
}

static const char *preflight_name(int error)
{
    static const char *const names[] = {
        "PASS", "MMU_DISABLED", "SV_DISABLED", "URB_NOT_63",
        "ADDIN_NOT_MAPPED", "P2_SNAPSHOT_FAILED",
        "SYSCALL_DISPATCHER_SIGNATURE_INVALID", "SYSCALL_TABLE_INVALID",
        "SYSCALL_0C_TARGET_INVALID", "SYSCALL_09_TARGET_INVALID",
        "ADDIN_IMAGE_SIZE_INVALID", "NO_VALID_COLD_PAGE_TABLE_ENTRY"
    };
    if (error < 0 || (unsigned)error >= sizeof(names) / sizeof(names[0]))
        return "UNKNOWN";
    return names[error];
}

static const char *probe_error_name(int error)
{
    static const char *const names[] = {
        "PASS", "COUNTER_OUT_OF_RANGE", "POST_BASELINE_SNAPSHOT_FAILED",
        "UNEXPECTED_REFILL_DURING_BASELINE", "CANDIDATE_NO_LONGER_COLD",
        "UNRELATED_REFILL_BEFORE_FAULTING_LOAD", "EXPECTED_SINGLE_REFILL_NOT_OBSERVED",
        "EXPEVT_NOT_READ_TLB_MISS", "TEA_DOES_NOT_MATCH_LOAD_ADDRESS",
        "LOADED_WORD_MISMATCH", "POST_READ_SNAPSHOT_FAILED",
        "UNEXPECTED_REFILL_DURING_VERIFICATION", "MAPPING_NOT_FOUND",
        "UTLB_PHYSICAL_PAGE_OR_SIZE_MISMATCH", "UTLB_SLOT_MISMATCH"
    };
    if (error < 0 || (unsigned)error >= sizeof(names) / sizeof(names[0]))
        return "UNKNOWN";
    return names[error];
}

static int build_report(void)
{
    size_t used = 0;
    bool passed = sPreflightError == 0 && sProbeError == 0 &&
                  sSnapshotAfterOk && sFoundSlot >= 0;

    if (appendf(&used,
        "== FXCG50 COLD DATA-TLB MISS PROBE ==\n"
        "FORMAT=1\n"
        "TARGET_OS_EXPECTED=3.20.0202\n"
        "OS_VERSION_DETECTED=NO\n"
        "HANDLER=GINT_STOCK_TLBH_AND_OS_SYSCALL_0C\n"
        "TRIGGER=MOV.L_DATA_READ\n"
        "SYSCALL_DISPATCHER=%08X TABLE=%08X\n"
        "SYSCALL_0C_TARGET=%08X CODE=%04X,%04X,%04X,%04X\n"
        "SYSCALL_09_TARGET=%08X\n"
        "MMUCR=%08X URB=%u AT=%u SV=%u ASID=%02X\n"
        "ADDIN_MAPPED=%02X PAGE_TABLE=%08X IMAGE_BYTES=%08lX\n"
        "CANDIDATE_INDEX=%d CANDIDATE_VA=%08lX ACCESS_VA=%08lX\n"
        "PAGE_TABLE_WORD=%08X PHYSICAL_PAGE=%08X P1_ALIAS=%08lX\n"
        "EXPECTED_WORD=%08X LOADED_WORD=%08X VALUE_MATCH=%u\n"
        "COLD_BEFORE_READ=%u\n"
        "COUNTER_BEFORE=%08X COUNTER_CAPTURE_BEFORE=%08X\n"
        "COUNTER_CAPTURE_AFTER=%08X COUNTER_AFTER_SNAPSHOT=%08X\n"
        "EXPEVT=%08X TEA=%08X PTEH=%08X\n"
        "UTLB_SLOT=%d UTLB_ADDRESS=%08X UTLB_DATA=%08X\n"
        "UTLB_PAGE_SIZE_CODE=%u UTLB_MATCH=%u\n"
        "PREFLIGHT=%s PREFLIGHT_CODE=%d\n"
        "PROBE_STATUS=%s PROBE_CODE=%d\n"
        "RESULT=%s\n",
        (unsigned)OS_SYSCALL_DISPATCHER, (unsigned)sSyscallTable,
        (unsigned)sSyscall0cTarget,
        (unsigned)sSyscall0cWords[0], (unsigned)sSyscall0cWords[1],
        (unsigned)sSyscall0cWords[2], (unsigned)sSyscall0cWords[3],
        (unsigned)sSyscall09Target,
        (unsigned)sMmucr, (unsigned)((sMmucr >> 18) & 0x3fu),
        (unsigned)(sMmucr & 1u), (unsigned)((sMmucr >> 8) & 1u),
        (unsigned)(sPtehBefore & 0xffu), (unsigned)sAddinMapped,
        (unsigned)OS_PAGE_TABLE_ADDR, (unsigned long)sImageBytes,
        sCandidateIndex,
        (unsigned long)sCandidateVa, (unsigned long)sAccessVa,
        (unsigned)sCandidateRaw, (unsigned)sCandidatePhysical,
        (unsigned long)sP1Alias, (unsigned)sExpectedValue,
        (unsigned)sCapture.loaded_value,
        (unsigned)(sCapture.loaded_value == sExpectedValue),
        (unsigned)sColdBeforeRead,
        (unsigned)sCounterBefore, (unsigned)sCapture.counter_before,
        (unsigned)sCapture.counter_after, (unsigned)sCounterAfterSnapshot,
        (unsigned)sCapture.expevt, (unsigned)sCapture.tea,
        (unsigned)sCapture.pteh, sFoundSlot,
        (unsigned)sUtlbAddressMatch, (unsigned)sUtlbDataMatch,
        (unsigned)((((sUtlbDataMatch >> 7) & 1u) << 1) |
                   ((sUtlbDataMatch >> 4) & 1u)),
        (unsigned)(sFoundSlot >= 0 &&
            (sUtlbAddressMatch & 0x100u) != 0u &&
            (sUtlbDataMatch & 0x100u) != 0u &&
            (sUtlbDataMatch & 0x1ffff000u) == sCandidatePhysical),
        preflight_name(sPreflightError), sPreflightError,
        (sPreflightError != 0 ? "NOT_RUN" : probe_error_name(sProbeError)),
        sProbeError,
        passed ? "PASS" : "FAIL") != 0)
        return -1;

    sReportSize = used;
    return 0;
}

struct ReportWriteArgs {
    const char *data;
    size_t size;
    int error_number;
};

static int write_report_os_world(void *opaque)
{
    struct ReportWriteArgs *args = (struct ReportWriteArgs *)opaque;
    args->error_number = 0;

    FILE *file = fopen(REPORT_PATH, "w");
    if (!file) {
        args->error_number = errno ? errno : EIO;
        return -1;
    }

    int failed = 0;
    if (fwrite(args->data, 1, args->size, file) != args->size)
        failed = 1;
    if (!failed && fflush(file) != 0)
        failed = 1;
    if (ferror(file))
        failed = 1;
    if (fclose(file) != 0)
        failed = 1;

    if (!failed) {
        struct stat after = {0};
        if (stat(REPORT_PATH, &after) != 0 || after.st_size < 0 ||
            (size_t)after.st_size != args->size) {
            failed = 1;
            args->error_number = errno ? errno : EIO;
        }
    }
    if (failed && args->error_number == 0)
        args->error_number = errno ? errno : EIO;
    return failed ? -1 : 0;
}

static int save_report(void)
{
    struct ReportWriteArgs args = {sReport, sReportSize, 0};
    int status = gint_world_switch(
        GINT_CALL(write_report_os_world, (void *)&args));
    if (status != 0 && args.error_number == 0)
        args.error_number = status;
    sSaveError = args.error_number;
    return status == 0 && sSaveError == 0 ? 0 : -1;
}

static void show_result(void)
{
    bool passed = sPreflightError == 0 && sProbeError == 0 &&
                  sSnapshotAfterOk && sFoundSlot >= 0;
    dclear(C_BLACK);
    dtext(4, 2, C_WHITE, "Cold data-TLB miss probe");

    if (sSaveError != 0) {
        dtext(4, 28, C_RED, "Could not save report");
        dprint(4, 46, C_WHITE, "File error: %d", sSaveError);
    } else if (passed) {
        dtext(4, 28, C_GREEN, "Cold data miss/refill passed");
        dtext(4, 46, C_WHITE, "EXPEVT, TEA and UTLB verified");
        dtext(4, 64, C_WHITE, "COLD-DATA-TLB-MISS.TXT saved");
    } else {
        dtext(4, 28, C_RED, "Cold data miss test failed");
        dprint(4, 46, C_WHITE, "Preflight: %d  Probe: %d",
            sPreflightError, sProbeError);
        dtext(4, 64, C_WHITE, "See COLD-DATA-TLB-MISS.TXT");
    }
    dtext(4, 104, C_WHITE, "Uses the stock OS/Gint miss path");
    dtext(4, 118, C_WHITE, "No custom vector or direct TLB write");
    dtext(4, 214, C_WHITE, "Press any key to exit");
    dupdate();

    for (;;) {
        key_event_t event = getkey_opt(GETKEY_NONE, NULL);
        if (event.type == KEYEV_DOWN)
            break;
    }
}

int main(int isappli, int optnum)
{
    /* TMC_COLD_TLB_PROBE_XBSS_INIT */
    memset(rxbss, 0, (size_t)sxbss);
    (void)isappli;
    (void)optnum;

    sPreflightError = preflight();
    if (sPreflightError == 0)
        (void)run_cold_read();

    if (build_report() == 0)
        (void)save_report();
    else
        sSaveError = EOVERFLOW;

    show_result();
    return 0;
}

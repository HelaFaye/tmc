/*
 * fx-CG50 D1 mapping proof probe.
 *
 * This is an ACTIVE diagnostic: it calls the OS 4 KiB round-robin mapper
 * (syscall 0x0009) to create up to eight temporary virtual aliases in the planned
 * D1 window. It does not write the OS page table or MMUCR directly, and it
 * never changes the normal add-in page-table entries. The OS allocator is
 * expected to recycle these UTLB entries naturally.
 *
 * The syscall number/ABI is described by CG50 Internals for OS 3.60. The
 * target is OS 3.20.0202; the probe performs conservative runtime checks,
 * but the actual syscall behavior still requires a hardware run.
 */
#include <gint/cpu.h>
#include <gint/display.h>
#include <gint/gint.h>
#include <gint/keyboard.h>
#include <gint/keycodes.h>
#include <gint/mpu/mmu.h>

#include <errno.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>


extern char rxbss[], sxbss[];
#define UTLB_COUNT          64u

#define OS_PAGE_TABLE_ADDR  0x8c04cf0cu
#define OS_PAGE_COUNTER_ADDR 0x8c04d70cu
#define OS_ADDIN_MAPPED_ADDR 0x8c04cee1u

#define ADDIN_CODE_BASE     0x00300000u
#define D1_WINDOW_BASE      0x20000000u
#define PAGE_SIZE           4096u
#define PAGE_COUNT_MAX      8u
#define PAGE_TABLE_SCAN_WORDS 256u
#define D1_WINDOW_END       (D1_WINDOW_BASE + PAGE_COUNT_MAX * PAGE_SIZE)
#define OS_MAP_FLAGS_4K     0x0000006cu
#define OS_MAP_SYSCALL      0x00000009u
#define OS_SYSCALL_DISPATCHER 0x80020070u
#define OS_FLASH_P1_BASE    0x80000000u
#define OS_FLASH_P1_LIMIT   0x82000000u
#define OS_MAP_SLOT_COUNT   55u
#define FLASH_PHYSICAL_END  0x02000000u

#define DUMP_CAPACITY       4096u
#define DUMP_PATH           "/D1-MAP-PROBE.TXT"

extern void os_map_page_4k(uint32_t virtual_page, uint32_t physical_page,
                           uint32_t flags);

static uint32_t sPageTable[PAGE_COUNT_MAX];
static uint32_t sSourceCrc[PAGE_COUNT_MAX];
static uint32_t sAliasCrc[PAGE_COUNT_MAX];
static uint32_t sMapSlot[PAGE_COUNT_MAX];
static uint8_t sUtlbMatch[PAGE_COUNT_MAX];
static uint8_t sCrcMatch[PAGE_COUNT_MAX];
/* The P4 TLB arrays are read only by this copied privileged P2 template. */
#define P2_SNAPSHOT_CODE ((uintptr_t)0xac7ff000u)
#define P2_SNAPSHOT_BUFFER ((uintptr_t)0xac7fe000u)
#define P2_SNAPSHOT_PAGE_END ((uintptr_t)0xac800000u)
#define P2_SNAPSHOT_CODE_MAX 0x200u
#define P2_SNAPSHOT_WORDS ((UTLB_COUNT + 4u) * 2u)

extern const uint8_t P2TlbSnapshotTemplate[];
extern const uint8_t P2TlbSnapshotTemplateEnd[];
typedef void (*P2TlbSnapshotFunction)(uint32_t *p2_output);

static uint32_t sUtlbAddr[UTLB_COUNT];
static uint32_t sUtlbData[UTLB_COUNT];
static int sP2SnapshotInstalled;
static uint32_t sMmucr;
static uint32_t sSyscallTable;
static uint32_t sMapperTarget;
static uint32_t sCounterBefore;
static uint32_t sCounterAfter;
static uint8_t sAddinMapped;
static unsigned sPageCount;
static unsigned sAddinCodePagesPrefetched;
static unsigned sPagesMapped;
static unsigned sUtlbMatches;
static unsigned sCrcMatches;
static int sPreflightError;
static int sMapError;
static int sSaveError;
static char sDump[DUMP_CAPACITY];
static size_t sDumpSize;

static uint32_t read_u32(uintptr_t address)
{
    return *(volatile uint32_t *)address;
}

static uint8_t read_u8(uintptr_t address)
{
    return *(volatile uint8_t *)address;
}

/* Install the position-independent helper once, then take a fresh snapshot.
 * The helper reads P4 UTLB/ITLB arrays only while executing in privileged P2,
 * writes its result buffer through the P2 alias, and performs SYNCO + ICBI
 * before returning to this ordinary add-in code. */
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

        volatile uint8_t *destination =
            (volatile uint8_t *)P2_SNAPSHOT_CODE;
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
    /* Prevent maskable interrupts from branching out of P2 between the
     * memory-mapped TLB reads and the helper's ICBI transition. Preserve and
     * restore the complete original SR; the P2 helper itself does not alter it. */
    uint32_t original_sr;
    __asm__ volatile("stc sr,%0" : "=r"(original_sr));
    uint32_t masked_sr = original_sr | 0x000000f0u;
    __asm__ volatile("ldc %0,sr\n\tnop" :: "r"(masked_sr) : "memory");
    read_arrays((uint32_t *)P2_SNAPSHOT_BUFFER);
    __asm__ volatile("ldc %0,sr\n\tnop" :: "r"(original_sr) : "memory");

    /* The P2 helper has already performed the required transition. */
    __asm__ volatile("synco" ::: "memory");
    volatile const uint32_t *words =
        (volatile const uint32_t *)P2_SNAPSHOT_BUFFER;
    for (unsigned i = 0; i < UTLB_COUNT; i++) {
        sUtlbAddr[i] = words[2u * i];
        sUtlbData[i] = words[2u * i + 1u];
    }
    return 0;
}

static uint32_t crc32_volatile(volatile const uint8_t *data, size_t size)
{
    uint32_t crc = 0xffffffffu;
    for (size_t i = 0; i < size; i++) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; bit++) {
            uint32_t mask = 0u - (crc & 1u);
            crc = (crc >> 1) ^ (0xedb88320u & mask);
        }
    }
    return ~crc;
}

static unsigned page_size_bytes(uint32_t data)
{
    unsigned sz1 = (data >> 7) & 1u;
    unsigned sz0 = (data >> 4) & 1u;
    unsigned code = (sz1 << 1) | sz0;
    static const unsigned sizes[4] = {1024u, 4096u, 65536u, 1048576u};
    return sizes[code];
}

static int d1_window_is_unmapped(void)
{
    /* Negative means the P2 snapshot itself could not be established. */
    if (snapshot_utlb() != 0)
        return -1;

    for (unsigned slot = 0; slot < UTLB_COUNT; slot++) {
        uint32_t address = sUtlbAddr[slot];
        uint32_t data = sUtlbData[slot];
        if (!((address >> 8) & 1u) || !((data >> 8) & 1u))
            continue;

        unsigned size = page_size_bytes(data);
        uint32_t base = (address & 0xfffff000u) & ~(size - 1u);
        uint64_t end = (uint64_t)base + size;
        if ((uint64_t)base < D1_WINDOW_END && end > D1_WINDOW_BASE)
            return 0;
    }
    return 1;
}

static int preflight(void)
{
    sMmucr = SH7305_MMU.MMUCR.lword;
    sAddinMapped = read_u8(OS_ADDIN_MAPPED_ADDR);

    if ((sMmucr & 1u) == 0u)
        return 1;
    if (((sMmucr >> 8) & 1u) == 0u)
        return 2;
    if (((sMmucr >> 18) & 0x3fu) != 63u)
        return 3;
    if (sAddinMapped != 1u)
        return 4;
    int d1_unmapped = d1_window_is_unmapped();
    if (d1_unmapped < 0)
        return 14;
    if (d1_unmapped == 0)
        return 5;

    /*
     * Check the OS syscall dispatcher before the assembly stub can call it.
     * The known dispatcher begins with MOV.L @(disp,PC),R2; SHLL2 R0.
     * Resolve its PC-relative table pointer at runtime (table addresses vary
     * between OS releases), then validate syscall 0x0009's target is in ROM.
     */
    uint16_t dispatch_mov = *(volatile uint16_t *)OS_SYSCALL_DISPATCHER;
    uint16_t dispatch_shll = *(volatile uint16_t *)(OS_SYSCALL_DISPATCHER + 2u);
    if ((dispatch_mov & 0xff00u) != 0xd200u || dispatch_shll != 0x4008u)
        return 11;
    uintptr_t literal_address = ((OS_SYSCALL_DISPATCHER + 4u) & ~3u)
                              + (uintptr_t)(dispatch_mov & 0xffu) * 4u;
    sSyscallTable = read_u32(literal_address);
    if ((sSyscallTable & 3u) != 0u || sSyscallTable < OS_FLASH_P1_BASE
        || sSyscallTable >= OS_FLASH_P1_LIMIT)
        return 12;
    sMapperTarget = read_u32(sSyscallTable + OS_MAP_SYSCALL * 4u);
    if ((sMapperTarget & 1u) != 0u || sMapperTarget < OS_FLASH_P1_BASE
        || sMapperTarget >= OS_FLASH_P1_LIMIT)
        return 13;

    /*
     * The OS clears the first 256 page-table words on add-in launch. Walk
     * that active span and touch each mapped source page before capturing
     * the starting counter, so later instruction fetches do not consume
     * allocator slots unexpectedly during the alias test.
     */
    for (unsigned i = 0; i < PAGE_TABLE_SCAN_WORDS; i++) {
        uint32_t raw = read_u32(OS_PAGE_TABLE_ADDR + (uintptr_t)i * 4u);
        if (raw == 0u)
            break;
        uint32_t physical = raw & 0x1fffffffu;
        if ((physical & (PAGE_SIZE - 1u)) != 0u)
            return 6;
        if (physical >= FLASH_PHYSICAL_END)
            return 7;
        if (i < PAGE_COUNT_MAX)
            sPageTable[i] = physical;

        uintptr_t source = ADDIN_CODE_BASE + (uintptr_t)i * PAGE_SIZE;
        volatile uint8_t first_byte = *(volatile uint8_t *)source;
        (void)first_byte;
        sAddinCodePagesPrefetched++;
    }
    if (sAddinCodePagesPrefetched == 0u)
        return 6;

    sPageCount = sAddinCodePagesPrefetched < PAGE_COUNT_MAX
               ? sAddinCodePagesPrefetched : PAGE_COUNT_MAX;

    /* CRC the first aliases before consuming allocator slots. */
    for (unsigned i = 0; i < sPageCount; i++) {
        uintptr_t source = ADDIN_CODE_BASE + (uintptr_t)i * PAGE_SIZE;
        sSourceCrc[i] = crc32_volatile(
            (volatile const uint8_t *)source, PAGE_SIZE);
    }

    sCounterBefore = read_u32(OS_PAGE_COUNTER_ADDR);
    if (sCounterBefore >= OS_MAP_SLOT_COUNT)
        return 8;

    /* The source-page prefetch must have left the candidate allocator sane. */
    if (read_u8(OS_ADDIN_MAPPED_ADDR) != 1u)
        return 9;
    if ((SH7305_MMU.MMUCR.lword & 1u) == 0u)
        return 10;

    return 0;
}

static int run_mapping_test(void)
{
    for (unsigned i = 0; i < sPageCount; i++) {
        uint32_t virtual_page = D1_WINDOW_BASE + i * PAGE_SIZE;
        uint32_t expected_slot = (sCounterBefore + i) % OS_MAP_SLOT_COUNT;
        uint32_t counter_expected = (sCounterBefore + i + 1u)
                                  % OS_MAP_SLOT_COUNT;

        sMapSlot[i] = expected_slot;
        os_map_page_4k(virtual_page, sPageTable[i], OS_MAP_FLAGS_4K);
        sPagesMapped++;

        uint32_t counter_now = read_u32(OS_PAGE_COUNTER_ADDR);
        if (counter_now != counter_expected) {
            sMapError = 1;
            return -1;
        }

        if (snapshot_utlb() != 0) {
            sMapError = 3;
            return -1;
        }
        uint32_t counter_after_snapshot = read_u32(OS_PAGE_COUNTER_ADDR);
        if (counter_after_snapshot != counter_expected) {
            sMapError = 1;
            return -1;
        }
        uint32_t address = sUtlbAddr[expected_slot];
        uint32_t data = sUtlbData[expected_slot];
        unsigned page_code = (((data >> 7) & 1u) << 1)
                           | ((data >> 4) & 1u);

        if (!((address >> 8) & 1u) || !((data >> 8) & 1u)
            || (address & 0xfffff000u) != virtual_page
            || (data & 0x1ffff000u) != sPageTable[i]
            || page_code != 1u) {
            sMapError = 2;
            return -1;
        }

        sUtlbMatch[i] = 1;
        sUtlbMatches++;
    }

    sCounterAfter = read_u32(OS_PAGE_COUNTER_ADDR);

    /* Only dereference aliases whose UTLB entries passed raw verification. */
    for (unsigned i = 0; i < sPageCount; i++) {
        if (!sUtlbMatch[i])
            continue;
        uintptr_t alias = D1_WINDOW_BASE + (uintptr_t)i * PAGE_SIZE;
        sAliasCrc[i] = crc32_volatile(
            (volatile const uint8_t *)alias, PAGE_SIZE);
        if (sAliasCrc[i] == sSourceCrc[i]) {
            sCrcMatch[i] = 1;
            sCrcMatches++;
        }
    }


    /* Re-snapshot after alias reads. A normal code-page refill or an
     * unexpected replacement must not silently invalidate a passing result. */
    if (snapshot_utlb() != 0) {
        sMapError = 3;
        return -1;
    }
    for (unsigned i = 0; i < sPageCount; i++) {
        unsigned slot = (unsigned)sMapSlot[i];
        uint32_t address = sUtlbAddr[slot];
        uint32_t data = sUtlbData[slot];
        unsigned page_code = (((data >> 7) & 1u) << 1)
                           | ((data >> 4) & 1u);
        if (!((address >> 8) & 1u) || !((data >> 8) & 1u) ||
            (address & 0xfffff000u) != D1_WINDOW_BASE + i * PAGE_SIZE ||
            (data & 0x1ffff000u) != sPageTable[i] || page_code != 1u) {
            sMapError = 2;
            return -1;
        }
    }

    sCounterAfter = read_u32(OS_PAGE_COUNTER_ADDR);
    if (sCounterAfter != (sCounterBefore + sPageCount) % OS_MAP_SLOT_COUNT) {
        sMapError = 1;
        return -1;
    }
    return 0;
}

static int appendf(size_t *used, const char *format, ...)
    __attribute__((format(printf, 2, 3)));

static int appendf(size_t *used, const char *format, ...)
{
    if (*used >= sizeof(sDump))
        return -1;

    va_list ap;
    va_start(ap, format);
    int n = vsnprintf(sDump + *used, sizeof(sDump) - *used, format, ap);
    va_end(ap);

    if (n < 0 || (size_t)n >= sizeof(sDump) - *used)
        return -1;

    *used += (size_t)n;
    return 0;
}

static const char *preflight_name(int error)
{
    static const char *const names[] = {
        "PASS", "MMU_DISABLED", "SV_DISABLED", "URB_NOT_63",
        "ADDIN_NOT_MAPPED", "D1_RANGE_ALREADY_MAPPED",
        "PAGE_TABLE_ENTRY_MISSING_OR_UNALIGNED", "PHYSICAL_PAGE_OUT_OF_FLASH",
        "PAGE_COUNTER_OUT_OF_RANGE", "MAPPED_FLAG_CHANGED", "MMU_AT_CHANGED",
        "SYSCALL_DISPATCHER_SIGNATURE_INVALID", "SYSCALL_TABLE_POINTER_INVALID",
        "SYSCALL_9_TARGET_INVALID", "P2_UTLB_SNAPSHOT_FAILED"
    };
    if (error < 0 || (unsigned)error >= sizeof(names) / sizeof(names[0]))
        return "UNKNOWN";
    return names[error];
}

static int build_dump(void)
{
    size_t used = 0;
    int result = (sPreflightError == 0 && sMapError == 0
                  && sPageCount != 0u
                  && sPagesMapped == sPageCount
                  && sUtlbMatches == sPageCount
                  && sCrcMatches == sPageCount);

    if (appendf(&used,
        "== FXCG50 D1 OS MAPPER PROOF ==\n"
        "FORMAT=1\n"
        "TARGET_OS=3.20.0202\n"
        "ACTIVE_DIAGNOSTIC=1\n"
        "UTLB_READ_PATH=PRIVILEGED_P2_TEMPLATE\n"
        "OS_LAYOUT=PAGE_TABLE_COUNTER_AND_FLAG_CONFIRMED_BY_PRIOR_CAPTURE\n"
        "MAP_SYSCALL=%02X\n"
        "MAP_FLAGS=%08X\n"
        "D1_BASE=%08X D1_END=%08X PAGE_SIZE=%u PAGE_COUNT_MAX=%u PAGE_COUNT=%u\n"
        "MMUCR=%08X URB=%u AT=%u SV=%u\n"
        "ADDIN_MAPPED=%02X\n"
        "SYSCALL_DISPATCHER=%08X TABLE=%08X MAP9_TARGET=%08X\n"
        "PREFLIGHT=%s PREFLIGHT_CODE=%d\n"
        "COUNTER_BEFORE=%08X COUNTER_AFTER=%08X\n"
        "ADDIN_CODE_PAGES_PREFETCHED=%u PAGES_MAPPED=%u UTLB_MATCHES=%u DATA_CRC_MATCHES=%u\n"
        "MAP_ERROR=%d\n"
        "RESULT=%s\n"
        "PAGE_COLUMNS=index,source_va,d1_va,physical_page,utlb_slot,source_crc32,d1_crc32,utlb_match,crc_match\n",
        OS_MAP_SYSCALL, (unsigned)OS_MAP_FLAGS_4K,
        (unsigned)D1_WINDOW_BASE, (unsigned)D1_WINDOW_END,
        PAGE_SIZE, PAGE_COUNT_MAX, sPageCount, (unsigned)sMmucr,
        (unsigned)((sMmucr >> 18) & 0x3fu),
        (unsigned)(sMmucr & 1u),
        (unsigned)((sMmucr >> 8) & 1u),
        (unsigned)sAddinMapped, (unsigned)OS_SYSCALL_DISPATCHER,
        (unsigned)sSyscallTable, (unsigned)sMapperTarget,
        preflight_name(sPreflightError),
        sPreflightError, (unsigned)sCounterBefore,
        (unsigned)sCounterAfter, sAddinCodePagesPrefetched, sPagesMapped,
        sUtlbMatches, sCrcMatches, sMapError, result ? "PASS" : "FAIL") != 0)
        return -1;

    for (unsigned i = 0; i < PAGE_COUNT_MAX; i++) {
        if (appendf(&used,
            "PAGE,%u,%08X,%08X,%08X,%u,%08X,%08X,%u,%u\n",
            i,
            (unsigned)(ADDIN_CODE_BASE + i * PAGE_SIZE),
            (unsigned)(D1_WINDOW_BASE + i * PAGE_SIZE),
            (unsigned)sPageTable[i], (unsigned)sMapSlot[i],
            (unsigned)sSourceCrc[i], (unsigned)sAliasCrc[i],
            (unsigned)sUtlbMatch[i], (unsigned)sCrcMatch[i]) != 0)
            return -1;
    }

    sDumpSize = used;
    return 0;
}

struct DumpWriteArgs {
    const char *data;
    size_t size;
    int error_number;
};

static int write_dump_os_world(void *opaque)
{
    struct DumpWriteArgs *args = (struct DumpWriteArgs *)opaque;
    args->error_number = 0;

    FILE *file = fopen(DUMP_PATH, "w");
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
        if (stat(DUMP_PATH, &after) != 0 || after.st_size < 0
            || (size_t)after.st_size != args->size) {
            failed = 1;
            args->error_number = errno ? errno : EIO;
        }
    }
    if (failed && args->error_number == 0)
        args->error_number = errno ? errno : EIO;
    return failed ? -1 : 0;
}

static int save_dump(void)
{
    struct DumpWriteArgs args = {sDump, sDumpSize, 0};
    int status = gint_world_switch(
        GINT_CALL(write_dump_os_world, (void *)&args));
    if (status != 0 && args.error_number == 0)
        args.error_number = status;
    sSaveError = args.error_number;
    return status == 0 && sSaveError == 0 ? 0 : -1;
}

static void show_result(void)
{
    int passed = sPreflightError == 0 && sMapError == 0
              && sPageCount != 0u
              && sPagesMapped == sPageCount
              && sUtlbMatches == sPageCount
              && sCrcMatches == sPageCount;

    dclear(C_BLACK);
    dtext(4, 2, C_WHITE, "D1 OS mapper proof");
    if (sSaveError != 0) {
        dtext(4, 28, C_RED, "Could not save report");
        dprint(4, 46, C_WHITE, "File error: %d", sSaveError);
    } else if (passed) {
        dtext(4, 28, C_GREEN, "D1 alias test passed");
        dtext(4, 46, C_WHITE, "UTLB entries and CRCs verified");
        dtext(4, 64, C_WHITE, "D1-MAP-PROBE.TXT saved");
    } else {
        dtext(4, 28, C_RED, "D1 alias test did not pass");
        dprint(4, 46, C_WHITE, "Preflight: %d  Map: %d",
            sPreflightError, sMapError);
        dprint(4, 64, C_WHITE, "Mapped: %u UTLB: %u CRC: %u",
            sPagesMapped, sUtlbMatches, sCrcMatches);
        dtext(4, 82, C_WHITE, "See D1-MAP-PROBE.TXT");
    }
    dtext(4, 132, C_WHITE, "OS allocator mappings are temporary;");
    dtext(4, 146, C_WHITE, "the probe does not change the page table.");
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
    /* TMC_D1_PROBE_XBSS_INIT */
    memset(rxbss, 0, (size_t)sxbss);
    (void)isappli;
    (void)optnum;

    sPreflightError = preflight();
    if (sPreflightError == 0)
        (void)run_mapping_test();
    sCounterAfter = read_u32(OS_PAGE_COUNTER_ADDR);

    if (build_dump() == 0)
        (void)save_dump();
    else
        sSaveError = EOVERFLOW;

    show_result();
    return 0;
}

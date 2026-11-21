/*
 * Read-only fx-CG50 OS paging-state diagnostic for the D1 investigation.
 *
 * Candidate OS RAM locations are taken from the public CG50 Internals OS 3.60
 * reverse-engineering notes. They are NOT assumed to be correct for OS
 * 3.20.0202. This program records the raw values and validates them against
 * the live add-in UTLB mappings; it does not write any OS RAM or MMU state.
 *
 * The diagnostic captures:
 *   - MMUCR, PTEH, PTEL, VBR and all 64 UTLB address/data pairs;
 *   - 512 candidate page-table words starting at 0x8C04CF0C;
 *   - the candidate software page-mapping counter at 0x8C04D70C;
 *   - the candidate add-in-mapped byte at 0x8C04CEE1.
 *
 * File output follows fxGLMark's tested pattern: stdio inside
 * gint_world_switch(), followed by flush/error/close/stat size checks.
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
#include <sys/stat.h>

#define UTLB_COUNT 64u
#define UTLB_ADDR_BASE 0xf6000000u
#define UTLB_DATA_BASE 0xf7000000u
#define UTLB_STRIDE 0x100u

#define OS_PAGE_TABLE_ADDR 0x8c04cf0cu
#define OS_PAGE_TABLE_WORDS 512u
#define OS_PAGE_COUNTER_ADDR 0x8c04d70cu
#define OS_ADDIN_MAPPED_ADDR 0x8c04cee1u

#define ADDIN_CODE_BASE 0x00300000u
#define ADDIN_CODE_LIMIT (ADDIN_CODE_BASE + OS_PAGE_TABLE_WORDS * 0x1000u)
#define PAGE_4K_SIZE_CODE 1u

#define DUMP_CAPACITY 32768u
#define DUMP_PATH "/OS-PAGING.TXT"

static uint32_t sUtlbAddr[UTLB_COUNT];
static uint32_t sUtlbData[UTLB_COUNT];
static uint32_t sPageTable[OS_PAGE_TABLE_WORDS];
static uint32_t sMmucr;
static uint32_t sPteh;
static uint32_t sPtel;
static uint32_t sVbr;
static uint32_t sPageCounterBefore;
static uint32_t sPageCounterAfter;
static uint8_t sAddinMapped;
static char sDump[DUMP_CAPACITY];
static size_t sDumpSize;
static int sSaveError;

static uint32_t read_u32(uintptr_t address)
{
    return *(volatile uint32_t *)address;
}

static uint8_t read_u8(uintptr_t address)
{
    return *(volatile uint8_t *)address;
}

static void snapshot(void)
{
    /* Bracket the reads: normal OS page faults may advance the counter. */
    sPageCounterBefore = read_u32(OS_PAGE_COUNTER_ADDR);
    sAddinMapped = read_u8(OS_ADDIN_MAPPED_ADDR);

    sMmucr = SH7305_MMU.MMUCR.lword;
    sPteh = SH7305_MMU.PTEH.lword;
    sPtel = SH7305_MMU.PTEL.lword;
    sVbr = cpu_getVBR();

    for (unsigned i = 0; i < UTLB_COUNT; i++) {
        uintptr_t offset = (uintptr_t)i * UTLB_STRIDE;
        sUtlbAddr[i] = read_u32(UTLB_ADDR_BASE + offset);
        sUtlbData[i] = read_u32(UTLB_DATA_BASE + offset);
    }

    for (unsigned i = 0; i < OS_PAGE_TABLE_WORDS; i++) {
        sPageTable[i] = read_u32(OS_PAGE_TABLE_ADDR + (uintptr_t)i * 4u);
    }

    sPageCounterAfter = read_u32(OS_PAGE_COUNTER_ADDR);
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

static int is_valid_4k_addin_utlb(unsigned slot, uint32_t *page_index)
{
    uint32_t address = sUtlbAddr[slot];
    uint32_t data = sUtlbData[slot];
    uint32_t virtual_page = address & 0xfffff000u;
    unsigned address_valid = (address >> 8) & 1u;
    unsigned data_valid = (data >> 8) & 1u;
    unsigned size_bit_1 = (data >> 7) & 1u;
    unsigned size_bit_0 = (data >> 4) & 1u;
    unsigned page_size_code = (size_bit_1 << 1) | size_bit_0;

    if (!address_valid || !data_valid || page_size_code != PAGE_4K_SIZE_CODE)
        return 0;
    if (virtual_page < ADDIN_CODE_BASE || virtual_page >= ADDIN_CODE_LIMIT)
        return 0;

    *page_index = (virtual_page - ADDIN_CODE_BASE) >> 12;
    return 1;
}

static int build_dump(void)
{
    size_t used = 0;
    unsigned table_nonzero = 0;
    unsigned table_zero = 0;
    unsigned table_unaligned = 0;
    unsigned first_seven_nonzero_aligned = 0;
    unsigned tlb_matches = 0;
    unsigned tlb_mismatches = 0;
    unsigned tlb_table_zero = 0;

    for (unsigned i = 0; i < OS_PAGE_TABLE_WORDS; i++) {
        uint32_t value = sPageTable[i];
        if (value == 0)
            table_zero++;
        else {
            table_nonzero++;
            if ((value & 0xfffu) != 0)
                table_unaligned++;
        }
        if (i < 7u && value != 0 && (value & 0xfffu) == 0)
            first_seven_nonzero_aligned++;
    }

    for (unsigned slot = 0; slot < UTLB_COUNT; slot++) {
        uint32_t page_index;
        if (is_valid_4k_addin_utlb(slot, &page_index)) {
            uint32_t table_value = sPageTable[page_index];
            uint32_t tlb_physical = sUtlbData[slot] & 0x1ffff000u;
            uint32_t table_physical = table_value & 0x1ffff000u;
            if (table_value == 0)
                tlb_table_zero++;
            else if (tlb_physical == table_physical)
                tlb_matches++;
            else
                tlb_mismatches++;
        }
    }

    if (appendf(&used,
        "== FXCG50 OS PAGING STATE ==\n"
        "FORMAT=1\n"
        "STATUS=RAW_READ_COMPLETE\n"
        "READ_ONLY=1\n"
        "TARGET_OS=3.20.0202\n"
        "LAYOUT_REFERENCE=CG50_INTERNALS_OS_3.60\n"
        "LAYOUT_STATUS=UNVERIFIED_FOR_TARGET_OS\n"
        "MMUCR=%08X PTEH=%08X PTEL=%08X VBR=%08X\n"
        "URB=%u URC=%u AT=%u SV=%u ASID=%02X\n"
        "PAGE_TABLE_ADDR=%08X PAGE_TABLE_WORDS=%u PAGE_TABLE_BYTES=%u\n"
        "PAGE_COUNTER_ADDR=%08X PAGE_COUNTER_BEFORE=%08X PAGE_COUNTER_AFTER=%08X\n"
        "PAGE_COUNTER_STABLE=%s\n"
        "ADDIN_MAPPED_ADDR=%08X ADDIN_MAPPED_RAW=%02X\n"
        "PAGE_TABLE_NONZERO_ALL512=%u PAGE_TABLE_ZERO_ALL512=%u PAGE_TABLE_UNALIGNED_ALL512=%u\n"
        "FIRST7_NONZERO_ALIGNED=%u FIRST7_SANITY=%s\n"
        "COUNTER_IN_0_54=%s ADDIN_MAPPED_IS_ONE=%s\n"
        "UTLB_PT_MATCHES=%u UTLB_PT_MISMATCHES=%u UTLB_PT_ZERO=%u\n"
        "UTLB_COLUMNS=slot,addr_word,data_word,addr_valid,data_valid,page_size_code,va_page,pt_index,pt_word,pa_page,pt_compare\n",
        (unsigned)sMmucr, (unsigned)sPteh, (unsigned)sPtel, (unsigned)sVbr,
        (unsigned)((sMmucr >> 18) & 0x3fu),
        (unsigned)((sMmucr >> 10) & 0x3fu),
        (unsigned)(sMmucr & 1u),
        (unsigned)((sMmucr >> 8) & 1u),
        (unsigned)(sPteh & 0xffu),
        (unsigned)OS_PAGE_TABLE_ADDR, (unsigned)OS_PAGE_TABLE_WORDS,
        (unsigned)(OS_PAGE_TABLE_WORDS * 4u),
        (unsigned)OS_PAGE_COUNTER_ADDR, (unsigned)sPageCounterBefore,
        (unsigned)sPageCounterAfter,
        sPageCounterBefore == sPageCounterAfter ? "YES" : "NO",
        (unsigned)OS_ADDIN_MAPPED_ADDR, (unsigned)sAddinMapped,
        table_nonzero, table_zero, table_unaligned,
        first_seven_nonzero_aligned,
        first_seven_nonzero_aligned == 7u ? "PASS" : "NOT_CONFIRMED",
        sPageCounterAfter <= 54u ? "YES" : "NO",
        sAddinMapped == 1u ? "YES" : "NO",
        tlb_matches, tlb_mismatches, tlb_table_zero) != 0)
        return -1;

    for (unsigned slot = 0; slot < UTLB_COUNT; slot++) {
        uint32_t address = sUtlbAddr[slot];
        uint32_t data = sUtlbData[slot];
        unsigned address_valid = (address >> 8) & 1u;
        unsigned data_valid = (data >> 8) & 1u;
        unsigned size_bit_1 = (data >> 7) & 1u;
        unsigned size_bit_0 = (data >> 4) & 1u;
        unsigned page_size_code = (size_bit_1 << 1) | size_bit_0;
        uint32_t virtual_page = address & 0xfffff000u;
        uint32_t page_index = 0xffffffffu;
        uint32_t table_word = 0;
        uint32_t physical_page = data & 0x1ffff000u;
        const char *comparison = "NOT_APPLICABLE";
        char page_index_text[12] = "-";

        if (is_valid_4k_addin_utlb(slot, &page_index)) {
            (void)snprintf(page_index_text, sizeof(page_index_text), "%u",
                (unsigned)page_index);
            table_word = sPageTable[page_index];
            if (table_word == 0)
                comparison = "TABLE_ZERO";
            else if ((table_word & 0x1ffff000u) == physical_page)
                comparison = "MATCH";
            else
                comparison = "MISMATCH";
        }

        if (appendf(&used,
            "UTLB,%02u,%08X,%08X,%u,%u,%u,%08X,%s,%08X,%08X,%s\n",
            slot, (unsigned)address, (unsigned)data,
            address_valid, data_valid, page_size_code,
            (unsigned)virtual_page, page_index_text,
            (unsigned)table_word, (unsigned)physical_page, comparison) != 0)
            return -1;
    }

    if (appendf(&used,
        "PAGE_COLUMNS=index,virtual_page,raw_word,physical_low29\n") != 0)
        return -1;

    for (unsigned i = 0; i < OS_PAGE_TABLE_WORDS; i++) {
        uint32_t virtual_page = ADDIN_CODE_BASE + i * 0x1000u;
        uint32_t raw = sPageTable[i];
        uint32_t physical = raw & 0x1fffffffu;
        if (appendf(&used, "PAGE,%03u,%08X,%08X,%08X\n",
            i, (unsigned)virtual_page, (unsigned)raw, (unsigned)physical) != 0)
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

    struct stat before = {0};
    errno = 0;
    if (stat(DUMP_PATH, &before) == 0) {
        if (before.st_size < 0) {
            args->error_number = EIO;
            return -1;
        }
    } else if (errno != ENOENT) {
        args->error_number = errno ? errno : EIO;
        return -1;
    }

    errno = 0;
    FILE *file = fopen(DUMP_PATH, "w");
    if (!file) {
        args->error_number = errno ? errno : EIO;
        return -1;
    }

    int failed = 0;
    size_t count = fwrite(args->data, 1, args->size, file);
    if (count != args->size)
        failed = 1;
    if (!failed && fflush(file) != 0)
        failed = 1;
    if (ferror(file))
        failed = 1;
    if (fclose(file) != 0)
        failed = 1;

    if (!failed) {
        struct stat after = {0};
        if (stat(DUMP_PATH, &after) != 0 || after.st_size < 0 ||
            (size_t)after.st_size != args->size) {
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
    int status = gint_world_switch(GINT_CALL(write_dump_os_world, (void *)&args));
    if (status != 0 && args.error_number == 0)
        args.error_number = status;
    sSaveError = args.error_number;
    return status == 0 && sSaveError == 0 ? 0 : -1;
}

static void show_result(void)
{
    dclear(C_BLACK);
    dtext(4, 2, C_WHITE, "OS paging state probe");

    if (sSaveError == 0) {
        dtext(4, 26, C_GREEN, "OS-PAGING.TXT saved");
        dtext(4, 44, C_WHITE, DUMP_PATH);
        dprint(4, 62, C_WHITE, "Bytes written: %u", (unsigned)sDumpSize);
        dtext(4, 88, C_WHITE, "Copy file from storage memory");
        dtext(4, 102, C_WHITE, "over USB for analysis.");
        dtext(4, 132, C_WHITE, "OS addresses are candidate offsets;");
        dtext(4, 146, C_WHITE, "not validated for OS 3.20.0202.");
    } else {
        dtext(4, 26, C_WHITE, "Could not save OS-PAGING.TXT");
        dprint(4, 44, C_WHITE, "File error: %d", sSaveError);
        dtext(4, 70, C_WHITE, "Check storage memory and retry.");
    }

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
    (void)isappli;
    (void)optnum;

    snapshot();
    sSaveError = build_dump() == 0 ? 0 : EOVERFLOW;
    if (sSaveError == 0)
        (void)save_dump();

    show_result();
    return 0;
}

/*
 * fx-CG50 upper-DRAM validation add-in.
 *
 * Tests physical DRAM 0x0c4e0000..0x0c7fffff through its uncached P2 alias
 * 0xac4e0000..0xac800000. The normal Picori linker uses the cached P1 alias
 * 0x8c4e0000..0x8c800000 for .xbss.
 *
 * Each 4 KiB block is backed up into ordinary add-in RAM, tested, restored,
 * and checked against the backup before the next block is touched. This
 * limits the amount of candidate memory modified at any one time and leaves
 * the candidate region's original contents intact on a successful run.
 *
 * This program deliberately does NOT call Picori, clear .xbss, sleep the CPU,
 * change clocks, or write to any flash/peripheral address.
 */
#include <gint/bfile.h>
#include <gint/display.h>
#include <gint/keyboard.h>

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define SYSRAM_P1_BASE UINT32_C(0x8c4e0000)
#define SYSRAM_P2_BASE UINT32_C(0xac4e0000)
#define SYSRAM_P1_END  UINT32_C(0x8c800000) /* exclusive */
#define SYSRAM_BYTES  (SYSRAM_P1_END - SYSRAM_P1_BASE)

#define BLOCK_BYTES 4096u
#define BLOCK_WORDS (BLOCK_BYTES / sizeof(uint32_t))
#define BLOCK_COUNT (SYSRAM_BYTES / BLOCK_BYTES)

#define LOG_CAPACITY 3072

static uint32_t s_backup[BLOCK_WORDS];
/* One saved word per 4 KiB block for cross-block address-line testing. */
static uint32_t s_page_original[BLOCK_COUNT];
static char s_log[LOG_CAPACITY];
static size_t s_log_length;

static uint32_t s_blocks_completed;
static uint32_t s_checks;
static uint32_t s_failed_checks;
static uint32_t s_restore_failures;
static uint32_t s_first_address;
static uint32_t s_first_expected;
static uint32_t s_first_actual;
static const char *s_first_phase;

static volatile uint8_t *const s_ram8 =
    (volatile uint8_t *)(uintptr_t)SYSRAM_P2_BASE;

static void reportf(const char *format, ...)
{
    char temp[192];
    va_list args;
    va_start(args, format);
    int n = vsnprintf(temp, sizeof(temp), format, args);
    va_end(args);
    if (n <= 0)
        return;

    size_t copy = (size_t)n;
    if (copy >= sizeof(temp))
        copy = sizeof(temp) - 1;
    if (copy > sizeof(s_log) - s_log_length - 1)
        copy = sizeof(s_log) - s_log_length - 1;
    memcpy(s_log + s_log_length, temp, copy);
    s_log_length += copy;
    s_log[s_log_length] = '\0';
}

static void note_failure(uint32_t offset, uint32_t expected, uint32_t actual,
                         const char *phase)
{
    if (s_failed_checks == 0) {
        s_first_address = SYSRAM_P1_BASE + offset;
        s_first_expected = expected;
        s_first_actual = actual;
        s_first_phase = phase;
    }
    if (s_failed_checks != UINT32_MAX)
        s_failed_checks++;
}

static int test_bytes(volatile uint8_t *p, uint32_t offset, uint8_t pattern,
                      const char *phase)
{
    for (uint32_t i = 0; i < BLOCK_BYTES; i++)
        p[i] = pattern;
    for (uint32_t i = 0; i < BLOCK_BYTES; i++) {
        uint8_t got = p[i];
        if (got != pattern) {
            note_failure(offset + i, pattern, got, phase);
            return 0;
        }
    }
    s_checks++;
    return 1;
}

static int test_halfwords(volatile uint16_t *p, uint32_t offset,
                          uint16_t pattern, const char *phase)
{
    for (uint32_t i = 0; i < BLOCK_BYTES / sizeof(uint16_t); i++)
        p[i] = pattern;
    for (uint32_t i = 0; i < BLOCK_BYTES / sizeof(uint16_t); i++) {
        uint16_t got = p[i];
        if (got != pattern) {
            note_failure(offset + i * sizeof(uint16_t), pattern, got, phase);
            return 0;
        }
    }
    s_checks++;
    return 1;
}

static int test_words(volatile uint32_t *p, uint32_t offset,
                      uint32_t block_index, uint32_t pattern,
                      int address_pattern, const char *phase)
{
    for (uint32_t i = 0; i < BLOCK_WORDS; i++) {
        uint32_t expected = pattern;
        if (address_pattern) {
            uint32_t word_address = block_index * BLOCK_WORDS + i;
            expected = UINT32_C(0xa5c35a7d) ^
                       (word_address * UINT32_C(0x9e3779b9)) ^
                       (word_address >> 5);
        }
        p[i] = expected;
    }

    for (uint32_t i = 0; i < BLOCK_WORDS; i++) {
        uint32_t expected = pattern;
        if (address_pattern) {
            uint32_t word_address = block_index * BLOCK_WORDS + i;
            expected = UINT32_C(0xa5c35a7d) ^
                       (word_address * UINT32_C(0x9e3779b9)) ^
                       (word_address >> 5);
        }
        uint32_t got = p[i];
        if (got != expected) {
            note_failure(offset + i * sizeof(uint32_t), expected, got, phase);
            return 0;
        }
    }
    s_checks++;
    return 1;
}

static void test_walking_bits(volatile uint32_t *p, uint32_t offset)
{
    /* Walk both polarities through all 32 data bits at each 4 KiB block. */
    for (unsigned bit = 0; bit < 32; bit++) {
        uint32_t one = UINT32_C(1) << bit;
        p[0] = one;
        uint32_t got = p[0];
        if (got != one) {
            note_failure(offset, one, got, "walk-one");
            return;
        }
        p[0] = ~one;
        got = p[0];
        if (got != ~one) {
            note_failure(offset, ~one, got, "walk-zero");
            return;
        }
    }
    s_checks++;
}

static uint32_t page_signature(uint32_t block_index)
{
    /* Multiplication by an odd constant gives distinct signatures for all
     * 800 block indices in the 32-bit word domain. */
    return UINT32_C(0x6d2b79f5) ^
           (block_index * UINT32_C(0x9e3779b9));
}

static void test_page_address_lines(void)
{
    /* Preserve the first word from every 4 KiB block before changing any.
     * Unique signatures are then resident at all block bases together, so
     * aliased high address lines cannot masquerade as independent blocks. */
    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        volatile uint32_t *word = (volatile uint32_t *)(uintptr_t)
            (SYSRAM_P2_BASE + block * BLOCK_BYTES);
        s_page_original[block] = *word;
    }

    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        volatile uint32_t *word = (volatile uint32_t *)(uintptr_t)
            (SYSRAM_P2_BASE + block * BLOCK_BYTES);
        *word = page_signature(block);
    }

    int signatures_ok = 1;
    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        volatile uint32_t *word = (volatile uint32_t *)(uintptr_t)
            (SYSRAM_P2_BASE + block * BLOCK_BYTES);
        uint32_t expected = page_signature(block);
        uint32_t actual = *word;
        if (actual != expected) {
            note_failure(block * BLOCK_BYTES, expected, actual,
                         "cross-block-address");
            signatures_ok = 0;
        }
    }
    if (signatures_ok)
        s_checks++;

    /* Restore every saved word even after a signature mismatch, then verify
     * all locations. If two addresses alias, their original saved values
     * are necessarily identical because both read the same physical word. */
    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        volatile uint32_t *word = (volatile uint32_t *)(uintptr_t)
            (SYSRAM_P2_BASE + block * BLOCK_BYTES);
        *word = s_page_original[block];
    }

    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        volatile uint32_t *word = (volatile uint32_t *)(uintptr_t)
            (SYSRAM_P2_BASE + block * BLOCK_BYTES);
        uint32_t actual = *word;
        if (actual != s_page_original[block]) {
            note_failure(block * BLOCK_BYTES, s_page_original[block], actual,
                         "cross-block-restore");
            s_restore_failures++;
        }
    }
}

static void show_progress(uint32_t completed, const char *phase)
{
    dclear(C_WHITE);
    dtext(4, 5, C_BLACK, "fx-CG50 DRAM check");
    dtext(4, 22, C_BLACK, phase);
    dprint(4, 42, C_BLACK, "Blocks: %u / %u", completed, (unsigned)BLOCK_COUNT);
    dprint(4, 58, C_BLACK, "Checked: %u KiB", completed * 4u);
    dprint(4, 74, C_BLACK, "Failed checks: %u", s_failed_checks);
    dtext(4, 202, C_BLACK, "Please keep the calculator powered");
    dupdate();
}

static void test_block(uint32_t block_index)
{
    uint32_t offset = block_index * BLOCK_BYTES;
    volatile uint8_t *bytes = s_ram8 + offset;
    volatile uint16_t *halfwords = (volatile uint16_t *)(uintptr_t)bytes;
    volatile uint32_t *words = (volatile uint32_t *)(uintptr_t)bytes;

    /* Preserve this block before any write to the candidate range. */
    for (uint32_t i = 0; i < BLOCK_WORDS; i++)
        s_backup[i] = words[i];

    (void)test_bytes(bytes, offset, 0x00, "byte-00");
    (void)test_bytes(bytes, offset, 0xff, "byte-ff");
    (void)test_bytes(bytes, offset, 0xaa, "byte-aa");
    (void)test_bytes(bytes, offset, 0x55, "byte-55");

    (void)test_halfwords(halfwords, offset, 0x0000, "half-0000");
    (void)test_halfwords(halfwords, offset, 0xffff, "half-ffff");
    (void)test_halfwords(halfwords, offset, 0xaaaa, "half-aaaa");
    (void)test_halfwords(halfwords, offset, 0x5555, "half-5555");

    (void)test_words(words, offset, block_index, 0x00000000, 0, "word-00000000");
    (void)test_words(words, offset, block_index, 0xffffffff, 0, "word-ffffffff");
    (void)test_words(words, offset, block_index, 0xaaaaaaaa, 0, "word-aaaaaaaa");
    (void)test_words(words, offset, block_index, 0x55555555, 0, "word-55555555");
    (void)test_words(words, offset, block_index, 0, 1, "address-pattern");
    test_walking_bits(words, offset);

    /* Restore and verify every original word before advancing. */
    for (uint32_t i = 0; i < BLOCK_WORDS; i++)
        words[i] = s_backup[i];
    int restored = 1;
    for (uint32_t i = 0; i < BLOCK_WORDS; i++) {
        uint32_t got = words[i];
        if (got != s_backup[i]) {
            note_failure(offset + i * sizeof(uint32_t), s_backup[i], got, "restore");
            s_restore_failures++;
            restored = 0;
            break;
        }
    }
    if (restored)
        s_checks++;
}

static int save_report(void)
{
    uint16_t const *path = u"\\\\fls0\\RAMCHECK.TXT";
    BFile_Remove(path);
    if (s_log_length & 1)
        s_log[s_log_length++] = '\n';
    s_log[s_log_length] = '\0';

    int size = (int)s_log_length;
    int rc = BFile_Create(path, BFile_File, &size);
    if (rc < 0)
        return rc;

    int fd = BFile_Open(path, BFile_WriteOnly);
    if (fd < 0)
        return fd;

    rc = BFile_Write(fd, s_log, (int)s_log_length);
    BFile_Close(fd);
    return rc == (int)s_log_length ? 0 : (rc < 0 ? rc : -1);
}

int main(void)
{
    dclear(C_WHITE);
    dtext(4, 5, C_BLACK, "fx-CG50 upper DRAM check");
    dtext(4, 24, C_BLACK, "Physical: 0C4E0000-0C7FFFFF");
    dtext(4, 40, C_BLACK, "Uncached P2: AC4E0000-AC800000");
    dtext(4, 58, C_BLACK, "Size: 3200 KiB (800 x 4 KiB)");
    dtext(4, 82, C_BLACK, "Each 4 KiB block is saved/restored.");
    dtext(4, 98, C_BLACK, "Final pass changes one word per page.");
    dtext(4, 114, C_BLACK, "Do not interrupt either test pass.");
    dtext(4, 130, C_BLACK, "Keep power connected during test.");
    dtext(4, 160, C_BLACK, "Press any key to begin.");
    dupdate();
    (void)getkey();

    reportf("FXCG50_RAMCHECK v2\n");
    reportf("P1 range: 0x%08lx..0x%08lx (end exclusive)\n",
         (unsigned long)SYSRAM_P1_BASE, (unsigned long)SYSRAM_P1_END);
    reportf("P2 alias: 0x%08lx..0x%08lx (uncached)\n",
         (unsigned long)SYSRAM_P2_BASE,
         (unsigned long)(SYSRAM_P2_BASE + SYSRAM_BYTES));
    reportf("Bytes: %lu; KiB: %lu; blocks: %lu; block_bytes: %u\n",
         (unsigned long)SYSRAM_BYTES, (unsigned long)(SYSRAM_BYTES / 1024u),
         (unsigned long)BLOCK_COUNT, BLOCK_BYTES);
    reportf("Method: byte/halfword/word patterns, within-block address pattern, "
         "walking bits, cross-block page signatures; save/restore/verify\n");

    for (uint32_t block = 0; block < BLOCK_COUNT; block++) {
        test_block(block);
        s_blocks_completed++;
        if ((s_blocks_completed % 32u) == 0 || s_blocks_completed == BLOCK_COUNT)
            show_progress(s_blocks_completed, "Testing uncached P2 DRAM...");
    }

    show_progress(s_blocks_completed, "Testing cross-block address lines...");
    test_page_address_lines();

    int pass = (s_blocks_completed == BLOCK_COUNT &&
                s_failed_checks == 0 && s_restore_failures == 0);
    reportf("Blocks completed: %lu / %lu\n", (unsigned long)s_blocks_completed,
         (unsigned long)BLOCK_COUNT);
    reportf("Checks passed: %lu\n", (unsigned long)(s_checks));
    reportf("Failed checks: %lu\n", (unsigned long)s_failed_checks);
    reportf("Restore failures: %lu\n", (unsigned long)s_restore_failures);
    if (s_failed_checks) {
        reportf("First failure: phase=%s P1=0x%08lx P2=0x%08lx "
             "expected=0x%08lx actual=0x%08lx\n",
             s_first_phase ? s_first_phase : "unknown",
             (unsigned long)s_first_address,
             (unsigned long)(SYSRAM_P2_BASE + (s_first_address - SYSRAM_P1_BASE)),
             (unsigned long)s_first_expected, (unsigned long)s_first_actual);
    }
    reportf("RESULT: %s\n", pass ? "PASS" : "FAIL");

    int save_rc = save_report();

    dclear(C_WHITE);
    dtext(4, 5, C_BLACK, "fx-CG50 DRAM check finished");
    dtext(4, 30, pass ? C_BLACK : C_RGB(31, 0, 0), pass ? "RESULT: PASS" : "RESULT: FAIL");
    dprint(4, 52, C_BLACK, "Blocks: %u / %u", s_blocks_completed, (unsigned)BLOCK_COUNT);
    dprint(4, 68, C_BLACK, "Failed checks: %u", s_failed_checks);
    dprint(4, 84, C_BLACK, "Restore failures: %u", s_restore_failures);
    if (s_failed_checks) {
        dprint(4, 108, C_BLACK, "First P1 address: %08X", (unsigned)s_first_address);
        dprint(4, 124, C_BLACK, "Expected: %08X", (unsigned)s_first_expected);
        dprint(4, 140, C_BLACK, "Actual:   %08X", (unsigned)s_first_actual);
    }
    dtext(4, 174, C_BLACK, save_rc == 0 ? "Saved: \\fls0\\RAMCHECK.TXT" : "Could not save report file");
    dtext(4, 202, C_BLACK, "Press any key to exit.");
    dupdate();
    (void)getkey();
    return pass ? 0 : 1;
}

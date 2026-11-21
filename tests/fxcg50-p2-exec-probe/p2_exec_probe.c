/*
 * Isolated SH-4A P2 instruction-fetch and return probe for the fx-CG50.
 *
 * This does not read or modify the UTLB, MMUCR, VBR, or exception vectors.
 * It copies three known SH instructions to the last 4 KiB page of the
 * already-tested upper-DRAM region, applies the SH-4A self-modifying-code
 * sequence, and calls the bytes through the uncached P2 alias.
 */
#include <stdint.h>

#include <gint/display.h>
#include <gint/keyboard.h>

#define P2_PROBE_ADDRESS ((uintptr_t)0xac7ff000u)
#define P2_PROBE_EXPECTED 0x35u

/* Big-endian SH-4 instructions:
 *   e0 35   mov #0x35,r0
 *   00 0b   rts
 *   00 09   nop (RTS delay slot)
 */
static const uint8_t sProbeCode[] = {
    0xe0, 0x35,
    0x00, 0x0b,
    0x00, 0x09,
};

typedef uint32_t (*P2ProbeFunction)(void);

static void ShowResult(const char *line1, const char *line2) {
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "fx-CG50 P2 execution probe");
    dtext(4, 24, C_WHITE, line1);
    if (line2)
        dtext(4, 40, C_WHITE, line2);
    dtext(4, 72, C_WHITE, "Press EXE to close");
    dupdate();
    getkey();
}

int main(void) {
    volatile uint8_t *destination =
        (volatile uint8_t *)P2_PROBE_ADDRESS;

    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "fx-CG50 P2 execution probe");
    dtext(4, 24, C_WHITE, "Copying code to 0xAC7FF000");
    dtext(4, 40, C_WHITE, "Then executing from P2...");
    dupdate();

    for (unsigned i = 0; i < sizeof(sProbeCode); i++)
        destination[i] = sProbeCode[i];

    /* The SH-4A manual requires SYNCO; ICBI before executing modified code
     * in a non-cacheable area, to drain prior operations and discard any
     * prefetched instruction state. P2 is non-cacheable on this CPU. */
    __asm__ volatile("synco\n\ticbi @%0"
                     :
                     : "r"(destination)
                     : "memory");

    for (unsigned i = 0; i < sizeof(sProbeCode); i++) {
        if (destination[i] != sProbeCode[i]) {
            ShowResult("Code copy/readback: FAIL",
                       "P2 execution was not attempted");
            return 1;
        }
    }

    /* The probe is leaf code: it uses no stack, globals, literal pool, or
     * external calls. RTS returns through the PR set by this C call. */
    P2ProbeFunction function =
        (P2ProbeFunction)(uintptr_t)P2_PROBE_ADDRESS;
    uint32_t result = function();

    if (result == P2_PROBE_EXPECTED) {
        ShowResult("P2 execute + return: PASS",
                   "Returned expected value 0x00000035");
        return 0;
    }

    ShowResult("P2 execute + return: FAIL",
               "Returned an unexpected value");
    return 1;
}

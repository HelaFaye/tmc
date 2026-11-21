/*
 * fx-CG50 SH-4A SR flag readback probe.
 *
 * Runs RB-only, BL-only, and combined RB|BL P2 intervals. Each interval reads
 * SR while the requested state is active, compares both bits against the
 * requested mask, restores the original SR, then returns a status-coded value.
 * No real exception is generated and VBR/MMU/TLB state is not modified.
 */
#include <stddef.h>
#include <stdint.h>

#include <gint/display.h>
#include <gint/keyboard.h>

#define P2_WRAPPER_ADDRESS ((uintptr_t)0xac7ff100u)
#define P2_WRAPPER_EXPECTED_SIZE 68u
#define P2_WRAPPER_MAX 0x100u
#define P2_TEST_PAGE_END ((uintptr_t)0xac800000u)
#define P2_WRAPPER_PASS_RETURN 0x155u
#define SR_MD 0x40000000u
#define SR_RB 0x20000000u
#define SR_BL 0x10000000u
#define SR_RB_BL (SR_RB | SR_BL)

typedef uint32_t (*P2WrapperFunction)(uint32_t flags);

extern const uint8_t P2ExecInTlbMissSrStateTemplate[];
extern const uint8_t P2ExecInTlbMissSrStateTemplateEnd[];

static uint32_t ReadSr(void)
{
    uint32_t sr;
    __asm__ volatile("stc sr,%0" : "=r"(sr));
    return sr;
}

static void ShowProgress(const char *stage, const char *detail)
{
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "fx-CG50 P2 flag isolation");
    dtext(4, 24, C_WHITE, stage);
    if (detail)
        dtext(4, 40, C_WHITE, detail);
    dtext(4, 72, C_WHITE, "Each stage should be brief");
    dupdate();
}

static void ShowResult(const char *line1, const char *line2)
{
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, "fx-CG50 P2 flag isolation");
    dtext(4, 24, C_WHITE, line1);
    if (line2)
        dtext(4, 40, C_WHITE, line2);
    dtext(4, 72, C_WHITE, "Press EXE to continue/close");
    dupdate();
    #ifndef TMC_FXCG_P2_EMULATOR_TEST
    getkey();
    #endif
}

int main(void)
{
    volatile uint8_t *wrapper = (volatile uint8_t *)P2_WRAPPER_ADDRESS;
    const size_t wrapper_size = (size_t)(
        (uintptr_t)P2ExecInTlbMissSrStateTemplateEnd -
        (uintptr_t)P2ExecInTlbMissSrStateTemplate);

    ShowProgress("1/9: Checking template size", "Expected: 68 bytes");
    if (wrapper_size != P2_WRAPPER_EXPECTED_SIZE ||
        wrapper_size > P2_WRAPPER_MAX) {
        ShowResult("Template size: FAIL", "No SR change was attempted");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }

    ShowProgress("2/9: Checking P2 destination", "Inside tested upper DRAM page");
    if (P2_WRAPPER_ADDRESS + wrapper_size > P2_TEST_PAGE_END) {
        ShowResult("P2 destination: FAIL", "Template exceeds tested RAM range");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }

    ShowProgress("3/9: Copying P2 wrapper", "68 bytes at 0xac7ff100");
    for (size_t i = 0; i < wrapper_size; i++)
        wrapper[i] = P2ExecInTlbMissSrStateTemplate[i];

    ShowProgress("4/9: Synchronizing I-cache", "SYNCO + ICBI");
    uintptr_t wrapper_line1 = P2_WRAPPER_ADDRESS + 32u;
    uintptr_t wrapper_line2 = P2_WRAPPER_ADDRESS + 64u;
    __asm__ volatile("synco\n\ticbi @%0\n\ticbi @%1\n\ticbi @%2"
                     :
                     : "r"(wrapper), "r"(wrapper_line1), "r"(wrapper_line2)
                     : "memory");

    ShowProgress("5/9: Verifying copied wrapper", "Readback all 68 bytes");
    for (size_t i = 0; i < wrapper_size; i++) {
        if (wrapper[i] != P2ExecInTlbMissSrStateTemplate[i]) {
            ShowResult("Wrapper readback: FAIL", "SR unchanged");
            #ifdef TMC_FXCG_P2_EMULATOR_TEST
            for (;;) __asm__ volatile("nop");
            #endif
            return 1;
        }
    }

    ShowProgress("6/9: Checking initial SR", "Requires MD=1, RB=0, BL=0");
    uint32_t sr = ReadSr();
    if (!(sr & SR_MD)) {
        ShowResult("SR precondition: FAIL", "Not in privileged mode");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }
    if (sr & SR_RB_BL) {
        ShowResult("SR precondition: FAIL", "RB or BL already set");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }

    ShowProgress("7/9: RB-only P2 test", "RB=1; BL=0; three NOPs");
    uint32_t result = ((P2WrapperFunction)P2_WRAPPER_ADDRESS)(SR_RB);
    if (result != P2_WRAPPER_PASS_RETURN) {
        ShowResult("RB-only P2: FAIL", "SR flag readback mismatch");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }
    ShowResult("RB-only P2: PASS", "SR bits read back; SR restored");

    ShowProgress("8/9: BL-only P2 test", "BL=1; RB=0; three NOPs");
    result = ((P2WrapperFunction)P2_WRAPPER_ADDRESS)(SR_BL);
    if (result != P2_WRAPPER_PASS_RETURN) {
        ShowResult("BL-only P2: FAIL", "SR flag readback mismatch");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }

    ShowResult("BL-only P2: PASS", "SR bits read back; SR restored");

    ShowProgress("9/9: Combined RB+BL P2 test",
        "RB=1; BL=1; three NOPs");
    result = ((P2WrapperFunction)P2_WRAPPER_ADDRESS)(SR_RB_BL);
    if (result != P2_WRAPPER_PASS_RETURN) {
        ShowResult("Combined RB+BL: FAIL", "SR flag readback mismatch");
        #ifdef TMC_FXCG_P2_EMULATOR_TEST
        for (;;) __asm__ volatile("nop");
        #endif
        return 1;
    }

    ShowResult("Combined RB+BL: PASS", "SR bits read back; SR restored");
    #ifdef TMC_FXCG_P2_EMULATOR_TEST
    dtext(4, 24, C_GREEN, "Flag isolation: PASS");
    dupdate();
    #endif
    #ifdef TMC_FXCG_P2_EMULATOR_TEST
    for (;;) __asm__ volatile("nop");
    #endif
    return 0;
}

/* Test-only MMU extension for the pinned casio-emu interpreter (used with
 * --d1-pager). See mmu_window.c. Inactive unless mmuWindowActive is set, so
 * the ordinary smoke tests run the emulator exactly as before. */
#pragma once
#include <setjmp.h>
#include <stdbool.h>
#include "int.h"

extern int mmuWindowActive;
extern jmp_buf mmuFaultJmp;
extern u32 mmuInstrPC;
extern bool mmuInSlot;
extern u32 mmuDone; /* nonzero once the add-in wrote the done port */

/* Translate a ROM-window address through the UTLB model; longjmp on miss. */
u32 mmuWindowTranslate(u32 address, bool write);
/* Writes the hooks route here first; true if consumed. */
bool mmuSpecialWrite(u32 address, u32 value);
/* At PC 0x80020070: emulate OS syscall 0x09; true if handled. */
bool mmuSyscall(void);
/* After a longjmp: take the TLB-miss exception for the aborted instruction. */
void mmuRaiseMiss(void);
/* Whether a pending interrupt must wait (BL set, or IMASK 15). */
bool mmuInterruptsHeld(void);

/* Provided by the patched tlb.c. */
int utlbFind4k(u32 va, u32 *pa);
void utlbSetEntry(u32 e, u32 addressWord, u32 dataWord);
void utlbArrayWrite(u32 address, u32 value);

#define MMU_WINDOW_HOOK(address, write)                                       \
    do {                                                                      \
        if (mmuWindowActive && (u32)((address) - 0x20000000u) < 0x01000000u) \
            (address) = mmuWindowTranslate((address), (write));              \
    } while (0)

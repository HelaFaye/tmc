/* Test-only MMU extension for the pinned casio-emu interpreter.
 *
 * The emulator maps add-in memory with a flat page table and has no MMU, no
 * TLB-miss exceptions and no OS. That is enough for Picori's boot smoke
 * test but cannot exercise the D1 pager (docs/fx-cg50-d1-pager.md). With
 * --d1-pager the harness adds, for the ROM window [0x20000000, 0x21000000)
 * only:
 *
 *  - translation through the emulator's 64-entry UTLB model (tlb.c) for
 *    valid 4 KiB entries;
 *  - a precise TLB-miss exception on a miss: the access is aborted
 *    (longjmp back to the interpreter loop), SPC = the faulting instruction
 *    (the branch, for a delay slot), SSR/SGR saved, EXPEVT 0x040/0x060,
 *    TEA = address, SR.MD/RB/BL set, PC = VBR + 0x400;
 *  - OS syscall 0x09 (map one 4 KiB page: r4 VA, r5 PA, r6 flags) into
 *    round-robin slots 0-54, as D1-MAP-PROBE observed on OS 3.20.0202;
 *  - writes to the memory-mapped UTLB address/data arrays, including
 *    associative writes, so the P2 eviction helper can clear entries;
 *  - interrupts held while SR.BL = 1 or IMASK = 15, as on hardware.
 *
 * This checks the pager's logic (handler paths, upcall register state,
 * eviction) on an interpreter, not the SH7305: caches, ITLB, the real OS
 * mapper and P2-only access rules are not modelled. */
#include "mmu_window.h"

#include <stdio.h>
#include <stdlib.h>

#include "cpu.h"

extern CpuState cpu;

int mmuWindowActive;
jmp_buf mmuFaultJmp;
u32 mmuInstrPC;
bool mmuInSlot;
u32 mmuDone;

static u32 sFaultAddress;
static bool sFaultWrite;
static u32 sOsSlot; /* the OS's round-robin counter, 0-54 */
static u32 sMisses, sMaps;

u32 mmuWindowTranslate(u32 address, bool write) {
    u32 pa;
    if (utlbFind4k(address, &pa))
        return 0x80000000u | pa; /* P1 alias of the physical address */
    sFaultAddress = address;
    sFaultWrite = write;
    longjmp(mmuFaultJmp, 1);
}

void mmuRaiseMiss(void) {
    sMisses++;
    if (cpu.reg.SR_parts.BL) {
        printf("D1_EMU: TLB miss at %08x with SR.BL=1 (PC %08x): the hardware would reset\n",
               sFaultAddress, mmuInstrPC);
        exit(3);
    }
    cpu.reg.SPC = mmuInSlot ? mmuInstrPC - 2 : mmuInstrPC;
    cpu.reg.SSR = cpu.reg.SR;
    cpu.reg.SGR = cpu.reg.r15;
    cpu.reg.EXPEVT = sFaultWrite ? 0x060 : 0x040;
    cpu.reg.TEA = sFaultAddress;
    cpu.isBranchDelaySlot = false;
    cpu.branchDelayDone = false;
    cpu.reg.SR_parts.MD = 1;
    cpu.reg.SR_parts.BL = 1;
    if (!cpu.reg.SR_parts.RB) {
        cpu.reg.SR_parts.RB = 1;
        for (int i = 0; i < 8; i++) {
            u32 t = cpu.reg.regArray[i];
            cpu.reg.regArray[i] = cpu.reg.regArray[i + 16];
            cpu.reg.regArray[i + 16] = t;
        }
    }
    cpu.reg.PC = cpu.reg.VBR + 0x400;
}

bool mmuSyscall(void) {
    if (cpu.reg.r0 != 0x09)
        return false;
    u32 va = cpu.reg.r4 & 0xFFFFF000u, pa = cpu.reg.r5 & 0x1FFFF000u, flags = cpu.reg.r6 & 0xFFu;
    /* 4 KiB (SZ1 = 0, SZ0 = 1), valid, with the caller's PR/C/D bits. */
    u32 data = pa | 0x100u | 0x10u | (flags & 0x6Cu);
    utlbSetEntry(sOsSlot, va | 0x100u, data);
    sOsSlot = (sOsSlot + 1) % 55;
    sMaps++;
    cpu.reg.r0 = 0;
    cpu.reg.PC = cpu.reg.PR;
    return true;
}

bool mmuSpecialWrite(u32 address, u32 value) {
    if (address >= 0xf6000000u && address <= 0xf70fffffu) {
        utlbArrayWrite(address, value);
        return true;
    }
    if (address == 0xfffff004u) { /* test done port */
        mmuDone = 0x100u | (value & 0xFFu);
        printf("D1_EMU_STATS misses=%u os_maps=%u\n", sMisses, sMaps);
        return true;
    }
    return false;
}

bool mmuInterruptsHeld(void) {
    return mmuWindowActive && (cpu.reg.SR_parts.BL || ((cpu.reg.SR >> 4) & 0xF) == 0xF);
}

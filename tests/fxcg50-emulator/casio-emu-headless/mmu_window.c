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
#include <string.h>

#include "cpu.h"
#include "memory/memory.h"

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

/* ---- --fs DIR: the OS file syscalls gint's fopen/fread/fseek use ----
 * Paths arrive as UTF-16 "\\\\fls0\\NAME"; NAME is opened in DIR on the
 * host, read-only. Enough for the D1 pak test; writes are refused. */
const char* mmuFsDir;
#define FS_MAX 8
static FILE* sFsFile[FS_MAX];
static u32 sFsReads, sFsBytes;

static int FsHostPath(u32 guest, char* out, size_t cap) {
    char name[256];
    size_t n = 0;
    for (u32 a = guest; n + 1 < sizeof(name); a += 2) {
        u32 c = readMemory(a, 2);
        if (!c)
            break;
        name[n++] = c < 0x80 ? (char)c : '?';
    }
    name[n] = 0;
    const char* base = strrchr(name, '\\');
    base = base ? base + 1 : name;
    if (!*base || strchr(base, '/') || strstr(base, ".."))
        return 0;
    snprintf(out, cap, "%s/%s", mmuFsDir, base);
    return 1;
}

static long FsSize(FILE* f) {
    long pos = ftell(f), size;
    fseek(f, 0, SEEK_END);
    size = ftell(f);
    fseek(f, pos, SEEK_SET);
    return size;
}

static int FsSyscall(u32 id, u32* result) {
    char path[512];
    int fd = (int)cpu.reg.r4;
    FILE* f = (fd >= 0 && fd < FS_MAX) ? sFsFile[fd] : NULL;
    switch (id) {
    case 0x1db6: { /* FindFirst(path, int* handle, u16* found, info*) */
        writeMemory(cpu.reg.r5, 4, 0);
        FILE* t = FsHostPath(cpu.reg.r4, path, sizeof(path)) ? fopen(path, "rb") : NULL;
        if (!t) {
            *result = (u32)-1; /* BFile_EntryNotFound */
            return 1;
        }
        long size = FsSize(t);
        fclose(t);
        writeMemory(cpu.reg.r6, 2, 0);
        u32 info = cpu.reg.r7;
        writeMemory(info + 0, 2, 0);
        writeMemory(info + 2, 2, 1); /* BFile_Type_File */
        writeMemory(info + 4, 4, (u32)size);
        writeMemory(info + 8, 4, (u32)size);
        writeMemory(info + 12, 4, 0);
        writeMemory(info + 16, 4, 0);
        *result = 0;
        return 1;
    }
    case 0x1dba: /* FindClose */
        *result = 0;
        return 1;
    case 0x1da3: { /* Open(path, mode) */
        if ((cpu.reg.r5 & 0x02) || !FsHostPath(cpu.reg.r4, path, sizeof(path))) {
            *result = (u32)-1;
            return 1;
        }
        for (int i = 0; i < FS_MAX; i++)
            if (!sFsFile[i]) {
                sFsFile[i] = fopen(path, "rb");
                *result = sFsFile[i] ? (u32)i : (u32)-1;
                return 1;
            }
        *result = (u32)-1;
        return 1;
    }
    case 0x1da4: /* Close(fd) */
        if (f)
            fclose(f), sFsFile[fd] = NULL;
        *result = f ? 0 : (u32)-1;
        return 1;
    case 0x1da6: /* Size(fd) */
        *result = f ? (u32)FsSize(f) : (u32)-1;
        return 1;
    case 0x1da9: { /* Seek(fd, absolute offset) -> bytes left */
        if (!f) {
            *result = (u32)-1;
            return 1;
        }
        long size = FsSize(f), off = (long)(int)cpu.reg.r5;
        if (off > size)
            off = size;
        fseek(f, off, SEEK_SET);
        *result = (u32)(size - off);
        return 1;
    }
    case 0x1dab: /* GetPos(fd) */
        *result = f ? (u32)ftell(f) : (u32)-1;
        return 1;
    case 0x1dac: { /* Read(fd, buf, size, whence: -1 = current, else absolute) */
        if (!f) {
            *result = (u32)-1;
            return 1;
        }
        int whence = (int)cpu.reg.r7;
        if (whence >= 0)
            fseek(f, whence, SEEK_SET);
        u32 buf = cpu.reg.r5, size = cpu.reg.r6, done = 0;
        int c;
        while (done < size && (c = fgetc(f)) != EOF)
            writeMemory(buf + done++, 1, (u32)c);
        sFsReads++;
        sFsBytes += done;
        *result = done;
        return 1;
    }
    default:
        if (id >= 0x1da0 && id <= 0x1dc0) {
            printf("D1_EMU: unsupported file syscall %#x\n", id);
            exit(5);
        }
        return 0;
    }
}

bool mmuSyscall(void) {
    u32 result;
    if (mmuFsDir && FsSyscall(cpu.reg.r0, &result)) {
        cpu.reg.r0 = result;
        cpu.reg.PC = cpu.reg.PR;
        return true;
    }
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
        printf("D1_EMU_STATS misses=%u os_maps=%u fs_reads=%u fs_bytes=%u\n", sMisses, sMaps, sFsReads, sFsBytes);
        return true;
    }
    return false;
}

bool mmuInterruptsHeld(void) {
    return mmuWindowActive && (cpu.reg.SR_parts.BL || ((cpu.reg.SR >> 4) & 0xF) == 0xF);
}

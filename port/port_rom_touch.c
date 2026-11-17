/*
 * port_rom_touch.c - ROM working-set measurement. See port_rom_touch.h.
 */
#include "port_rom_touch.h"

#if defined(__linux__)

#define _GNU_SOURCE 1
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define TOUCH_PAGE_SHIFT 12u
#define TOUCH_MAX_PAGES 4096u /* 16 MB of ROM */

enum { PHASE_OFF, PHASE_LOAD, PHASE_PLAY };

static uint8_t* sBase;
static size_t sSize;
static volatile sig_atomic_t sPhase = PHASE_OFF;
static volatile uint32_t sFrame;
/* First-touch frame + 1 per page and phase; 0 = untouched. */
static volatile uint32_t sLoadTouch[TOUCH_MAX_PAGES];
static volatile uint32_t sPlayTouch[TOUCH_MAX_PAGES];
static struct sigaction sPreviousHandler;

static void RomTouchFault(int sig, siginfo_t* info, void* context) {
    uint8_t* address = (uint8_t*)info->si_addr;
    if (sBase && address >= sBase && address < sBase + sSize) {
        const size_t page = (size_t)(address - sBase) >> TOUCH_PAGE_SHIFT;
        if (page < TOUCH_MAX_PAGES) {
            volatile uint32_t* table = sPhase == PHASE_PLAY ? sPlayTouch : sLoadTouch;
            if (table[page] == 0)
                table[page] = sFrame + 1u;
        }
        mprotect(sBase + (page << TOUCH_PAGE_SHIFT), (size_t)1 << TOUCH_PAGE_SHIFT,
                 PROT_READ | PROT_WRITE);
        return;
    }
    /* Not ours: hand the fault to whatever was installed before. */
    sigaction(SIGSEGV, &sPreviousHandler, NULL);
    (void)context;
    raise(sig);
}

static void RomTouchWriteLog(void) {
    const char* path = getenv("TMC_ROM_TOUCH_LOG");
    if (!path || !sBase)
        return;
    FILE* f = fopen(path, "w");
    if (!f)
        return;
    unsigned loadCount = 0, playCount = 0;
    for (unsigned p = 0; p < TOUCH_MAX_PAGES; ++p) {
        if (sLoadTouch[p]) {
            fprintf(f, "load %u 0\n", p);
            ++loadCount;
        }
    }
    for (unsigned p = 0; p < TOUCH_MAX_PAGES; ++p) {
        if (sPlayTouch[p]) {
            fprintf(f, "play %u %u\n", p, (unsigned)(sPlayTouch[p] - 1u));
            ++playCount;
        }
    }
    fclose(f);
    fprintf(stderr, "[ROM touch] %u load pages, %u play pages over %u frames -> %s\n", loadCount, playCount,
            (unsigned)sFrame, path);
}

void* Port_RomTouch_Alloc(size_t size) {
    if (!getenv("TMC_ROM_TOUCH_LOG") || sBase || size == 0)
        return NULL;
    const size_t pageSize = (size_t)1 << TOUCH_PAGE_SHIFT;
    const size_t rounded = (size + pageSize - 1) & ~(pageSize - 1);
    void* buffer = mmap(NULL, rounded, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (buffer == MAP_FAILED)
        return NULL;
    sBase = (uint8_t*)buffer;
    sSize = rounded;
    return buffer;
}

int Port_RomTouch_Free(void* buffer) {
    if (!buffer || buffer != sBase)
        return 0;
    munmap(sBase, sSize);
    sBase = NULL;
    sSize = 0;
    sPhase = PHASE_OFF;
    return 1;
}

void Port_RomTouch_BeginLoad(void) {
    if (!sBase || sPhase != PHASE_OFF)
        return;
    struct sigaction action;
    memset(&action, 0, sizeof(action));
    action.sa_sigaction = RomTouchFault;
    action.sa_flags = SA_SIGINFO;
    sigemptyset(&action.sa_mask);
    sigaction(SIGSEGV, &action, &sPreviousHandler);
    atexit(RomTouchWriteLog);
    sPhase = PHASE_LOAD;
    mprotect(sBase, sSize, PROT_NONE);
    fprintf(stderr, "[ROM touch] recording ROM page accesses\n");
}

void Port_RomTouch_Frame(void) {
    if (sPhase == PHASE_OFF)
        return;
    if (sPhase == PHASE_LOAD) {
        sPhase = PHASE_PLAY;
        mprotect(sBase, sSize, PROT_NONE);
    }
    ++sFrame;
    /* TMC_ROM_TOUCH_FRAMES=N ends a scripted run after N play frames. */
    static long sLimit = -1;
    if (sLimit < 0) {
        const char* limit = getenv("TMC_ROM_TOUCH_FRAMES");
        sLimit = limit ? strtol(limit, NULL, 10) : 0;
    }
    if (sLimit > 0 && sFrame >= (uint32_t)sLimit) {
        RomTouchWriteLog();
        _exit(0);
    }
}

#else

void* Port_RomTouch_Alloc(size_t size) {
    (void)size;
    return NULL;
}
int Port_RomTouch_Free(void* buffer) {
    (void)buffer;
    return 0;
}
void Port_RomTouch_BeginLoad(void) {
}
void Port_RomTouch_Frame(void) {
}

#endif

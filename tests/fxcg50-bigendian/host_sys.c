/* Host stand-in for port/fxcg/fxcg_sys.c (gint) and fxcg_rom.c, so the
 * calculator configuration of the game (TMC_EMBEDDED, TMC_ROM_WINDOW, and
 * TMC_BE on big-endian hosts) runs as a Linux program. Built twice by
 * run_compare.sh: big-endian PowerPC under qemu-ppc and little-endian
 * 32-bit x86. Only byte order differs, so frames that differ point at
 * byte-order bugs in code the calculator runs.
 *
 * usage: picori-host <baserom.gba> <frames> [input-script] [ppm-every]
 *   input-script: "tick:button[:hold],..." as TMC_TEST_INPUT
 *                 (a b select start right left up down r l)
 * Prints "FRAME n hash" (FNV-1a of the 396x224 RGB565 screen) per frame. */
#include "fxcg_rom.h"
#include "fxcg_sys.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define WINDOW 0x20000000u

extern unsigned char* gRomData;
extern unsigned int gRomSize;

static uint16_t sVram[FXCG_SCREEN_W * FXCG_SCREEN_H];
static unsigned sFrame, sFrames, sPpmEvery;
static const char* sRomPath;

static struct {
    unsigned tick, until;
    uint16_t bit;
} sInput[256];
static int sInputCount;

static void ParseInput(const char* script) {
    static const char* names[] = { "a", "b", "select", "start", "right", "left", "up", "down", "r", "l" };
    char buf[4096];
    snprintf(buf, sizeof(buf), "%s", script);
    for (char* tok = strtok(buf, ","); tok && sInputCount < 256; tok = strtok(NULL, ",")) {
        unsigned tick = 0, hold = 1;
        char name[16] = "";
        if (sscanf(tok, "%u:%15[a-z]:%u", &tick, name, &hold) < 2)
            continue;
        for (int b = 0; b < 10; b++)
            if (strcmp(name, names[b]) == 0) {
                sInput[sInputCount].tick = tick;
                sInput[sInputCount].until = tick + (hold ? hold : 1);
                sInput[sInputCount].bit = (uint16_t)(1u << b);
                sInputCount++;
            }
    }
}

uint16_t Fxcg_ReadButtons(void) {
    uint16_t held = 0;
    for (int i = 0; i < sInputCount; i++)
        if (sFrame >= sInput[i].tick && sFrame < sInput[i].until)
            held |= sInput[i].bit;
    return held;
}

uint16_t* Fxcg_Vram(void) {
    return sVram;
}

static void SavePpm(void) {
    char name[64];
    snprintf(name, sizeof(name), "frame-%05u.ppm", sFrame);
    FILE* f = fopen(name, "wb");
    if (!f)
        return;
    fprintf(f, "P6\n%d %d\n255\n", FXCG_SCREEN_W, FXCG_SCREEN_H);
    for (int i = 0; i < FXCG_SCREEN_W * FXCG_SCREEN_H; i++) {
        uint16_t p = sVram[i];
        unsigned char rgb[3] = { (unsigned char)((p >> 11) << 3), (unsigned char)(((p >> 5) & 63) << 2),
                                 (unsigned char)((p & 31) << 3) };
        fwrite(rgb, 3, 1, f);
    }
    fclose(f);
}

void Fxcg_Update(void) {
    uint32_t h = 2166136261u;
    for (int i = 0; i < FXCG_SCREEN_W * FXCG_SCREEN_H; i++) {
        h = (h ^ (sVram[i] & 0xFF)) * 16777619u;
        h = (h ^ (sVram[i] >> 8)) * 16777619u;
    }
    printf("FRAME %u %08x\n", sFrame, (unsigned)h);
    if (sPpmEvery && sFrame % sPpmEvery == 0)
        SavePpm();
}

void Fxcg_Clear(void) {
    memset(sVram, 0, sizeof(sVram));
}

void Fxcg_ShowMessage(const char* line1, const char* line2) {
    printf("MESSAGE %s / %s\n", line1, line2 ? line2 : "");
    fflush(stdout);
    exit(1);
}

void Fxcg_StartVBlank(void) {}

void Fxcg_WaitVBlank(void) {
    if (++sFrame >= sFrames) {
        printf("DONE %u\n", sFrame);
        fflush(stdout);
        exit(0);
    }
}

/* The ROM window: the whole ROM mapped read-only at 0x20000000, where the
 * linker put the ROM-resident symbols (rom_symbols.ld). */
bool Fxcg_RomInit(void) {
    int fd = open(sRomPath, O_RDONLY);
    if (fd < 0)
        return false;
    void* p = mmap((void*)(uintptr_t)WINDOW, 16u << 20, PROT_READ, MAP_PRIVATE | MAP_FIXED, fd, 0);
    close(fd);
    if (p != (void*)(uintptr_t)WINDOW)
        return false;
    gRomData = (unsigned char*)p;
    gRomSize = 16u << 20;
    return true;
}

const char* Fxcg_RomError(void) {
    return "cannot map the ROM at 0x20000000";
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <baserom.gba> <frames> [input-script] [ppm-every]\n", argv[0]);
        return 2;
    }
    sRomPath = argv[1];
    sFrames = (unsigned)strtoul(argv[2], NULL, 0);
    if (argc > 3)
        ParseInput(argv[3]);
    if (argc > 4)
        sPpmEvery = (unsigned)strtoul(argv[4], NULL, 0);
    setvbuf(stdout, NULL, _IOLBF, 0);
    return Fxcg_GameMain();
}

/* Fxcg_GameMain() clears the calculator's sysram BSS (fxcg.ld). On the host
 * that BSS is ordinary BSS, already zero: clear a dummy instead. The linker
 * defines sxbss = 0 (run_compare.sh). */
char rxbss[16];

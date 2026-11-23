/* Host test of the fx-CG50 ROM window's pak reader and page cache.
 *
 *   cc -DTMC_FXCG_D1_PAGER -DFXCG_ROM_HOST -Iport/fxcg \
 *      -Iexternal/fxLauncher/lib/cglink/include port/fxcg/tests/test_rom_cache.c \
 *      port/fxcg/fxcg_rom.c external/fxLauncher/lib/cglink/src/inflate.c -o test_rom_cache
 *   ./test_rom_cache <baserom.gba> <Picori.pak>
 *
 * Every page in the pak must come back byte-identical to the ROM, through
 * a 16-frame cache, so most fetches evict, plus pinning; pages
 * absent from the pak must fail cleanly. Needs your own ROM and a pak built
 * from it (scripts/fxcg/build_page_pak.py); neither is in the repository. */
#include "fxcg_rom.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

unsigned char* gRomData;
unsigned int gRomSize;
extern uint32_t fxcg_rom_phys[4096];
#define FRAMES 16

int main(int argc, char** argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: %s <baserom.gba> <Picori.pak>\n", argv[0]);
        return 2;
    }
    FILE* f = fopen(argv[1], "rb");
    static unsigned char rom[16u << 20];
    size_t romSize = f ? fread(rom, 1, sizeof(rom), f) : 0;
    if (!romSize) {
        fprintf(stderr, "cannot read %s\n", argv[1]);
        return 2;
    }
    if (!Fxcg_RomInitHost(argv[2], FRAMES)) {
        fprintf(stderr, "open: %s\n", Fxcg_RomError());
        return 1;
    }
    int failures = 0, present = 0, absent = 0;
    /* Two passes in different orders so frames are evicted and reused. */
    for (int pass = 0; pass < 2; pass++) {
        for (uint32_t i = 0; i < 4096; i++) {
            uint32_t page = pass ? (i * 2654435761u) % 4096u : i;
            uint8_t* frame = Fxcg_RomFetch(page, false);
            if (!frame) {
                if (!strstr(Fxcg_RomError(), "not in Picori.pak")) {
                    fprintf(stderr, "page %u: %s\n", page, Fxcg_RomError());
                    failures++;
                }
                absent += pass == 0;
                continue;
            }
            present += pass == 0;
            if (((uintptr_t)frame >> 12 & 1) != (page & 1)) {
                fprintf(stderr, "page %u: frame parity differs\n", page);
                failures++;
            }
            if (memcmp(frame, rom + (size_t)page * 4096, 4096) != 0) {
                fprintf(stderr, "page %u: bytes differ from the ROM\n", page);
                failures++;
            }
            if (fxcg_rom_phys[page] == 0) {
                fprintf(stderr, "page %u: not in the page table after a fetch\n", page);
                failures++;
            }
        }
        int mapped = 0;
        for (uint32_t p = 0; p < 4096; p++)
            mapped += fxcg_rom_phys[p] != 0;
        if (mapped > FRAMES) {
            fprintf(stderr, "%d pages mapped with %d frames\n", mapped, FRAMES);
            failures++;
        }
    }
    /* Pinning: two pinned pages per parity must survive a full sweep. */
    uint32_t pinned[4] = { 0, 0, 0, 0 };
    int npin = 0;
    for (uint32_t p = 0; p < 4096 && npin < 4; p++)
        if ((p & 1) == (uint32_t)(npin & 1) && Fxcg_RomFetch(p, true))
            pinned[npin++] = p;
    for (uint32_t p = 0; p < 4096; p++)
        Fxcg_RomFetch(p, false);
    for (int i = 0; i < npin; i++)
        if (fxcg_rom_phys[pinned[i]] == 0) {
            fprintf(stderr, "pinned page %u was evicted\n", pinned[i]);
            failures++;
        }
    FxcgRomStats st;
    Fxcg_RomGetStats(&st);
    printf("pak pages %d, absent %d, fetches %u, evictions %u, pinned %d, failures %d\n", present, absent,
           st.fetches, st.evictions, npin, failures);
    return failures ? 1 : 0;
}

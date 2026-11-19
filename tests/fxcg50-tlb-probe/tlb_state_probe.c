/*
 * Read-only fx-CG50 UTLB state snapshot for the D1 paging investigation.
 *
 * This program itself only reads MMUCR/PTEH/PTEL and the 64 UTLB address/data
 * arrays. It does not write MMU registers or UTLB entries, install a custom
 * TLB handler, or map the planned ROM window. Gint's normal handler remains
 * active and may naturally refill add-in code pages while the app runs.
 */
#include <gint/cpu.h>
#include <gint/display.h>
#include <gint/keyboard.h>
#include <gint/keycodes.h>
#include <gint/mpu/mmu.h>

#include <stdint.h>
#include <stddef.h>

#define UTLB_COUNT 64
#define ENTRIES_PER_PAGE 16
#define UTLB_ADDR_BASE 0xf6000000u
#define UTLB_DATA_BASE 0xf7000000u
#define UTLB_STRIDE 0x100u

static uint32_t sAddr[UTLB_COUNT];
static uint32_t sData[UTLB_COUNT];
static uint32_t sMmucr;
static uint32_t sPteh;
static uint32_t sPtel;
static uint32_t sVbr;

static void snapshot(void)
{
    sMmucr = SH7305_MMU.MMUCR.lword;
    sPteh = SH7305_MMU.PTEH.lword;
    sPtel = SH7305_MMU.PTEL.lword;
    sVbr = cpu_getVBR();

    for (unsigned i = 0; i < UTLB_COUNT; i++) {
        uintptr_t offset = (uintptr_t)i * UTLB_STRIDE;
        sAddr[i] = *(volatile uint32_t *)(uintptr_t)(UTLB_ADDR_BASE + offset);
        sData[i] = *(volatile uint32_t *)(uintptr_t)(UTLB_DATA_BASE + offset);
    }
}

static void draw_page(unsigned page)
{
    unsigned first = page * ENTRIES_PER_PAGE;

    dclear(C_BLACK);
    dtext(4, 2, C_WHITE, "fx-CG50 UTLB snapshot (read-only)");
    dprint(4, 14, C_WHITE, "MMUCR=%08X PTEH=%08X", sMmucr, sPteh);
    dprint(4, 25, C_WHITE, "PTEL=%08X VBR=%08X", sPtel, sVbr);
    dprint(4, 36, C_WHITE,
        "URB=%02u URC=%02u AT=%u SV=%u ASID=%02X PAGE=%u/4",
        (unsigned)((sMmucr >> 18) & 0x3f),
        (unsigned)((sMmucr >> 10) & 0x3f),
        (unsigned)(sMmucr & 1),
        (unsigned)((sMmucr >> 8) & 1),
        (unsigned)(sPteh & 0xff), page + 1);

    for (unsigned row = 0; row < ENTRIES_PER_PAGE; row++) {
        unsigned index = first + row;
        int valid = (sAddr[index] & 0x100u) && (sData[index] & 0x100u);
        int color = valid ? C_GREEN : C_WHITE;
        dprint(4, 49 + (int)row * 10, color,
            "%02u A=%08X D=%08X", index, sAddr[index], sData[index]);
    }

    dtext(4, 214, C_WHITE, "LEFT/RIGHT: page    EXIT: quit");
    dupdate();
}

int main(int isappli, int optnum)
{
    (void)isappli;
    (void)optnum;

    snapshot();

    unsigned page = 0;
    for (;;) {
        draw_page(page);
        key_event_t event = getkey_opt(GETKEY_NONE, NULL);
        if (event.type != KEYEV_DOWN)
            continue;

        if (event.key == KEY_EXIT)
            break;
        if (event.key == KEY_LEFT && page > 0)
            page--;
        if (event.key == KEY_RIGHT && page < 3)
            page++;
    }

    return 0;
}

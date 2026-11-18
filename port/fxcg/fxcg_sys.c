/* fx-CG50 frontend, gint side: keyboard, display, 60 Hz timer and main(). */
#include "fxcg_sys.h"

#include <gint/clock.h>
#include <gint/cpu.h>
#include <gint/display.h>
#include <gint/gint.h>
#include <gint/keyboard.h>
#include <gint/timer.h>

/* D-pad on the arrows; A = SHIFT, B = ALPHA, L = F1, R = F2, Select = F5,
 * Start = F6 or EXE. MENU goes to the calculator's main menu and back.
 * Bits follow GBA KEYINPUT: A, B, Select, Start, Right, Left, Up, Down, R, L. */
static const struct {
    int key;
    uint16_t bit;
} sKeyMap[] = {
    { KEY_SHIFT, 1u << 0 }, { KEY_ALPHA, 1u << 1 }, { KEY_F5, 1u << 2 },   { KEY_F6, 1u << 3 },
    { KEY_EXE, 1u << 3 },   { KEY_RIGHT, 1u << 4 }, { KEY_LEFT, 1u << 5 }, { KEY_UP, 1u << 6 },
    { KEY_DOWN, 1u << 7 },  { KEY_F2, 1u << 8 },    { KEY_F1, 1u << 9 },
};

uint16_t Fxcg_ReadButtons(void) {
    uint16_t held = 0;
    clearevents();
    if (keydown(KEY_MENU))
        gint_osmenu();
    for (unsigned i = 0; i < sizeof(sKeyMap) / sizeof(sKeyMap[0]); i++) {
        if (keydown(sKeyMap[i].key))
            held |= sKeyMap[i].bit;
    }
    return held;
}

uint16_t* Fxcg_Vram(void) {
    return gint_vram;
}

void Fxcg_Update(void) {
    dupdate();
}

void Fxcg_Clear(void) {
    dclear(C_BLACK);
}

void Fxcg_ShowMessage(const char* line1, const char* line2) {
    dclear(C_BLACK);
    dtext(4, 4, C_WHITE, line1);
    if (line2)
        dtext(4, 20, C_WHITE, line2);
    dupdate();
    getkey();
}

static volatile int sTicks;
static int sLastTick;

static int Fxcg_Tick(void) {
    sTicks++;
    return TIMER_CONTINUE;
}

void Fxcg_StartVBlank(void) {
    int t = timer_configure(TIMER_ANY, 16667, GINT_CALL(Fxcg_Tick));
    if (t >= 0)
        timer_start(t);
}

/* Frames that run long are not made up: the game runs slower, as a GBA does
 * under lag. */
void Fxcg_WaitVBlank(void) {
    while (sTicks == sLastTick)
        sleep();
    sLastTick = sTicks;
}

int main(void) {
    return Fxcg_GameMain();
}

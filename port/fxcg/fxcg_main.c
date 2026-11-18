/* fx-CG50 frontend, game side: VBlankIntrWait, input into KEYINPUT and the
 * 240x160 -> 336x224 scaler. The PC equivalent is port_main.c and the SDL
 * half of port_bios.c; see docs/fx-cg50-port-plan.md (Phase 1, D3, D5). */
#include "port_target.h"
#include "global.h"
#include "gba/io_reg.h"
#include "port_gba_mem.h"
#include "port_hdma.h"
#include "port_rom.h"
#include "virtuappu.h"
#include "cpu/mode1.h"
#include "fxcg_rom.h"
#include "fxcg_sys.h"
#include <string.h>

extern void AgbMain(void);
extern void VBlankIntr(void);

/* VirtuaPPU renders 240x160 ABGR8888. Scale by 1.4 to 336x224 (each group of
 * five source pixels becomes seven: the 2nd and 4th are doubled) and centre
 * it on the 396x224 screen (D3). */
#define OUT_W 336
#define OUT_H 224
#define OUT_X ((FXCG_SCREEN_W - OUT_W) / 2)

static u16 sSrcX[OUT_W];
static u8 sSrcY[OUT_H];

static void InitScaler(void) {
    for (int x = 0; x < OUT_W; x++)
        sSrcX[x] = (u16)(x * 5 / 7);
    for (int y = 0; y < OUT_H; y++)
        sSrcY[y] = (u8)(y * 5 / 7);
    Fxcg_Clear();
}

static inline u16 ToRgb565(u32 abgr) {
    return (u16)(((abgr & 0xF8u) << 8) | ((abgr >> 5) & 0x07E0u) | ((abgr >> 19) & 0x001Fu));
}

static void Present(void) {
    u16 dispcnt = (u16)(gIoMem[0x00] | (gIoMem[0x01] << 8));
    virtuappu_registers.frame_width = MODE1_GBA_WIDTH;
    virtuappu_registers.frame_pitch = MODE1_GBA_WIDTH;
    /* Same routing as Port_PPU_PresentFrame: GBA mode 0 -> VPPU mode 1
     * (text BGs), GBA modes 1 and 2 -> VPPU mode 2 (affine BG2). */
    virtuappu_registers.mode = ((dispcnt & 7) == 1 || (dispcnt & 7) == 2) ? 2 : 1;
    virtuappu_mode1_pre_line_callback = port_hdma_has_active_channels() ? port_hdma_step_line : NULL;
    virtuappu_mode1_bg2x_hdma_strobe = port_hdma_dest_overlaps(gIoMem + 0x28, gIoMem + 0x2C) != 0;
    virtuappu_mode1_bg2y_hdma_strobe = port_hdma_dest_overlaps(gIoMem + 0x2C, gIoMem + 0x30) != 0;
    virtuappu_render_frame();

    const uint32_t* src = virtuappu_frame_buffer;
    u16* vram = Fxcg_Vram();
    int lastRow = -1;
    for (int y = 0; y < OUT_H; y++) {
        u16* dst = vram + y * FXCG_SCREEN_W + OUT_X;
        if (sSrcY[y] == lastRow) {
            const u16* prev = dst - FXCG_SCREEN_W;
            for (int x = 0; x < OUT_W; x++)
                dst[x] = prev[x];
            continue;
        }
        lastRow = sSrcY[y];
        const uint32_t* row = src + lastRow * MODE1_GBA_WIDTH;
        for (int x = 0; x < OUT_W; x++)
            dst[x] = ToRgb565(row[sSrcX[x]]);
    }
    Fxcg_Update();
}

/* The game calls this once per frame. */
void VBlankIntrWait(void) {
    *(vu16*)(gIoMem + REG_OFFSET_KEYINPUT) = (u16)(0x03FF & ~Fxcg_ReadButtons());
    Present();
    port_hdma_vblank_reset();
    Fxcg_WaitVBlank();
    VBlankIntr();
}

/* The game's BSS lives in sysram (port/fxcg/fxcg.ld), which gint does not
 * clear at start-up. */
extern char rxbss[], sxbss[];

int Fxcg_GameMain(void) {
    memset(rxbss, 0, (size_t)sxbss);
    InitScaler();
    if (!Fxcg_RomInit()) {
        Fxcg_ShowMessage("Picori: cannot open the ROM pages.", Fxcg_RomError());
        return 1;
    }
    Port_LoadRom(NULL);
    Fxcg_StartVBlank();
    AgbMain();
    return 0;
}

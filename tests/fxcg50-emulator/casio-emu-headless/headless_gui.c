#include "gui/gui.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SCREEN_WIDTH 396
#define SCREEN_HEIGHT 224
#define EXPECTED_ROM_ERROR_MARKER 0x07e0
#define MIN_VISIBLE_MESSAGE_PIXELS 24

extern int headless_p2_flag_mode;
extern int headless_d1_pager_mode;
extern u32 mmuDone;
#define D1_PAGER_MAX_FRAMES 3600
#define MIN_P2_PASS_PIXELS 8

static u16 last_frame[SCREEN_WIDTH * SCREEN_HEIGHT];
static int have_frame;

void initGui(void)
{
    memset(last_frame, 0, sizeof(last_frame));
    have_frame = 0;
}

void updateDisplay(u16 *vram)
{
    if (!vram)
        return;

    memcpy(last_frame, vram, sizeof(last_frame));
    have_frame = 1;
}

static int save_ppm(void)
{
    FILE *file = fopen("emulated-screen.ppm", "wb");
    if (!file)
    {
        perror("emulated-screen.ppm");
        return 0;
    }

    int ok = fprintf(file, "P6\n%d %d\n255\n",
                     SCREEN_WIDTH, SCREEN_HEIGHT) > 0;
    for (size_t i = 0; ok && i < SCREEN_WIDTH * SCREEN_HEIGHT; ++i)
    {
        const u16 pixel = last_frame[i];
        const unsigned char rgb[3] = {
            (unsigned char)((((pixel >> 11) & 0x1f) * 255) / 31),
            (unsigned char)((((pixel >> 5) & 0x3f) * 255) / 63),
            (unsigned char)(((pixel & 0x1f) * 255) / 31)
        };
        ok = fwrite(rgb, sizeof(rgb), 1, file) == 1;
    }
    if (fclose(file) != 0)
        ok = 0;
    return ok;
}

static int green_pass_pixels(void)
{
    int count = 0;
    /* The probe redraws its terminal all-tests-PASS line in C_GREEN (RGB565
       0x07e0). Earlier per-stage PASS screens are deliberately not marked. */
    for (int y = 20; y < 48; ++y)
    {
        for (int x = 2; x < SCREEN_WIDTH - 2; ++x)
        {
            if (last_frame[y * SCREEN_WIDTH + x] == 0x07e0)
                ++count;
        }
    }
    return count;
}

static int visible_message_pixels(void)
{
    int count = 0;

    /* The current boot gate renders two message lines at (4,4) and (4,20).
       Count the complete text band without depending on the font rasterizer's
       exact glyph shapes. Pixel (0,0) is the dedicated pass marker. */
    for (int y = 2; y < 36; ++y)
    {
        for (int x = 2; x < SCREEN_WIDTH - 2; ++x)
        {
            if (last_frame[y * SCREEN_WIDTH + x] != 0)
                ++count;
        }
    }
    return count;
}

void runMainLoop(void (*callback)(void))
{
    if (headless_d1_pager_mode)
    {
        /* D1-PAGER-PROBE prints its report through the debug port and
           writes the done port (mmu_window.c) when it finishes. */
        int frame = 0;
        while (!mmuDone && frame < D1_PAGER_MAX_FRAMES)
        {
            callback();
            ++frame;
        }
        if (have_frame)
            (void)save_ppm();
        printf("\nD1_EMU_FRAMES=%d\n", frame);
        if (!mmuDone)
        {
            fprintf(stderr, "D1 pager probe did not finish in %d frames\n", D1_PAGER_MAX_FRAMES);
            exit(1);
        }
        printf("D1_EMU_DONE_CODE=%u\n", mmuDone & 0xff);
        exit((mmuDone & 0xff) == 0 ? 0 : 1);
    }

    /* Match fxOGL's headless harness: advance enough emulated frames for the
       Gint LCD driver to publish its framebuffer, then inspect the capture. */
    for (int frame = 0; frame < 16; ++frame)
        callback();

    const int capture_saved = have_frame && save_ppm();

    if (headless_p2_flag_mode)
    {
        const int pass_pixels = have_frame ? green_pass_pixels() : 0;
        printf("FXCG_P2_FLAG_EMU_PASS_PIXELS=%d\n", pass_pixels);
        printf("FXCG_P2_FLAG_EMU_DISPLAY_CAPTURED=%s\n",
               have_frame ? "yes" : "no");
        if (!have_frame || !capture_saved || pass_pixels < MIN_P2_PASS_PIXELS)
        {
            fprintf(stderr, "P2 SR flag readback emulator test failed\n");
            exit(1);
        }
        puts("FXCG_P2_FLAG_EMU_RESULT=PASS");
        return;
    }

    const unsigned marker = have_frame ? last_frame[0] : 0;
    const int message_pixels = have_frame ? visible_message_pixels() : 0;

    printf("PICORI_CASIO_EMU_MARKER=0x%04x\n", marker);
    printf("PICORI_CASIO_EMU_MESSAGE_PIXELS=%d\n", message_pixels);
    printf("PICORI_CASIO_EMU_DISPLAY_CAPTURED=%s\n",
           have_frame ? "yes" : "no");
    printf("PICORI_CASIO_EMU_CAPTURE_SAVED=%s\n",
           capture_saved ? "yes" : "no");

    if (!have_frame || !capture_saved ||
        marker != EXPECTED_ROM_ERROR_MARKER ||
        message_pixels < MIN_VISIBLE_MESSAGE_PIXELS)
    {
        fprintf(stderr, "Picori fx-CG50 emulator smoke test failed\n");
        exit(1);
    }

    puts("PICORI_CASIO_EMU_RESULT=PASS");
}

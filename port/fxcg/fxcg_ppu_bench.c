/* Stock-clock, on-device PPU timing overlay for the fx-CG50 port.
 *
 * All timings use libprof's free-running TMU profiler (microseconds). This module
 * only reads the current clock profile; it never changes the clock. */
#include "fxcg_ppu_bench.h"

#include <gint/clock.h>
#include <gint/display.h>
#include <libprof.h>

#include <stdint.h>

#define BENCH_WINDOW_FRAMES 60u
#define BENCH_STAGE_COUNT 8u

enum {
    STAGE_PPU = 0,
    STAGE_SETUP,
    STAGE_PALETTE,
    STAGE_BG,
    STAGE_OBJ,
    STAGE_COMPOSITE,
    STAGE_SCALE,
    STAGE_LCD
};

static int sActive;
static int sInitFailed;
static prof_t sPpuTimer;
static prof_t sScaleTimer;
static prof_t sLcdTimer;
static uint32_t sFrameUs[BENCH_STAGE_COUNT];

static uint32_t sWindowSum[BENCH_STAGE_COUNT];
static uint32_t sWindowMin[BENCH_STAGE_COUNT];
static uint32_t sWindowMax[BENCH_STAGE_COUNT];
static unsigned sWindowFrames;

static uint32_t sPublishedAvg[BENCH_STAGE_COUNT];
static uint32_t sPublishedMin[BENCH_STAGE_COUNT];
static uint32_t sPublishedMax[BENCH_STAGE_COUNT];
static unsigned sPublishedFrames;

static const char *clock_label(void)
{
    switch (clock_get_speed()) {
    case CLOCK_SPEED_F1: return "F1";
    case CLOCK_SPEED_F2: return "F2";
    case CLOCK_SPEED_F3: return "F3";
    case CLOCK_SPEED_F4: return "F4";
    case CLOCK_SPEED_F5: return "F5";
    default: return "UNKNOWN";
    }
}

void FxcgPpuBench_Init(void)
{
    unsigned i;

    /* libprof can fail to reserve TMU0. Never mark the profiler active unless
     * its timer setup succeeded: prof_time() requires a valid Pphi/timer. */
    sActive = 0;
    sInitFailed = 0;
    if (prof_init() != 0) {
        sInitFailed = 1;
        return;
    }
    sActive = 1;
    sWindowFrames = 0;
    sPublishedFrames = 0;
    for (i = 0; i < BENCH_STAGE_COUNT; ++i) {
        sFrameUs[i] = 0;
        sWindowSum[i] = 0;
        sWindowMin[i] = UINT32_MAX;
        sWindowMax[i] = 0;
        sPublishedAvg[i] = 0;
        sPublishedMin[i] = 0;
        sPublishedMax[i] = 0;
    }
}

void FxcgPpuBench_Quit(void)
{
    if (sActive) {
        prof_quit();
        sActive = 0;
    }
}

int FxcgPpuBench_Active(void)
{
    return sActive;
}

void FxcgPpuBench_FrameBegin(void)
{
    unsigned i;
    if (!sActive)
        return;

    for (i = 0; i < BENCH_STAGE_COUNT; ++i)
        sFrameUs[i] = 0;
    sPpuTimer = prof_make();
    sScaleTimer = prof_make();
    sLcdTimer = prof_make();
}

void FxcgPpuBench_PpuBegin(void)
{
    if (sActive)
        prof_enter(sPpuTimer);
}

void FxcgPpuBench_PpuEnd(void)
{
    if (sActive) {
        prof_leave(sPpuTimer);
        sFrameUs[STAGE_PPU] = prof_time(sPpuTimer);
    }
}

void FxcgPpuBench_ScaleBegin(void)
{
    if (sActive)
        prof_enter(sScaleTimer);
}

void FxcgPpuBench_ScaleEnd(void)
{
    if (sActive) {
        prof_leave(sScaleTimer);
        sFrameUs[STAGE_SCALE] = prof_time(sScaleTimer);
    }
}

void FxcgPpuBench_LcdBegin(void)
{
    if (sActive)
        prof_enter(sLcdTimer);
}

void FxcgPpuBench_LcdEnd(void)
{
    if (sActive) {
        prof_leave(sLcdTimer);
        sFrameUs[STAGE_LCD] = prof_time(sLcdTimer);
    }
}

void FxcgPpuBench_RecordPpuStages(uint32_t setup_us, uint32_t palette_us,
    uint32_t bg_us, uint32_t obj_us, uint32_t composite_us)
{
    if (!sActive)
        return;

    sFrameUs[STAGE_SETUP] = setup_us;
    sFrameUs[STAGE_PALETTE] = palette_us;
    sFrameUs[STAGE_BG] = bg_us;
    sFrameUs[STAGE_OBJ] = obj_us;
    sFrameUs[STAGE_COMPOSITE] = composite_us;
}

void FxcgPpuBench_DrawOverlay(void)
{
    if (sInitFailed) {
        drect(0, 0, 302, 32, C_BLACK);
        dprint(4, 2, C_WHITE, "FXCG50 PPU BENCH");
        dprint(4, 14, C_WHITE, "ERROR: PROFILER TIMER UNAVAILABLE");
        return;
    }
    if (!sActive)
        return;

    /* A deliberately small, opt-in overlay. It is drawn after scaling, so
     * renderer/scaler timings exclude the diagnostic text itself. */
    drect(0, 0, 302, 74, C_BLACK);
    dprint(4, 2, C_WHITE, "FXCG50 PPU BENCH  CLK %s", clock_label());
    if (!sPublishedFrames) {
        dprint(4, 14, C_WHITE, "WARMING: %u / %u frames", sWindowFrames,
            BENCH_WINDOW_FRAMES);
        return;
    }

    dprint(4, 14, C_WHITE, "PPU %u us avg  [%u..%u]",
        sPublishedAvg[STAGE_PPU], sPublishedMin[STAGE_PPU],
        sPublishedMax[STAGE_PPU]);
    dprint(4, 26, C_WHITE, "PREP %u  PAL %u us",
        sPublishedAvg[STAGE_SETUP], sPublishedAvg[STAGE_PALETTE]);
    dprint(4, 38, C_WHITE, "BG %u  OBJ %u  MIX %u us",
        sPublishedAvg[STAGE_BG], sPublishedAvg[STAGE_OBJ],
        sPublishedAvg[STAGE_COMPOSITE]);
    dprint(4, 50, C_WHITE, "SCALE %u  LCD %u us",
        sPublishedAvg[STAGE_SCALE], sPublishedAvg[STAGE_LCD]);
    dprint(4, 62, C_WHITE, "WINDOW %u frames", sPublishedFrames);
}

void FxcgPpuBench_FrameEnd(void)
{
    unsigned i;
    if (!sActive)
        return;

    if (sWindowFrames == 0) {
        for (i = 0; i < BENCH_STAGE_COUNT; ++i) {
            sWindowSum[i] = 0;
            sWindowMin[i] = UINT32_MAX;
            sWindowMax[i] = 0;
        }
    }

    for (i = 0; i < BENCH_STAGE_COUNT; ++i) {
        uint32_t sample = sFrameUs[i];
        sWindowSum[i] += sample;
        if (sample < sWindowMin[i])
            sWindowMin[i] = sample;
        if (sample > sWindowMax[i])
            sWindowMax[i] = sample;
    }

    ++sWindowFrames;
    if (sWindowFrames == BENCH_WINDOW_FRAMES) {
        for (i = 0; i < BENCH_STAGE_COUNT; ++i) {
            sPublishedAvg[i] = sWindowSum[i] / BENCH_WINDOW_FRAMES;
            sPublishedMin[i] = sWindowMin[i];
            sPublishedMax[i] = sWindowMax[i];
        }
        sPublishedFrames = BENCH_WINDOW_FRAMES;
        sWindowFrames = 0;
    }
}

#ifndef TMC_FXCG_PPU_BENCH_H
#define TMC_FXCG_PPU_BENCH_H

#include <stdint.h>

/* Diagnostic-only profiler. These entry points are linked only when
 * TMC_FXCG_PPU_BENCH=ON; the normal calculator build has no references. */
void FxcgPpuBench_Init(void);
void FxcgPpuBench_Quit(void);
int FxcgPpuBench_Active(void);
void FxcgPpuBench_FrameBegin(void);
void FxcgPpuBench_PpuBegin(void);
void FxcgPpuBench_PpuEnd(void);
void FxcgPpuBench_ScaleBegin(void);
void FxcgPpuBench_ScaleEnd(void);
void FxcgPpuBench_LcdBegin(void);
void FxcgPpuBench_LcdEnd(void);
void FxcgPpuBench_RecordPpuStages(uint32_t setup_us, uint32_t palette_us,
    uint32_t bg_us, uint32_t obj_us, uint32_t composite_us);
void FxcgPpuBench_DrawOverlay(void);
void FxcgPpuBench_FrameEnd(void);

#endif /* TMC_FXCG_PPU_BENCH_H */

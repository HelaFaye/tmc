#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * ROM working-set measurement (Linux only; see docs/fx-cg50-port-plan.md,
 * Phase 0). Set TMC_ROM_TOUCH_LOG=<file> to enable it. The ROM buffer is
 * then page-aligned and protected, and the first access to each 4 KB ROM
 * page is recorded:
 *   - "load" pages are touched while the ROM, its pointer tables and the
 *     asset overlays are set up, before the first game frame;
 *   - "play" pages are touched from the first game frame on. Every page is
 *     protected again when play starts, so this set counts pages read during
 *     play whether or not they were also touched at load.
 * The log is written at exit: one line per touched page, "<phase> <page>
 * <frame>", where frame is the game frame of the first access (0 at load).
 * TMC_ROM_TOUCH_FRAMES=<n> also ends the run after n play frames (the log is
 * written first), for scripted measurements.
 * Without TMC_ROM_TOUCH_LOG every function does nothing.
 */

/* Returns a zeroed, tracked ROM buffer of `size` bytes, or NULL when
   tracking is off (the caller then allocates as usual). */
void* Port_RomTouch_Alloc(size_t size);
/* Frees a buffer from Port_RomTouch_Alloc; returns 0 for any other pointer. */
int Port_RomTouch_Free(void* buffer);
/* Starts recording "load" accesses: call once the ROM file has been read. */
void Port_RomTouch_BeginLoad(void);
/* Called once per game frame; the first call starts the "play" phase. */
void Port_RomTouch_Frame(void);

#ifdef __cplusplus
}
#endif

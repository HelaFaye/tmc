/* fx-CG50: the game's view of the 16 MB ROM (docs/fx-cg50-port-plan.md, D1).
 *
 * gRomData points at a 16 MB virtual window. Touching a page that is not
 * mapped raises a TLB miss; the handler fetches that 4 KB ROM page from the
 * page pak (storage memory in the bring-up build, the ESP32 through the
 * launcher later), decompresses it into a page-cache frame and maps it. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Open the page pak and set gRomData/gRomSize to the window. False on
 * failure, with a reason in Fxcg_RomError(). */
bool Fxcg_RomInit(void);
const char* Fxcg_RomError(void);

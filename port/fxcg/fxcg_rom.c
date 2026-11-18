/* fx-CG50 ROM window: placeholder until the D1 TLB-backed window lands.
 * It fails cleanly so the add-in boots to a message instead of crashing. */
#include "fxcg_rom.h"

static const char* sError = "";

bool Fxcg_RomInit(void) {
    sError = "ROM window (plan D1) not built yet.";
    return false;
}

const char* Fxcg_RomError(void) {
    return sError;
}

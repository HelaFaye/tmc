#include <stdio.h>
#include <string.h>

#include "cpu.h"
#include "gui/gui.h"

extern CpuState cpu;
int headless_p2_flag_mode;

int startInterpreter(const char *filename);

int main(int argc, char **argv)
{
    if (argc != 2 && argc != 3)
    {
        fprintf(stderr, "usage: casio-emu-headless path/to/addin.g3a [--p2-flags]\n");
        return 2;
    }

    if (argc == 3)
    {
        if (strcmp(argv[2], "--p2-flags") != 0)
        {
            fprintf(stderr, "unknown headless test mode: %s\n", argv[2]);
            return 2;
        }
        headless_p2_flag_mode = 1;
    }

    initGui();

    /* The pinned emulator starts CpuState zeroed and does not model the
       OS add-in launch state. The synthetic P2 probe requires MD=1.
       Seed only that raw SR bit in this dedicated test mode; the normal
       Picori smoke test and all ordinary emulator runs are unchanged. */
    if (headless_p2_flag_mode)
        cpu.reg.SR = 0x40000000u;

    return startInterpreter(argv[1]);
}

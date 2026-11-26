#include <stdio.h>
#include <string.h>

#include "cpu.h"
#include "gui/gui.h"

extern CpuState cpu;
int headless_p2_flag_mode;
int headless_d1_pager_mode;
extern int mmuWindowActive;
extern const char* mmuFsDir;

int startInterpreter(const char *filename);

int main(int argc, char **argv)
{
    /* --fs DIR (with --d1-pager): serve the OS file syscalls from DIR. */
    if (argc == 5 && strcmp(argv[3], "--fs") == 0)
    {
        mmuFsDir = argv[4];
        argc = 3;
    }
    if (argc != 2 && argc != 3)
    {
        fprintf(stderr, "usage: casio-emu-headless path/to/addin.g3a [--p2-flags|--d1-pager [--fs DIR]]\n");
        return 2;
    }

    if (argc == 3)
    {
        if (strcmp(argv[2], "--d1-pager") == 0)
        {
            /* Test-only MMU extension (mmu_window.c) for D1-PAGER-PROBE. */
            headless_d1_pager_mode = 1;
            mmuWindowActive = 1;
        }
        else if (strcmp(argv[2], "--p2-flags") != 0)
        {
            fprintf(stderr, "unknown headless test mode: %s\n", argv[2]);
            return 2;
        }
        else
            headless_p2_flag_mode = 1;
    }

    initGui();

    /* The pinned emulator starts CpuState zeroed and does not model the
       OS add-in launch state. The synthetic P2 probe requires MD=1.
       Seed only that raw SR bit in this dedicated test mode; the normal
       Picori smoke test and all ordinary emulator runs are unchanged. */
    if (headless_p2_flag_mode || headless_d1_pager_mode)
        cpu.reg.SR = 0x40000000u;

    return startInterpreter(argv[1]);
}

#include <stdio.h>

#include "gui/gui.h"

int startInterpreter(const char *filename);

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        fprintf(stderr, "usage: casio-emu-headless path/to/Picori.g3a\n");
        return 2;
    }

    initGui();
    return startInterpreter(argv[1]);
}

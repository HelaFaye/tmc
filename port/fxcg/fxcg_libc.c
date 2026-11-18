/* fx-CG50: the few C library functions gint's libc (fxlibc) lacks. */
#include <stddef.h>

/* No environment on the calculator: every TMC_* debug switch reads as unset. */
char* getenv(const char* name) {
    (void)name;
    return NULL;
}

/* No symbolic links in the calculator's file system. */
long readlink(const char* path, char* buf, unsigned long bufsiz) {
    (void)path; (void)buf; (void)bufsiz;
    return -1;
}

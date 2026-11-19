/*
 * Link-only Gint TLB-handler override probe.
 *
 * The corresponding .gint.tlbh assembly stub is deliberately nonfunctional
 * and MUST NOT be executed. CI only builds this target and inspects its ELF
 * symbols; the normal Picori add-in remains the only binary booted in the
 * headless emulator.
 */
int main(int isappli, int optnum)
{
    (void)isappli;
    (void)optnum;
    return 0;
}

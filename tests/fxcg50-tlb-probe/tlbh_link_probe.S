/*
 * Link-only probe for Gint 2.11's SH-4A TLB-miss hook.
 *
 * DO NOT RUN THIS ADD-IN. The stub intentionally returns from a TLB miss
 * without installing a translation. It exists only to prove that this
 * application object supplies the .gint.tlbh section and that Gint's
 * kinit() references resolve to this symbol rather than the archive member.
 */
    .section .gint.tlbh,"ax"
    .balign 4
    .global _gint_tlbh
    .type _gint_tlbh,@function
_gint_tlbh:
    rte
    nop
    .size _gint_tlbh, .-_gint_tlbh

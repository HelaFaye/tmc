/*
 * Position-independent P2 helper for read-only SH-4A UTLB/ITLB inspection.
 *
 * The C harness copies this template to 0xac7ff000 and calls it with a P2
 * output-buffer pointer in r4. It saves its P1 stack state BEFORE accessing
 * the memory-mapped arrays. After the first array read, all data accesses and
 * instruction fetches remain in P2 until SYNCO + ICBI have completed.
 * Only then does it restore the P1 stack and RTS to the P0 caller.
 *
 * Output layout: 64 interleaved UTLB (address,data) pairs, then 4 interleaved
 * ITLB (address,data) pairs. This routine never writes an MMU/TLB register or
 * array; it performs 136 longword reads in total.
 */
        .section .text.p2_tlb_snapshot,"ax",@progbits
        .align  2
        .global _P2TlbSnapshotTemplate
        .global _P2TlbSnapshotTemplateEnd
        .type   _P2TlbSnapshotTemplate,@function
_P2TlbSnapshotTemplate:
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov.l   r11,@-r15
        mov.l   r12,@-r15

        /* r8 = P2 output pointer; r9 = entry index. */
        mov     r4,r8
        mov     #0,r9

        /* UTLB: 64 address/data pairs. */
        mov.l   .Lutlb_addr_base,r10
        mov.l   .Lutlb_data_base,r11
.Lutlb_loop:
        mov     r9,r12
        shll8   r12
        add     r10,r12
        mov.l   @r12,r0
        mov.l   r0,@r8
        add     #4,r8

        mov     r9,r12
        shll8   r12
        add     r11,r12
        mov.l   @r12,r0
        mov.l   r0,@r8
        add     #4,r8

        add     #1,r9
        mov     #64,r0
        cmp/eq  r0,r9
        bf      .Lutlb_loop

        /* ITLB: 4 address/data pairs. */
        mov     #0,r9
        mov.l   .Litlb_addr_base,r10
        mov.l   .Litlb_data_base,r11
.Litlb_loop:
        mov     r9,r12
        shll8   r12
        add     r10,r12
        mov.l   @r12,r0
        mov.l   r0,@r8
        add     #4,r8

        mov     r9,r12
        shll8   r12
        add     r11,r12
        mov.l   @r12,r0
        mov.l   r0,@r8
        add     #4,r8

        add     #1,r9
        mov     #4,r0
        cmp/eq  r0,r9
        bf      .Litlb_loop

        /* Required P2-to-non-P2 transition after memory-mapped TLB access. */
        synco
        icbi    @r8

        /* No non-P2 access occurs before the ICBI above. */
        mov     #0,r0
        mov.l   @r15+,r12
        mov.l   @r15+,r11
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

        .align  2
.Lutlb_addr_base:
        .long   0xf6000000
.Lutlb_data_base:
        .long   0xf7000000
.Litlb_addr_base:
        .long   0xf2000000
.Litlb_data_base:
        .long   0xf3000000
_P2TlbSnapshotTemplateEnd:
        .size   _P2TlbSnapshotTemplate,.-_P2TlbSnapshotTemplate

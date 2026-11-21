/*
 * Position-independent P2 code template for exact SR.RB/SR.BL readback.
 *
 * The C harness copies this whole block, including its PC-relative literal,
 * to 0xac7ff100. The literal load is completed before changing SR.
 *
 * R8  = original SR (kept intact until restoration)
 * R9  = requested RB/BL bits (copied from incoming R4)
 * R10 = temporary SR value
 * R11 = readback comparison result
 * R12 = mask covering both SR.RB and SR.BL
 *
 * R8-R12 are unbanked. While BL=1, this code performs no data-memory
 * accesses and no control transfers. Instruction fetches continue from P2.
 * The original SR is restored before any stack access or return.
 */
        .section .text.p2_bl_rb_trampoline,"ax",@progbits
        .align  2
        .global _P2ExecInTlbMissSrStateTemplate
        .global _P2ExecInTlbMissSrStateTemplateEnd
        .type   _P2ExecInTlbMissSrStateTemplate,@function
_P2ExecInTlbMissSrStateTemplate:
        /* Preserve the C ABI state before changing the register bank. */
        sts.l   pr,@-r15
        mov.l   r8,@-r15
        mov.l   r9,@-r15
        mov.l   r10,@-r15
        mov.l   r11,@-r15
        mov.l   r12,@-r15

        /* Retain requested flags and original SR in separate unbanked GPRs. */
        mov     r4,r9
        stc     sr,r8
        mov.l   .Lrb_bl_mask,r12
        mov     r9,r10
        or      r8,r10
        ldc     r10,sr

        /* Three instruction slots in the requested SR state. */
        nop
        nop
        nop

        /* Compare BOTH bits, so an unwanted extra bit also fails. */
        stc     sr,r11
        and     r12,r11
        cmp/eq  r9,r11
        movt    r11

        /* Restore the exact original SR before touching the stack. */
        ldc     r8,sr
        nop

        /* Return 0x155 on exact flag readback, 0x055 on mismatch. */
        shll8   r11
        mov     #85,r0
        or      r11,r0

        mov.l   @r15+,r12
        mov.l   @r15+,r11
        mov.l   @r15+,r10
        mov.l   @r15+,r9
        mov.l   @r15+,r8
        lds.l   @r15+,pr
        rts
        nop

        .align  2
.Lrb_bl_mask:
        .long   0x30000000
_P2ExecInTlbMissSrStateTemplateEnd:
        .size   _P2ExecInTlbMissSrStateTemplate,.-_P2ExecInTlbMissSrStateTemplate

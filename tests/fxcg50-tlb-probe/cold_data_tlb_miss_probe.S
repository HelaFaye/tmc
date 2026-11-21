/*
 * One controlled data-side read from an add-in page that is valid in the OS
 * page table but absent from the UTLB.
 *
 * r4 = faulting virtual address (word aligned)
 * r5 = struct ColdDataTlbCapture * in ordinary add-in RAM
 *
 * The first counter sample is taken after this routine's entry instructions
 * have been fetched. Therefore, if any unrelated add-in page refill occurs
 * between the caller's baseline and this sample, C detects the counter change.
 * The only deliberate faulting access is
 * the MOV.L through r4. No syscall is issued here.
 */
        .section .text.cold_data_tlb_load,"ax",@progbits
        .align  2
        .global _cold_data_tlb_load
        .type   _cold_data_tlb_load,@function
_cold_data_tlb_load:
        /* Capture the OS page-mapping counter immediately before the load. */
        mov.l   .Lcounter_addr,r1
        mov.l   @r1,r2
        mov.l   r2,@r5

        /* The single deliberate data access. The OS/Gint handler must refill
         * the missing add-in translation and resume this instruction. */
        mov.l   @r4,r0
        mov.l   r0,@(4,r5)

        /* Capture evidence immediately after the retried load completes. */
        mov.l   .Lcounter_addr,r1
        mov.l   @r1,r2
        mov.l   r2,@(8,r5)

        mov.l   .Lexpevt_addr,r1
        mov.l   @r1,r2
        mov.l   r2,@(12,r5)

        mov.l   .Ltea_addr,r1
        mov.l   @r1,r2
        mov.l   r2,@(16,r5)

        mov.l   .Lpteh_addr,r1
        mov.l   @r1,r2
        mov.l   r2,@(20,r5)

        rts
        nop

        .align  2
.Lcounter_addr:
        .long   0x8c04d70c
.Lexpevt_addr:
        .long   0xff000024
.Ltea_addr:
        .long   0xff00000c
.Lpteh_addr:
        .long   0xff000000
        .size   _cold_data_tlb_load,.-_cold_data_tlb_load

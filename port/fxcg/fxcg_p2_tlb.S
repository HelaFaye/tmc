/* fx-CG50 D1: drop the UTLB entry of one evicted ROM-window page.
 *
 * Position-independent template. fxcg_rom.c copies it into a P2 buffer and
 * calls it there: the SH4AL-DSP manual (section 7.6) allows memory-mapped
 * UTLB array access only from P2, followed by SYNCO + ICBI before returning
 * outside P2. Same structure as the P2 snapshot template the TLB snapshot
 * probe ran on hardware; this one also writes.
 *
 * void inval(uint32_t page_va), page_va in r4, 4 KiB aligned.
 * Scans the 64 UTLB address-array entries; any valid entry whose VPN equals
 * page_va (4 KiB pages: bits 31-12) is rewritten with V = 0. No stack, no
 * other memory access. Interrupts must be masked by the caller. */

	.section .rodata.fxcg_p2_tlb, "a"
	.align	2
	.global	_FxcgP2InvalTemplate
	.global	_FxcgP2InvalTemplateEnd

_FxcgP2InvalTemplate:
	mov.l	.utlb_addr, r1		! 0xf6000000
	mov.l	.vpn_mask, r3		! 0xfffff000
	mov.l	.v_bit, r5		! 0x00000100
	mov	#64, r6
	mov	#0, r7			! entry index
1:
	mov	r7, r2
	shll8	r2
	add	r1, r2			! array address of entry r7
	mov.l	@r2, r0
	tst	r5, r0
	bt	2f			! not valid
	mov	r0, r6
	and	r3, r6
	cmp/eq	r4, r6
	bf	3f
	not	r5, r6
	and	r6, r0
	mov.l	r0, @r2			! V = 0
3:
	mov	#64, r6
2:
	add	#1, r7
	cmp/eq	r6, r7
	bf	1b
	synco
	mova	.utlb_addr, r0
	icbi	@r0
	rts
	nop

	.align	2
.utlb_addr:	.long	0xf6000000
.vpn_mask:	.long	0xfffff000
.v_bit:		.long	0x00000100
_FxcgP2InvalTemplateEnd:

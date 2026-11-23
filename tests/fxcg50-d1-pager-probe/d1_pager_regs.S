/* D1-PAGER-PROBE stage 3: does a ROM-window miss that goes through the
 * upcall (fxcg_tlbh.S) return with every register the game could hold?
 *
 * uint32_t d1_regs_check(uint32_t addr, uint32_t *loaded);
 * Loads sentinels into r0-r7 (r4/r5 after use), MACH, MACL, GBR and T,
 * performs one MOV.L from `addr` (a page that is not resident), and returns
 * a bitmask of registers that changed: bit n = rn (0-7), bit 8 = MACH,
 * 9 = MACL, 10 = GBR, 11 = T, 12 = R14 (frame pointer) and 13 = R15. */
	.section .text
	.align	2
	.global	_d1_regs_check
_d1_regs_check:
	mov.l	r8, @-r15
	mov.l	r9, @-r15
	mov.l	r10, @-r15
	mov.l	r11, @-r15
	mov.l	r12, @-r15
	mov.l	r13, @-r15
	mov.l	r14, @-r15
	stc.l	gbr, @-r15
	sts.l	pr, @-r15
	mov	r4, r8			! address
	mov	r5, r13			! where to store the loaded word
	mov	r15, r12		! expected r15 afterwards
	mov.l	.k14, r14
	mov.l	.kmach, r0
	lds	r0, mach
	mov.l	.kmacl, r0
	lds	r0, macl
	mov.l	.kgbr, r0
	ldc	r0, gbr
	mov.l	.k0, r0
	mov.l	.k1, r1
	mov.l	.k2, r2
	mov.l	.k3, r3
	mov.l	.k4, r4
	mov.l	.k5, r5
	mov.l	.k6, r6
	mov.l	.k7, r7
	sett
	mov.l	@r8, r9			! the faulting read
	movt	r10			! T after the read (expect 1)
	mov	#0, r11			! mismatch mask
	mov.l	r9, @r13

	mov.l	.k0, r9
	cmp/eq	r9, r0
	bt	1f
	mov.l	.b0, r9
	or	r9, r11
1:	mov.l	.k1, r9
	cmp/eq	r9, r1
	bt	1f
	mov.l	.b1, r9
	or	r9, r11
1:	mov.l	.k2, r9
	cmp/eq	r9, r2
	bt	1f
	mov.l	.b2, r9
	or	r9, r11
1:	mov.l	.k3, r9
	cmp/eq	r9, r3
	bt	1f
	mov.l	.b3, r9
	or	r9, r11
1:	mov.l	.k4, r9
	cmp/eq	r9, r4
	bt	1f
	mov.l	.b4, r9
	or	r9, r11
1:	mov.l	.k5, r9
	cmp/eq	r9, r5
	bt	1f
	mov.l	.b5, r9
	or	r9, r11
1:	mov.l	.k6, r9
	cmp/eq	r9, r6
	bt	1f
	mov.l	.b6, r9
	or	r9, r11
1:	mov.l	.k7, r9
	cmp/eq	r9, r7
	bt	1f
	mov.l	.b7, r9
	or	r9, r11
1:	sts	mach, r0
	mov.l	.kmach, r9
	cmp/eq	r9, r0
	bt	1f
	mov.l	.b8, r9
	or	r9, r11
1:	sts	macl, r0
	mov.l	.kmacl, r9
	cmp/eq	r9, r0
	bt	1f
	mov.l	.b9, r9
	or	r9, r11
1:	stc	gbr, r0
	mov.l	.kgbr, r9
	cmp/eq	r9, r0
	bt	1f
	mov.l	.b10, r9
	or	r9, r11
1:	mov	#1, r9
	cmp/eq	r9, r10
	bt	1f
	mov.l	.b11, r9
	or	r9, r11
1:	mov.l	.k14, r9
	cmp/eq	r9, r14
	bt	1f
	mov.l	.b12, r9
	or	r9, r11
1:	cmp/eq	r12, r15
	bt	1f
	mov.l	.b13, r9
	or	r9, r11
1:	mov	r11, r0
	lds.l	@r15+, pr
	ldc.l	@r15+, gbr
	mov.l	@r15+, r14
	mov.l	@r15+, r13
	mov.l	@r15+, r12
	mov.l	@r15+, r11
	mov.l	@r15+, r10
	mov.l	@r15+, r9
	rts
	mov.l	@r15+, r8

	.align	2
.k0:	.long	0x10C0FFEE
.k1:	.long	0x11111111
.k2:	.long	0x22222222
.k3:	.long	0x33333333
.k4:	.long	0x44444444
.k5:	.long	0x55555555
.k6:	.long	0x66666666
.k7:	.long	0x77777777
.k14:	.long	0xEEEE1414
.kmach:	.long	0x4D414348
.kmacl:	.long	0x4D41434C
.kgbr:	.long	0x47425221
.b0:	.long	0x0001
.b1:	.long	0x0002
.b2:	.long	0x0004
.b3:	.long	0x0008
.b4:	.long	0x0010
.b5:	.long	0x0020
.b6:	.long	0x0040
.b7:	.long	0x0080
.b8:	.long	0x0100
.b9:	.long	0x0200
.b10:	.long	0x0400
.b11:	.long	0x0800
.b12:	.long	0x1000
.b13:	.long	0x2000

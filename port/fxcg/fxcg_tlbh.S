/* fx-CG50 TLB-miss handler with the D1 ROM window (docs/fx-cg50-d1-pager.md).
 *
 * Replaces gint's .gint.tlbh (the link-time replacement verified by the
 * tlbh link probe). gint's kinit() copies this section to VBR+0x400, so it
 * runs from P1 and must fit in the 0x200 bytes before VBR+0x600 (fxcg.ld
 * asserts this). Built only with TMC_FXCG_D1_PAGER.
 *
 * Entry: SR.MD = SR.RB = SR.BL = 1; r0-r7 are bank 1; r15 is the game's
 * stack, which sits in the permanently mapped add-in RAM (UTLB 55-62).
 *
 * 1. Add-in code/data misses in [0x00300000, 0x00300000 + _srom): exactly
 *    gint 2.11.0's path, OS syscall 0x0C through the dispatcher at
 *    0x80020070 (COLD-DATA-TLB-MISS probe: PASS on OS 3.20.0202).
 * 2. ROM-window misses in [0x20000000, 0x21000000) whose page is resident
 *    (fxcg_rom_phys[page] != 0): OS syscall 0x09 maps the 4 KiB page to its
 *    frame with flags 0x6C, in the OS's own round-robin slots 0-54, the
 *    call the D1-MAP-PROBE validated (PASS). No LDTLB here: with URB = 63 a
 *    raw LDTLB could evict the permanent RAM mappings in slots 55-63.
 * 3. ROM-window misses on a page that is not resident: no file I/O in this
 *    context. The handler records SPC/SSR/page in fxcg_rom_save and returns
 *    into fxcg_tlbh_upcall with interrupts masked (IMASK = 15, BL = 0). The
 *    upcall saves the caller-saved registers, restores the game's SR, calls
 *    Fxcg_RomServiceFault() to read and inflate the page, masks interrupts
 *    again, restores every register and RTEs to the faulting instruction,
 *    which misses again and takes path 2.
 * 4. Anything else: gint's panic path, VBR+0x100.
 *
 * Everything paths 2-3 touch while masked is P1 (this copy, fxcg_rom_save
 * and fxcg_rom_phys in sysram), so they cannot fault. */

	.section .gint.tlbh, "ax"
	.align	4
	.global	_gint_tlbh

_gint_tlbh:
	sts.l	pr, @-r15
	stc.l	gbr, @-r15
	sts.l	mach, @-r15
	sts.l	macl, @-r15

	mov.l	.tea, r0
	mov.l	@r0, r0			! r0 = faulting address
	mov.l	.win_base, r1
	mov	r0, r3
	sub	r1, r3			! r3 = offset in the ROM window
	mov.l	.win_size, r1
	cmp/hs	r1, r3
	bf	.window			! unsigned offset < 16 MB

	/* gint's own path (src/kernel/tlbh.S, SH4 branch) */
	mov.l	.max_rom, r1
	cmp/ge	r1, r0
	bt	.panic
	mov.l	.min_rom, r1
	cmp/ge	r1, r0
	bf	.panic
	mov.l	.syscall, r2
	jsr	@r2
	mov	#12, r0			! syscall 0x0C: map an add-in page

.done:
	lds.l	@r15+, macl
	lds.l	@r15+, mach
	ldc.l	@r15+, gbr
	lds.l	@r15+, pr
	rte
	nop

.panic:
	lds.l	@r15+, macl
	lds.l	@r15+, mach
	ldc.l	@r15+, gbr
	lds.l	@r15+, pr
	stc	vbr, r0
	mov	#1, r1
	shll8	r1
	add	r1, r0
	jmp	@r0
	nop

.window:
	shlr8	r3
	shlr2	r3
	shlr2	r3			! r3 = ROM page (0-4095)
	mov	r3, r1
	shll2	r1
	mov.l	.phys, r2
	add	r1, r2
	mov.l	@r2, r5			! r5 = frame physical address, 0 if absent
	tst	r5, r5
	bt	.upcall
	mov.l	.page_mask, r1
	mov	r0, r4
	and	r1, r4			! r4 = window page address
	mov	#0x6c, r6		! flags used by the OS (D1-MAP-PROBE)
	mov.l	.syscall, r2
	jsr	@r2
	mov	#9, r0			! syscall 0x09: map one 4 KiB page
	bra	.done
	nop

.upcall:
	lds.l	@r15+, macl		! the upcall runs as the game: undo the pushes
	lds.l	@r15+, mach
	ldc.l	@r15+, gbr
	lds.l	@r15+, pr
	mov.l	.save, r1
	stc	spc, r2
	mov.l	r2, @(0, r1)		! save.spc
	stc	ssr, r2
	mov.l	r2, @(4, r1)		! save.ssr
	mov.l	r3, @(8, r1)		! save.page
	mov	r2, r0
	or	#0xf0, r0		! start the upcall with IMASK = 15
	ldc	r0, ssr
	mova	fxcg_tlbh_upcall, r0
	ldc	r0, spc			! P1 address of this copy
	nop				! keep the SPC write apart from RTE
	rte
	nop

/* Runs in the game's context (RB = 0, BL = 0) with IMASK = 15. Save area:
   0 spc, 4 ssr, 8 page, 12-36 r1-r7, 40 r0, 44 pr, 48 mach, 52 macl. */
	.align	2
fxcg_tlbh_upcall:
	ldc	r0, r1_bank		! hold the game's r0 in bank 1 (no interrupts)
	mov.l	.save, r0
	mov.l	r1, @(12, r0)
	mov.l	r2, @(16, r0)
	mov.l	r3, @(20, r0)
	mov.l	r4, @(24, r0)
	mov.l	r5, @(28, r0)
	mov.l	r6, @(32, r0)
	mov.l	r7, @(36, r0)
	stc	r1_bank, r1
	mov.l	r1, @(40, r0)
	sts	pr, r1
	mov.l	r1, @(44, r0)
	sts	mach, r1
	mov.l	r1, @(48, r0)
	sts	macl, r1
	mov.l	r1, @(52, r0)
	mov.l	@(4, r0), r1
	ldc	r1, sr			! the game's SR: interrupts as they were
	mov.l	.service, r1
	jsr	@r1			! Fxcg_RomServiceFault(): read + inflate
	nop
	stc	sr, r0
	or	#0xf0, r0
	ldc	r0, sr			! mask interrupts; only P1 from here
	mov.l	.save, r0
	mov.l	@(0, r0), r1
	ldc	r1, spc
	mov.l	@(4, r0), r1
	ldc	r1, ssr
	mov.l	@(44, r0), r1
	lds	r1, pr
	mov.l	@(48, r0), r1
	lds	r1, mach
	mov.l	@(52, r0), r1
	lds	r1, macl
	mov.l	@(12, r0), r1
	mov.l	@(16, r0), r2
	mov.l	@(20, r0), r3
	mov.l	@(24, r0), r4
	mov.l	@(28, r0), r5
	mov.l	@(32, r0), r6
	mov.l	@(36, r0), r7
	mov.l	@(40, r0), r0
	rte				! retry the faulting instruction
	nop

	.align	2
.tea:		.long	0xff00000c
.win_base:	.long	0x20000000	/* FXCG_ROM_WINDOW */
.win_size:	.long	0x01000000
.min_rom:	.long	0x00300000
.max_rom:	.long	0x00300000 + _srom
.syscall:	.long	0x80020070
.page_mask:	.long	0xfffff000
.phys:		.long	_fxcg_rom_phys
.save:		.long	_fxcg_rom_save
.service:	.long	_Fxcg_RomServiceFault

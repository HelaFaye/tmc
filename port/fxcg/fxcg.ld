/*
	Picori's fx-CG50 linker script: gint's fxcg50.ld plus two additions
	(docs/fx-cg50-port-plan.md, "Memory on the calculator").

	1. The ROM window (D1). FXCG_ROM_WINDOW is the virtual address where the
	   16 MB ROM appears; port/fxcg/fxcg_rom.c maps its pages on demand.
	   rom_symbols.ld places the ROM-resident tables inside it.
	2. sysram: the game's own BSS (about 1.54 MB) does not fit gint's 491 KB
	   add-in RAM. It goes in the upper 3,200 KiB of DRAM, starting at
	   0x8c4e0000 (P1 alias of physical 0x0c4e0000). This range is outside
	   the 3 MB that the OS clears at add-in launch and is not used by the
	   OS in the measured fx-CG50 memory map. Keep the linker ASSERT below:
	   it prevents a future BSS increase from silently overrunning the range.
*/
/* All fxcg50 have SH4 processors (finally rid of compatibility issues) */
OUTPUT_ARCH(sh4)
/* ELF offers a lot of symbol/section/relocation insights */
OUTPUT_FORMAT(elf32-sh)
/* Located in core/start.c */
ENTRY(_start)


MEMORY
{
	/* Userspace mapping of the add-in (without G3A header) */
	rom   (rx):   o = 0x00300000, l = 2M
	/* Static RAM; stack grows down from the end of this region.
	   The first 5k (0x1400 bytes) are reserved by gint for the VBR space,
	   which is loaded dynamically and accessed through P1 */
	ram   (rw):   o = 0x08101400, l = 491k
	/* On-chip IL memory */
	ilram (rwx):  o = 0xe5200000, l = 4k
	/* On-chip X and Y memory */
	xyram (rwx):  o = 0xe500e000, l = 16k
	/* Upper DRAM, unused by the OS: physical 0x0c4e0000-0x0c7fffff.
	   P1 alias 0x8c4e0000-0x8c7fffff, 0x320000 bytes = 3,200 KiB. */
	sysram (rw):  o = 0x8c4e0000, l = 0x320000
}

FXCG_ROM_WINDOW = 0x20000000;
INCLUDE rom_symbols.ld

SECTIONS
{
	/*
	**  ROM sections
	*/

	/* First address to be mapped to ROM */
	_brom = ORIGIN(rom);
	/* Size of ROM mappings */
	_srom = SIZEOF(.text) + SIZEOF(.rodata)
	      + SIZEOF(.gint.drivers) + SIZEOF(.gint.blocks);

	/* Machine code going to ROM:
	   - Entry function (.text.entry)
	   - Compiler-provided constructors (.ctors) and destructors (.dtors)
	   - All text from .text and .text.* (including user code) */
	.text : {
		*(.text.entry)

		_bctors = . ;
		KEEP(*(.ctors .ctors.*))
		_ectors = . ;

		_bdtors = . ;
		KEEP(*(.dtors .dtors.*))
		_edtors = . ;

		_gint_exch_start = . ;
		KEEP(*(.gint.exch))
		_gint_exch_size = ABSOLUTE(. - _gint_exch_start);

		_gint_tlbh_start = . ;
		KEEP(*(.gint.tlbh))
		_gint_tlbh_size = ABSOLUTE(. - _gint_tlbh_start);
		/* kinit() copies this to VBR+0x400; VBR+0x600 is the interrupt
		   entry, so the handler (fxcg_tlbh.S with the D1 pager) must fit
		   in 0x200 bytes. */
		ASSERT(_gint_tlbh_size <= 0x200, "fx-CG50: .gint.tlbh exceeds VBR+0x400..0x600");

		*(.text .text.*)
	} > rom

	/* gint's interrupt handler blocks (.gint.blocks)
	   Although gint's blocks end up in VBR space, they are relocated at
	   startup by the library/drivers, so we store them here for now */
	.gint.blocks : {
		KEEP(*(.gint.blocks));
	} > rom

	/* Exposed driver interfaces (.gint.drivers)
	   The driver information is required to start and configure the driver,
	   even if the symbols are not referenced */
	.gint.drivers : {
		_gint_drivers = . ;
		KEEP(*(SORT_BY_NAME(.gint.drivers.*)));
		_gint_drivers_end = . ;
	} > rom

	/* Read-only data going to ROM:
	   - Resources or assets from fxconv or similar converters
	   - Data marked read-only by the compiler (.rodata and .rodata.*) */
	.rodata : SUBALIGN(4) {
		/* Put these first, they need to be 4-aligned */
		*(.rodata.4)

		*(.rodata .rodata.*)
	} > rom



	/*
	**  RAM sections
	*/

	/* The game's BSS goes to sysram; gint's and the C library's stay in
	   static RAM, which gint needs before main() runs. */
	.xbss (NOLOAD) : {
		_rxbss = . ;
		/* Keep Gint and C-library runtime state in internal user RAM. The
		   EXCLUDE_FILE selector must wrap the input-file wildcard (not sit
		   inside *()), otherwise archive-member BSS can leak into this NOLOAD
		   game-BSS region and remain uninitialized when kinit() runs. */
		/* Keep BSS from every archive member in internal user RAM. The game
             * translation units are linked as direct .obj inputs; runtime and
             * support-library objects arrive as archive members. The single-*
             * archive selector also matches archives supplied by absolute path. */
            /* GCC LTO may merge archive-member BSS into temporary ...ltrans*.o inputs. */
            EXCLUDE_FILE (
                *:*
                *ltrans*.o
            ) *(.bss .bss.* COMMON)
		. = ALIGN(16);
	} > sysram :NONE

	_sxbss = SIZEOF(.xbss);
	ASSERT(SIZEOF(.xbss) <= LENGTH(sysram),
	       "fx-CG50 game BSS exceeds the measured upper-DRAM region")

	. = ORIGIN(ram);

	/* BSS data going to RAM. The BSS section is to be stripped from the
	   ELF file later, and wiped at startup */
	.bss (NOLOAD) : {
		_rbss = . ;

		*(.bss .bss.* COMMON)

		. = ALIGN(16);
	} > ram :NONE

	_sbss = SIZEOF(.bss);

	/* Read-write data sections going to RAM (.data and .data.*) */
	.data ALIGN(4) : ALIGN(4) {
		_ldata = LOADADDR(.data);
		_rdata = . ;

		*(.data .data.*)
		/* References to mapped code - no relocation needed */
		*(.gint.mappedrel)

		. = ALIGN(16);
	} > ram AT> rom

	/* Read-write data sub-aligned to 4 bytes (mainly from fxconv) */
	.data.4 : SUBALIGN(4) {
		*(.data.4)
		. = ALIGN(16);
	} > ram AT> rom

	_sdata = SIZEOF(.data) + SIZEOF(.data.4);

	/* On-chip memory sections: IL, X and Y memory */

	. = ORIGIN(ilram);
	.ilram ALIGN(4) : ALIGN(4) {
		_lilram = LOADADDR(.ilram);
		_rilram = . ;

		*(.ilram)
		/* Code that must remain mapped is placed here */
		*(.gint.mapped)

		. = ALIGN(16);
	} > ilram AT> rom

	. = ORIGIN(xyram);
	.xyram ALIGN(4) : ALIGN(4) {
		_lxyram = LOADADDR(.xyram);
		_rxyram = . ;

		*(.xram .yram .xyram)

		. = ALIGN(16);
	} > xyram AT> rom

	_silram = SIZEOF(.ilram);
	_sxyram = SIZEOF(.xyram);

	_lgmapped = ABSOLUTE(0);
	_sgmapped = ABSOLUTE(0);
	_lreloc = ABSOLUTE(0);
	_sreloc = ABSOLUTE(0);

	/* gint's uninitialized BSS section, going to static RAM. All the large
	   data arrays will be located here */
	.gint.bss (NOLOAD) : {
		*(.gint.bss)
		. = ALIGN(16);

		/* End of user RAM */
		_euram = . ;
	} > ram :NONE

	_sgbss = SIZEOF(.gint.bss);



	/*
	**  Unused sections
	*/

	/DISCARD/ : {
		/* SH3-only data sections */
		*(.gint.rodata.sh3 .gint.data.sh3 .gint.bss.sh3)
		/* Java class registration (why are they even here?!) */
		*(.jcr)
		/* Asynchronous unwind tables: no C++ exception handling */
		*(.eh_frame_hdr)
		*(.eh_frame)
		/* Comments or anything the compiler might generate */
		*(.comment)
	}
}

/* libc's errno is referenced by malloc(); keep this link-time canary so a
   future linker-script edit cannot silently move runtime BSS into NOLOAD
   external game RAM again. */
ASSERT(_errno >= ORIGIN(ram) && _errno < ORIGIN(ram) + LENGTH(ram),
       "fx-CG50: libc _errno escaped internal RAM; check .xbss EXCLUDE_FILE")

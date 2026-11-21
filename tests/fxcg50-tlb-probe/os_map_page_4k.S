/*
 * Call the Casio OS syscall dispatcher using the SH C ABI.
 * Arguments arrive in r4 (virtual), r5 (physical), r6 (flags).
 * The dispatcher is a tail-jump and preserves PR; the OS mapper returns
 * directly to the C caller. The syscall number is passed in r0.
 */
    .section .text
    .balign 4
    .global _os_map_page_4k
    .type _os_map_page_4k, @function
_os_map_page_4k:
    mov     #9, r0
    mov.l   .Lsyscall_dispatcher, r2
    jmp     @r2
    nop
    .balign 4
.Lsyscall_dispatcher:
    .long   0x80020070
    .size _os_map_page_4k, .-_os_map_page_4k

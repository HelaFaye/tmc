/* fx-CG50: the gint side of the frontend, behind plain C types.
 * gint's u32 is not the game's u32, so no file includes both. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

#define FXCG_SCREEN_W 396
#define FXCG_SCREEN_H 224

/* GBA button bits held down now (bit set = pressed, KEYINPUT order).
 * Handles MENU itself by visiting the calculator's main menu. */
uint16_t Fxcg_ReadButtons(void);
/* The RGB565 frame being drawn, FXCG_SCREEN_W x FXCG_SCREEN_H. */
uint16_t* Fxcg_Vram(void);
void Fxcg_Update(void);
void Fxcg_Clear(void);
void Fxcg_ShowMessage(const char* line1, const char* line2);
/* Start the 60 Hz tick, then block until the next one. */
void Fxcg_StartVBlank(void);
void Fxcg_WaitVBlank(void);

/* The game-side entry point, in fxcg_main.c. */
int Fxcg_GameMain(void);

/* fx-CG50: the PC port's optional features, switched off.
 *
 * The game code calls into features the calculator build does not carry:
 * the asset loader (the calculator reads graphics from the ROM pages, which
 * every call site falls back to), the randomizer, the "Reborn" tweaks,
 * text-to-speech, soft item slots, the level editor, the bug reporter and
 * the PC window and audio settings. Each function here returns what the PC
 * port returns when that feature is off, so the game behaves as the original.
 *
 * Music and sound effects (the m4a backend) are silent for now; the plan is
 * to send them to the ESP32 as events (docs/fx-cg50-port-plan.md, D5). */
#include "global.h"
#include "area.h"
#include "port_reborn.h"
#include "port_tts.h"
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* ---- asset loader: absent, so the ROM-backed path is used ---- */
bool32 Port_LoadPaletteGroupFromAssets(u32 group) { (void)group; return FALSE; }
bool32 Port_LoadGfxGroupFromAssets(u32 group) { (void)group; return FALSE; }
bool32 Port_LoadAreaTablesFromAssets(void) { return FALSE; }
bool32 Port_LoadSpritePtrsFromAssets(void) { return FALSE; }
bool32 Port_LoadTextsFromAssets(void) { return FALSE; }
bool32 Port_AreSpritePtrsLoadedFromAssets(void) { return FALSE; }
void Port_LogAssetLoaderStatus(void) {}
bool32 Port_RefreshAreaDataFromAssets(u32 area) { (void)area; return FALSE; }
bool32 Port_IsAreaTablePtrFromAssets(u32 area, const void* ptr) { (void)area; (void)ptr; return FALSE; }
bool32 Port_IsLoadedAssetBytes(const void* ptr, u32 size) { (void)ptr; (void)size; return FALSE; }
const u8* Port_LoadedAssetBytesEnd(const void* ptr) { (void)ptr; return NULL; }
const u8* Port_GetMapAssetDataByIndex(u32 assetIndex, u32* size) {
    (void)assetIndex;
    if (size)
        *size = 0;
    return NULL;
}
const u8* Port_GetSpriteAnimationData(u16 spriteIndex, u32 animIndex) { (void)spriteIndex; (void)animIndex; return NULL; }
void Port_LogTextLookup(u32 langIndex, u32 textIndex) { (void)langIndex; (void)textIndex; }

/* ---- runtime settings: the defaults of a fresh config.json ---- */
void Port_Config_CycleTargetFps(int direction) { (void)direction; }
u32 Port_Config_TargetFps(void) { return 60; }
bool Port_Config_GetHoldToAdvanceText(void) { return false; }
bool Port_Config_PortSettingsMenuEnabled(void) { return false; }
int Port_Config_PreferredLanguage(void) { return -1; }
int Port_Config_PreferredRegion(void) { return -1; }
void Port_Config_SetActiveSaveProfile(const char* path) { (void)path; }

/* ---- PC window and presentation ---- */
void Port_PPU_CyclePresentationMode(int direction) { (void)direction; }
void Port_PPU_CycleWindowScale(int direction) { (void)direction; }
bool Port_PPU_IsFullscreen(void) { return true; }
const char* Port_PPU_PresentationModeName(void) { return "fx-CG50"; }
void Port_PPU_ToggleFullscreen(void) {}
unsigned char Port_PPU_WindowScale(void) { return 1; }

/* ---- sound: silent until the ESP32 plays it ---- */
void Port_M4A_Backend_SoundInit(uint32_t soundMode) { (void)soundMode; }
void Port_M4A_Backend_SetSoundMode(uint32_t soundMode) { (void)soundMode; }
void Port_M4A_Backend_SetVSyncEnabled(bool enabled) { (void)enabled; }
bool Port_M4A_Backend_StartSongById(uint8_t playerIndex, uint16_t songId) { (void)playerIndex; (void)songId; return false; }
void Port_M4A_Backend_StopPlayer(uint8_t playerIndex) { (void)playerIndex; }
void Port_M4A_Backend_ContinuePlayer(uint8_t playerIndex) { (void)playerIndex; }
bool Port_M4A_Backend_IsPlayerActive(uint8_t playerIndex) { (void)playerIndex; return false; }
void Port_M4A_Backend_SetTrackVolume(uint8_t playerIndex, uint16_t trackBits, uint16_t volume) {
    (void)playerIndex; (void)trackBits; (void)volume;
}
void Port_M4A_Backend_SetTrackPan(uint8_t playerIndex, uint16_t trackBits, int8_t pan) {
    (void)playerIndex; (void)trackBits; (void)pan;
}
bool Port_AudioMute_ShouldSuppress(unsigned int soundReq) { (void)soundReq; return false; }
void Port_Audio_Reset(void) {}

/* ---- accessibility, Reborn, editor, bug reporter ---- */
bool Port_TTS_GetEnabled(void) { return false; }
void Port_TTS_Speak(const char* text, const PortTtsOptions* opts) { (void)text; (void)opts; }
void Port_TTS_Stop(void) {}
bool Port_Reborn_IsEnabled(RebornFeature f) { (void)f; return false; }
bool Port_Reborn_ConsumeJustResumed(void) { return false; }
void Port_Reborn_NotifyJustResumed(void) {}
void Port_LevelEditor_OnRoomLoad(void) {}
void Port_BugReport_NoteBadHitbox(unsigned kind, unsigned id, unsigned type, unsigned long long ptr) {
    (void)kind; (void)id; (void)type; (void)ptr;
}

/* ---- soft item slots: B keeps the item the save assigned ---- */
bool Port_SoftSlots_IsBHeld(void) { return false; }
int Port_SoftSlots_GetActiveSlot(void) { return -1; }
uint8_t Port_SoftSlots_GetEffectiveBItem(uint8_t saved) { return saved; }
uint8_t Port_SoftSlots_GetAssignment(int slot) { (void)slot; return 0; }
void Port_SoftSlots_SetAssignment(int slot, uint8_t itemId) { (void)slot; (void)itemId; }
void Port_SoftSlots_NotifyPauseActive(void) {}
bool Port_SoftSlots_IsPauseActive(void) { return false; }

/* ---- randomizer: never active ---- */
bool Rando_IsActive(void) { return false; }
void Rando_Reset(void) {}
bool Rando_OverrideItem(uint8_t* type, uint8_t* subtype) { (void)type; (void)subtype; return false; }
bool Rando_OverrideLocationKey(uint32_t location_key, uint8_t* type, uint8_t* subtype) {
    (void)location_key; (void)type; (void)subtype;
    return false;
}
bool Rando_RouteDungeonItem(unsigned item, unsigned subtype) { (void)item; (void)subtype; return false; }
int Rando_Music_Remap(int area, int song) { (void)area; return song; }
bool Rando_Homewarp_Request(void) { return false; }
int Rando_Runtime_DamageMultiplier(void) { return 1; }
void Rando_Runtime_OnNewFile(void) {}
bool Rando_Runtime_OpenTingleBrothers(void) { return false; }
void Rando_Runtime_Refresh(void) {}
void Rando_Entrance_RemapExit(uint8_t cur_area, uint8_t* area, uint8_t* room, int16_t* x, int16_t* y, uint8_t* layer,
                              uint8_t* spawn_type, uint8_t* facing) {
    (void)cur_area; (void)area; (void)room; (void)x; (void)y; (void)layer; (void)spawn_type; (void)facing;
}
void Rando_Entrance_RemapGreenWarp(uint8_t cur_area, uint32_t warp_type, uint8_t* area, uint8_t* room, int16_t* x,
                                   int16_t* y) {
    (void)cur_area; (void)warp_type; (void)area; (void)room; (void)x; (void)y;
}
void Rando_Entrance_RemapHole(uint8_t cur_area, uint8_t* area, uint8_t* room, uint8_t* layer, int16_t* x, int16_t* y) {
    (void)cur_area; (void)area; (void)room; (void)layer; (void)x; (void)y;
}
bool RandoLogic_IsLoaded(void) { return false; }
int RandoLogic_FindLocationByKey(uint32_t key) { (void)key; return -1; }
bool Port_RandoFileMenu_IsOpen(void) { return false; }
void Port_RandoFileMenu_Open(int save_slot) { (void)save_slot; }
bool Port_RandoFileMenu_ShouldOpenForNewFile(void) { return false; }
void Port_RandoFileMenu_ToggleSidebar(void) {}
uint64_t Port_RandoSave_ActiveBindingHash(void) { return 0; }
void Port_RandoSave_ClearSlot(int slot) { (void)slot; }
void Port_RandoSave_CopySlot(int src, int dst) { (void)src; (void)dst; }
bool Port_RandoSave_LoadSlot(int slot) { (void)slot; return false; }
bool Port_RandoSave_LoadedLegacySlot(void) { return false; }
bool Port_RandoSave_SaveActiveSlot(int slot) { (void)slot; return false; }

/* ---- room headers: without the asset loader, only ROM headers exist ---- */
extern u8* gRomData;
extern u32 gRomSize;
bool32 Port_IsRoomHeaderPtrReadable(const void* ptr) {
    const u8* p = (const u8*)ptr;
    return gRomData && p >= gRomData && p + sizeof(RoomHeader) <= gRomData + gRomSize;
}

/* ---- debug menu and analog stick: absent ---- */
int Port_Debug_NoclipEnabled(void) { return 0; }
void Port_Debug_OnFlagSet(u32 offset, u32 flag) { (void)offset; (void)flag; }
bool Port_AnalogMovement_Apply(u32* heldInputOut, u32* directionOut) {
    (void)heldInputOut; (void)directionOut;
    return false;
}

/*
 * vr_anchor.h — stabilized camera and dual-scale anchoring.
 *
 * The invariant this enforces:
 *
 *     The VR camera never moves as a consequence of gameplay. It moves only
 *     when the player physically moves their head, when the player deliberately
 *     requests a reposition, or on a room transition covered by a fade.
 *
 * Scale is a parameter, not an architecture: tabletop, diorama and life scale
 * are the same system with a different metresPerPixel.
 *
 * Build this in Phase 2, on a monitor, with viewCount == 1. Checklist items 1-6
 * in Part III are all testable flat. If you defer it to Phase 4 you will be
 * restructuring the render graph instead of substituting two matrices.
 *
 * What NOT to read anywhere in the render path
 * --------------------------------------------
 * gRoomControls.scroll_x / scroll_y   (follow camera; scripts busy-wait on it)
 * gRoomControls.aff_x / aff_y         (affine effects)
 * gRoomControls.oam_offset_x / _y     (screen-space OAM nudge)
 * gMap*.bgSettings->xOffset / yOffset (screen shake lives here)
 *
 * Screen shake writes ONLY to the last two pairs — UpdateScreenShake never
 * touches scroll_x/scroll_y (src/scroll.c:892). So a world-space renderer is
 * immune to shake by construction, with no suppression code at all. Keep it
 * that way: don't expose those fields to the renderer in the first place.
 *
 * scroll_x/scroll_y remain a valid STREAMING hint — they tell you what the
 * engine has loaded and is updating. Read them for residency, never for a view.
 */

#ifndef VR_ANCHOR_H
#define VR_ANCHOR_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VR_MAX_VIEWS 2

/* ---- scale presets (metres per game pixel) ------------------------------- */
#define VR_SCALE_TABLETOP 0.005f  /* Link  8 cm; GBA screen 1.2 x 0.8 m  */
#define VR_SCALE_DIORAMA  0.010f  /* Link 16 cm; GBA screen 2.4 x 1.6 m  */
#define VR_SCALE_LIFE     0.100f  /* Link 1.6 m; GBA screen  24 x 16  m  */

typedef enum {
    VR_FOLLOW_STATIC,    /* anchor fixed per room              — diorama default */
    VR_FOLLOW_BLINK,     /* fade-snap on dead-zone exit        — life default    */
    VR_FOLLOW_ATTACHED,  /* continuous follow                  — opt-in only     */
} VrFollowMode;

typedef struct {
    float view[16];      /* world -> eye */
    float proj[16];      /* eye -> clip; asymmetric in VR */
    float position[3];   /* world-space eye origin: LOD, billboard facing */
} VrView;

typedef struct {
    VrView   views[VR_MAX_VIEWS];
    uint32_t count;          /* 1 = flat 3D, 2 = stereo */
    uint32_t multiviewMask;  /* 0b01 flat, 0b11 stereo */
} VrViewSet;

typedef struct {
    float        worldOrigin[3];  /* game-world point mapped to play-space origin */
    float        metresPerPixel;
    float        zScale;          /* entity->z multiplier; calibrate by eye */
    float        yaw;             /* world rotation; player-set only */
    float        deadZonePx;      /* BLINK: resnap radius in world pixels */
    VrFollowMode follow;

    /* fade state — internal */
    float fadeT;                  /* 0 = clear, 1 = fully black */
    int   fadePhase;              /* 0 idle, 1 fading out, 2 fading in */
    float pendingOrigin[3];
} VrAnchor;

extern VrAnchor gVrAnchor;

/* Apply a scale preset and the follow mode that suits it. Safe mid-session:
 * meshes are stored in world-pixel units, so nothing needs rebuilding. */
void VrAnchor_SetScale(float metresPerPixel);

/* Recentre on the current room. Call on room change, at the fade's black point.
 * Uses origin_x/origin_y + width/height — NOT scroll. */
void VrAnchor_CentreOnRoom(void);

/* Per-tick. linkX/linkY are entity->x.HALF.HI / y.HALF.HI in world pixels.
 * dtSeconds drives the blink fade. */
void VrAnchor_Tick(float linkX, float linkY, float dtSeconds);

/* Begin a deliberate reposition (blink teleport, scale switch, room change). */
void VrAnchor_BlinkTo(float worldX, float worldY);

/* 0..1 black-out factor to composite over both eyes. */
float VrAnchor_FadeAmount(void);

/* Build the view set.
 *   flat   : pass eyeCount 1 and one eye pose (your debug camera)
 *   stereo : pass eyeCount 2 and the poses from xrLocateViews
 * eyePose is a column-major 4x4 eye->playspace transform per view;
 * proj is supplied by the caller (xrCreateProjectionFov or your flat frustum).
 */
void VrAnchor_BuildViewSet(VrViewSet* out,
                           const float* eyePose, const float* proj,
                           uint32_t eyeCount);

/* World-pixel -> metre transform, column-major. Exposed for the model matrix:
 * keep meshes in world-pixel units and apply scale here, so switching presets
 * costs one uniform update and never a remesh. */
void VrAnchor_WorldToPlaySpace(float out[16]);

#ifdef __cplusplus
}
#endif

#endif /* VR_ANCHOR_H */

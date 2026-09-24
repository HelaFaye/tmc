/*
 * vr_interp.h — 60 Hz simulation to 72/90/120 Hz display interpolation.
 *
 * Picori's decoupled present path (port/port_bios.c, sDecoupleRender = true by
 * default) already re-presents identical frames above the tick rate. Its own
 * comment says "identical until interpolation lands." On a monitor that is
 * invisible; at tabletop scale in a headset, with Link 40 cm from your face,
 * the stutter is obvious. This is that landing.
 *
 * Two bugs from the draft version are fixed here, both of which produce
 * artefacts that are easy to misdiagnose as something else:
 *
 *  1. ENTITY SLOT REUSE. gEntities[i] is recycled. When one entity dies and
 *     another spawns into the same slot, blending from the dead one's position
 *     draws a one-frame streak across the room. We track an identity tuple and
 *     snap rather than blend when it changes.
 *
 *  2. CLOCK DOMAIN. XrTime is the runtime's own time base, not your system
 *     clock. Subtracting a raw clock_gettime() value from predictedDisplayTime
 *     yields a meaningless ratio. Convert once with XR_KHR_convert_timespec_time
 *     (Linux/Android) or XR_KHR_win32_convert_performance_counter_time, or —
 *     simpler and what this module does — stamp the tick in XrTime directly by
 *     asking the runtime to convert at tick time.
 */

#ifndef VR_INTERP_H
#define VR_INTERP_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VR_MAX_TRACKED 72                    /* gEntities[] slots */
/* Link lives in gPlayerEntity, not in gEntities, so he gets his own slot
 * after the pool. Without it the one entity the player watches most would
 * never be interpolated. */
#define VR_TRACK_LINK    VR_MAX_TRACKED
#define VR_TRACKED_SLOTS (VR_MAX_TRACKED + 1)

typedef struct {
    uint8_t kind, id, type;   /* identity tuple; slot reuse changes at least one */
    uint8_t valid;
    uint8_t frameIndex;       /* NOT interpolated — animation frames must pop */
    uint8_t direction;

    float prevPos[3];         /* world pixels, (x, y_up, z_south) */
    float currPos[3];
    float blended[3];
} VrTrackedEntity;

extern VrTrackedEntity gVrTracked[VR_TRACKED_SLOTS];

/* Call once per GAME TICK, from the 60 Hz path (port_bios.c, after the engine
 * has finished updating entities). tickTimeXr must be in the OpenXR time
 * domain; see VrInterp_NowXrTime below. Pass 0 when running flat (no session),
 * and the module falls back to tick counting. */
void VrInterp_OnTick(int64_t tickTimeXr);

/* Call once per RENDER FRAME with frameState.predictedDisplayTime straight from
 * xrWaitFrame. tickDurationNs should come from Port_Config_TickTimeNs(), not a
 * hardcoded 16666666 — Picori lets the tick rate be configured. */
void VrInterp_Evaluate(int64_t predictedDisplayTimeXr, int64_t tickDurationNs);

/* Helper: current time in the OpenXR domain. Requires the conversion extension
 * to have been enabled at xrCreateInstance. Returns 0 if unavailable, which
 * VrInterp_OnTick treats as "no session, count ticks instead".
 *
 * Enable at instance creation:
 *   Linux/Android : XR_KHR_CONVERT_TIMESPEC_TIME_EXTENSION_NAME
 *   Windows       : XR_KHR_WIN32_CONVERT_PERFORMANCE_COUNTER_TIME_EXTENSION_NAME
 */
int64_t VrInterp_NowXrTime(void);

#ifdef __cplusplus
}
#endif

#endif /* VR_INTERP_H */

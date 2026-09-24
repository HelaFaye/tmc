#include "vr_interp.h"
#include <string.h>

#ifndef VR_INTERP_STANDALONE_TEST
#include "entity.h"
#include "player.h"    /* gPlayerEntity: Link is not in gEntities */
/* gEntities is GenericEntity[MAX_ENTITIES] and entity.h already declares it.
 * GenericEntity's first member is `Entity base`, so that is what we read. */
#define VR_ENT(i) (&gEntities[(i)].base)
#endif

VrTrackedEntity gVrTracked[VR_TRACKED_SLOTS];

static int64_t sLastTickXr = 0;
static int     sHaveXrClock = 0;

/* Provided by the VR layer once a session exists. Left weak/overridable so the
 * flat 3D build links without OpenXR at all. */
#if defined(TMC_VR) && !defined(VR_INTERP_STANDALONE_TEST)
int64_t VrInterp_NowXrTime(void);   /* implemented in vr_openxr.c */
#else
int64_t VrInterp_NowXrTime(void) { return 0; }
#endif

static int SameIdentity(const VrTrackedEntity* t,
                        uint8_t kind, uint8_t id, uint8_t type) {
    return t->valid && t->kind == kind && t->id == id && t->type == type;
}

/* Split out so the logic is testable without the engine. */
void VrInterp_UpdateSlot(VrTrackedEntity* t,
                         uint8_t kind, uint8_t id, uint8_t type,
                         uint8_t frameIndex, uint8_t direction,
                         float x, float y, float z) {
    int continuous = SameIdentity(t, kind, id, type);

    if (continuous) {
        memcpy(t->prevPos, t->currPos, sizeof(t->prevPos));
    } else {
        /* New occupant of this slot. Seed prev == curr so the first frame is a
         * snap, not a streak from wherever the previous entity died. */
        t->prevPos[0] = x; t->prevPos[1] = y; t->prevPos[2] = z;
    }

    t->kind = kind; t->id = id; t->type = type;
    t->frameIndex = frameIndex;
    t->direction = direction;
    t->valid = 1;

    t->currPos[0] = x; t->currPos[1] = y; t->currPos[2] = z;
    memcpy(t->blended, t->currPos, sizeof(t->blended));
}

#ifndef VR_INTERP_STANDALONE_TEST
void VrInterp_OnTick(int64_t tickTimeXr) {
    sLastTickXr = tickTimeXr;
    sHaveXrClock = (tickTimeXr != 0);

    for (int i = 0; i < VR_MAX_TRACKED; i++) {
        Entity* e = VR_ENT(i);
        VrTrackedEntity* t = &gVrTracked[i];

        if (e->kind == 0) {
            t->valid = 0;
            continue;
        }

        /* World position. Note what is NOT here: spriteOffsetX/Y. Those are
         * art-centring offsets for the 2D draw path; folding them into world
         * position displaces the entity from where the game thinks it is.
         * Apply them when placing the hull relative to its own origin instead.
         *
         * And note z is on its own axis rather than folded into y, which is
         * what port_draw.c does for drawing (y_screen = y + z). Screen Y grows
         * downward, so an entity rising has negative z; negate for world up. */
        float x = (float)e->x.HALF.HI;
        float z = (float)e->y.HALF.HI;
        float y = -(float)e->z.HALF.HI;

        VrInterp_UpdateSlot(t, e->kind, e->id, e->type,
                            e->frameIndex, e->direction, x, y, z);
    }

    /* Link, same axis convention as above. */
    {
        const Entity* e = &gPlayerEntity.base;
        VrInterp_UpdateSlot(&gVrTracked[VR_TRACK_LINK],
                            e->kind, e->id, e->type,
                            e->frameIndex, e->direction,
                            (float)e->x.HALF.HI,
                            -(float)e->z.HALF.HI,
                            (float)e->y.HALF.HI);
    }
}
#else
void VrInterp_OnTick(int64_t tickTimeXr) {
    sLastTickXr = tickTimeXr;
    sHaveXrClock = (tickTimeXr != 0);
}
#endif

void VrInterp_Evaluate(int64_t predictedDisplayTimeXr, int64_t tickDurationNs) {
    float t = 0.0f;

    if (sHaveXrClock && tickDurationNs > 0 &&
        predictedDisplayTimeXr > sLastTickXr) {
        int64_t elapsed = predictedDisplayTimeXr - sLastTickXr;
        t = (float)elapsed / (float)tickDurationNs;
        if (t > 1.0f) t = 1.0f;   /* clamp: never extrapolate past the next tick */
        if (t < 0.0f) t = 0.0f;
    }

    for (int i = 0; i < VR_TRACKED_SLOTS; i++) {
        VrTrackedEntity* e = &gVrTracked[i];
        if (!e->valid) continue;
        for (int c = 0; c < 3; c++)
            e->blended[c] = e->prevPos[c] + t * (e->currPos[c] - e->prevPos[c]);
    }
}

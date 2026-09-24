/*
 * vr_anchor.c — stabilized camera and dual-scale anchoring.
 *
 * See vr_anchor.h for the invariant and the do-not-read list.
 */

#include "vr_anchor.h"
#include "vr_world.h"
#include <math.h>
#include <string.h>

/* Blink timing. 80 ms each way is short enough to feel like a cut rather than a
 * transition, which is the point: blink teleportation is comfort-safe precisely
 * because it removes optic flow instead of smoothing it. */
#define VR_FADE_OUT_SEC 0.08f
#define VR_FADE_IN_SEC  0.08f

/* Hysteresis on the dead zone, so a player oscillating on the boundary doesn't
 * blink repeatedly. Re-arm only once Link is back inside 0.7 * radius. */
#define VR_DEADZONE_REARM 0.70f

VrAnchor gVrAnchor = {
    .worldOrigin    = { 0.0f, 0.0f, 0.0f },
    .metresPerPixel = VR_SCALE_TABLETOP,
    .zScale         = 1.0f,
    .yaw            = 0.0f,
    .deadZonePx     = 120.0f,
    .follow         = VR_FOLLOW_STATIC,
    .fadeT          = 0.0f,
    .fadePhase      = 0,
};

static int sBlinkArmed = 1;

/* ---- small matrix helpers (column-major, OpenGL/Vulkan convention) -------- */

static void MatIdentity(float m[16]) {
    memset(m, 0, sizeof(float) * 16);
    m[0] = m[5] = m[10] = m[15] = 1.0f;
}

static void MatMul(float out[16], const float a[16], const float b[16]) {
    float t[16];
    for (int c = 0; c < 4; c++)
        for (int r = 0; r < 4; r++) {
            float s = 0.0f;
            for (int k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
            t[c * 4 + r] = s;
        }
    memcpy(out, t, sizeof(t));
}

/* Inverse of a rigid transform (rotation + translation, no scale). */
static void MatInvertRigid(float out[16], const float m[16]) {
    float r[16];
    MatIdentity(r);
    for (int i = 0; i < 3; i++)
        for (int j = 0; j < 3; j++) r[j * 4 + i] = m[i * 4 + j];  /* transpose */
    float tx = m[12], ty = m[13], tz = m[14];
    r[12] = -(r[0] * tx + r[4] * ty + r[8]  * tz);
    r[13] = -(r[1] * tx + r[5] * ty + r[9]  * tz);
    r[14] = -(r[2] * tx + r[6] * ty + r[10] * tz);
    memcpy(out, r, sizeof(r));
}

/* ---- public -------------------------------------------------------------- */

void VrAnchor_SetScale(float metresPerPixel) {
    gVrAnchor.metresPerPixel = metresPerPixel;

    /* Life scale needs BLINK: at 0.10 m/px a maximum room is over 100 m across,
     * and continuous following there is the classic vection failure. The
     * diorama scales are STATIC — the room is a fixed model on a fixed table. */
    if (metresPerPixel >= VR_SCALE_LIFE * 0.9f) {
        gVrAnchor.follow     = VR_FOLLOW_BLINK;
        gVrAnchor.deadZonePx = 120.0f;   /* half a GBA screen */
    } else {
        gVrAnchor.follow = VR_FOLLOW_STATIC;
    }
}

void VrAnchor_CentreOnRoom(void) {
    if (!gVrWorld.valid) return;
    gVrAnchor.worldOrigin[0] = (float)gVrWorld.originX + gVrWorld.width  * 0.5f;
    gVrAnchor.worldOrigin[1] = 0.0f;
    gVrAnchor.worldOrigin[2] = (float)gVrWorld.originY + gVrWorld.height * 0.5f;
    sBlinkArmed = 1;
}

void VrAnchor_BlinkTo(float worldX, float worldY) {
    if (gVrAnchor.fadePhase != 0) return;      /* already blinking */
    gVrAnchor.pendingOrigin[0] = worldX;
    gVrAnchor.pendingOrigin[1] = 0.0f;
    gVrAnchor.pendingOrigin[2] = worldY;
    gVrAnchor.fadePhase = 1;
    gVrAnchor.fadeT = 0.0f;
}

void VrAnchor_Tick(float linkX, float linkY, float dtSeconds) {
    /* --- advance any in-flight blink ------------------------------------- */
    if (gVrAnchor.fadePhase == 1) {
        gVrAnchor.fadeT += dtSeconds / VR_FADE_OUT_SEC;
        if (gVrAnchor.fadeT >= 1.0f) {
            gVrAnchor.fadeT = 1.0f;
            /* black point: this is the only place the anchor jumps */
            memcpy(gVrAnchor.worldOrigin, gVrAnchor.pendingOrigin, sizeof(float) * 3);
            gVrAnchor.fadePhase = 2;
            sBlinkArmed = 0;
        }
        return;
    }
    if (gVrAnchor.fadePhase == 2) {
        gVrAnchor.fadeT -= dtSeconds / VR_FADE_IN_SEC;
        if (gVrAnchor.fadeT <= 0.0f) {
            gVrAnchor.fadeT = 0.0f;
            gVrAnchor.fadePhase = 0;
        }
        return;
    }

    /* --- follow policy ---------------------------------------------------- */
    switch (gVrAnchor.follow) {
    case VR_FOLLOW_STATIC:
        /* Nothing. The room is a fixed model; Link walks around inside it.
         * Note that Scroll1 clamps scroll to [origin, origin + width - 0xF0],
         * so for any room <= 240x160 the engine camera never moves either. */
        break;

    case VR_FOLLOW_BLINK: {
        float dx = linkX - gVrAnchor.worldOrigin[0];
        float dz = linkY - gVrAnchor.worldOrigin[2];
        float d2 = dx * dx + dz * dz;
        float r  = gVrAnchor.deadZonePx;

        if (!sBlinkArmed && d2 < (r * VR_DEADZONE_REARM) * (r * VR_DEADZONE_REARM))
            sBlinkArmed = 1;

        if (sBlinkArmed && d2 > r * r)
            VrAnchor_BlinkTo(linkX, linkY);
        break;
    }

    case VR_FOLLOW_ATTACHED:
        /* Opt-in, never default, never a new player's first experience. */
        gVrAnchor.worldOrigin[0] = linkX;
        gVrAnchor.worldOrigin[2] = linkY;
        break;
    }
}

float VrAnchor_FadeAmount(void) {
    return gVrAnchor.fadeT;
}

void VrAnchor_WorldToPlaySpace(float out[16]) {
    float s = gVrAnchor.metresPerPixel;
    float c = cosf(gVrAnchor.yaw), n = sinf(gVrAnchor.yaw);

    /* scale, then rotate about Y, then translate so worldOrigin sits at the
     * play-space origin. Composed directly rather than via three MatMuls. */
    float ox = gVrAnchor.worldOrigin[0];
    float oy = gVrAnchor.worldOrigin[1];
    float oz = gVrAnchor.worldOrigin[2];

    out[0]  =  c * s; out[1]  = 0.0f; out[2]  = -n * s; out[3]  = 0.0f;
    out[4]  =  0.0f;  out[5]  = s;    out[6]  =  0.0f;  out[7]  = 0.0f;
    out[8]  =  n * s; out[9]  = 0.0f; out[10] =  c * s; out[11] = 0.0f;

    /* translation = -R*S*origin */
    out[12] = -(out[0] * ox + out[4] * oy + out[8]  * oz);
    out[13] = -(out[1] * ox + out[5] * oy + out[9]  * oz);
    out[14] = -(out[2] * ox + out[6] * oy + out[10] * oz);
    out[15] = 1.0f;
}

void VrAnchor_BuildViewSet(VrViewSet* out,
                           const float* eyePose, const float* proj,
                           uint32_t eyeCount) {
    if (!out) return;
    if (eyeCount > VR_MAX_VIEWS) eyeCount = VR_MAX_VIEWS;

    float w2p[16];
    VrAnchor_WorldToPlaySpace(w2p);

    out->count = eyeCount;
    out->multiviewMask = (eyeCount >= 2) ? 0x3u : 0x1u;

    for (uint32_t i = 0; i < eyeCount; i++) {
        const float* pose = eyePose + i * 16;

        float inv[16];
        MatInvertRigid(inv, pose);             /* playspace -> eye */
        MatMul(out->views[i].view, inv, w2p);  /* world -> eye */

        memcpy(out->views[i].proj, proj + i * 16, sizeof(float) * 16);

        /* Eye origin in WORLD pixels: undo the anchor so LOD and billboard
         * facing work in the same units as the meshes. */
        float s = gVrAnchor.metresPerPixel;
        float c = cosf(gVrAnchor.yaw), n = sinf(gVrAnchor.yaw);
        float px = pose[12] / s, py = pose[13] / s, pz = pose[14] / s;
        out->views[i].position[0] = gVrAnchor.worldOrigin[0] + ( c * px + n * pz);
        out->views[i].position[1] = gVrAnchor.worldOrigin[1] + py;
        out->views[i].position[2] = gVrAnchor.worldOrigin[2] + (-n * px + c * pz);
    }
}

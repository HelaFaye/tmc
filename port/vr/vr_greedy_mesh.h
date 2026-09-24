/*
 * vr_greedy_mesh.h — 3-axis greedy mesher for voxel grids.
 *
 * Corrected from the brainstorm draft. Two things were wrong there:
 *
 *   1. It used CHUNK_W as the loop bound and row stride on all three axes, so
 *      it only worked on cubes. Sprite hulls are not cubes — a 12x16x6 hull is
 *      typical — and terrain chunks certainly aren't.
 *   2. It stored `quad.direction = d`, losing the sign. Without knowing whether
 *      a face points +d or -d you cannot wind triangles consistently, cannot
 *      back-face cull, and cannot light. The draft's meshes would have rendered
 *      with half the faces inside-out.
 *
 * Both are fixed here. Faces carry a signed direction 0..5 and correct winding.
 *
 * The grid is a dense array of palette indices; 0 means empty. Index order is
 *      grid[x + y*sx + z*sx*sy]
 * which is x-fastest, matching how the hull carver and the terrain classifier
 * both write their grids.
 */

#ifndef VR_GREEDY_MESH_H
#define VR_GREEDY_MESH_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VR_FACE_NEG_X = 0, VR_FACE_POS_X,
    VR_FACE_NEG_Y,     VR_FACE_POS_Y,
    VR_FACE_NEG_Z,     VR_FACE_POS_Z,
} VrFaceDir;

typedef struct {
    /* Four corners in voxel-grid space, counter-clockwise when viewed from
     * outside, so a single winding rule works for every face. */
    float    v[4][3];
    uint8_t  palette;
    uint8_t  dir;      /* VrFaceDir */
} VrQuad;

typedef struct {
    VrQuad*  quads;
    size_t   count;
    size_t   capacity;
} VrQuadList;

void VrQuadList_Init(VrQuadList* list);
void VrQuadList_Free(VrQuadList* list);

/* Mesh a dense palette grid of dimensions (sx, sy, sz).
 * Returns the number of quads emitted, or (size_t)-1 on allocation failure.
 *
 * Merges only faces that share a palette index, so colour boundaries stay
 * crisp. Pass mergeAcrossColour = 1 to ignore palette when merging (useful for
 * shadow/occlusion-only passes where colour is irrelevant). */
size_t VrGreedyMesh(const uint8_t* grid, int sx, int sy, int sz,
                    VrQuadList* out, int mergeAcrossColour);

#ifdef __cplusplus
}
#endif

#endif /* VR_GREEDY_MESH_H */

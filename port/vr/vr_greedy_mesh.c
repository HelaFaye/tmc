#include "vr_greedy_mesh.h"
#include <stdlib.h>
#include <string.h>

void VrQuadList_Init(VrQuadList* list) {
    list->quads = NULL;
    list->count = 0;
    list->capacity = 0;
}

void VrQuadList_Free(VrQuadList* list) {
    free(list->quads);
    VrQuadList_Init(list);
}

static int PushQuad(VrQuadList* list, const VrQuad* q) {
    if (list->count == list->capacity) {
        size_t cap = list->capacity ? list->capacity * 2 : 256;
        VrQuad* n = (VrQuad*)realloc(list->quads, cap * sizeof(VrQuad));
        if (!n) return 0;
        list->quads = n;
        list->capacity = cap;
    }
    list->quads[list->count++] = *q;
    return 1;
}

size_t VrGreedyMesh(const uint8_t* grid, int sx, int sy, int sz,
                    VrQuadList* out, int mergeAcrossColour) {
    if (!grid || !out || sx <= 0 || sy <= 0 || sz <= 0) return 0;

    const int dim[3] = { sx, sy, sz };
    const size_t cells = (size_t)sx * sy * sz;

    /* Slice masks are sized for the largest face, allocated once. */
    int maxU = sx > sy ? (sx > sz ? sx : sz) : (sy > sz ? sy : sz);
    uint8_t* mask = (uint8_t*)malloc((size_t)maxU * maxU);
    int8_t*  sign = (int8_t*) malloc((size_t)maxU * maxU);
    if (!mask || !sign) { free(mask); free(sign); return (size_t)-1; }

    #define AT(X, Y, Z) grid[(size_t)(X) + (size_t)(Y) * sx + (size_t)(Z) * sx * sy]
    (void)cells;

    for (int d = 0; d < 3; d++) {
        const int u = (d + 1) % 3;   /* mask column axis */
        const int v = (d + 2) % 3;   /* mask row axis    */
        const int du = dim[u], dv = dim[v], dd = dim[d];
        const int stride = du;       /* mask row stride — NOT a fixed CHUNK_W */

        int x[3] = { 0, 0, 0 };
        int q[3] = { 0, 0, 0 };
        q[d] = 1;

        /* Sweep the d axis. Slice k sits between cell k-1 and cell k, so the
         * range is -1 .. dd-1 inclusive: dd+1 slices. */
        for (x[d] = -1; x[d] < dd; x[d]++) {
            memset(mask, 0, (size_t)du * dv);
            memset(sign, 0, (size_t)du * dv);

            for (x[v] = 0; x[v] < dv; x[v]++) {
                for (x[u] = 0; x[u] < du; x[u]++) {
                    uint8_t a = 0, b = 0;
                    if (x[d] >= 0)
                        a = AT(x[0], x[1], x[2]);
                    if (x[d] < dd - 1)
                        b = AT(x[0] + q[0], x[1] + q[1], x[2] + q[2]);

                    size_t m = (size_t)x[v] * stride + x[u];
                    if ((a != 0) == (b != 0)) continue;   /* no boundary here */

                    if (a) { mask[m] = a; sign[m] = +1; }  /* face points +d */
                    else   { mask[m] = b; sign[m] = -1; }  /* face points -d */
                }
            }

            /* Greedy rectangles over the slice. */
            for (int j = 0; j < dv; j++) {
                for (int i = 0; i < du; ) {
                    size_t m = (size_t)j * stride + i;
                    if (!mask[m]) { i++; continue; }

                    uint8_t colour = mask[m];
                    int8_t  s = sign[m];

                    int w = 1;
                    while (i + w < du) {
                        size_t mm = m + w;
                        if (!mask[mm] || sign[mm] != s) break;
                        if (!mergeAcrossColour && mask[mm] != colour) break;
                        w++;
                    }

                    int h = 1;
                    for (; j + h < dv; h++) {
                        int ok = 1;
                        for (int k = 0; k < w; k++) {
                            size_t mm = (size_t)(j + h) * stride + (i + k);
                            if (!mask[mm] || sign[mm] != s ||
                                (!mergeAcrossColour && mask[mm] != colour)) {
                                ok = 0; break;
                            }
                        }
                        if (!ok) break;
                    }

                    /* Emit. The quad plane sits at d = x[d]+1 (the boundary). */
                    int base[3];
                    base[d] = x[d] + 1;
                    base[u] = i;
                    base[v] = j;

                    int eu[3] = { 0, 0, 0 }; eu[u] = w;
                    int ev[3] = { 0, 0, 0 }; ev[v] = h;

                    VrQuad quad;
                    quad.palette = colour;
                    quad.dir = (uint8_t)(d * 2 + (s > 0 ? 1 : 0));

                    /* Wind counter-clockwise as seen from outside: swap the two
                     * edge vectors when the face points -d. Getting this wrong
                     * is what made the draft mesher render half its faces
                     * inside-out. */
                    const int* e0 = (s > 0) ? eu : ev;
                    const int* e1 = (s > 0) ? ev : eu;

                    for (int c = 0; c < 3; c++) {
                        quad.v[0][c] = (float)base[c];
                        quad.v[1][c] = (float)(base[c] + e0[c]);
                        quad.v[2][c] = (float)(base[c] + e0[c] + e1[c]);
                        quad.v[3][c] = (float)(base[c] + e1[c]);
                    }

                    if (!PushQuad(out, &quad)) {
                        free(mask); free(sign);
                        return (size_t)-1;
                    }

                    for (int l = 0; l < h; l++)
                        for (int k = 0; k < w; k++)
                            mask[(size_t)(j + l) * stride + (i + k)] = 0;

                    i += w;
                }
            }
        }
    }

    #undef AT
    free(mask);
    free(sign);
    return out->count;
}

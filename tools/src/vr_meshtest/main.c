#include "vr_greedy_mesh.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void){
  /* non-cube grid to catch the cube assumption */
  int sx=5, sy=4, sz=3;
  uint8_t* g = calloc(sx*sy*sz,1);
  #define S(x,y,z,c) g[(x)+(y)*sx+(z)*sx*sy]=(c)
  for(int z=0;z<sz;z++)for(int y=0;y<sy;y++)for(int x=0;x<sx;x++) S(x,y,z,7);
  VrQuadList L; VrQuadList_Init(&L);
  size_t n=VrGreedyMesh(g,sx,sy,sz,&L,0);
  printf("solid box %dx%dx%d -> %zu quads (expect 6)\n",sx,sy,sz,n);
  int per[6]={0}; for(size_t i=0;i<L.count;i++) per[L.quads[i].dir]++;
  printf("faces per dir: %d %d %d %d %d %d\n",per[0],per[1],per[2],per[3],per[4],per[5]);
  /* winding check: compute normal of each quad, must match dir */
  int bad=0;
  for(size_t i=0;i<L.count;i++){
    float*a=L.quads[i].v[0],*b=L.quads[i].v[1],*c=L.quads[i].v[2];
    float e1[3]={b[0]-a[0],b[1]-a[1],b[2]-a[2]},e2[3]={c[0]-b[0],c[1]-b[1],c[2]-b[2]};
    float nx=e1[1]*e2[2]-e1[2]*e2[1],ny=e1[2]*e2[0]-e1[0]*e2[2],nz=e1[0]*e2[1]-e1[1]*e2[0];
    float nv[3]={nx,ny,nz};
    int axis=L.quads[i].dir/2, s=(L.quads[i].dir%2)?1:-1;
    if(!((nv[axis]>0)==(s>0) && nv[axis]!=0)) bad++;
  }
  printf("winding mismatches: %d (expect 0)\n",bad);
  VrQuadList_Free(&L);
  /* two-colour split should not merge */
  memset(g,0,sx*sy*sz);
  for(int z=0;z<sz;z++)for(int y=0;y<sy;y++)for(int x=0;x<sx;x++) S(x,y,z, x<2?3:9);
  VrQuadList_Init(&L); n=VrGreedyMesh(g,sx,sy,sz,&L,0);
  printf("two-colour box -> %zu quads\n",n);
  VrQuadList_Free(&L);
  /* hollow shell / single voxel */
  memset(g,0,sx*sy*sz); S(2,2,1,4);
  VrQuadList_Init(&L); n=VrGreedyMesh(g,sx,sy,sz,&L,0);
  printf("single voxel -> %zu quads (expect 6)\n",n);
  free(g); VrQuadList_Free(&L);
  return 0;
}

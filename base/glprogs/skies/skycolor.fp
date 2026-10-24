# skies/skycolor: gradient cube plus the sun halo, max(N.sun * halo.x + halo.y, 0).
TEMP	dir, halo, look;
DP3	dir.w, fragment.texcoord[1], fragment.texcoord[1];
RSQ	dir.w, dir.w;
MUL	dir.xyz, fragment.texcoord[1], dir.w;
DP3	halo.x, dir, fragment.texcoord[2];
MAD	halo.x, halo.x, $sunHaloParameters.x, $sunHaloParameters.y;
MAX	halo.x, halo.x, 0;
TEX	look, fragment.texcoord[0], $skyGradientCubeMap, CUBE;
ADD	result.color, look, halo.x;

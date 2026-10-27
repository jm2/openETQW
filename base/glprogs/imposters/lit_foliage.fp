# imposters/lit_foliage: retail shares imposters/lit's vertex program by
# reference and differs only in the fragment: a screen-space noise dither on the
# vertex alpha, the normal rebuilt in the vertex tangent frame, and the alpha
# test at 0.5.  The r_megaDrawMethod 0 path is not ported, as in the other ports.
TEMP	noise, coord, innorm, diffuse, normal, cube, light;

MAD	coord.xy, fragment.position, 0.0625, 0.0625;
TEX	noise, coord, $noiseMap, 2D;
SUB	coord.x, fragment.color.primary.w, noise.w;
KIL	coord.x;

TEX	diffuse, fragment.texcoord[0], $diffuseMap, 2D;
TEX	innorm, fragment.texcoord[1], $diffuseMap, 2D;
MAD	normal.xyz, innorm, 2, -1;
MUL	light.xyz, fragment.texcoord[4], -normal.x;
MAD	light.xyz, fragment.texcoord[5], normal.y, light;
MAD	light.xyz, fragment.texcoord[6], normal.z, light;
DP3	light.w, light, light;
RSQ	light.w, light.w;
MUL	normal.xyz, light, light.w;

TEX	cube, normal, $ambientCubeMap, CUBE;
MUL	cube.rgb, cube, $ambientBrightness;
DP3	light.x, $sunDirectionWorld, normal;
MAX	light.x, light.x, innorm.w;
MUL	light.rgb, light.x, $sunColor;
ADD	light.rgb, light.rgb, cube;

# alpha test at retail's 0.5
SUB	coord.x, diffuse.w, 0.5;
KIL	coord.x;

MUL	diffuse.rgb, diffuse, light;
SUB	light.rgb, $fogColor, diffuse;
MAD	diffuse.rgb, fragment.color.secondary.x, light, diffuse;
MOV	result.color.rgb, diffuse;
MOV	result.color.a, diffuse.w;

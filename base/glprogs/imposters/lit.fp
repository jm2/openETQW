# imposters/lit: the card's two-row atlas (diffuse on top, tangent-space normal
# at v + 0.5), lit by the sun carried into tangent space plus the ambient cube,
# fogged by the extinction imposters/lit.vp puts in the secondary colour.
TEMP	diffuse, normal, cube, light;

TEX	diffuse, fragment.texcoord[0], $diffuseMap, 2D;
TEX	normal, fragment.texcoord[1], $diffuseMap, 2D;
MAD	normal.rgb, normal, 2, -1;
TEX	cube, normal, $ambientCubeMap, CUBE;
MUL	cube.rgb, cube, $ambientBrightness;
DP3	light.x, fragment.texcoord[2], normal;
MAX	light.x, light.x, 0;
MUL	light.rgb, light.x, $sunColor;
ADD	light.rgb, light.rgb, cube;

# alpha test at retail's 0.1
SUB	light.w, diffuse.w, 0.1;
KIL	light.w;

MUL	diffuse.rgb, diffuse, light;
SUB	light.rgb, $fogColor, diffuse;
MAD	diffuse.rgb, fragment.color.secondary.x, light, diffuse;
MUL	diffuse.a, diffuse.w, fragment.color.primary.w;
MUL	result.color.a, diffuse.a, 3;
MOV	result.color.rgb, diffuse;

# stuff/grass_alphatestcg: noise and alpha-tested grass, lit by the ambient cube plus
# the sun term in the primary colour, tinted by the secondary colour, fogged by
# texcoord[2].x (from stuff/grass's vertex program).
TEMP	noise, diffuse, ambient, coord, fogged;
MUL	coord.xy, fragment.texcoord[0], 3;
TEX	noise, coord, $noiseMap, 2D;
SUB	coord.x, fragment.color.primary.w, noise.w;
KIL	coord.x;
TEX	diffuse, fragment.texcoord[0], $diffuseMap, 2D;
SUB	coord.x, diffuse.w, 0.4;
KIL	coord.x;
TEX	ambient, fragment.texcoord[1], $ambientCubeMap, CUBE;
MAD	ambient.rgb, ambient, $ambientBrightness, fragment.color.primary;
MUL	diffuse.rgb, diffuse, fragment.color.secondary;
MUL	diffuse.rgb, diffuse, ambient;
SUB	fogged.rgb, $fogColor, diffuse;
MAD	result.color.rgb, fragment.texcoord[2].x, fogged, diffuse;
MUL	result.color.a, diffuse.a, 1.15;

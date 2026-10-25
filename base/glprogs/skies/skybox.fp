# skies/skybox: the mask (top) and the offset map (side), blended by the vertex
# colour's red, tinted by the cloud colour.
TEMP	top, side, coord, blend;
TEX	top, fragment.texcoord[0], $mask, 2D;
ADD	coord.xy, fragment.texcoord[0], { 0.75, 1.0, 0, 0 };
TEX	side, coord, $map, 2D;
LRP	blend, fragment.color.primary.x, side, top;
MUL	result.color, blend, $skies_cloudColor;

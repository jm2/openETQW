# postprocess/glowstrogg: glow, with _currentRender offset by the $mask noise
# (scale $parameters.y / 16 of the screen; y at 0.4).
PARAM	luma = { 0.299, 0.587, 0.114, 0 };
TEMP	noise, a, coord, clean, blur, f, g;
TEX	noise, fragment.texcoord[2], $mask, 2D;
MUL	a.xy, $currentRenderTexelSize, $parameters.y;
MUL	a.xy, a, 0.0625;
MAD	coord.x, a.x, noise.x, fragment.texcoord[0].x;
MAD	coord.x, -a.y, noise.y, coord.x;
MUL	a.z, a.y, noise.x;
MAD	a.z, a.x, noise.y, a.z;
MAD	coord.y, a.z, 0.4, fragment.texcoord[0].y;
TEX	clean, coord, $currentRender, RECT;
TEX	blur, fragment.texcoord[1], $map, RECT;
MUL	f, clean, $postGlareParameters.x;
MAD	f, blur, $postGlareParameters.y, f;
DP3	g, f, luma;
LRP	f, $postSaturationContrast.x, f, g;
SUB	f, f, 0.5;
MAD	f, f, $postSaturationContrast.y, 0.5;
MUL	result.color, f, $postTint;

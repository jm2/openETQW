# postprocess/glow: clean * glare.x + blur * glare.y, then the atmosphere's
# saturation (lerp from grey), contrast (about 0.5) and tint.
PARAM	luma = { 0.299, 0.587, 0.114, 0 };
TEMP	clean, blur, f, g;
TEX	clean, fragment.texcoord[0], $currentRender, RECT;
TEX	blur, fragment.texcoord[1], $map, RECT;
MUL	f, clean, $postGlareParameters.x;
MAD	f, blur, $postGlareParameters.y, f;
DP3	g, f, luma;
LRP	f, $postSaturationContrast.x, f, g;
SUB	f, f, 0.5;
MAD	f, f, $postSaturationContrast.y, 0.5;
MUL	result.color, f, $postTint;

# postprocess/downsample_glare: the downsample, rgb scaled by
# lerp( 1, smoothstep( glare.z, 1, luminance ), glare.w ) * alpha.
PARAM	third = { 0.33, 0.33, 0.33, 0 };
TEMP	c, t, s;
TEX	c, fragment.texcoord[0], $currentRender, RECT;
TEX	t, fragment.texcoord[1], $currentRender, RECT;
ADD	c, c, t;
TEX	t, fragment.texcoord[2], $currentRender, RECT;
ADD	c, c, t;
TEX	t, fragment.texcoord[3], $currentRender, RECT;
ADD	c, c, t;
MUL	c, c, 0.25;
DP3	s.x, c, third;
SUB	s.x, s.x, $postGlareParameters.z;
SUB	s.y, 1, $postGlareParameters.z;
RCP	s.y, s.y;
MUL_SAT	s.x, s.x, s.y;
MAD	s.y, s.x, -2, 3;
MUL	s.x, s.x, s.x;
MUL	s.x, s.x, s.y;
SUB	s.x, s.x, 1;
MAD	s.x, s.x, $postGlareParameters.w, 1;
MUL	s.x, s.x, c.w;
MUL	result.color.xyz, c, s.x;
MOV	result.color.w, c.w;

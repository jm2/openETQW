# postprocess/downsample: the mean of the four _currentRender taps.
TEMP	c, t;
TEX	c, fragment.texcoord[0], $currentRender, RECT;
TEX	t, fragment.texcoord[1], $currentRender, RECT;
ADD	c, c, t;
TEX	t, fragment.texcoord[2], $currentRender, RECT;
ADD	c, c, t;
TEX	t, fragment.texcoord[3], $currentRender, RECT;
ADD	c, c, t;
MUL	result.color, c, 0.25;

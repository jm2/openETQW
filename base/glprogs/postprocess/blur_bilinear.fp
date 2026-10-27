# postprocess/blur_bilinear: weights 0.4, 0.6, 0.8, 1, 1, 0.8, 0.6, 0.4, normalised by 5.6.
TEMP	r, t;
TEX	t, fragment.texcoord[0], $map, RECT;
MUL	r, t, 0.0714285714;
TEX	t, fragment.texcoord[1], $map, RECT;
MAD	r, t, 0.1071428571, r;
TEX	t, fragment.texcoord[2], $map, RECT;
MAD	r, t, 0.1428571429, r;
TEX	t, fragment.texcoord[3], $map, RECT;
MAD	r, t, 0.1785714286, r;
TEX	t, fragment.texcoord[4], $map, RECT;
MAD	r, t, 0.1785714286, r;
TEX	t, fragment.texcoord[5], $map, RECT;
MAD	r, t, 0.1428571429, r;
TEX	t, fragment.texcoord[6], $map, RECT;
MAD	r, t, 0.1071428571, r;
TEX	t, fragment.texcoord[7], $map, RECT;
MAD	result.color, t, 0.0714285714, r;

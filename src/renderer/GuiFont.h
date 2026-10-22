// SPDX-License-Identifier: GPL-3.0-or-later
// The GUI's fonts (docs/gui-text.md, #181): the game's .font declarations and
// the faces they name, read from the paks and rasterised with stb_truetype
// into atlases at the screen's pixel size. Client only.

#ifndef __GUI_FONT_H__
#define __GUI_FONT_H__

#include "GuiTextLayout.h"

class idImage;

struct sdGuiGlyphImage {
	idImage*		image;		// NULL for a glyph with no pixels (a space)
	float			s0, t0, s1, t1;
	float			left, top;	// from the pen position and baseline, screen pixels
	float			width, height;	// screen pixels
};

class sdGuiFontManager {
public:
	// the handle sdDeviceContext::FindFont returns; -1 for no name
	int						FindFont( const char* name );

	// the screen's pixels per virtual unit (screen width / 640, height / 480)
	static void				ScreenScale( float& xScale, float& yScale );

	// lays out text in font at pointSize (the em height in virtual units)
	void					Layout( int font, int pointSize, const wchar_t* text, const sdBounds2D& rect,
								unsigned int flags, unsigned int color, sdGuiTextLayout& out );

	// the atlas cell for a laid-out glyph; rasterises it the first time. Call
	// UploadChanged before drawing with the returned images.
	bool					Glyph( int font, int pointSize, int codepoint, sdGuiGlyphImage& out );
	void					UploadChanged();

	void					Shutdown();
};

extern sdGuiFontManager guiFonts;

#endif /* !__GUI_FONT_H__ */

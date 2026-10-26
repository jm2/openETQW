// SPDX-License-Identifier: GPL-3.0-or-later
// GUI text layout (docs/gui-text.md, #181): one routine for drawing and for
// sdDeviceContext::GetTextDimensions, so what is measured is what is drawn.
// It works in screen pixels against a face's metrics and returns glyph
// positions in the GUI's 640x480 virtual units.

#ifndef __GUI_TEXT_LAYOUT_H__
#define __GUI_TEXT_LAYOUT_H__

// A face at one pixel size, in screen pixels. GuiFont.cpp implements it with
// stb_truetype; the unit test with fixed numbers.
class sdGuiTextMetrics {
public:
	virtual					~sdGuiTextMetrics() {}
	virtual float			Advance( int codepoint ) const = 0;
	virtual float			Kerning( int left, int right ) const = 0;
	virtual float			Ascent() const = 0;		// above the baseline, positive
	virtual float			LineHeight() const = 0;	// baseline to baseline
	virtual float			CapHeight() const = 0;	// top of the capitals above the baseline
	virtual float			Descent() const = 0;	// below the baseline, positive
};

struct sdGuiTextGlyph {
	int						codepoint;
	float					x;			// pen position, virtual units
	float					baseline;	// virtual units
	unsigned int			color;		// packed as sdGuiModel packs colours (bytes r, g, b, a)
};

struct sdGuiTextLayout {
	idList< sdGuiTextGlyph >	glyphs;
	float					width;		// widest line, virtual units
	float					height;		// all lines, virtual units
	float					descent;	// the face's, below the baseline, virtual units
	float					accent;		// the face's ascent above its capitals, virtual units
	float					scale;		// screen pixels per virtual unit, horizontal
	int						numLines;
	idList< int >			advances;	// per source character, 1/64 screen pixel (escapes 0)
	idList< int >			lineBreaks;	// first index of each later line, or the '\n' ending one

	sdGuiTextLayout() : width( 0.0f ), height( 0.0f ), descent( 0.0f ), accent( 0.0f ), scale( 1.0f ), numLines( 0 ) {}
};

// xScale and yScale: screen pixels per virtual unit (screen width / 640, height / 480).
void GuiText_Layout( const sdGuiTextMetrics& metrics, const wchar_t* text, const sdBounds2D& rect,
	unsigned int flags, unsigned int color, float xScale, float yScale, sdGuiTextLayout& out );

#endif /* !__GUI_TEXT_LAYOUT_H__ */

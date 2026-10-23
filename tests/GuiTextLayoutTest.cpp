// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: GuiTextLayoutTest.cpp
GUI text layout (src/renderer/GuiTextLayout.h, docs/gui-text.md, #181) on a
stand-in face with fixed metrics: alignment, colour escapes, line ends,
DTF_SINGLELINE, word wrap (at a space and inside a word), DTF_TRUNCATE,
kerning, the screen scale, pixel snapping, and the advances and line breaks
the game's sdTextDimensionHelper reads.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "DeviceContext.h"
#include "GuiTextLayout.h"

#include <cmath>
#include <cstdio>

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

static bool Near( float a, float b ) {
	return std::fabs( a - b ) < 1e-3f;
}

// every character 10 pixels wide, 'W' 20, a space 5; "AV" kerns by -2;
// ascent 8, 12 pixels from baseline to baseline
class sdFixedMetrics : public sdGuiTextMetrics {
public:
	virtual float Advance( int c ) const { return c == L'W' ? 20.0f : c == L' ' ? 5.0f : 10.0f; }
	virtual float Kerning( int a, int b ) const { return a == L'A' && b == L'V' ? -2.0f : 0.0f; }
	virtual float Ascent() const { return 8.0f; }
	virtual float LineHeight() const { return 12.0f; }
};

static const unsigned int WHITE = 0xFFFFFFFF;

static void Lay( const wchar_t* text, float w, float h, unsigned int flags, sdGuiTextLayout& out,
	float xScale = 1.0f, float yScale = 1.0f, float x = 0.0f, float y = 0.0f ) {
	static sdFixedMetrics metrics;
	GuiText_Layout( metrics, text, sdBounds2D( x, y, w, h ), flags, WHITE, xScale, yScale, out );
}

int main() {
	sdGuiTextLayout l;

	// left and top: pens at 0 and 10, the baseline at the ascent
	Lay( L"AB", 100, 20, 0, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].x, 0 ) && Near( l.glyphs[ 1 ].x, 10 ), "left: pens at 0 and 10" );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].baseline, 8 ), "top: baseline at the ascent" );
	Expect( Near( l.width, 20 ) && Near( l.height, 12 ) && l.numLines == 1, "AB is 20 x 12, one line" );
	Expect( l.advances.Num() == 2 && l.advances[ 0 ] == 640 && l.advances[ 1 ] == 640, "advances in 1/64 pixel" );
	Expect( l.lineBreaks.Num() == 0, "one line has no line breaks" );

	Lay( L"AB", 100, 20, DTF_CENTER, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].x, 40 ), "DTF_CENTER: (100 - 20) / 2" );
	Lay( L"AB", 100, 20, DTF_RIGHT, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].x, 80 ), "DTF_RIGHT: 100 - 20" );
	Lay( L"AB", 100, 20, DTF_VCENTER, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].baseline, 12 ), "DTF_VCENTER: (20 - 12) / 2 + 8" );
	Lay( L"AB", 100, 20, DTF_BOTTOM, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].baseline, 16 ), "DTF_BOTTOM: 20 - 12 + 8" );

	// colour escapes: ^1 is red, ^0 the draw colour; neither is drawn or advances
	Lay( L"^1A^0B", 100, 20, 0, l );
	Expect( l.glyphs.Num() == 2, "escapes are not drawn" );
	if ( l.glyphs.Num() == 2 ) {
		const byte* a = reinterpret_cast< const byte* >( &l.glyphs[ 0 ].color );
		Expect( a[ 0 ] == 255 && a[ 1 ] == 0 && a[ 2 ] == 0 && a[ 3 ] == 255, "^1 colours A red, alpha kept" );
		Expect( l.glyphs[ 1 ].color == WHITE, "^0 restores the draw colour" );
		Expect( Near( l.glyphs[ 1 ].x, 10 ), "escapes take no space" );
	}
	Expect( l.advances.Num() == 6 && l.advances[ 0 ] == 0 && l.advances[ 1 ] == 0 && l.advances[ 2 ] == 640 &&
		l.advances[ 3 ] == 0 && l.advances[ 4 ] == 0 && l.advances[ 5 ] == 640, "an escape's two characters advance 0" );
	Lay( L"^1A", 100, 20, DTF_NOCOLORS, l );
	Expect( l.glyphs.Num() == 1 && l.glyphs[ 0 ].color == WHITE, "DTF_NOCOLORS: no colour, escape still hidden" );
	Lay( L"^1A", 100, 20, DTF_INCLUDECOLORESCAPES, l );
	Expect( l.glyphs.Num() == 3 && l.glyphs[ 0 ].codepoint == L'^', "DTF_INCLUDECOLORESCAPES draws the escape" );

	// line ends
	Lay( L"A\nB", 100, 40, 0, l );
	Expect( l.numLines == 2 && Near( l.height, 24 ), "A\\nB is two lines" );
	Expect( l.lineBreaks.Num() == 1 && l.lineBreaks[ 0 ] == 1, "the break is the index of the \\n" );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 1 ].x, 0 ) && Near( l.glyphs[ 1 ].baseline, 20 ), "B starts the second line" );
	Lay( L"A\nB", 100, 40, DTF_SINGLELINE, l );
	Expect( l.numLines == 1 && l.glyphs.Num() == 2 && Near( l.glyphs[ 1 ].x, 10 ), "DTF_SINGLELINE ignores \\n" );

	// word wrap at the last space that fits: "AA AA" is 45, the next word does not fit in 50
	Lay( L"AA AA AA", 50, 40, DTF_WORDWRAP, l );
	Expect( l.numLines == 2 && l.lineBreaks.Num() == 1 && l.lineBreaks[ 0 ] == 6, "wraps after the second space" );
	Expect( Near( l.width, 45 ), "the space at the break is not counted" );
	Expect( l.glyphs.Num() == 6 && Near( l.glyphs[ 4 ].x, 0 ) && Near( l.glyphs[ 4 ].baseline, 20 ), "the third word starts line 2" );
	// one word wider than the rectangle breaks inside it
	Lay( L"AAAAAAAA", 35, 40, DTF_WORDWRAP, l );
	Expect( l.numLines == 3 && l.lineBreaks.Num() == 2 && l.lineBreaks[ 0 ] == 3 && l.lineBreaks[ 1 ] == 6, "a long word breaks every 3" );
	Lay( L"AA AA AA", 50, 40, 0, l );
	Expect( l.numLines == 1, "no wrap without DTF_WORDWRAP" );

	// DTF_TRUNCATE: as many characters as fit with "..."
	Lay( L"AAAAAAAAAA", 50, 20, DTF_TRUNCATE, l );
	Expect( l.glyphs.Num() == 5 && l.glyphs[ 2 ].codepoint == L'.' && l.glyphs[ 4 ].codepoint == L'.', "AA..." );
	Expect( Near( l.width, 50 ), "the truncated line fits" );
	// a truncated line before another leaves the next line's characters alone
	Lay( L"AAAAAAAAAA\nAB", 50, 40, DTF_TRUNCATE, l );
	Expect( l.glyphs.Num() == 7 && l.glyphs[ 4 ].codepoint == L'.' && l.glyphs[ 5 ].codepoint == L'A' &&
		l.glyphs[ 6 ].codepoint == L'B', "AA... then AB" );
	Expect( l.glyphs.Num() == 7 && Near( l.glyphs[ 5 ].x, 0 ) && Near( l.glyphs[ 5 ].baseline, 20 ) &&
		Near( l.glyphs[ 6 ].x, 10 ), "the second line starts at the left on line 2" );
	Lay( L"AAA", 50, 20, DTF_TRUNCATE, l );
	Expect( l.glyphs.Num() == 3 && l.glyphs[ 2 ].codepoint == L'A', "a line that fits is not truncated" );

	// kerning
	Lay( L"AV", 100, 20, 0, l );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 1 ].x, 8 ) && Near( l.width, 18 ), "AV kerns by -2" );

	// the screen scale: pixels per virtual unit, positions back in virtual units
	Lay( L"AB", 100, 20, 0, l, 2.0f, 2.0f, 10.0f, 10.0f );
	Expect( Near( l.scale, 2 ), "scale is the horizontal pixels per virtual unit" );
	Expect( l.glyphs.Num() == 2 && Near( l.glyphs[ 0 ].x, 10 ) && Near( l.glyphs[ 1 ].x, 15 ), "pens at 20 and 30 pixels" );
	Expect( Near( l.width, 10 ) && Near( l.height, 6 ), "20 x 12 pixels is 10 x 6 virtual" );
	Expect( l.advances.Num() == 2 && l.advances[ 0 ] == 640, "advances stay in screen pixels" );

	// pixel snapping: x = 1 virtual at 1.5 pixels per unit is 1.5 pixels, snapped to 2
	Lay( L"A", 100, 20, 0, l, 1.5f, 1.0f, 1.0f, 0.0f );
	Expect( l.glyphs.Num() == 1 && Near( l.glyphs[ 0 ].x, 2.0f / 1.5f ), "snapped to a whole pixel" );
	Lay( L"A", 100, 20, DTF_NO_SNAP_TO_PIXELS, l, 1.5f, 1.0f, 1.0f, 0.0f );
	Expect( l.glyphs.Num() == 1 && Near( l.glyphs[ 0 ].x, 1.0f ), "DTF_NO_SNAP_TO_PIXELS keeps 1.5 pixels" );

	// nothing to draw
	Lay( L"", 100, 20, 0, l );
	Expect( l.glyphs.Num() == 0 && l.numLines == 1 && Near( l.height, 12 ), "empty text is one empty line" );

	if ( failures == 0 ) {
		std::printf( "gui-text-layout: all checks passed\n" );
	}
	return failures == 0 ? 0 : 1;
}

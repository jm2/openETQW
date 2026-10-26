// SPDX-License-Identifier: GPL-3.0-or-later
// GUI text layout (docs/gui-text.md, #181).

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "DeviceContext.h"
#include "GuiTextLayout.h"

namespace {

// one visible character, or a line end, after escapes are read
struct textItem_t {
	int				source;		// index into the text; -1 for the truncation dots
	int				codepoint;
	unsigned int	color;
	bool			lineEnd;
};

struct textLine_t {
	int		first;		// items [first, end)
	int		end;
	float	width;		// screen pixels
};

unsigned int PackColor( const idVec4& color, unsigned int alphaFrom ) {
	byte packed[ 4 ];
	packed[ 0 ] = static_cast< byte >( idMath::ClampInt( 0, 255, idMath::Ftoi( color.x * 255.0f ) ) );
	packed[ 1 ] = static_cast< byte >( idMath::ClampInt( 0, 255, idMath::Ftoi( color.y * 255.0f ) ) );
	packed[ 2 ] = static_cast< byte >( idMath::ClampInt( 0, 255, idMath::Ftoi( color.z * 255.0f ) ) );
	packed[ 3 ] = reinterpret_cast< const byte* >( &alphaFrom )[ 3 ];
	unsigned int result;
	memcpy( &result, packed, sizeof( result ) );
	return result;
}

float ItemsWidth( const sdGuiTextMetrics& metrics, const idList< textItem_t >& items, int first, int end ) {
	float width = 0.0f;
	for ( int i = first; i < end; i++ ) {
		width += metrics.Advance( items[ i ].codepoint );
		if ( i + 1 < end ) {
			width += metrics.Kerning( items[ i ].codepoint, items[ i + 1 ].codepoint );
		}
	}
	return width;
}

}

void GuiText_Layout( const sdGuiTextMetrics& metrics, const wchar_t* text, const sdBounds2D& rect,
	unsigned int flags, unsigned int color, float xScale, float yScale, sdGuiTextLayout& out ) {
	out.glyphs.SetNum( 0, false );
	out.advances.SetNum( 0, false );
	out.lineBreaks.SetNum( 0, false );
	out.width = 0.0f;
	out.height = 0.0f;
	out.numLines = 0;
	xScale = Max( xScale, 0.001f );
	yScale = Max( yScale, 0.001f );
	out.descent = metrics.Descent() / yScale;
	out.accent = Max( 0.0f, metrics.Ascent() - metrics.CapHeight() ) / yScale;
	out.scale = xScale;
	if ( text == NULL ) {
		return;
	}
	const int length = static_cast< int >( wcslen( text ) );
	out.advances.SetNum( length, false );
	for ( int i = 0; i < length; i++ ) {
		out.advances[ i ] = 0;
	}

	// escapes, line ends and the advance of each source character
	const bool singleLine = ( flags & DTF_SINGLELINE ) != 0;
	idList< textItem_t > items;
	items.SetGranularity( 64 );
	unsigned int current = color;
	for ( int i = 0; i < length; i++ ) {
		const wchar_t c = text[ i ];
		if ( ( flags & DTF_INCLUDECOLORESCAPES ) == 0 && idWStr::IsColor( text + i ) ) {
			if ( ( flags & DTF_NOCOLORS ) == 0 ) {
				current = text[ i + 1 ] == WC_COLOR_DEFAULT ? color :
					PackColor( idWStr::ColorForChar( text[ i + 1 ] ), color );
			}
			i++;
			continue;
		}
		if ( c == L'\r' ) {
			continue;
		}
		textItem_t item;
		item.source = i;
		item.codepoint = c;
		item.color = current;
		item.lineEnd = c == L'\n';
		if ( item.lineEnd && singleLine ) {
			continue;
		}
		if ( !item.lineEnd ) {
			out.advances[ i ] = idMath::Ftoi( metrics.Advance( c ) * 64.0f + 0.5f );
		}
		items.Append( item );
	}

	// lines: at line ends, and with DTF_WORDWRAP at the last space that fits
	const float maxWidth = rect.GetWidth() * xScale;
	const bool wrap = ( flags & DTF_WORDWRAP ) != 0 && maxWidth > 0.0f;
	idList< textLine_t > lines;
	textLine_t line = { 0, 0, 0.0f };
	int lastSpace = -1;		// the last space item on this line
	float running = 0.0f;	// width of items [line.first, i)
	for ( int i = 0; i < items.Num(); i++ ) {
		if ( items[ i ].lineEnd ) {
			line.end = i;
			line.width = running;
			lines.Append( line );
			out.lineBreaks.Append( items[ i ].source );
			line.first = i + 1;
			lastSpace = -1;
			running = 0.0f;
			continue;
		}
		const int c = items[ i ].codepoint;
		float next = running + metrics.Advance( c );
		if ( i > line.first ) {
			next += metrics.Kerning( items[ i - 1 ].codepoint, c );
		}
		if ( wrap && c != L' ' && i > line.first && next > maxWidth ) {
			// break after the last space, or before this character when one word is too wide
			const int first = lastSpace >= line.first ? lastSpace + 1 : i;
			line.end = lastSpace >= line.first ? lastSpace : i;
			line.width = ItemsWidth( metrics, items, line.first, line.end );
			lines.Append( line );
			out.lineBreaks.Append( items[ first ].source );
			line.first = first;
			lastSpace = -1;
			next = ItemsWidth( metrics, items, first, i + 1 );
		}
		if ( c == L' ' ) {
			lastSpace = i;
		}
		running = next;
	}
	line.end = items.Num();
	line.width = running;
	lines.Append( line );

	// DTF_TRUNCATE: a line too wide for the rectangle ends in "..."
	if ( ( flags & DTF_TRUNCATE ) != 0 && maxWidth > 0.0f ) {
		const float dotsWidth = metrics.Advance( L'.' ) * 3.0f + metrics.Kerning( L'.', L'.' ) * 2.0f;
		for ( int l = 0; l < lines.Num(); l++ ) {
			textLine_t& tl = lines[ l ];
			if ( tl.width <= maxWidth ) {
				continue;
			}
			int end = tl.end;
			while ( end > tl.first && ItemsWidth( metrics, items, tl.first, end ) + dotsWidth > maxWidth ) {
				end--;
			}
			const unsigned int dotColor = end > tl.first ? items[ end - 1 ].color : color;
			// the cut characters go, and three dots take their place
			const int removed = tl.end - end;
			for ( int r = 0; r < removed; r++ ) {
				items.RemoveIndex( end );
			}
			for ( int d = 0; d < 3; d++ ) {
				textItem_t dot;
				dot.source = -1;
				dot.codepoint = L'.';
				dot.color = dotColor;
				dot.lineEnd = false;
				items.Insert( dot, end + d );
			}
			const int added = 3 - removed;
			tl.end = end + 3;
			for ( int later = l + 1; later < lines.Num(); later++ ) {
				lines[ later ].first += added;
				lines[ later ].end += added;
			}
			tl.width = ItemsWidth( metrics, items, tl.first, tl.end );
		}
	}

	const float lineHeight = metrics.LineHeight();
	float widest = 0.0f;
	for ( int l = 0; l < lines.Num(); l++ ) {
		widest = Max( widest, lines[ l ].width );
	}
	const float totalHeight = lineHeight * lines.Num();
	out.numLines = lines.Num();
	out.width = widest / xScale;
	out.height = totalHeight / yScale;

	// placement, in screen pixels from the screen's origin, then virtual units
	const bool snap = ( flags & DTF_NO_SNAP_TO_PIXELS ) == 0;
	float top = rect.GetMins().y * yScale;
	if ( ( flags & DTF_VCENTER ) && lines.Num() == 1 ) {
		// retail 1.5 centres a single line's ink, from the top of the capitals to
		// the descenders, not its line box (#193: the main menu's banner)
		const float ink = metrics.CapHeight() + metrics.Descent();
		top += ( rect.GetHeight() * yScale - ink ) * 0.5f + metrics.CapHeight() - metrics.Ascent();
	} else if ( flags & DTF_VCENTER ) {
		top += ( rect.GetHeight() * yScale - totalHeight ) * 0.5f;
	} else if ( flags & DTF_BOTTOM ) {
		top += rect.GetHeight() * yScale - totalHeight;
	}
	for ( int l = 0; l < lines.Num(); l++ ) {
		const textLine_t& tl = lines[ l ];
		float x = rect.GetMins().x * xScale;
		if ( flags & DTF_CENTER ) {
			x += ( rect.GetWidth() * xScale - tl.width ) * 0.5f;
		} else if ( flags & DTF_RIGHT ) {
			x += rect.GetWidth() * xScale - tl.width;
		}
		float baseline = top + lineHeight * l + metrics.Ascent();
		if ( snap ) {
			x = idMath::Floor( x + 0.5f );
			baseline = idMath::Floor( baseline + 0.5f );
		}
		for ( int i = tl.first; i < tl.end; i++ ) {
			const int c = items[ i ].codepoint;
			if ( c != L' ' ) {
				sdGuiTextGlyph glyph;
				glyph.codepoint = c;
				glyph.x = ( snap ? idMath::Floor( x + 0.5f ) : x ) / xScale;
				glyph.baseline = baseline / yScale;
				glyph.color = items[ i ].color;
				out.glyphs.Append( glyph );
			}
			x += metrics.Advance( c );
			if ( i + 1 < tl.end ) {
				x += metrics.Kerning( c, items[ i + 1 ].codepoint );
			}
		}
	}
}

// SPDX-License-Identifier: GPL-3.0-or-later
// The GUI's fonts (docs/gui-text.md, #181).

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "GuiFont.h"
#include "Image.h"

// stb_truetype (public domain), the copy Dear ImGui carries; static, as
// imgui_draw.cpp compiles its own
#define STBTT_STATIC
#define STB_TRUETYPE_IMPLEMENTATION
#include "../imgui/imstb_truetype.h"

sdGuiFontManager guiFonts;

namespace {

const char* const FALLBACK_FACE = "localization/fonts/arialuni.ttf";
const int ATLAS_WIDTH = 1024;
const int ATLAS_MAX_HEIGHT = 4096;
const int GLYPH_PADDING = 1;

struct guiFace_t {
	idStr			path;
	byte*			data;
	stbtt_fontinfo	info;
	bool			valid;
};

struct guiFontName_t {
	idStr			name;
	bool			resolved;
	int				face;		// index into faces, or -1
};

struct guiGlyph_t {
	int				codepoint;
	int				face;		// -1: no face has it
	int				glyphIndex;
	float			advance;
	bool			rasterised;
	bool			empty;
	int				x, y, w, h;	// atlas cell
	int				left, top;	// bitmap offset from the pen and baseline
};

struct guiFontSize_t;

class sdGuiFaceMetrics : public sdGuiTextMetrics {
public:
	guiFontSize_t*	size;
	virtual float	Advance( int codepoint ) const;
	virtual float	Kerning( int left, int right ) const;
	virtual float	Ascent() const;
	virtual float	LineHeight() const;
	virtual float	CapHeight() const;
	virtual float	Descent() const;
};

struct guiFontSize_t {
	int				font;
	int				pixelEm;
	float			ascent;
	float			lineHeight;
	float			capHeight;
	float			descent;
	idList< guiGlyph_t >	glyphs;
	idHashIndex		glyphHash;
	// the atlas: one byte of coverage per pixel, packed in shelves
	idList< byte >	pixels;
	int				atlasHeight;
	int				shelfX, shelfY, shelfHeight;
	bool			full;
	bool			changed;
	idStr			imageName;
	idImage*		image;
	sdGuiFaceMetrics	metrics;
};

idList< guiFace_t* >		faces;
idList< guiFontName_t >		fontNames;
idList< guiFontSize_t* >	sizes;
int							fallbackFace = -2;	// -2: not looked for yet
int							sizesScreenHeight = 0;

int LoadFace( const char* path ) {
	for ( int i = 0; i < faces.Num(); i++ ) {
		if ( !faces[ i ]->path.Icmp( path ) ) {
			return faces[ i ]->valid ? i : -1;
		}
	}
	guiFace_t* face = new guiFace_t;
	face->path = path;
	face->data = NULL;
	face->valid = false;
	faces.Append( face );
	if ( fileSystem == NULL ) {
		return -1;
	}
	void* buffer = NULL;
	const int length = fileSystem->ReadFile( path, &buffer );
	if ( length <= 0 || buffer == NULL ) {
		common->Warning( "GUI font face '%s' not found", path );
		return -1;
	}
	face->data = static_cast< byte* >( Mem_Alloc( length ) );
	memcpy( face->data, buffer, length );
	fileSystem->FreeFile( buffer );
	const int offset = stbtt_GetFontOffsetForIndex( face->data, 0 );
	if ( offset < 0 || !stbtt_InitFont( &face->info, face->data, offset ) ) {
		common->Warning( "GUI font face '%s' cannot be read", path );
		return -1;
	}
	face->valid = true;
	return faces.Num() - 1;
}

int FallbackFace() {
	if ( fallbackFace == -2 ) {
		fallbackFace = LoadFace( FALLBACK_FACE );
	}
	return fallbackFace;
}

// a .font declaration: file "<path>" and faceIndex <n>
bool ParseFontDecl( const char* text, idStr& file, int& faceIndex ) {
	file.Clear();
	faceIndex = 0;
	idStr source( text );
	idStr word;
	int i = 0;
	const int length = source.Length();
	idStrList tokens;
	while ( i < length ) {
		while ( i < length && ( source[ i ] == ' ' || source[ i ] == '\t' || source[ i ] == '\r' || source[ i ] == '\n' ) ) {
			i++;
		}
		if ( i >= length ) {
			break;
		}
		word.Clear();
		if ( source[ i ] == '"' ) {
			i++;
			while ( i < length && source[ i ] != '"' ) {
				word.Append( source[ i++ ] );
			}
			i++;
		} else {
			while ( i < length && source[ i ] != ' ' && source[ i ] != '\t' && source[ i ] != '\r' && source[ i ] != '\n' ) {
				word.Append( source[ i++ ] );
			}
		}
		tokens.Append( word );
	}
	for ( int t = 0; t + 1 < tokens.Num(); t += 2 ) {
		if ( !tokens[ t ].Icmp( "file" ) ) {
			file = tokens[ t + 1 ];
		} else if ( !tokens[ t ].Icmp( "faceIndex" ) ) {
			faceIndex = atoi( tokens[ t + 1 ].c_str() );
		}
	}
	return !file.IsEmpty();
}

// the face for a font name: localization/<sys_lang>/fonts/<name>.font, then the English one
int ResolveFont( const idStr& requested ) {
	idStr name = requested;
	name.BackSlashesToSlashes();
	name.StripFileExtension();
	if ( !name.Icmpn( "fonts/", 6 ) ) {
		name = name.Right( name.Length() - 6 );
	}
	const char* language = cvarSystem != NULL ? cvarSystem->GetCVarString( "sys_lang" ) : "english";
	const char* languages[ 2 ] = { language, "english" };
	for ( int l = 0; l < 2 && fileSystem != NULL; l++ ) {
		if ( l == 1 && !idStr::Icmp( language, "english" ) ) {
			break;
		}
		void* buffer = NULL;
		const int length = fileSystem->ReadFile( va( "localization/%s/fonts/%s.font", languages[ l ], name.c_str() ), &buffer );
		if ( length <= 0 || buffer == NULL ) {
			continue;
		}
		idStr text;
		text.Append( static_cast< const char* >( buffer ), length );
		fileSystem->FreeFile( buffer );
		idStr file;
		int faceIndex = 0;
		if ( !ParseFontDecl( text.c_str(), file, faceIndex ) ) {
			common->Warning( "GUI font '%s': its .font names no file", name.c_str() );
			continue;
		}
		if ( faceIndex != 0 ) {
			common->Warning( "GUI font '%s': faceIndex %d is not supported; using face 0", name.c_str(), faceIndex );
		}
		const int face = LoadFace( file.c_str() );
		if ( face >= 0 ) {
			return face;
		}
	}
	common->Warning( "GUI font '%s' not found; using %s", requested.c_str(), FALLBACK_FACE );
	return FallbackFace();
}

int FontFace( int font ) {
	if ( font < 0 || font >= fontNames.Num() ) {
		return FallbackFace();
	}
	guiFontName_t& entry = fontNames[ font ];
	if ( !entry.resolved ) {
		entry.face = ResolveFont( entry.name );
		entry.resolved = true;
	}
	return entry.face;
}

void FreeSizes() {
	for ( int i = 0; i < sizes.Num(); i++ ) {
		if ( sizes[ i ]->image != NULL ) {
			sizes[ i ]->image->Purge();
		}
		delete sizes[ i ];
	}
	sizes.Clear();
}

guiFontSize_t* FindSize( int font, int pixelEm ) {
	int screenHeight = 0;
	if ( renderSystem != NULL ) {
		screenHeight = renderSystem->GetScreenHeight();
	}
	if ( screenHeight != sizesScreenHeight ) {
		// every pixel size follows the screen; the old atlases are not used again
		FreeSizes();
		sizesScreenHeight = screenHeight;
	}
	if ( font < 0 || font >= fontNames.Num() ) {
		font = -1;
	}
	for ( int i = 0; i < sizes.Num(); i++ ) {
		if ( sizes[ i ]->font == font && sizes[ i ]->pixelEm == pixelEm ) {
			return sizes[ i ];
		}
	}
	guiFontSize_t* size = new guiFontSize_t;
	size->font = font;
	size->pixelEm = pixelEm;
	size->ascent = pixelEm * 0.8f;
	size->lineHeight = pixelEm * 1.2f;
	size->capHeight = pixelEm * 0.7f;
	size->descent = pixelEm * 0.2f;
	size->glyphs.SetGranularity( 128 );
	size->atlasHeight = 128;
	size->pixels.SetNum( ATLAS_WIDTH * size->atlasHeight, false );
	memset( size->pixels.Begin(), 0, size->pixels.Num() );
	size->shelfX = size->shelfY = size->shelfHeight = 0;
	size->full = false;
	size->changed = false;
	size->imageName = va( "_guifont_%d_%d", font, pixelEm );
	// the same name after a screen change reuses the image object
	size->image = globalImages != NULL ? globalImages->GetImage( size->imageName.c_str() ) : NULL;
	size->metrics.size = size;
	const int face = FontFace( font );
	if ( face >= 0 ) {
		const stbtt_fontinfo& info = faces[ face ]->info;
		int ascent = 0, descent = 0, lineGap = 0;
		stbtt_GetFontVMetrics( &info, &ascent, &descent, &lineGap );
		const float scale = stbtt_ScaleForMappingEmToPixels( &info, static_cast< float >( pixelEm ) );
		size->ascent = ascent * scale;
		size->lineHeight = ( ascent - descent + lineGap ) * scale;
		size->descent = -descent * scale;
		int x0, y0, x1, y1;
		if ( stbtt_GetCodepointBox( &info, 'H', &x0, &y0, &x1, &y1 ) && y1 > 0 ) {
			size->capHeight = y1 * scale;
		} else {
			size->capHeight = size->ascent;
		}
	}
	sizes.Append( size );
	return size;
}

guiGlyph_t& FindGlyph( guiFontSize_t* size, int codepoint ) {
	for ( int i = size->glyphHash.GetFirst( codepoint ); i != -1; i = size->glyphHash.GetNext( i ) ) {
		if ( size->glyphs[ i ].codepoint == codepoint ) {
			return size->glyphs[ i ];
		}
	}
	guiGlyph_t glyph;
	memset( &glyph, 0, sizeof( glyph ) );
	glyph.codepoint = codepoint;
	glyph.face = -1;
	glyph.advance = size->pixelEm * 0.5f;
	const int primary = FontFace( size->font );
	int candidates[ 2 ] = { primary, FallbackFace() };
	for ( int c = 0; c < 2; c++ ) {
		const int face = candidates[ c ];
		if ( face < 0 ) {
			continue;
		}
		const int index = stbtt_FindGlyphIndex( &faces[ face ]->info, codepoint );
		if ( index != 0 || ( c == 1 && glyph.face < 0 ) ) {
			glyph.face = face;
			glyph.glyphIndex = index;
			if ( index != 0 ) {
				break;
			}
		}
	}
	if ( glyph.face < 0 && primary >= 0 ) {
		glyph.face = primary;	// its missing-glyph box
		glyph.glyphIndex = 0;
	}
	if ( glyph.face >= 0 ) {
		const stbtt_fontinfo& info = faces[ glyph.face ]->info;
		int advance = 0, bearing = 0;
		stbtt_GetGlyphHMetrics( &info, glyph.glyphIndex, &advance, &bearing );
		glyph.advance = advance * stbtt_ScaleForMappingEmToPixels( &info, static_cast< float >( size->pixelEm ) );
	}
	const int index = size->glyphs.Append( glyph );
	size->glyphHash.Add( codepoint, index );
	return size->glyphs[ index ];
}

bool Rasterise( guiFontSize_t* size, guiGlyph_t& glyph ) {
	if ( glyph.rasterised ) {
		return !glyph.empty;
	}
	glyph.rasterised = true;
	glyph.empty = true;
	if ( glyph.face < 0 ) {
		return false;
	}
	const stbtt_fontinfo& info = faces[ glyph.face ]->info;
	const float scale = stbtt_ScaleForMappingEmToPixels( &info, static_cast< float >( size->pixelEm ) );
	int x0 = 0, y0 = 0, x1 = 0, y1 = 0;
	stbtt_GetGlyphBitmapBox( &info, glyph.glyphIndex, scale, scale, &x0, &y0, &x1, &y1 );
	const int w = x1 - x0;
	const int h = y1 - y0;
	if ( w <= 0 || h <= 0 ) {
		return false;
	}
	const int cellW = w + GLYPH_PADDING * 2;
	const int cellH = h + GLYPH_PADDING * 2;
	if ( cellW > ATLAS_WIDTH ) {
		return false;
	}
	if ( size->shelfX + cellW > ATLAS_WIDTH ) {
		size->shelfY += size->shelfHeight;
		size->shelfX = 0;
		size->shelfHeight = 0;
	}
	while ( size->shelfY + cellH > size->atlasHeight ) {
		if ( size->atlasHeight >= ATLAS_MAX_HEIGHT ) {
			if ( !size->full ) {
				common->Warning( "GUI font atlas %s is full; some characters are not drawn", size->imageName.c_str() );
				size->full = true;
			}
			return false;
		}
		const int oldBytes = size->pixels.Num();
		size->atlasHeight *= 2;
		size->pixels.SetNum( ATLAS_WIDTH * size->atlasHeight, true );
		memset( size->pixels.Begin() + oldBytes, 0, size->pixels.Num() - oldBytes );
	}
	glyph.x = size->shelfX + GLYPH_PADDING;
	glyph.y = size->shelfY + GLYPH_PADDING;
	glyph.w = w;
	glyph.h = h;
	glyph.left = x0;
	glyph.top = y0;
	stbtt_MakeGlyphBitmap( &info, size->pixels.Begin() + glyph.y * ATLAS_WIDTH + glyph.x, w, h, ATLAS_WIDTH,
		scale, scale, glyph.glyphIndex );
	size->shelfX += cellW;
	size->shelfHeight = Max( size->shelfHeight, cellH );
	size->changed = true;
	glyph.empty = false;
	return true;
}

int PixelEm( int pointSize ) {
	float xScale, yScale;
	sdGuiFontManager::ScreenScale( xScale, yScale );
	return idMath::ClampInt( 1, 512, idMath::Ftoi( ( pointSize > 0 ? pointSize : 12 ) * yScale + 0.5f ) );
}

float sdGuiFaceMetrics::Advance( int codepoint ) const {
	return FindGlyph( size, codepoint ).advance;
}

float sdGuiFaceMetrics::Kerning( int left, int right ) const {
	// copies: the second lookup may grow the glyph list
	const guiGlyph_t a = FindGlyph( size, left );
	const guiGlyph_t b = FindGlyph( size, right );
	if ( a.face < 0 || a.face != b.face ) {
		return 0.0f;
	}
	const stbtt_fontinfo& info = faces[ a.face ]->info;
	return stbtt_GetGlyphKernAdvance( &info, a.glyphIndex, b.glyphIndex ) *
		stbtt_ScaleForMappingEmToPixels( &info, static_cast< float >( size->pixelEm ) );
}

float sdGuiFaceMetrics::Ascent() const {
	return size->ascent;
}

float sdGuiFaceMetrics::LineHeight() const {
	return size->lineHeight;
}

float sdGuiFaceMetrics::CapHeight() const {
	return size->capHeight;
}

float sdGuiFaceMetrics::Descent() const {
	return size->descent;
}

}

int sdGuiFontManager::FindFont( const char* name ) {
	if ( name == NULL || name[ 0 ] == '\0' ) {
		return -1;
	}
	for ( int i = 0; i < fontNames.Num(); i++ ) {
		if ( !fontNames[ i ].name.Icmp( name ) ) {
			return i;
		}
	}
	guiFontName_t entry;
	entry.name = name;
	entry.resolved = false;
	entry.face = -1;
	return fontNames.Append( entry );
}

void sdGuiFontManager::ScreenScale( float& xScale, float& yScale ) {
	xScale = yScale = 1.0f;
	if ( renderSystem != NULL ) {
		xScale = Max( 1, renderSystem->GetScreenWidth() ) / static_cast< float >( SCREEN_WIDTH );
		yScale = Max( 1, renderSystem->GetScreenHeight() ) / static_cast< float >( SCREEN_HEIGHT );
	}
}

void sdGuiFontManager::Layout( int font, int pointSize, const wchar_t* text, const sdBounds2D& rect,
	unsigned int flags, unsigned int color, sdGuiTextLayout& out ) {
	float xScale, yScale;
	ScreenScale( xScale, yScale );
	guiFontSize_t* size = FindSize( font, PixelEm( pointSize ) );
	GuiText_Layout( size->metrics, text, rect, flags, color, xScale, yScale, out );
}

bool sdGuiFontManager::Glyph( int font, int pointSize, int codepoint, sdGuiGlyphImage& out ) {
	guiFontSize_t* size = FindSize( font, PixelEm( pointSize ) );
	guiGlyph_t& glyph = FindGlyph( size, codepoint );
	if ( !Rasterise( size, glyph ) || globalImages == NULL ) {
		return false;
	}
	if ( size->image == NULL ) {
		size->image = globalImages->AllocImage( size->imageName.c_str() );
		if ( size->image == NULL ) {
			return false;
		}
	}
	out.image = size->image;
	out.left = static_cast< float >( glyph.left );
	out.top = static_cast< float >( glyph.top );
	out.width = static_cast< float >( glyph.w );
	out.height = static_cast< float >( glyph.h );
	out.s0 = glyph.x / static_cast< float >( ATLAS_WIDTH );
	out.s1 = ( glyph.x + glyph.w ) / static_cast< float >( ATLAS_WIDTH );
	out.t0 = static_cast< float >( glyph.y );
	out.t1 = static_cast< float >( glyph.y + glyph.h );
	return true;
}

int sdGuiFontManager::AtlasHeight( const idImage* image ) const {
	for ( int i = 0; i < sizes.Num(); i++ ) {
		if ( sizes[ i ]->image == image ) {
			return sizes[ i ]->atlasHeight;
		}
	}
	return 1;
}

void sdGuiFontManager::UploadChanged() {
	idList< byte > rgba;
	for ( int i = 0; i < sizes.Num(); i++ ) {
		guiFontSize_t* size = sizes[ i ];
		if ( size->image == NULL || ( !size->changed && size->image->IsLoaded() ) ) {
			continue;
		}
		const int pixelCount = ATLAS_WIDTH * size->atlasHeight;
		rgba.SetNum( pixelCount * 4, false );
		for ( int p = 0; p < pixelCount; p++ ) {
			byte* out = &rgba[ p * 4 ];
			out[ 0 ] = out[ 1 ] = out[ 2 ] = 255;
			out[ 3 ] = size->pixels[ p ];
		}
		size->image->GenerateImageEx( rgba.Begin(), ATLAS_WIDTH, size->atlasHeight,
			TF_LINEAR, false, TR_CLAMP, TD_HIGH_QUALITY, GL_RGBA8, 1 );
		size->changed = false;
	}
}

void sdGuiFontManager::Shutdown() {
	FreeSizes();
	for ( int i = 0; i < faces.Num(); i++ ) {
		if ( faces[ i ]->data != NULL ) {
			Mem_Free( faces[ i ]->data );
		}
		delete faces[ i ];
	}
	faces.Clear();
	for ( int i = 0; i < fontNames.Num(); i++ ) {
		fontNames[ i ].resolved = false;
		fontNames[ i ].face = -1;
	}
	fallbackFace = -2;
	sizesScreenHeight = 0;
}

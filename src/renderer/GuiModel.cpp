// Copyright (C) 2007 Id Software, Inc.
//
// ETQW's GUI renderer records front-end commands and consumes them at the
// render-system frame boundary.  The command IDs and state transitions here
// follow the recovered sdGuiModel implementation; this first backend uses the
// compatibility OpenGL path while render programs/images are reconstructed.

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "GuiModel.h"
#include "GuiFont.h"
#include "DeviceContext.h"
#include "Material.h"
#include "Image.h"
#include "tr_render.h"
#include "VulkanBackend.h"
#include "renderbindings.h"
#include "../decllib/declRenderProgram.h"
#include "../decllib/DeclRenderProgram_opengl.h"
#include "../decllib/declRenderBinding.h"
#include "../libs/qglLib/qgl.h"
#include "../sound/SoundEmitter.h"

#if defined( __APPLE__ )
#include <OpenGL/gl.h>
#else
#include <GL/gl.h>
#endif

extern idCVar r_32ByteVtx;
extern glconfig_t glConfig;

namespace {

// The finished frame as _currentRender, and _postProcessBuffer_0/1 at a quarter
// of it (RB_ARB2_SetupPostProcessingFrameBuffer's sizes), for a GUI post-process
// material.  The GUI is submitted after the views, so there is no view def here.
void GuiCopyFrameForPostProcess( int x, int y, int width, int height ) {
	if ( globalImages == NULL || globalImages->currentRenderImage == NULL ) {
		return;
	}
	const int bufferWidth = Max( 1, width >> 2 );
	const int bufferHeight = Max( 1, height >> 2 );
	for ( int index = 0; index < 2; ++index ) {
		idImage* image = globalImages->postProcessBuffer[ index ];
		if ( image != NULL && ( image->uploadWidth != bufferWidth || image->uploadHeight != bufferHeight ) ) {
			image->FromParameters( bufferWidth, bufferHeight, GL_RGBA8, TT_RECT, TF_LINEAR, TR_CLAMP );
		}
	}
	if ( rbinds != NULL ) {
		rbinds->currentRenderTexelSize->Set( static_cast< float >( width ), static_cast< float >( height ),
			1.0f / Max( width, 1 ), 1.0f / Max( height, 1 ) );
	}
	globalImages->currentRenderImage->CopyFramebuffer( x, y, width, height, false );
}

byte ClampCinematicChannel( int value ) {
	return static_cast< byte >( idMath::ClampInt( 0, 255, value ) );
}

idImage* ResolveGuiImage( const idMaterial* material, idSoundEmitter* referenceSound ) {
	if ( referenceSound == NULL ) {
		return material != NULL ? material->GetEditorImage() : NULL;
	}

	// ETQW's RB_Evaluator_UpdateCinematicImageYUV obtains the video frame from
	// the surface's reference sound.  A missing/stopped frame is black with
	// neutral chroma; it is never the diagnostic default image.
	if ( globalImages == NULL || !referenceSound->CurrentlyPlaying() ) {
		return globalImages != NULL ? globalImages->blackImage : NULL;
	}

	const cinData_t frame = referenceSound->ImageForTime( 0 );
	if ( frame.imageWidth <= 0 || frame.imageHeight <= 0 ||
		 frame.imageWidth > 8192 || frame.imageHeight > 8192 ||
		 frame.image[ 0 ] == NULL ) {
		return globalImages->blackImage;
	}

	if ( frame.image[ 1 ] == NULL || frame.image[ 2 ] == NULL ) {
		globalImages->cinematicImage->UploadScratch( frame.image[ 0 ], frame.imageWidth, frame.imageHeight );
		return globalImages->cinematicImage;
	}

	// The compatibility GUI backend is single-texture fixed-function OpenGL.
	// Convert the Theora 4:2:0 planes to the same RGBA result produced by the
	// retail trivialCinematicYUV program, then use the shared cinematic image.
	static idList< byte > rgba;
	const int pixelCount = frame.imageWidth * frame.imageHeight;
	rgba.SetNum( pixelCount * 4, false );
	const int chromaWidth = Max( 1, frame.imageWidth >> 1 );
	for ( int y = 0; y < frame.imageHeight; y++ ) {
		for ( int x = 0; x < frame.imageWidth; x++ ) {
			const int lumaIndex = y * frame.imageWidth + x;
			const int chromaIndex = ( y >> 1 ) * chromaWidth + ( x >> 1 );
			const int c = Max( 0, static_cast< int >( frame.image[ 0 ][ lumaIndex ] ) - 16 );
			const int d = static_cast< int >( frame.image[ 1 ][ chromaIndex ] ) - 128;
			const int e = static_cast< int >( frame.image[ 2 ][ chromaIndex ] ) - 128;
			byte* pixel = &rgba[ lumaIndex * 4 ];
			pixel[ 0 ] = ClampCinematicChannel( ( 298 * c + 409 * e + 128 ) >> 8 );
			pixel[ 1 ] = ClampCinematicChannel( ( 298 * c - 100 * d - 208 * e + 128 ) >> 8 );
			pixel[ 2 ] = ClampCinematicChannel( ( 298 * c + 516 * d + 128 ) >> 8 );
			pixel[ 3 ] = 255;
		}
	}
	globalImages->cinematicImage->UploadScratch( rgba.Begin(), frame.imageWidth, frame.imageHeight );
	return globalImages->cinematicImage;
}

}

sdGuiModel guiModel;

sdGuiModel::sdGuiModel() :
	currentColor( 0xFFFFFFFF ),
	clippingEnabled( true ),
	fullScreen( true ),
	currentFont( -1 ),
	currentFontSize( 0 ),
	writePosition( 0 ) {
	memset( materialParms, 0, sizeof( materialParms ) );
	materialParms[ 0 ] = materialParms[ 1 ] = materialParms[ 2 ] = materialParms[ 3 ] = 1.0f;
	primitives.SetGranularity( 256 );
	texts.SetGranularity( 128 );
	clipRects.SetGranularity( 8 );
}

void sdGuiModel::BeginFrame() {
	primitives.SetNum( 0, false );
	texts.SetNum( 0, false );
	clipRects.SetNum( 0, false );
	writePosition = 0;
	fullScreen = true;
}

void sdGuiModel::BeginRender() {
}

int sdGuiModel::GetWritePos() const {
	return writePosition;
}

void sdGuiModel::BeginEmitToCurrentView( const float*, int, bool ) {
	fullScreen = false;
}

void sdGuiModel::BeginEmitFullScreen() {
	fullScreen = true;
}

void sdGuiModel::End() {
	writePosition += sizeof( int );
}

void sdGuiModel::RenderScene() {
	writePosition += sizeof( int );
}

idImage* ResolveVulkanStageImage( const materialStage_t* stage,
	idImage* overrideImage ) {
	if ( overrideImage != NULL ) {
		return overrideImage;
	}
	idImage* fallback = NULL;
	for ( int i = 0; i < stage->numTextures; ++i ) {
		const stageTexture_t& texture = stage->textures[ i ];
		if ( rbinds != NULL && ( texture.renderBinding == rbinds->diffuseMap ||
			texture.renderBinding == rbinds->map ||
			texture.renderBinding == rbinds->cinematicY ) ) {
			return texture.image;
		}
		if ( fallback == NULL ) {
			fallback = texture.image;
		}
	}
	return fallback;
}

void sdGuiModel::EmitFullScreen( int end ) {
	// The retail command stream uses 'end' as a byte offset into its read
	// buffer.  This reconstruction stores decoded GUI primitives directly, so
	// every queued primitive is already within the active command range.
	(void)end;
	FlushFrame( renderSystem->GetScreenWidth(), renderSystem->GetScreenHeight() );
}

void sdGuiModel::SetColor( unsigned int color ) {
	currentColor = color;
	writePosition += sizeof( int ) * 2;
}

void sdGuiModel::SetRegister( int index, float value ) {
	if ( index >= 0 && index < MAX_ENTITY_SHADER_PARMS ) {
		materialParms[ index ] = value;
	}
	writePosition += sizeof( int ) * 3;
}

void sdGuiModel::SetRegisters( const float* values ) {
	if ( values != NULL ) {
		memcpy( materialParms + 4, values, sizeof( float ) * 8 );
	}
	writePosition += sizeof( int ) + sizeof( float ) * 8;
}

void sdGuiModel::EnableClipping( bool enable ) {
	clippingEnabled = enable;
	writePosition += sizeof( int );
}

void sdGuiModel::PushClipRect( const sdBounds2D& rect ) {
	clipRects.Append( rect );
	writePosition += sizeof( int ) + sizeof( sdBounds2D );
}

void sdGuiModel::PopClipRect() {
	if ( clipRects.Num() > 0 ) {
		clipRects.SetNum( clipRects.Num() - 1, false );
	}
	writePosition += sizeof( int );
}

bool sdGuiModel::ClipToMask( idWinding2D& winding, float x, float y, float w, float h,
	float ms1, float mt1, float ms2, float mt2 ) {
	// the mask coordinates run from (ms1, mt1) at (x, y) to (ms2, mt2) at
	// (x + w, y + h); find where they are 0 and 1
	// an axis whose mask coordinate is constant shows everywhere or nowhere
	if ( ( ms1 == ms2 && ( ms1 < 0.0f || ms1 > 1.0f ) ) || ( mt1 == mt2 && ( mt1 < 0.0f || mt1 > 1.0f ) ) ) {
		return false;
	}
	const float unbounded = 1.0e6f;
	float x0 = -unbounded;
	float x1 = unbounded;
	float y0 = -unbounded;
	float y1 = unbounded;
	if ( ms1 != ms2 ) {
		x0 = x + ( 0.0f - ms1 ) / ( ms2 - ms1 ) * w;
		x1 = x + ( 1.0f - ms1 ) / ( ms2 - ms1 ) * w;
		if ( x0 > x1 ) {
			Swap( x0, x1 );
		}
	}
	if ( mt1 != mt2 ) {
		y0 = y + ( 0.0f - mt1 ) / ( mt2 - mt1 ) * h;
		y1 = y + ( 1.0f - mt1 ) / ( mt2 - mt1 ) * h;
		if ( y0 > y1 ) {
			Swap( y0, y1 );
		}
	}
	if ( ms1 == ms2 && mt1 == mt2 ) {
		return winding.GetNumPoints() >= 3;
	}
	return winding.ClipByBounds( sdBounds2D( x0, y0, x1 - x0, y1 - y0 ) ) && winding.GetNumPoints() >= 3;
}

bool sdGuiModel::ClipWinding( idWinding2D& winding ) const {
	if ( !clippingEnabled || clipRects.Num() == 0 ) {
		return winding.GetNumPoints() >= 3;
	}
	for ( int i = 0; i < clipRects.Num(); i++ ) {
		if ( !winding.ClipByBounds( clipRects[ i ] ) ) {
			return false;
		}
	}
	return winding.GetNumPoints() >= 3;
}

void sdGuiModel::QueueRect(
	float x, float y, float w, float h,
	float s1, float t1, float s2, float t2,
	float ms1, float mt1, float ms2, float mt2,
	const idMaterial* material, idSoundEmitter* referenceSound,
	float angle, bool clipped, bool masked
) {
	idWinding2D winding;
	winding.AddPoint( x, y, s1, t1 );
	winding.AddPoint( x + w, y, s2, t1 );
	winding.AddPoint( x + w, y + h, s2, t2 );
	winding.AddPoint( x, y + h, s1, t2 );

	if ( angle != 0.0f ) {
		const idVec2 center( x + w * 0.5f, y + h * 0.5f );
		winding.Rotation( center, angle * idMath::M_DEG2RAD );
	}

	// a masked draw shows only where its mask coordinates are within 0..1; the
	// territory outlines are drawn far larger than the command map they are
	// masked to, so cut them there (#189). The mask coordinates below come from
	// the rotated positions, so the cut comes after the rotation
	if ( masked && !ClipToMask( winding, x, y, w, h, ms1, mt1, ms2, mt2 ) ) {
		return;
	}

	if ( clipped && !ClipWinding( winding ) ) {
		return;
	}

	guiPrimitive_t primitive;
	memset( &primitive, 0, sizeof( primitive ) );
	primitive.sequence = writePosition;
	primitive.material = material;
	primitive.referenceSound = referenceSound;
	primitive.color = currentColor;
	memcpy( primitive.materialParms, materialParms, sizeof( primitive.materialParms ) );
	primitive.masked = masked;
	primitive.numVerts = Min( winding.GetNumPoints(), idWinding2D::MAX_POINTS );
	for ( int i = 0; i < primitive.numVerts; i++ ) {
		primitive.verts[ i ].xy = winding[ i ];
		primitive.verts[ i ].st = winding.GetST( i );
		const float fx = w != 0.0f ? ( winding[ i ].x - x ) / w : 0.0f;
		const float fy = h != 0.0f ? ( winding[ i ].y - y ) / h : 0.0f;
		primitive.verts[ i ].maskST.Set(
			ms1 + ( ms2 - ms1 ) * fx,
			mt1 + ( mt2 - mt1 ) * fy
		);
	}
	primitives.Append( primitive );
	writePosition += sizeof( primitive );
}

void sdGuiModel::QueueWinding(
	const idWinding2D& source,
	const idMaterial* material,
	idSoundEmitter* referenceSound,
	bool masked,
	float minx, float miny, float width, float height
) {
	idWinding2D winding = source;
	if ( masked && !ClipToMask( winding, minx, miny, width, height, 0.0f, 0.0f, 1.0f, 1.0f ) ) {
		return;
	}
	if ( !ClipWinding( winding ) ) {
		return;
	}

	guiPrimitive_t primitive;
	memset( &primitive, 0, sizeof( primitive ) );
	primitive.sequence = writePosition;
	primitive.material = material;
	primitive.referenceSound = referenceSound;
	primitive.color = currentColor;
	memcpy( primitive.materialParms, materialParms, sizeof( primitive.materialParms ) );
	primitive.masked = masked;
	primitive.numVerts = Min( winding.GetNumPoints(), idWinding2D::MAX_POINTS );
	for ( int i = 0; i < primitive.numVerts; i++ ) {
		primitive.verts[ i ].xy = winding[ i ];
		primitive.verts[ i ].st = winding.GetST( i );
		primitive.verts[ i ].maskST.Set(
			width != 0.0f ? ( winding[ i ].x - minx ) / width : 0.0f,
			height != 0.0f ? ( winding[ i ].y - miny ) / height : 0.0f
		);
	}
	primitives.Append( primitive );
	writePosition += sizeof( primitive );
}

void sdGuiModel::DrawRect(
	float x, float y, float w, float h,
	float s1, float t1, float s2, float t2,
	const idMaterial* material, float angle
) {
	QueueRect( x, y, w, h, s1, t1, s2, t2, 0.0f, 0.0f, 1.0f, 1.0f,
		material, NULL, angle, false, false );
}

void sdGuiModel::DrawClippedRect(
	float x, float y, float w, float h,
	float s1, float t1, float s2, float t2,
	const idMaterial* material, float angle
) {
	QueueRect( x, y, w, h, s1, t1, s2, t2, 0.0f, 0.0f, 1.0f, 1.0f,
		material, NULL, angle, true, false );
}

void sdGuiModel::DrawMaskedClippedRect(
	float x, float y, float w, float h,
	float s01, float t01, float s02, float t02,
	float s11, float t11, float s12, float t12,
	const idMaterial* material, float angle
) {
	QueueRect( x, y, w, h, s01, t01, s02, t02, s11, t11, s12, t12,
		material, NULL, angle, true, true );
}

void sdGuiModel::DrawCinematic(
	float x, float y, float w, float h,
	float s1, float t1, float s2, float t2,
	const idMaterial* material, idSoundEmitter* referenceSound, float angle
) {
	QueueRect( x, y, w, h, s1, t1, s2, t2, 0.0f, 0.0f, 1.0f, 1.0f,
		material, referenceSound, angle, true, false );
}

void sdGuiModel::DrawClippedWinding( const idWinding2D& winding, const idMaterial* material ) {
	QueueWinding( winding, material, NULL, false, 0.0f, 0.0f, 1.0f, 1.0f );
}

void sdGuiModel::DrawClippedWindingMasked(
	const idWinding2D& winding,
	const idMaterial* material,
	float minx, float miny, float width, float height
) {
	QueueWinding( winding, material, NULL, true, minx, miny, width, height );
}

void sdGuiModel::SetFont( int font ) {
	currentFont = font;
	writePosition += sizeof( int ) * 2;
}

void sdGuiModel::SetFontSize( int pointSize ) {
	currentFontSize = pointSize;
	writePosition += sizeof( int ) * 2;
}

void sdGuiModel::DrawTextA( const wchar_t* text, const sdBounds2D& rect, unsigned int flags ) {
	if ( text == NULL || text[ 0 ] == L'\0' ) {
		return;
	}
	guiText_t command;
	command.sequence = writePosition;
	command.text = text;
	command.rect = rect;
	command.flags = flags;
	command.color = currentColor;
	command.font = currentFont;
	command.pointSize = currentFontSize;
	// clipped to its rectangle and the clip stack, as it is queued; the
	// rectangle's top gives way by the face's room above its capitals and its
	// bottom by the descent, applied once the string is laid out, because retail
	// 1.5 draws a line's ink whole in a rectangle only as tall as the text (#193:
	// the main menu's banner)
	command.clipped = ( flags & DTF_NOCLIPPING ) == 0;
	command.clip = rect;
	command.clip.GetMins().y = -1.0e6f;
	command.clip.GetMaxs().y = 1.0e6f;
	command.clipTop = rect.GetMins().y;
	command.clipBottom = rect.GetMaxs().y;
	if ( rect.GetWidth() <= 0.0f || rect.GetHeight() <= 0.0f ) {
		// an empty rectangle places the text and does not hide it
		command.clip.GetMins().Set( -1.0e6f, -1.0e6f );
		command.clip.GetMaxs().Set( 1.0e6f, 1.0e6f );
		command.clipTop = -1.0e6f;
		command.clipBottom = 1.0e6f;
	}
	if ( command.clipped && clippingEnabled ) {
		for ( int i = 0; i < clipRects.Num(); i++ ) {
			sdBounds2D clip;
			command.clip.IntersectBounds( clipRects[ i ], clip );
			command.clip = clip;
		}
	}
	texts.Append( command );
	writePosition += sizeof( int ) * 8;
}

// Lays out every queued string, rasterises the glyphs it needs and uploads the
// atlases that changed, so neither renderer changes an image mid-frame.
void sdGuiModel::BuildTextQuads() {
	textQuads.SetNum( 0, false );
	textQuadStart.SetNum( 0, false );
	sdGuiTextLayout layout;
	for ( int t = 0; t < texts.Num(); t++ ) {
		textQuadStart.Append( textQuads.Num() );
		const guiText_t& command = texts[ t ];
		guiFonts.Layout( command.font, command.pointSize, command.text.c_str(), command.rect,
			command.flags, command.color, layout );
		float xScale, yScale;
		sdGuiFontManager::ScreenScale( xScale, yScale );
		for ( int g = 0; g < layout.glyphs.Num(); g++ ) {
			const sdGuiTextGlyph& glyph = layout.glyphs[ g ];
			sdGuiGlyphImage cell;
			if ( !guiFonts.Glyph( command.font, command.pointSize, glyph.codepoint, cell ) ) {
				continue;
			}
			guiTextQuad_t quad;
			quad.image = cell.image;
			quad.color = glyph.color;
			quad.x0 = glyph.x + cell.left / xScale;
			quad.y0 = glyph.baseline + cell.top / yScale;
			quad.x1 = quad.x0 + cell.width / xScale;
			quad.y1 = quad.y0 + cell.height / yScale;
			quad.s0 = cell.s0;
			quad.t0 = cell.t0;
			quad.s1 = cell.s1;
			quad.t1 = cell.t1;
			if ( command.clipped ) {
				idVec2 mins = command.clip.GetMins();
				idVec2 maxs = command.clip.GetMaxs();
				mins.y = Max( mins.y, command.clipTop - layout.accent );
				maxs.y = Min( maxs.y, command.clipBottom + layout.descent );
				if ( quad.x1 <= mins.x || quad.x0 >= maxs.x || quad.y1 <= mins.y || quad.y0 >= maxs.y ) {
					continue;
				}
				const float ds = ( quad.s1 - quad.s0 ) / ( quad.x1 - quad.x0 );
				const float dt = ( quad.t1 - quad.t0 ) / ( quad.y1 - quad.y0 );
				if ( quad.x0 < mins.x ) { quad.s0 += ( mins.x - quad.x0 ) * ds; quad.x0 = mins.x; }
				if ( quad.x1 > maxs.x ) { quad.s1 -= ( quad.x1 - maxs.x ) * ds; quad.x1 = maxs.x; }
				if ( quad.y0 < mins.y ) { quad.t0 += ( mins.y - quad.y0 ) * dt; quad.y0 = mins.y; }
				if ( quad.y1 > maxs.y ) { quad.t1 -= ( quad.y1 - maxs.y ) * dt; quad.y1 = maxs.y; }
			}
			textQuads.Append( quad );
		}
	}
	textQuadStart.Append( textQuads.Num() );
	// rows to texture coordinates now that this frame's glyphs are all in, so a
	// quad built before its atlas grew still points at its glyph
	const idImage* lastImage = NULL;
	float rowScale = 1.0f;
	for ( int i = 0; i < textQuads.Num(); i++ ) {
		guiTextQuad_t& quad = textQuads[ i ];
		if ( quad.image != lastImage ) {
			lastImage = quad.image;
			rowScale = 1.0f / static_cast< float >( guiFonts.AtlasHeight( quad.image ) );
		}
		quad.t0 *= rowScale;
		quad.t1 *= rowScale;
	}
	guiFonts.UploadChanged();
}

void sdGuiModel::SubmitFrame( int windowWidth, int windowHeight ) {
	if ( ( primitives.Num() == 0 && texts.Num() == 0 ) || !fullScreen ) {
		return;
	}
	if ( vulkanBackend.IsFrameActive() ) {
		SubmitFrameVulkan();
		return;
	}
	BuildTextQuads();

	glPushAttrib( GL_COLOR_BUFFER_BIT | GL_CURRENT_BIT | GL_DEPTH_BUFFER_BIT |
		GL_ENABLE_BIT | GL_LIST_BIT | GL_PIXEL_MODE_BIT | GL_POLYGON_BIT |
		GL_SCISSOR_BIT | GL_TEXTURE_BIT );
	glDisable( GL_DEPTH_TEST );
	glDisable( GL_CULL_FACE );
	glDisable( GL_ALPHA_TEST );
	glEnable( GL_BLEND );
	glBlendFunc( GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA );

	glMatrixMode( GL_PROJECTION );
	glPushMatrix();
	glLoadIdentity();
	glOrtho( 0.0, 640.0, 480.0, 0.0, -1.0, 1.0 );
	glMatrixMode( GL_MODELVIEW );
	glPushMatrix();
	glLoadIdentity();
	// The retail GUI model turns its quads into idDrawVert surfaces, so render
	// programs receive colorAttrib and texCoordAttrib from generic arrays.  This
	// compatibility submission uses immediate vertices and must provide those
	// generic attributes explicitly instead of inheriting the last world VBO.
	GL_EnableVertexAttribs( 0 );
	// the calls above set GL directly: without this, a stage whose bits match the
	// last ones cached (the previous frame's last GUI stage, on a frame with no
	// world render) skips its blend function (#203)
	GL_ResyncStateCache();

	auto submitPrimitive = [&]( const guiPrimitive_t& primitive ) {
		idImage* image = primitive.referenceSound != NULL ? ResolveGuiImage( primitive.material, primitive.referenceSound ) : NULL;
		if ( primitive.material == NULL || ( image != NULL && image->defaulted ) ) {
			return;
		}
		const byte* rgba = reinterpret_cast< const byte* >( &primitive.color );
		idList< float > evaluated;
		evaluated.SetNum( primitive.material->GetNumRegisters(), false );
		primitive.material->EvaluateRegisters( evaluated.Begin(), primitive.materialParms, NULL, primitive.referenceSound, 0 );
		auto stageDrawable = [&]( const materialStage_t* stage ) {
			if ( evaluated[ stage->conditionRegister ] == 0.0f ) return false;
			// Missing optional GUI layers are skipped individually.  The material's
			// editor image is only a preview and may be defaulted even though another
			// program stage has all of its real images available.
			if ( primitive.referenceSound == NULL ) {
				for ( int textureIndex = 0; textureIndex < stage->numTextures; ++textureIndex ) {
					const idImage* stageImage = stage->textures[ textureIndex ].image;
					if ( stageImage != NULL && stageImage->defaulted ) return false;
				}
			}
			// a program that failed to parse (retail's Cg postprocess passes) would
			// draw its default constant orange over the whole view (#185)
			return stage->renderProgram == NULL || stage->renderProgram->GetState() != DS_DEFAULTED;
		};
		// guis/postprocess draws postprocess/glow: stages that render into the
		// quarter-size _postProcessBuffer_0/1 (destinationBuffer), all reading the
		// finished frame as _currentRender (#233).  Those stages draw in the back
		// buffer's corner, which only the material's last stage covers again, so
		// the material draws whole or not at all (postprocess/heatsight's Cg has no
		// port).
		bool postProcess = false;
		bool allDrawable = true;
		for ( int stageIndex = 0; stageIndex < primitive.material->GetNumStages(); ++stageIndex ) {
			const materialStage_t* stage = primitive.material->GetStage( stageIndex );
			postProcess |= stage->destinationBuffer >= 0;
			allDrawable &= stageDrawable( stage );
		}
		if ( postProcess && !allDrawable ) {
			return;
		}
		// the drawable's own size (a HiDPI drawable is larger than the window)
		GLint frameViewport[ 4 ] = { 0, 0, windowWidth, windowHeight };
		if ( postProcess || primitive.material->TestMaterialFlag( MF_UPDATECURRENTRENDER ) ) {
			glGetIntegerv( GL_VIEWPORT, frameViewport );
			GuiCopyFrameForPostProcess( frameViewport[ 0 ], frameViewport[ 1 ], frameViewport[ 2 ], frameViewport[ 3 ] );
		}
		for ( int stageIndex = 0; stageIndex < primitive.material->GetNumStages(); ++stageIndex ) {
			const materialStage_t* stage = primitive.material->GetStage( stageIndex );
			if ( !stageDrawable( stage ) ) continue;
			idVec4 stageColor;
			idVec4 matrixS;
			idVec4 matrixT;
			// Ordinary ETQW draw surfaces initialize diffuseColor before applying
			// the stage's explicit vectors.  The compatibility GUI path must do
			// the same: trivial.rprog always multiplies by diffuseColor, and a GUI
			// stage without an rgb/rgba keyword otherwise inherits the last world
			// surface's value (the purple console tint seen after loading a map).
			if ( rbinds != NULL && rbinds->diffuseColor != NULL ) {
				rbinds->diffuseColor->Set( 1.0f, 1.0f, 1.0f, 1.0f );
			}
			if ( !RB_SetupMaterialStage( stage, evaluated.Begin(), image, stageColor, matrixS, matrixT ) ) continue;
			// GUI quads are two-sided, as on Vulkan (cull mode none): a stage's own
			// cull type would drop a mirrored quad such as the rotated command map.
			GL_Cull( CT_TWO_SIDED );
			// ARB vertex programs are precompiled for ETQW's packed idDrawVert
			// layout when r_32ByteVtx is enabled.  The retail GUI path builds those
			// packed vertices before drawing; this immediate-mode compatibility path
			// does not, so emulate the packed texcoord attribute at the boundary.
			// Without this, the program divides ordinary 0..1 GUI UVs by 4096 (or
			// 32768), collapsing font atlases and cursor images onto one texel.
			float texCoordAttribScale = 1.0f;
			if ( r_32ByteVtx.GetBool() && stage->renderProgram != NULL &&
				dynamic_cast< sdRenderProgramARB* >( stage->renderProgram->GetProgram() ) != NULL ) {
				texCoordAttribScale = stage->renderProgram->UsesLowRangeUVs() ? 32768.0f : 4096.0f;
			}
			const float vertexColor[ 4 ] = {
				rgba[ 0 ] / 255.0f,
				rgba[ 1 ] / 255.0f,
				rgba[ 2 ] / 255.0f,
				rgba[ 3 ] / 255.0f
			};
			idVec4 fixedFunctionColor = stageColor;
			switch ( stage->vertexColor ) {
				case SVC_MODULATE:
					fixedFunctionColor.x *= vertexColor[ 0 ];
					fixedFunctionColor.y *= vertexColor[ 1 ];
					fixedFunctionColor.z *= vertexColor[ 2 ];
					fixedFunctionColor.w *= vertexColor[ 3 ];
					break;
				case SVC_MODULATE_ALPHA:
					fixedFunctionColor.w *= vertexColor[ 3 ];
					break;
				case SVC_INVERSE_MODULATE:
					fixedFunctionColor.x *= 1.0f - vertexColor[ 0 ];
					fixedFunctionColor.y *= 1.0f - vertexColor[ 1 ];
					fixedFunctionColor.z *= 1.0f - vertexColor[ 2 ];
					fixedFunctionColor.w *= 1.0f - vertexColor[ 3 ];
					break;
				case SVC_IGNORE:
				default:
					break;
			}
			glColor4f( fixedFunctionColor.x, fixedFunctionColor.y, fixedFunctionColor.z, fixedFunctionColor.w );
			if ( qglVertexAttrib4fvARB != NULL && rbinds != NULL ) {
				qglVertexAttrib4fvARB( rbinds->colorAttrib->GetAttribIndex(), vertexColor );
			}
			idImage* destination = stage->destinationBuffer >= 0 && stage->destinationBuffer < 2 && globalImages != NULL ?
				globalImages->postProcessBuffer[ stage->destinationBuffer ] : NULL;
			if ( destination != NULL ) {
				glViewport( frameViewport[ 0 ], frameViewport[ 1 ], destination->uploadWidth, destination->uploadHeight );
			}
			glBegin( GL_TRIANGLE_FAN );
			for ( int j = 0; j < primitive.numVerts; j++ ) {
				const idVec2& st = primitive.verts[ j ].st;
				const float texCoord[ 4 ] = { st.x * texCoordAttribScale, st.y * texCoordAttribScale, 0.0f, 1.0f };
				// In the compatibility profile glTexCoord aliases generic attribute 8.
				// Set the fixed-function coordinate first, then restore the packed ARB
				// attribute so it is the value captured when glVertex emits the vertex.
				glTexCoord2f( matrixS.x * st.x + matrixS.y * st.y + matrixS.w, matrixT.x * st.x + matrixT.y * st.y + matrixT.w );
				if ( qglVertexAttrib4fvARB != NULL && rbinds != NULL ) {
					qglVertexAttrib4fvARB( rbinds->texCoordAttrib->GetAttribIndex(), texCoord );
				}
				glVertex2f( primitive.verts[ j ].xy.x, primitive.verts[ j ].xy.y );
			}
			glEnd();
			if ( destination != NULL ) {
				destination->CopyFramebuffer( frameViewport[ 0 ], frameViewport[ 1 ], destination->uploadWidth, destination->uploadHeight, false );
				glViewport( frameViewport[ 0 ], frameViewport[ 1 ], frameViewport[ 2 ], frameViewport[ 3 ] );
			}
		}
	};

	auto submitText = [&]( int textIndex ) {
		// fixed-function textured quads: a GUI material stage may have left an
		// ARB or GLSL program bound
		const int first = textQuadStart[ textIndex ];
		const int end = textQuadStart[ textIndex + 1 ];
		if ( first == end ) {
			return;
		}
		SD_UnbindRenderProgram();
		// glPopAttrib restores the driver's active unit behind the state cache's
		// back; selecting through unit 1 makes unit 0 really active
		GL_SelectTexture( 1 );
		GL_SelectTexture( 0 );
		// fixed function also samples every other enabled unit: a material stage
		// drawn before may have left one on (its white image draws solid boxes)
		for ( int unit = Min( glConfig.maxTextureUnits, 8 ) - 1; unit >= 1; unit-- ) {
			qglActiveTextureARB( GL_TEXTURE0_ARB + unit );
			glDisable( GL_TEXTURE_1D );
			glDisable( GL_TEXTURE_2D );
			glDisable( GL_TEXTURE_RECTANGLE_ARB );
			if ( glConfig.texture3DAvailable ) glDisable( GL_TEXTURE_3D );
			if ( glConfig.cubeMapAvailable ) glDisable( GL_TEXTURE_CUBE_MAP_ARB );
		}
		qglActiveTextureARB( GL_TEXTURE0_ARB );
		// and on unit 0, the targets that take precedence over 2D
		glDisable( GL_TEXTURE_1D );
		glDisable( GL_TEXTURE_RECTANGLE_ARB );
		if ( glConfig.texture3DAvailable ) glDisable( GL_TEXTURE_3D );
		if ( glConfig.cubeMapAvailable ) glDisable( GL_TEXTURE_CUBE_MAP_ARB );
		glDisable( GL_TEXTURE_GEN_S );
		glDisable( GL_TEXTURE_GEN_T );
		glDisable( GL_TEXTURE_GEN_R );
		glDisable( GL_TEXTURE_GEN_Q );
		glMatrixMode( GL_TEXTURE );
		glPushMatrix();
		glLoadIdentity();
		glMatrixMode( GL_MODELVIEW );
		// the stage drawn before may have been opaque: without blending every
		// glyph is a solid box (seen on macOS and Linux under OpenGL)
		// (GLS bits, as the Vulkan path's 0x65: source alpha, one minus source
		// alpha; 0x100 no depth writes; 0x10000 depth always).  Set through the
		// state cache: setting GL directly leaves the cache holding the stage
		// drawn before, so a following stage with the same bits skips its blend
		// function and draws with this one (#193: the main menu's sheet_add pass
		// after the Mods/Replay/Credits labels drew opaque and covered them).
		GL_State( 0x65 | 0x100 | 0x10000 );
		GL_Cull( CT_TWO_SIDED );
		glDisable( GL_ALPHA_TEST );
		glEnable( GL_TEXTURE_2D );
		glTexEnvi( GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE );
		idImage* bound = NULL;
		for ( int i = first; i < end; i++ ) {
			const guiTextQuad_t& quad = textQuads[ i ];
			if ( quad.image != bound ) {
				if ( bound != NULL ) {
					glEnd();
				}
				quad.image->Bind();
				bound = quad.image;
				glBegin( GL_QUADS );
			}
			const byte* rgba = reinterpret_cast< const byte* >( &quad.color );
			glColor4ub( rgba[ 0 ], rgba[ 1 ], rgba[ 2 ], rgba[ 3 ] );
			glTexCoord2f( quad.s0, quad.t0 ); glVertex2f( quad.x0, quad.y0 );
			glTexCoord2f( quad.s1, quad.t0 ); glVertex2f( quad.x1, quad.y0 );
			glTexCoord2f( quad.s1, quad.t1 ); glVertex2f( quad.x1, quad.y1 );
			glTexCoord2f( quad.s0, quad.t1 ); glVertex2f( quad.x0, quad.y1 );
		}
		glEnd();
		glMatrixMode( GL_TEXTURE );
		glPopMatrix();
		glMatrixMode( GL_MODELVIEW );
		glDisable( GL_TEXTURE_2D );
	};

	// The retail GUI model is one command stream.  Keeping primitives and text
	// in separate decoded arrays is fine only if they are merged again here;
	// otherwise all GUI text is drawn after the console even though the console
	// is the final producer in Session::Draw.
	int primitiveIndex = 0;
	int textIndex = 0;
	while ( primitiveIndex < primitives.Num() || textIndex < texts.Num() ) {
		const bool usePrimitive = textIndex >= texts.Num() ||
			( primitiveIndex < primitives.Num() && primitives[ primitiveIndex ].sequence < texts[ textIndex ].sequence );
		if ( usePrimitive ) {
			submitPrimitive( primitives[ primitiveIndex++ ] );
		} else {
			submitText( textIndex++ );
		}
	}

	glPopMatrix();
	glMatrixMode( GL_PROJECTION );
	glPopMatrix();
	glMatrixMode( GL_MODELVIEW );
	glPopAttrib();
	// glPopAttrib restored the driver's state behind the cache
	GL_ResyncStateCache();
}

void sdGuiModel::SubmitFrameVulkan() {
	// laid out and uploaded before any draw of this frame uses an atlas
	BuildTextQuads();
	idList< float > evaluated;
	idList< sdVulkanToolVertex > batchedVertices;
	batchedVertices.SetGranularity( 1024 );
	idImage* batchedImage = NULL;
	auto flushBatch = [&]() {
		if ( batchedImage != NULL && batchedVertices.Num() > 0 ) {
			vulkanBackend.DrawGuiTriangles( batchedImage, batchedVertices.Begin(),
				batchedVertices.Num() );
		}
		batchedVertices.SetNum( 0, false );
		batchedImage = NULL;
	};
	auto selectBatchImage = [&]( idImage* image ) {
		if ( batchedImage != image ) {
			flushBatch();
			batchedImage = image;
		}
	};
	auto submitPrimitive = [&]( const guiPrimitive_t& primitive ) {
		if ( primitive.material == NULL || primitive.numVerts < 3 ) {
			return;
		}
		idImage* overrideImage = primitive.referenceSound != NULL ?
			ResolveGuiImage( primitive.material, primitive.referenceSound ) : NULL;
		evaluated.SetNum( primitive.material->GetNumRegisters(), false );
		primitive.material->EvaluateRegisters( evaluated.Begin(),
			primitive.materialParms, NULL, primitive.referenceSound, 0 );
		for ( int stageIndex = 0; stageIndex < primitive.material->GetNumStages();
			++stageIndex ) {
			const materialStage_t* stage = primitive.material->GetStage( stageIndex );
			if ( evaluated[ stage->conditionRegister ] == 0.0f ) {
				continue;
			}
			idImage* image = ResolveVulkanStageImage( stage, overrideImage );
			// Legacy postprocess programs obtain their inputs from framebuffer render
			// bindings rather than material textures.  Until a Vulkan implementation
			// exists for a particular pass, omitting it preserves the already-rendered
			// scene.  Substituting the generic white image here turns an unresolved
			// full-screen pass (notably postprocess/glow without the x86 Cg runtime)
			// into an opaque white quad on x64.
			if ( image == NULL && stage->renderProgram != NULL &&
				idStr::Icmpn( stage->renderProgram->GetName(), "postprocess/", 12 ) == 0 ) {
				continue;
			}
			if ( image == NULL && globalImages != NULL ) {
				image = globalImages->whiteImage;
			}
			if ( image != NULL && !image->IsLoaded() ) {
				image->BindFragment();
			}
			if ( image == NULL || image->defaulted ) {
				continue;
			}

			idVec4 stageColor( 1.0f, 1.0f, 1.0f, 1.0f );
			if ( stage->colorVector != NULL ) {
				stageColor.Set(
					evaluated[ stage->colorVector->registers[ 0 ] ],
					evaluated[ stage->colorVector->registers[ 1 ] ],
					evaluated[ stage->colorVector->registers[ 2 ] ],
					evaluated[ stage->colorVector->registers[ 3 ] ] );
			}
			const byte* rgba = reinterpret_cast< const byte* >( &primitive.color );
			const idVec4 vertexColor( rgba[ 0 ] / 255.0f, rgba[ 1 ] / 255.0f,
				rgba[ 2 ] / 255.0f, rgba[ 3 ] / 255.0f );
			switch ( stage->vertexColor ) {
				case SVC_MODULATE:
					stageColor.x *= vertexColor.x;
					stageColor.y *= vertexColor.y;
					stageColor.z *= vertexColor.z;
					stageColor.w *= vertexColor.w;
					break;
				case SVC_MODULATE_ALPHA:
					stageColor.w *= vertexColor.w;
					break;
				case SVC_INVERSE_MODULATE:
					stageColor.x *= 1.0f - vertexColor.x;
					stageColor.y *= 1.0f - vertexColor.y;
					stageColor.z *= 1.0f - vertexColor.z;
					stageColor.w *= 1.0f - vertexColor.w;
					break;
				default:
					break;
			}

			sdVulkanGuiVertex vertices[ idWinding2D::MAX_POINTS ];
			for ( int vertexIndex = 0; vertexIndex < primitive.numVerts; ++vertexIndex ) {
				const guiVertex_t& source = primitive.verts[ vertexIndex ];
				vertices[ vertexIndex ].x = source.xy.x;
				vertices[ vertexIndex ].y = source.xy.y;
				vertices[ vertexIndex ].s = source.st.x;
				vertices[ vertexIndex ].t = source.st.y;
				if ( stage->diffuseTextureMatrix != NULL ) {
					const int ( *matrix )[ 3 ] = stage->diffuseTextureMatrix->matrix;
					vertices[ vertexIndex ].s =
						evaluated[ matrix[ 0 ][ 0 ] ] * source.st.x +
						evaluated[ matrix[ 0 ][ 1 ] ] * source.st.y +
						evaluated[ matrix[ 0 ][ 2 ] ];
					vertices[ vertexIndex ].t =
						evaluated[ matrix[ 1 ][ 0 ] ] * source.st.x +
						evaluated[ matrix[ 1 ][ 1 ] ] * source.st.y +
						evaluated[ matrix[ 1 ][ 2 ] ];
				}
			}
			// The trivialMakeAlpha* programs turn the texel's brightness into alpha
			// (the compass ring, the menu sheets); drawn as plain textures, their
			// black backgrounds cover what lies under them (#189).
			const bool makeAlpha = stage->renderProgram != NULL &&
				idStr::Icmpn( stage->renderProgram->GetName(), "trivialMakeAlpha", 16 ) == 0;
			if ( ( stage->drawStateBits & 0xFF ) == 0x65 && !makeAlpha ) {
				selectBatchImage( image );
				for ( int triangle = 1; triangle + 1 < primitive.numVerts; ++triangle ) {
					const int fanIndexes[ 3 ] = { 0, triangle, triangle + 1 };
					for ( int corner = 0; corner < 3; ++corner ) {
						const sdVulkanGuiVertex& source = vertices[ fanIndexes[ corner ] ];
						sdVulkanToolVertex vertex;
						vertex.x = source.x * ( 2.0f / 640.0f ) - 1.0f;
						vertex.y = 1.0f - source.y * ( 2.0f / 480.0f );
						vertex.z = 0.0f;
						vertex.w = 1.0f;
						vertex.s = source.s;
						vertex.t = source.t;
						vertex.r = stageColor.x;
						vertex.g = stageColor.y;
						vertex.b = stageColor.z;
						vertex.a = stageColor.w;
						batchedVertices.Append( vertex );
					}
				}
			} else {
				flushBatch();
				vulkanBackend.DrawGuiFan( image, vertices, primitive.numVerts,
					stageColor.ToFloatPtr(), stage->drawStateBits, makeAlpha );
			}
		}
	};

	auto submitText = [&]( int textIndex ) {
		for ( int i = textQuadStart[ textIndex ]; i < textQuadStart[ textIndex + 1 ]; i++ ) {
			const guiTextQuad_t& quad = textQuads[ i ];
			selectBatchImage( quad.image );
			const byte* rgba = reinterpret_cast< const byte* >( &quad.color );
			const float left = quad.x0 * ( 2.0f / 640.0f ) - 1.0f;
			const float right = quad.x1 * ( 2.0f / 640.0f ) - 1.0f;
			const float top = 1.0f - quad.y0 * ( 2.0f / 480.0f );
			const float bottom = 1.0f - quad.y1 * ( 2.0f / 480.0f );
			const float positions[ 4 ][ 4 ] = {
				{ left, top, quad.s0, quad.t0 }, { right, top, quad.s1, quad.t0 },
				{ right, bottom, quad.s1, quad.t1 }, { left, bottom, quad.s0, quad.t1 }
			};
			const int corners[ 6 ] = { 0, 1, 2, 0, 2, 3 };
			for ( int c = 0; c < 6; c++ ) {
				const float* p = positions[ corners[ c ] ];
				sdVulkanToolVertex vertex;
				vertex.x = p[ 0 ];
				vertex.y = p[ 1 ];
				vertex.z = 0.0f;
				vertex.w = 1.0f;
				vertex.s = p[ 2 ];
				vertex.t = p[ 3 ];
				vertex.r = rgba[ 0 ] / 255.0f;
				vertex.g = rgba[ 1 ] / 255.0f;
				vertex.b = rgba[ 2 ] / 255.0f;
				vertex.a = rgba[ 3 ] / 255.0f;
				batchedVertices.Append( vertex );
			}
		}
	};

	// Primitives and text are decoded into separate arrays, but they originate
	// from one GUI command stream.  Preserve that stream here so the console,
	// which Session::Draw produces last, stays in front of earlier HUD commands.
	int primitiveIndex = 0;
	int textIndex = 0;
	while ( primitiveIndex < primitives.Num() || textIndex < texts.Num() ) {
		const bool usePrimitive = textIndex >= texts.Num() ||
			( primitiveIndex < primitives.Num() &&
			primitives[ primitiveIndex ].sequence < texts[ textIndex ].sequence );
		if ( usePrimitive ) {
			submitPrimitive( primitives[ primitiveIndex++ ] );
		} else {
			submitText( textIndex++ );
		}
	}
	flushBatch();
}

void sdGuiModel::FlushFrame( int windowWidth, int windowHeight ) {
	SubmitFrame( windowWidth, windowHeight );
	primitives.SetNum( 0, false );
	texts.SetNum( 0, false );
}

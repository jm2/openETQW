// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: GuiStageColorTest.cpp
GuiStageColor (src/renderer/GuiStageColor.h), the colour a Vulkan GUI stage is
drawn with: retail's trivial programs apply the stage's vertexColor mode to its
colour vector, and trivialMaskImage takes the draw's colour as it is (#398). The
Vulkan path used the stage colour for every program, so a trivialMaskImage stage
with a colour vector or without a vertexColor keyword was drawn untinted.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "GuiStageColor.h"

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

static bool Near( const idVec4& a, const idVec4& b ) {
	for ( int i = 0; i < 4; i++ ) {
		if ( std::fabs( a[ i ] - b[ i ] ) >= 1e-5f ) {
			return false;
		}
	}
	return true;
}

int main() {
	const idVec4 stage( 0.5f, 0.25f, 1.0f, 0.8f );
	const idVec4 vertex( 0.2f, 0.4f, 0.6f, 0.5f );

	// the trivial programs: diffuseColor x ( vertex x colorModulate + colorAdd )
	Expect( Near( GuiStageColor( stage, SVC_IGNORE, vertex, false ), stage ),
		"SVC_IGNORE: the stage colour" );
	Expect( Near( GuiStageColor( stage, SVC_MODULATE, vertex, false ),
		idVec4( 0.1f, 0.1f, 0.6f, 0.4f ) ), "SVC_MODULATE: stage x vertex" );
	Expect( Near( GuiStageColor( stage, SVC_MODULATE_ALPHA, vertex, false ),
		idVec4( 0.5f, 0.25f, 1.0f, 0.4f ) ), "SVC_MODULATE_ALPHA: only alpha x vertex" );
	Expect( Near( GuiStageColor( stage, SVC_INVERSE_MODULATE, vertex, false ),
		idVec4( 0.4f, 0.15f, 0.4f, 0.4f ) ), "SVC_INVERSE_MODULATE: stage x ( 1 - vertex )" );

	// trivialMaskImage: the draw's colour, whatever the stage says
	for ( int mode = SVC_IGNORE; mode <= SVC_INVERSE_MODULATE; mode++ ) {
		Expect( Near( GuiStageColor( stage, static_cast< stageVertexColor_t >( mode ), vertex, true ), vertex ),
			va( "trivialMaskImage, vertexColor mode %d: the raw vertex colour", mode ) );
	}
	Expect( GuiStageUsesRawVertexColor( "trivialMaskImage" ), "trivialMaskImage takes the raw colour" );
	Expect( GuiStageUsesRawVertexColor( "trivialmaskimage" ), "the program name is matched without case" );
	Expect( !GuiStageUsesRawVertexColor( "trivialWithTextureMatrix" ), "trivialWithTextureMatrix does not" );
	Expect( !GuiStageUsesRawVertexColor( "trivialMaskImageScreen" ), "trivialMaskImageScreen is a different program" );
	Expect( !GuiStageUsesRawVertexColor( NULL ), "no program: not raw" );

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: GUI stages are coloured as retail's programs colour them\n" );
	return 0;
}

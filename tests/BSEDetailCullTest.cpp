// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: BSEDetailCullTest.cpp
BSE_SegmentDetailCulled (src/bse/BSE_DetailCull.h), which leaves out an effect
segment whose "detail" is above bse_detailLevel (#423). The Low preset sets the
level to 0.5; before this every segment drew at every level.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "../src/bse/BSE_DetailCull.h"

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
	std::printf( "  %s %s\n", ok ? "ok  " : "FAIL", what );
	if ( !ok ) {
		failures++;
	}
}

int main() {
	// the default level (1.0, every preset but Low)
	Expect( !BSE_SegmentDetailCulled( 0.0f, 1.0f ), "no detail draws at the default level" );
	Expect( !BSE_SegmentDetailCulled( 0.5f, 1.0f ), "detail 0.5 draws at the default level" );
	Expect( !BSE_SegmentDetailCulled( 1.0f, 1.0f ), "detail 1 draws at the default level" );
	Expect( BSE_SegmentDetailCulled( 1.5f, 1.0f ), "detail 1.5 is left out at the default level" );

	// the Low preset (0.5)
	Expect( !BSE_SegmentDetailCulled( 0.0f, 0.5f ), "no detail draws in Low" );
	Expect( !BSE_SegmentDetailCulled( 0.25f, 0.5f ), "detail 0.25 draws in Low" );
	Expect( !BSE_SegmentDetailCulled( 0.5f, 0.5f ), "detail 0.5 draws in Low (the level itself)" );
	Expect( BSE_SegmentDetailCulled( 0.75f, 0.5f ), "detail 0.75 is left out in Low" );
	Expect( BSE_SegmentDetailCulled( 1.0f, 0.5f ), "detail 1 is left out in Low" );

	// level 0: only segments with no detail
	Expect( !BSE_SegmentDetailCulled( 0.0f, 0.0f ), "no detail draws at level 0" );
	Expect( BSE_SegmentDetailCulled( 0.1f, 0.0f ), "any detail is left out at level 0" );
	Expect( !BSE_SegmentDetailCulled( -1.0f, 0.0f ), "a negative detail counts as none" );

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: effect segments are left out by their detail against bse_detailLevel\n" );
	return 0;
}

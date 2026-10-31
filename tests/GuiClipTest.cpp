// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: GuiClipTest.cpp
GuiClipWindingToBounds (src/renderer/GuiClip.h), which cuts a GUI draw to a
window's clip rectangle or to its mask's range: a quad inside is kept whole, a
quad outside is dropped, and a quad that overhangs any combination of sides is
cut to exactly the overlap, with its texture coordinates interpolated.
idWinding2D::ClipByBounds, used before, kept nothing that was inside a clip
rectangle and made a triangle of a quad overhanging two opposite sides.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "GuiClip.h"

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

// a quad from (x0, y0) to (x1, y1) with s, t running 0..1 across it
static idWinding2D Quad( float x0, float y0, float x1, float y1 ) {
	idWinding2D w;
	w.AddPoint( x0, y0, 0.0f, 0.0f );
	w.AddPoint( x1, y0, 1.0f, 0.0f );
	w.AddPoint( x1, y1, 1.0f, 1.0f );
	w.AddPoint( x0, y1, 0.0f, 1.0f );
	return w;
}

static float Area( const idWinding2D& w ) {
	float area = 0.0f;
	for ( int i = 0; i < w.GetNumPoints(); i++ ) {
		const idVec2& a = w[ i ];
		const idVec2& b = w[ ( i + 1 ) % w.GetNumPoints() ];
		area += a.x * b.y - b.x * a.y;
	}
	return std::fabs( area ) * 0.5f;
}

// every point within the bounds, and s, t matching the point's place in the
// original quad from (x0, y0) to (x1, y1)
static bool Consistent( const idWinding2D& w, const sdBounds2D& b, float x0, float y0, float x1, float y1 ) {
	for ( int i = 0; i < w.GetNumPoints(); i++ ) {
		const idVec2& p = w[ i ];
		const idVec2& st = w.GetST( i );
		if ( p.x < b.GetMins().x - 1e-3f || p.x > b.GetMaxs().x + 1e-3f ||
			p.y < b.GetMins().y - 1e-3f || p.y > b.GetMaxs().y + 1e-3f ) {
			return false;
		}
		if ( !Near( st.x, ( p.x - x0 ) / ( x1 - x0 ) ) || !Near( st.y, ( p.y - y0 ) / ( y1 - y0 ) ) ) {
			return false;
		}
	}
	return true;
}

static void Check( const char* name, float x0, float y0, float x1, float y1, float expectedArea ) {
	const sdBounds2D bounds( 0.0f, 0.0f, 50.0f, 50.0f );
	idWinding2D w = Quad( x0, y0, x1, y1 );
	const bool kept = GuiClipWindingToBounds( w, bounds );
	char what[ 256 ];
	if ( expectedArea == 0.0f ) {
		idStr::snPrintf( what, sizeof( what ), "%s: dropped", name );
		Expect( !kept, what );
		return;
	}
	idStr::snPrintf( what, sizeof( what ), "%s: kept with area %.1f (got %s, area %.1f, %d points)", name, expectedArea,
		kept ? "kept" : "dropped", Area( w ), w.GetNumPoints() );
	Expect( kept && Near( Area( w ), expectedArea ), what );
	idStr::snPrintf( what, sizeof( what ), "%s: points inside the bounds, texture coordinates interpolated", name );
	Expect( !kept || Consistent( w, bounds, x0, y0, x1, y1 ), what );
}

int main() {
	// bounds 0..50 x 0..50
	Check( "inside", 10, 10, 40, 40, 900 );
	Check( "equal to the bounds", 0, 0, 50, 50, 2500 );
	Check( "outside", 60, 10, 90, 40, 0 );
	Check( "touching an edge from outside", 50, 10, 80, 40, 0 );
	Check( "overhanging the left", -10, 10, 40, 40, 1200 );
	Check( "overhanging the left and top", -10, -10, 40, 40, 1600 );
	Check( "overhanging the left and right", -10, 10, 100, 30, 1000 );
	Check( "overhanging the top and bottom", 10, -10, 30, 100, 1000 );
	Check( "on the left edge, overhanging top and bottom", 0, -10, 20, 100, 1000 );
	Check( "covering the bounds", -10, -10, 100, 100, 2500 );
	Check( "a vertex on each edge, overhanging all sides", -10, 0, 60, 50, 2500 );

	// a mask range far larger (the 1e6 'unbounded' of ClipToMask) keeps the quad whole
	{
		idWinding2D w = Quad( 10, 10, 40, 40 );
		Expect( GuiClipWindingToBounds( w, sdBounds2D( -1.0e6f, 20.0f, 2.0e6f, 1.0e6f ) ) && Near( Area( w ), 600 ),
			"an unbounded axis: cut only on the other" );
	}
	// a 32-sided circle overhanging one edge keeps every point it has inside
	{
		idWinding2D w;
		for ( int i = 0; i < idWinding2D::MAX_POINTS; i++ ) {
			const float a = i * idMath::TWO_PI / idWinding2D::MAX_POINTS;
			w.AddPoint( 25.0f + 30.0f * idMath::Cos( a ), 25.0f + 20.0f * idMath::Sin( a ), 0.0f, 0.0f );
		}
		const bool kept = GuiClipWindingToBounds( w, sdBounds2D( 0, 0, 50, 50 ) );
		bool inside = true;
		for ( int i = 0; i < w.GetNumPoints(); i++ ) {
			inside &= w[ i ].x >= -1e-3f && w[ i ].x <= 50.001f;
		}
		Expect( kept && inside && w.GetNumPoints() >= 8, "a circle overhanging left and right: cut to the bounds" );
	}
	// a concave star with many points outside is dropped, not written past the arrays
	{
		idWinding2D w;
		for ( int i = 0; i < idWinding2D::MAX_POINTS; i++ ) {
			const float a = i * idMath::TWO_PI / idWinding2D::MAX_POINTS;
			const float r = ( i & 1 ) ? 10.0f : 80.0f;
			w.AddPoint( 25.0f + r * idMath::Cos( a ), 25.0f + r * idMath::Sin( a ), 0.0f, 0.0f );
		}
		GuiClipWindingToBounds( w, sdBounds2D( 0, 0, 50, 50 ) );
		Expect( w.GetNumPoints() <= idWinding2D::MAX_POINTS, "a concave star: at most MAX_POINTS points" );
	}
	// a winding of fewer than three points is never drawn
	{
		idWinding2D w;
		w.AddPoint( 10, 10, 0, 0 );
		w.AddPoint( 20, 20, 1, 1 );
		Expect( !GuiClipWindingToBounds( w, sdBounds2D( 0, 0, 50, 50 ) ), "two points: dropped" );
	}

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: GUI clipping keeps exactly the overlap\n" );
	return 0;
}

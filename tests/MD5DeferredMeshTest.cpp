// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: MD5DeferredMeshTest.cpp
R_CompactDeferredMeshIndexes (src/renderer/MD5DeferredMesh.h, #461): a deferred
mesh of a binary MD5 model gets its own vertex list, the source vertices its
indexes use in order of first use, and its indexes renumbered into it, so it can
be drawn (and hidden) as its own surface.
===========================================================================
*/

#include "MD5DeferredMesh.h"

#include <cstdio>

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

int main() {
	int sourceOf[ 16 ];
	int compact[ 16 ];
	int remap[ 16 ];

	{
		// two triangles sharing an edge, high in the source list
		const unsigned short indexes[] = { 7, 9, 8, 9, 7, 10 };
		const int n = R_CompactDeferredMeshIndexes( indexes, 6, 12, sourceOf, compact, remap );
		Expect( n == 4, "four distinct vertices" );
		Expect( sourceOf[ 0 ] == 7 && sourceOf[ 1 ] == 9 && sourceOf[ 2 ] == 8 && sourceOf[ 3 ] == 10,
			"compact vertices in order of first use" );
		const int want[] = { 0, 1, 2, 1, 0, 3 };
		bool same = true;
		for ( int i = 0; i < 6; i++ ) {
			same = same && compact[ i ] == want[ i ];
			// every renumbered index names the same source vertex
			same = same && sourceOf[ compact[ i ] ] == indexes[ i ];
		}
		Expect( same, "indexes renumbered onto the same source vertices" );
	}
	{
		// the scratch is cleared on each call: a second mesh does not inherit the first's map
		const unsigned short first[] = { 0, 1, 2 };
		R_CompactDeferredMeshIndexes( first, 3, 6, sourceOf, compact, remap );
		const unsigned short second[] = { 2, 3, 4 };
		const int n = R_CompactDeferredMeshIndexes( second, 3, 6, sourceOf, compact, remap );
		Expect( n == 3 && compact[ 0 ] == 0 && sourceOf[ 0 ] == 2, "each mesh numbered from zero" );
	}
	{
		const unsigned short outside[] = { 0, 1, 6 };
		Expect( R_CompactDeferredMeshIndexes( outside, 3, 6, sourceOf, compact, remap ) == -1,
			"an index past the source vertices is refused" );
	}
	{
		Expect( R_CompactDeferredMeshIndexes( NULL, 0, 6, sourceOf, compact, remap ) == 0, "no indexes, no vertices" );
		Expect( R_CompactDeferredMeshIndexes( NULL, -1, 6, sourceOf, compact, remap ) == -1, "a negative count is refused" );
	}

	if ( failures == 0 ) {
		std::printf( "md5-deferred-mesh: all checks passed\n" );
		return 0;
	}
	return 1;
}

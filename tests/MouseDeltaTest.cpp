// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: MouseDeltaTest.cpp
Sub-unit mouse motion (src/sys/sdl/MouseDelta.h, #141): SDL3's float deltas
are summed with the carried remainder and only whole units are sent, so slow
motion made of sub-unit events is not lost. Truncating each event on its own
(the old code) sent nothing for 0.3 x 10.
===========================================================================
*/

#include "MouseDelta.h"

#include <cmath>
#include <cstdio>
#include <limits>

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

// the sum of the whole units sent for count events of delta
static int Feed( float delta, int count, float& remainder ) {
	int total = 0;
	for ( int i = 0; i < count; i++ ) {
		total += Sys_ConsumeMouseDelta( delta, remainder );
	}
	return total;
}

int main() {
	float r = 0.0f;

	// the issue's acceptance case: 0.3 per event for 10 events gives 3 units, not 0
	Expect( Feed( 0.3f, 10, r ) == 3, "0.3 x 10 sends 3" );
	Expect( std::fabs( r ) < 1e-4f, "0.3 x 10 leaves no remainder" );

	// the old per-event truncation, for contrast
	int truncated = 0;
	for ( int i = 0; i < 10; i++ ) {
		truncated += static_cast< int >( 0.3f );
	}
	Expect( truncated == 0, "per-event truncation sends nothing (the bug)" );

	// negative motion carries the same way, toward zero
	r = 0.0f;
	Expect( Feed( -0.25f, 8, r ) == -2, "-0.25 x 8 sends -2" );

	// mixed directions cancel instead of drifting
	r = 0.0f;
	int sum = 0;
	for ( int i = 0; i < 100; i++ ) {
		sum += Sys_ConsumeMouseDelta( ( i & 1 ) ? -0.7f : 0.7f, r );
	}
	Expect( sum == 0, "+0.7/-0.7 alternating sends 0" );

	// a long slow drag: nothing is lost apart from the final fraction
	r = 0.0f;
	const int sent = Feed( 0.1f, 1000, r );
	Expect( sent == 99 || sent == 100, "0.1 x 1000 sends 100 (99 with float rounding)" );
	Expect( std::fabs( static_cast< float >( sent ) + r - 100.0f ) < 0.01f, "sent + remainder is the motion" );

	// whole deltas pass through unchanged
	r = 0.0f;
	Expect( Sys_ConsumeMouseDelta( 5.0f, r ) == 5 && r == 0.0f, "5.0 sends 5" );
	Expect( Sys_ConsumeMouseDelta( -12.0f, r ) == -12 && r == 0.0f, "-12.0 sends -12" );

	// a non-finite delta or remainder drops the remainder and sends nothing
	r = 0.5f;
	Expect( Sys_ConsumeMouseDelta( std::numeric_limits< float >::quiet_NaN(), r ) == 0 && r == 0.0f, "NaN delta resets" );
	r = std::numeric_limits< float >::infinity();
	Expect( Sys_ConsumeMouseDelta( 1.0f, r ) == 0 && r == 0.0f, "infinite remainder resets" );

	// a huge delta is clamped, so the int conversion stays defined
	r = 0.0f;
	Expect( Sys_ConsumeMouseDelta( 1e12f, r ) == 32767, "1e12 clamps to 32767" );
	r = 0.0f;
	Expect( Sys_ConsumeMouseDelta( -1e12f, r ) == -32767, "-1e12 clamps to -32767" );

	if ( failures == 0 ) {
		std::printf( "mouse-delta: all checks passed\n" );
		return 0;
	}
	return 1;
}

// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: SoundFadeArgumentsTest.cpp
Snd_FadeArgumentsFinite (src/sound/SoundFadeArguments.h, #458): a fade to a
non-finite volume or over a non-finite time is refused, so the mixer never
computes inf - inf into a NaN volume (#453).
===========================================================================
*/

#include "SoundFadeArguments.h"

#include <cstdio>
#include <limits>

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

int main() {
	const float inf = std::numeric_limits< float >::infinity();
	const float nan = std::numeric_limits< float >::quiet_NaN();

	Expect( Snd_FadeArgumentsFinite( 0.0f, 0.5f ), "the game's per-frame fade (0 dB over 0.5 s)" );
	Expect( Snd_FadeArgumentsFinite( -60.0f, 0.0f ), "a fade to silence at once" );
	Expect( Snd_FadeArgumentsFinite( 12.0f, 3.0f ), "a fade above 0 dB" );
	Expect( !Snd_FadeArgumentsFinite( -inf, 1.0f ), "a fade to -inf dB" );
	Expect( !Snd_FadeArgumentsFinite( inf, 1.0f ), "a fade to +inf dB" );
	Expect( !Snd_FadeArgumentsFinite( nan, 1.0f ), "a fade to NaN dB" );
	Expect( !Snd_FadeArgumentsFinite( 0.0f, inf ), "a fade over infinite time" );
	Expect( !Snd_FadeArgumentsFinite( 0.0f, nan ), "a fade over NaN seconds" );

	if ( failures == 0 ) {
		std::printf( "sound-fade-arguments: all checks passed\n" );
		return 0;
	}
	return 1;
}

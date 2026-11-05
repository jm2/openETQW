// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: EffectGameClockTest.cpp
sdEffectGameClock (src/renderer/EffectGameClock.h): when a stopped, non-looping effect
is over, by game time alone (#433). The stop time came from the last drawn frame, so the
game's removal of the effect (a game event) moved with the frame schedule.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "../src/renderer/EffectGameClock.h"

#include <cstdio>

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

static int failures = 0;

static bool Near( float a, float b ) {
	return idMath::Fabs( a - b ) < 1e-5f;
}

static void Expect( bool ok, const char* what ) {
	std::printf( "  %s %s\n", ok ? "ok  " : "FAIL", what );
	if ( !ok ) {
		failures++;
	}
}

int main() {
	const float duration = 0.5f;

	// a stop between two ticks takes the last tick's game time, however many frames
	// were drawn since: the clock is never given a drawn frame's time
	{
		sdEffectGameClock clock;
		clock.Start( 1000 );
		clock.Update( 1033 );
		clock.Stop( 0.0f );
		Expect( clock.IsStopped() && Near( clock.GetStopTime(), 1.033f ), "a stop takes the last game time (1.033 s)" );
		Expect( !clock.StoppedEffectOver( 1.033f + duration, duration ), "not over at stop + duration" );
		Expect( clock.StoppedEffectOver( 1.533f + 0.001f, duration ), "over just after stop + duration" );
	}

	// two hosts with different frame schedules give the effect the same game times, so
	// they decide its removal at the same tick
	{
		sdEffectGameClock fast, slow;
		fast.Start( 2000 );
		slow.Start( 2000 );
		for ( int t = 2033; t <= 2099; t += 33 ) {
			fast.Update( t );	// many drawn frames per tick change nothing here
			slow.Update( t );
		}
		fast.Stop( 0.0f );
		slow.Stop( 0.0f );
		int fastOver = -1, slowOver = -1;
		for ( int t = 2132; t < 4000 && ( fastOver < 0 || slowOver < 0 ); t += 33 ) {
			fast.Update( t );
			slow.Update( t );
			if ( fastOver < 0 && fast.StoppedEffectOver( t * 0.001f, duration ) ) fastOver = t;
			if ( slowOver < 0 && slow.StoppedEffectOver( t * 0.001f, duration ) ) slowOver = t;
		}
		Expect( fastOver > 0 && fastOver == slowOver, "both remove it at the same tick" );
	}

	// stopped before the game updated it: the start time, not "unknown"
	{
		sdEffectGameClock clock;
		clock.Start( 500 );
		clock.Stop( 0.0f );
		Expect( Near( clock.GetStopTime(), 0.5f ), "a stop before any update takes the start time" );
	}

	// the effect's time offset applies, as to its other times
	{
		sdEffectGameClock clock;
		clock.Start( 1000 );
		clock.Stop( 0.25f );
		Expect( Near( clock.GetStopTime(), 1.25f ), "the time offset is added (1.25 s)" );
	}

	// a stop at game time 0 is still a stop
	{
		sdEffectGameClock clock;
		clock.Start( 0 );
		clock.Stop( 0.0f );
		Expect( clock.GetStopTime() > 0.0f && clock.StoppedEffectOver( duration + 0.01f, duration ),
			"a stop at time 0 still ends the effect" );
	}

	// a restart clears the stop
	{
		sdEffectGameClock clock;
		clock.Start( 1000 );
		clock.Stop( 0.0f );
		clock.Restart();
		Expect( !clock.IsStopped() && !clock.StoppedEffectOver( 100.0f, duration ), "a restarted effect isn't over" );
	}

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: a stopped effect ends by game time, whatever the frame schedule\n" );
	return 0;
}

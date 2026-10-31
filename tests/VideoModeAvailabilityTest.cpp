// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: VideoModeAvailabilityTest.cpp
Sys_MarkAvailableVideoModes (src/sys/sdl/VideoModeAvailability.h), which marks
the r_mode table's modes the display lists as available. The game's fullscreen
mode list shows only those (#383); none were marked, so the list was empty.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "VideoModeAvailability.h"

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

int main() {
	vidmode_t modes[] = {
		{ "Mode  3: 640x480",   640,  480, 0, false },
		{ "Mode  5: 1024x768", 1024,  768, 0, false },
		{ "Mode 16: 1920x1080",1920, 1080, 1, false },
		{ "Mode 17: 2560x1440",2560, 1440, 1, false },
		{ "Mode 23: 2560x1600",2560, 1600, 2, false },
	};
	const int numModes = sizeof( modes ) / sizeof( modes[0] );

	// a 1080p monitor: its modes, a refresh rate apart, list a size more than once
	{
		const int display[] = { 1920, 1080, 1920, 1080, 1024, 768, 800, 600 };
		const int available = Sys_MarkAvailableVideoModes( modes, numModes, display, 4 );
		Expect( available == 2, "1080p display: two table modes available" );
		Expect( !modes[0].available, "1080p display: 640x480 is not listed" );
		Expect( modes[1].available, "1080p display: 1024x768 is available" );
		Expect( modes[2].available, "1080p display: 1920x1080 is available" );
		Expect( !modes[3].available && !modes[4].available, "1080p display: nothing larger" );
	}
	// a scaled display (macOS): its point sizes and pixel sizes are both passed
	{
		const int display[] = { 1280, 800, 2560, 1600, 1440, 900, 2880, 1800 };
		const int available = Sys_MarkAvailableVideoModes( modes, numModes, display, 4 );
		Expect( available == 1, "scaled display: one table mode available" );
		Expect( modes[4].available, "scaled display: 2560x1600 (pixels) is available" );
		Expect( !modes[2].available, "scaled display: the earlier 1080p marks are cleared" );
	}
	// no display modes (a headless video driver): nothing is available
	{
		const int available = Sys_MarkAvailableVideoModes( modes, numModes, NULL, 0 );
		Expect( available == 0, "no display modes: nothing available" );
		bool any = false;
		for ( int i = 0; i < numModes; i++ ) {
			any |= modes[i].available;
		}
		Expect( !any, "no display modes: every flag cleared" );
	}

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: the display's modes are marked available\n" );
	return 0;
}

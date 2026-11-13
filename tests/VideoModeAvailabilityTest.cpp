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

	// the nearest of the menu's four aspects (#455)
	{
		Expect( Sys_NearestVideoModeAspect( 3840, 2160 ) == 1, "aspect: 3840x2160 is 16:9" );
		Expect( Sys_NearestVideoModeAspect( 3456, 2234 ) == 2, "aspect: 3456x2234 (a MacBook Pro panel) is 16:10" );
		Expect( Sys_NearestVideoModeAspect( 3440, 1440 ) == 1, "aspect: 3440x1440 (ultrawide) is 16:9, the widest" );
		Expect( Sys_NearestVideoModeAspect( 2048, 1536 ) == 0, "aspect: 2048x1536 is 4:3" );
		Expect( Sys_NearestVideoModeAspect( 1280, 1024 ) == 3, "aspect: 1280x1024 is 5:4" );
		Expect( Sys_NearestVideoModeAspect( 0, 1080 ) == 0, "aspect: no size gives 4:3" );
	}
	// the display's native size becomes a mode when the table lacks it (#455)
	{
		vidmode_t table[] = {
			{ "Mode  0: 1920x1080", 1920, 1080, 1, false },
			{ "Mode  1: 3840x2160", 3840, 2160, 1, true },
			{ "", 0, 0, 0, true },
		};
		char description[ 48 ] = { 0 };
		int num = Sys_AddNativeVideoMode( table, 2, description, sizeof( description ), 3840, 2160 );
		Expect( num == 2, "native: a size the table has adds nothing" );
		num = Sys_AddNativeVideoMode( table, 2, description, sizeof( description ), 0, 0 );
		Expect( num == 2, "native: no display size adds nothing" );
		num = Sys_AddNativeVideoMode( table, 2, description, sizeof( description ), 3456, 2234 );
		Expect( num == 3, "native: 3456x2234 is added" );
		Expect( table[2].width == 3456 && table[2].height == 2234, "native: the new mode has the display's size" );
		Expect( table[2].aspectRatio == 2, "native: the new mode is 16:10" );
		Expect( !table[2].available, "native: availability is left to the display's mode list" );
		Expect( table[2].description == description && idStr::Cmp( description, "Mode  2: 3456x2234 (native)" ) == 0,
			"native: described as the next mode, (native)" );
		const int display[] = { 3456, 2234 };
		const int marked = Sys_MarkAvailableVideoModes( table, num, display, 1 );
		Expect( marked == 1 && table[2].available, "native: marked available when the display lists it" );
	}

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: the display's modes are marked available\n" );
	return 0;
}

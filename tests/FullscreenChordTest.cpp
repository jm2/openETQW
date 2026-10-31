// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: FullscreenChordTest.cpp
The fullscreen chord (src/sys/sdl/FullscreenChord.h): Alt+Return on Windows and
Linux, as retail; Command+Return on macOS (docs/compat-ledger.md 081), where
Option+Return does nothing.
===========================================================================
*/

#include "FullscreenChord.h"

#include <cstdio>

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

int main() {
	// Windows and Linux: Alt+Return
	Expect( Sys_IsFullscreenChord( true, true, false, FSCHORD_ALT_RETURN ), "Alt+Return toggles (Alt style)" );
	Expect( !Sys_IsFullscreenChord( true, false, true, FSCHORD_ALT_RETURN ), "Command+Return does not (Alt style)" );
	Expect( !Sys_IsFullscreenChord( true, false, false, FSCHORD_ALT_RETURN ), "Return alone does not (Alt style)" );
	Expect( !Sys_IsFullscreenChord( false, true, false, FSCHORD_ALT_RETURN ), "Alt with another key does not (Alt style)" );

	// macOS: Command+Return
	Expect( Sys_IsFullscreenChord( true, false, true, FSCHORD_COMMAND_RETURN ), "Command+Return toggles (Command style)" );
	Expect( !Sys_IsFullscreenChord( true, true, false, FSCHORD_COMMAND_RETURN ), "Option+Return does not (Command style)" );
	Expect( !Sys_IsFullscreenChord( true, false, false, FSCHORD_COMMAND_RETURN ), "Return alone does not (Command style)" );
	Expect( !Sys_IsFullscreenChord( false, false, true, FSCHORD_COMMAND_RETURN ), "Command with another key does not (Command style)" );

	// this build's platform
#if defined( __APPLE__ )
	Expect( SYS_FULLSCREEN_CHORD == FSCHORD_COMMAND_RETURN, "macOS builds use Command+Return" );
	Expect( Sys_IsFullscreenChord( true, false, true ) && !Sys_IsFullscreenChord( true, true, false ),
		"the default on macOS: Command+Return, not Option+Return" );
#else
	Expect( SYS_FULLSCREEN_CHORD == FSCHORD_ALT_RETURN, "Windows and Linux builds use Alt+Return" );
	Expect( Sys_IsFullscreenChord( true, true, false ) && !Sys_IsFullscreenChord( true, false, true ),
		"the default on Windows and Linux: Alt+Return, not Win/Super+Return" );
#endif

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: the fullscreen chord is Alt+Return, or Command+Return on macOS\n" );
	return 0;
}

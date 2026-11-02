// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: TextInputFocusTest.cpp
When SDL text input is on, and the keys an input method's open composition owns
(src/sys/sdl/TextInputFocus.h, #394).
===========================================================================
*/

#include "TextInputFocus.h"

#include <cstdio>

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

int main() {
	// text input: only while something takes typed text
	Expect( !Sys_WantTextInput( false, false ), "off in play" );
	Expect( Sys_WantTextInput( true, false ), "on with the console open" );
	Expect( Sys_WantTextInput( false, true ), "on with a GUI edit box focused" );
	Expect( Sys_WantTextInput( true, true ), "on with both" );

	// no composition: every key passes
	{
		sdTextInputComposition c;
		Expect( !c.IsComposing(), "starts closed" );
		Expect( !c.DropKey( COMPKEY_BACKSPACE, true ) && !c.DropKey( COMPKEY_BACKSPACE, false ), "Backspace passes without a composition" );
		Expect( !c.DropKey( COMPKEY_ENTER, true ) && !c.DropKey( COMPKEY_ENTER, false ), "Enter passes without a composition" );
		Expect( !c.DropKey( COMPKEY_NONE, true ), "other keys pass" );
	}

	// while composing: the owned keys' key-downs are dropped, and their key-ups with them
	{
		sdTextInputComposition c;
		c.OnEditing( true );
		Expect( c.IsComposing(), "a non-empty editing text opens it" );
		Expect( c.DropKey( COMPKEY_BACKSPACE, true ), "Backspace down dropped while composing" );
		Expect( c.DropKey( COMPKEY_BACKSPACE, true ), "a repeated Backspace down dropped" );
		Expect( c.DropKey( COMPKEY_ENTER, true ), "Enter down dropped while composing" );
		Expect( c.DropKey( COMPKEY_KP_ENTER, true ), "keypad Enter down dropped while composing" );
		Expect( c.DropKey( COMPKEY_ESCAPE, true ), "Escape down dropped while composing" );
		Expect( !c.DropKey( COMPKEY_NONE, true ), "a letter passes while composing" );
		Expect( !c.DropKey( COMPKEY_COUNT, true ), "an out-of-range key passes" );
		// the input method commits on Enter, then the key comes up
		c.OnCommit();
		Expect( !c.IsComposing(), "the committed text closes it" );
		Expect( c.DropKey( COMPKEY_ENTER, false ), "Enter's key-up dropped with its key-down" );
		Expect( !c.DropKey( COMPKEY_ENTER, false ), "a second Enter key-up passes" );
		Expect( !c.DropKey( COMPKEY_ENTER, true ), "the next Enter passes" );
		Expect( !c.DropKey( COMPKEY_ENTER, false ), "and its key-up" );
	}

	// Backspace that empties the composition: dropped; the empty editing text closes it
	{
		sdTextInputComposition c;
		c.OnEditing( true );
		Expect( c.DropKey( COMPKEY_BACKSPACE, true ), "the last Backspace in a composition dropped" );
		c.OnEditing( false );
		Expect( !c.IsComposing(), "an empty editing text closes it" );
		Expect( c.DropKey( COMPKEY_BACKSPACE, false ), "its key-up dropped" );
		Expect( !c.DropKey( COMPKEY_BACKSPACE, true ), "the next Backspace reaches the field" );
	}

	// a key held from before the composition keeps its key-up
	{
		sdTextInputComposition c;
		Expect( !c.DropKey( COMPKEY_ESCAPE, true ), "Escape down before composing passes" );
		c.OnEditing( true );
		Expect( !c.DropKey( COMPKEY_ESCAPE, false ), "its key-up passes while composing" );
		c.OnStop();
		Expect( !c.IsComposing(), "stopping text input closes it" );
	}

	if ( failures == 0 ) {
		std::printf( "PASS: text input focus and composition keys\n" );
	}
	return failures == 0 ? 0 : 1;
}

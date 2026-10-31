// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: KeyCharTest.cpp
The character a key types on the SDL keyboard (src/sys/sdl/KeyChar.h, #366).
idKeyboard::KeyNumToString names a key by that character when it is non-zero and
by its localized name (engine/keys/f3) otherwise. The SDL keyboard cast the key
number to char, so F3 (0x97) and every other special key got a negative char that
no wide conversion prints: the HUD drew an empty box for the bound key.
===========================================================================
*/

#include "KeyChar.h"
#include "keynum.h"

#include <cstdio>

static int failures = 0;

static void Expect( bool ok, const char* what, int value ) {
	if ( !ok ) {
		std::printf( "FAIL: %s (0x%02X)\n", what, value );
		failures++;
	}
}

int main() {
	// printable ASCII keys type themselves
	Expect( Sys_KeyNumToChar( K_A ) == 'a', "K_A types 'a'", K_A );
	Expect( Sys_KeyNumToChar( K_R ) == 'r', "K_R types 'r'", K_R );
	Expect( Sys_KeyNumToChar( K_1 ) == '1', "K_1 types '1'", K_1 );
	Expect( Sys_KeyNumToChar( K_EQUALS ) == '=', "K_EQUALS types '='", K_EQUALS );
	Expect( Sys_KeyNumToChar( K_BACKQUOTE ) == '`', "K_BACKQUOTE types '`'", K_BACKQUOTE );

	// control and special keys type nothing, so they get their localized names
	const int special[] = { K_INVALID, K_BACKSPACE, K_TAB, K_ENTER, K_ESCAPE, K_COMMAND, K_CAPSLOCK,
		K_UPARROW, K_LWIN, K_ALT, K_CTRL, K_SHIFT, K_INS, K_DEL, K_HOME, K_END,
		K_F1, K_F3, K_F12, K_F16, K_KP_HOME, K_KP_ENTER, K_KP_EQUALS, K_GRAVE_A, K_TILDE_N,
		K_PRINT_SCR, K_RIGHT_ALT, K_RIGHT_CTRL, K_CONSOLE };
	for ( int key : special ) {
		Expect( Sys_KeyNumToChar( key ) == '\0', "a special key types nothing", key );
	}
	// every key: either '\0' or a printable ASCII character, never a negative char
	for ( int key = K_INVALID; key < K_NUM_KEYS; key++ ) {
		const char ch = Sys_KeyNumToChar( key );
		Expect( ch == '\0' || ( ch >= 0x20 && ch <= 0x7E ), "a key types '\\0' or printable ASCII", key );
	}

	// characters map back to their keys; upper case to the lower-case key
	Expect( Sys_CharToKeyNum( 'a' ) == K_A, "'a' is K_A", 'a' );
	Expect( Sys_CharToKeyNum( 'R' ) == K_R, "'R' is K_R", 'R' );
	Expect( Sys_CharToKeyNum( '5' ) == K_5, "'5' is K_5", '5' );
	Expect( Sys_CharToKeyNum( '\x97' ) == K_INVALID, "a non-ASCII char is no key", 0x97 );
	Expect( Sys_CharToKeyNum( '\n' ) == K_INVALID, "a control char is no key", '\n' );

	if ( failures != 0 ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: only printable ASCII keys type a character\n" );
	return 0;
}

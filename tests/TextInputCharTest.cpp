// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: TextInputCharTest.cpp
The characters the SDL keyboard sends as SE_CHAR (src/sys/sdl/TextInputChar.h).
Edit fields take typed text, and Backspace as Ctrl+H, from SE_CHAR only. SDL sends
typed text as UTF-8 and never sends the control characters, so the SDL clients
could move the cursor in a name box but could neither type nor erase.
===========================================================================
*/

#include "TextInputChar.h"

#include <cstdio>

static int failures = 0;

static void Expect( bool ok, const char* what, int value ) {
	if ( !ok ) {
		std::printf( "FAIL: %s (got 0x%X)\n", what, value );
		failures++;
	}
}

// the characters of a UTF-8 string, -1 for each malformed byte, up to max of them
static int Decode( const char* text, int* out, int max, int maxChar = 0x10FFFF ) {
	int n = 0;
	for ( int ch = Sys_NextUTF8Char( text, maxChar ); ch != 0 && n < max; ch = Sys_NextUTF8Char( text, maxChar ) ) {
		out[n++] = ch;
	}
	return n;
}

int main() {
	// the control characters WM_CHAR gives, from key presses
	Expect( Sys_ControlCharForKey( SYS_SDLK_BACKSPACE, false ) == 8, "Backspace sends 8 (Ctrl+H)", Sys_ControlCharForKey( SYS_SDLK_BACKSPACE, false ) );
	Expect( Sys_ControlCharForKey( SYS_SDLK_TAB, false ) == 9, "Tab sends 9", Sys_ControlCharForKey( SYS_SDLK_TAB, false ) );
	Expect( Sys_ControlCharForKey( SYS_SDLK_RETURN, false ) == 13, "Return sends 13", Sys_ControlCharForKey( SYS_SDLK_RETURN, false ) );
	Expect( Sys_ControlCharForKey( SYS_SDLK_KP_ENTER, false ) == 13, "keypad Enter sends 13", Sys_ControlCharForKey( SYS_SDLK_KP_ENTER, false ) );
	Expect( Sys_ControlCharForKey( 'a', true ) == 1, "Ctrl+A sends 1", Sys_ControlCharForKey( 'a', true ) );
	Expect( Sys_ControlCharForKey( 'v', true ) == 22, "Ctrl+V sends 22 (paste)", Sys_ControlCharForKey( 'v', true ) );
	Expect( Sys_ControlCharForKey( 'z', true ) == 26, "Ctrl+Z sends 26", Sys_ControlCharForKey( 'z', true ) );

	// plain letters, digits and other keys send nothing here: their text comes from SDL_EVENT_TEXT_INPUT
	for ( unsigned int key = 0x20; key <= 0x7E; key++ ) {
		Expect( Sys_ControlCharForKey( key, false ) == 0, "a printable key without Ctrl sends no control character", static_cast< int >( key ) );
	}
	Expect( Sys_ControlCharForKey( '1', true ) == 0, "Ctrl+1 sends nothing", Sys_ControlCharForKey( '1', true ) );
	Expect( Sys_ControlCharForKey( 0x4000003Au, false ) == 0, "F1 sends nothing", Sys_ControlCharForKey( 0x4000003Au, false ) );

	// UTF-8 text
	int out[8];
	int n = Decode( "aZ~", out, 8 );
	Expect( n == 3 && out[0] == 'a' && out[1] == 'Z' && out[2] == '~', "ASCII decodes to itself", n );
	n = Decode( "\xC3\xA9", out, 8 );	// e acute
	Expect( n == 1 && out[0] == 0xE9, "a two-byte sequence decodes", n == 1 ? out[0] : n );
	n = Decode( "\xE2\x82\xAC", out, 8 );	// euro sign
	Expect( n == 1 && out[0] == 0x20AC, "a three-byte sequence decodes", n == 1 ? out[0] : n );
	n = Decode( "\xF0\x9F\x98\x80", out, 8 );	// emoji, outside the BMP
	Expect( n == 1 && out[0] == 0x1F600, "a four-byte sequence decodes", n == 1 ? out[0] : n );
	n = Decode( "\xF0\x9F\x98\x80", out, 8, 0xFFFF );
	Expect( n >= 1 && out[0] == -1, "outside the BMP is rejected where wchar_t is 16 bits", n >= 1 ? out[0] : n );
	n = Decode( "\xC3" "a", out, 8 );	// truncated sequence, then 'a'
	Expect( n == 2 && out[0] == -1 && out[1] == 'a', "a truncated sequence is skipped and the next character kept", n );
	n = Decode( "\xC0\xAF", out, 8 );	// overlong '/'
	Expect( n >= 1 && out[0] == -1, "an overlong form is rejected", n >= 1 ? out[0] : n );
	n = Decode( "\xED\xA0\x80", out, 8 );	// a UTF-16 surrogate
	Expect( n >= 1 && out[0] == -1, "a surrogate is rejected", n >= 1 ? out[0] : n );
	n = Decode( "\x80" "b", out, 8 );	// a stray continuation byte
	Expect( n == 2 && out[0] == -1 && out[1] == 'b', "a stray continuation byte is skipped", n );

	if ( failures != 0 ) {
		std::printf( "%d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: SDL key presses and UTF-8 text become the characters edit fields expect\n" );
	return 0;
}

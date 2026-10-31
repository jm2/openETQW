// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_SDL_TEXTINPUTCHAR_H__
#define __SYS_SDL_TEXTINPUTCHAR_H__

/*
The characters the SDL keyboard sends as SE_CHAR events. Edit fields (the console,
idEditField, and the game's GUI edit boxes in UIEditHelper.h) take typed text, and
Backspace as Ctrl+H (8), from SE_CHAR only, as Windows' WM_CHAR delivers them.

SDL sends typed text as UTF-8 in SDL_EVENT_TEXT_INPUT, and only after
SDL_StartTextInput. It never sends the control characters, so these come from key
presses: Backspace 8, Tab 9, Return and keypad Enter 13, and Ctrl+A..Ctrl+Z 1..26,
as WM_CHAR gives them.
*/

// SDL3 keycodes (SDL_keycode.h) these tests need; the ASCII keys' keycodes are their characters
const unsigned int SYS_SDLK_BACKSPACE = 0x08u;
const unsigned int SYS_SDLK_TAB = 0x09u;
const unsigned int SYS_SDLK_RETURN = 0x0Du;
const unsigned int SYS_SDLK_KP_ENTER = 0x40000058u;

// the control character a key-down types, or 0; ctrlOnly: Ctrl held without Alt or Command
inline int Sys_ControlCharForKey( unsigned int keycode, bool ctrlOnly ) {
	if ( keycode == SYS_SDLK_BACKSPACE ) {
		return 8;
	}
	if ( keycode == SYS_SDLK_TAB ) {
		return 9;
	}
	if ( keycode == SYS_SDLK_RETURN || keycode == SYS_SDLK_KP_ENTER ) {
		return 13;
	}
	if ( ctrlOnly && keycode >= 'a' && keycode <= 'z' ) {
		return static_cast< int >( keycode - 'a' ) + 1;
	}
	return 0;
}

/*
The next character of a UTF-8 string, advancing text past it; 0 at the end. A
malformed sequence, an overlong form, a surrogate or a code point above maxChar
(0xFFFF where wchar_t is 16 bits) gives -1 and skips one byte.
*/
inline int Sys_NextUTF8Char( const char*& text, int maxChar = 0x10FFFF ) {
	const unsigned char* p = reinterpret_cast< const unsigned char* >( text );
	if ( p[0] == 0 ) {
		return 0;
	}
	int length;
	int c;
	int min;
	if ( p[0] < 0x80 ) {
		text++;
		return p[0];
	} else if ( ( p[0] & 0xE0 ) == 0xC0 ) {
		length = 2; c = p[0] & 0x1F; min = 0x80;
	} else if ( ( p[0] & 0xF0 ) == 0xE0 ) {
		length = 3; c = p[0] & 0x0F; min = 0x800;
	} else if ( ( p[0] & 0xF8 ) == 0xF0 ) {
		length = 4; c = p[0] & 0x07; min = 0x10000;
	} else {
		text++;
		return -1;
	}
	for ( int i = 1; i < length; i++ ) {
		if ( ( p[i] & 0xC0 ) != 0x80 ) {
			text++;
			return -1;
		}
		c = ( c << 6 ) | ( p[i] & 0x3F );
	}
	if ( c < min || c > maxChar || ( c >= 0xD800 && c <= 0xDFFF ) ) {
		text++;
		return -1;
	}
	text += length;
	return c;
}

#endif /* !__SYS_SDL_TEXTINPUTCHAR_H__ */

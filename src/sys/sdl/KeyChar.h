// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_SDL_KEYCHAR_H__
#define __SYS_SDL_KEYCHAR_H__

/*
The character a key types, for the SDL keyboard (#366). keyNum_t (keynum.h) is ASCII
below 0x80 and special keys from there up (arrows, F keys, the keypad, the
Latin-1 accented keys). idKeyboard::KeyNumToString takes a non-zero character as the
key's display name and only falls back to its localized name (engine/keys/f3) when it
gets '\0'. Casting the key number straight to char gave the F keys and the rest a
negative char, which no wide conversion can print: the warmup hint drew "Press [box]
to ready up!" for F3. Only printable ASCII is a character here.
*/
inline char Sys_KeyNumToChar( int keyNum ) {
	return keyNum >= 0x20 && keyNum <= 0x7E ? static_cast< char >( keyNum ) : '\0';
}

// the key that types ch: printable ASCII only, upper case as the lower-case key; 0 (K_INVALID) otherwise
inline int Sys_CharToKeyNum( char ch ) {
	int c = static_cast< unsigned char >( ch );
	if ( c >= 'A' && c <= 'Z' ) {
		c += 'a' - 'A';
	}
	return c >= 0x20 && c <= 0x7E ? c : 0;
}

#endif /* !__SYS_SDL_KEYCHAR_H__ */

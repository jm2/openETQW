// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_SDL_FULLSCREENCHORD_H__
#define __SYS_SDL_FULLSCREENCHORD_H__

/*
The key chord that switches the game window between fullscreen and a window. On
Windows and Linux it is Alt+Return, as retail's. On macOS it is Command+Return, the
platform's own (docs/compat-ledger.md 081); Option+Return does nothing there.
*/
enum fullscreenChord_t {
	FSCHORD_ALT_RETURN,
	FSCHORD_COMMAND_RETURN
};

#if defined( __APPLE__ )
const fullscreenChord_t SYS_FULLSCREEN_CHORD = FSCHORD_COMMAND_RETURN;
#else
const fullscreenChord_t SYS_FULLSCREEN_CHORD = FSCHORD_ALT_RETURN;
#endif

// true for a Return key-down with the chord's modifier held
inline bool Sys_IsFullscreenChord( bool returnKey, bool altHeld, bool commandHeld,
		fullscreenChord_t chord = SYS_FULLSCREEN_CHORD ) {
	if ( !returnKey ) {
		return false;
	}
	return chord == FSCHORD_COMMAND_RETURN ? commandHeld : altHeld;
}

#endif /* !__SYS_SDL_FULLSCREENCHORD_H__ */

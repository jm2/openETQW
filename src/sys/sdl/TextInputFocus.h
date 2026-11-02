// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_SDL_TEXTINPUTFOCUS_H__
#define __SYS_SDL_TEXTINPUTFOCUS_H__

/*
When SDL text input, and with it any input method, is on (#394).

It is on only while something takes typed text: the console, or a GUI edit box with
the focus, which the game reports each frame through sdIME::Enable. In play it is off,
so an input method cannot take keys meant for binds. Retail did the same: its sdIME
was enabled only while an edit box had the focus.

While an input method has a composition open, the composition owns Backspace, Enter
and Escape. SDL's Cocoa backend sends their key-down before the input method sees the
key, so without this they would also act on the field: delete a character, send the
line, or close the chat. Those keys are dropped while composing, their key-up with
their key-down. The composition itself is not drawn; retail's
sdUIEditW::DrawComposition is compiled out as well.
*/

inline bool Sys_WantTextInput( bool consoleActive, bool editFocused ) {
	return consoleActive || editFocused;
}

// the keys an open composition owns
enum compositionKey_t {
	COMPKEY_NONE = -1,
	COMPKEY_BACKSPACE,
	COMPKEY_ENTER,
	COMPKEY_KP_ENTER,
	COMPKEY_ESCAPE,
	COMPKEY_COUNT
};

class sdTextInputComposition {
public:
	sdTextInputComposition() : composing( false ), droppedDown( 0 ) {}

	// SDL_EVENT_TEXT_EDITING: an empty text closes the composition
	void OnEditing( bool hasText ) { composing = hasText; }
	// SDL_EVENT_TEXT_INPUT commits it; stopping text input cancels it
	void OnCommit() { composing = false; }
	void OnStop() { composing = false; }
	bool IsComposing() const { return composing; }

	// true when the key event must not reach the game: an owned key's key-down while
	// composing, and later the key-up of a key-down dropped that way
	bool DropKey( compositionKey_t key, bool down ) {
		if ( key <= COMPKEY_NONE || key >= COMPKEY_COUNT ) {
			return false;
		}
		const unsigned int bit = 1u << key;
		if ( down ) {
			if ( composing ) {
				droppedDown |= bit;
				return true;
			}
			droppedDown &= ~bit;
			return false;
		}
		if ( ( droppedDown & bit ) != 0 ) {
			droppedDown &= ~bit;
			return true;
		}
		return false;
	}

private:
	bool composing;
	unsigned int droppedDown;
};

#endif /* !__SYS_SDL_TEXTINPUTFOCUS_H__ */

// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <SDL3/SDL.h>
#include "sys_local.h"
#include "keynum.h"
#include "KeyChar.h"
#include "FullscreenChord.h"
#include "MouseDelta.h"
#include "TextInputChar.h"
#include "TextInputFocus.h"

SDL_Window* Sys_GetGameSDLWindow( void );

/*
SDL3 sends SDL_EVENT_TEXT_INPUT only for a window that has called SDL_StartTextInput,
so without it no typed text reaches the console or the GUI edit boxes. It is on only
while the console is open or a GUI edit box has the focus (TextInputFocus.h, #394).
Called each time the events are read, and when the game window is created or gains
the focus.
*/
idCVar in_logTextInput( "in_logTextInput", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "log SDL text input: starting and stopping it, and each text input event" );

namespace {

// a GUI edit box has the focus; the game reports it each frame (sdIMESDL::Enable)
bool guiEditFocused = false;

// an input method's open composition (TextInputFocus.h)
sdTextInputComposition composition;

} // namespace

void Sys_UpdateSDLTextInput( void ) {
	SDL_Window* window = Sys_GetGameSDLWindow();
	if ( window == NULL ) {
		return;
	}
	const bool consoleActive = console != NULL && console->Active();
	const bool want = Sys_WantTextInput( consoleActive, guiEditFocused );
	if ( want == SDL_TextInputActive( window ) ) {
		return;
	}
	bool ok;
	if ( want ) {
		ok = SDL_StartTextInput( window );
	} else {
		ok = SDL_StopTextInput( window );
		composition.OnStop();
	}
	if ( in_logTextInput.GetBool() ) {
		common->Printf( "TEXT_INPUT: %s %s, active %d (driver %s), console %d, edit box %d%s%s\n", want ? "start" : "stop",
			ok ? "ok" : "FAILED", SDL_TextInputActive( window ) ? 1 : 0, SDL_GetCurrentVideoDriver(),
			consoleActive ? 1 : 0, guiEditFocused ? 1 : 0, ok ? "" : ": ", ok ? "" : SDL_GetError() );
	}
}

namespace {

// set by a console-key press, so the text input it types ('`' or '~') is dropped
bool swallowConsoleText = false;

// the sub-unit motion not yet sent (MouseDelta.h); dropped when the grab or focus changes
float mouseRemainderX = 0.0f;
float mouseRemainderY = 0.0f;

void ResetMouseRemainder() {
	mouseRemainderX = 0.0f;
	mouseRemainderY = 0.0f;
}

compositionKey_t CompositionKey( keyNum_t key ) {
	switch ( key ) {
		case K_BACKSPACE: return COMPKEY_BACKSPACE;
		case K_ENTER:     return COMPKEY_ENTER;
		case K_KP_ENTER:  return COMPKEY_KP_ENTER;
		case K_ESCAPE:    return COMPKEY_ESCAPE;
		default:          return COMPKEY_NONE;
	}
}

keyNum_t MapSDLScancode( SDL_Scancode scancode ) {
	if ( scancode == SDL_SCANCODE_GRAVE ) return K_CONSOLE;
	if ( scancode == SDL_SCANCODE_ESCAPE ) return K_ESCAPE;
	if ( scancode == SDL_SCANCODE_RETURN || scancode == SDL_SCANCODE_RETURN2 ) return K_ENTER;
	if ( scancode == SDL_SCANCODE_KP_ENTER ) return K_KP_ENTER;
	if ( scancode == SDL_SCANCODE_TAB ) return K_TAB;
	if ( scancode == SDL_SCANCODE_BACKSPACE ) return K_BACKSPACE;
	if ( scancode == SDL_SCANCODE_SPACE ) return K_SPACE;

	if ( scancode >= SDL_SCANCODE_A && scancode <= SDL_SCANCODE_Z ) {
		return static_cast< keyNum_t >( K_A + ( scancode - SDL_SCANCODE_A ) );
	}
	if ( scancode >= SDL_SCANCODE_1 && scancode <= SDL_SCANCODE_9 ) {
		return static_cast< keyNum_t >( K_1 + ( scancode - SDL_SCANCODE_1 ) );
	}
	if ( scancode == SDL_SCANCODE_0 ) return K_0;

	switch ( scancode ) {
		case SDL_SCANCODE_UP: return K_UPARROW;
		case SDL_SCANCODE_DOWN: return K_DOWNARROW;
		case SDL_SCANCODE_LEFT: return K_LEFTARROW;
		case SDL_SCANCODE_RIGHT: return K_RIGHTARROW;
		case SDL_SCANCODE_LCTRL: return K_CTRL;
		case SDL_SCANCODE_RCTRL: return K_RIGHT_CTRL;
		case SDL_SCANCODE_LSHIFT: return K_SHIFT;
		case SDL_SCANCODE_RSHIFT: return K_RIGHT_SHIFT;
		case SDL_SCANCODE_LALT: return K_ALT;
		case SDL_SCANCODE_RALT: return K_RIGHT_ALT;
		case SDL_SCANCODE_LGUI: return K_LWIN;
		case SDL_SCANCODE_RGUI: return K_RWIN;
		case SDL_SCANCODE_INSERT: return K_INS;
		case SDL_SCANCODE_DELETE: return K_DEL;
		case SDL_SCANCODE_PAGEUP: return K_PGUP;
		case SDL_SCANCODE_PAGEDOWN: return K_PGDN;
		case SDL_SCANCODE_HOME: return K_HOME;
		case SDL_SCANCODE_END: return K_END;
		case SDL_SCANCODE_CAPSLOCK: return K_CAPSLOCK;
		case SDL_SCANCODE_SCROLLLOCK: return K_SCROLL;
		case SDL_SCANCODE_PAUSE: return K_PAUSE;
		case SDL_SCANCODE_PRINTSCREEN: return K_PRINT_SCR;
		case SDL_SCANCODE_MINUS: return K_MINUS;
		case SDL_SCANCODE_EQUALS: return K_EQUALS;
		case SDL_SCANCODE_LEFTBRACKET: return K_LEFTBRACKET;
		case SDL_SCANCODE_RIGHTBRACKET: return K_RIGHTBRACKET;
		case SDL_SCANCODE_BACKSLASH: return K_BACKSLASH;
		case SDL_SCANCODE_SEMICOLON: return K_SEMICOLON;
		case SDL_SCANCODE_APOSTROPHE: return K_APOSTROPHE;
		case SDL_SCANCODE_COMMA: return K_COMMA;
		case SDL_SCANCODE_PERIOD: return K_PERIOD;
		case SDL_SCANCODE_SLASH: return K_SLASH;
		default: break;
	}

	if ( scancode >= SDL_SCANCODE_F1 && scancode <= SDL_SCANCODE_F12 ) {
		return static_cast< keyNum_t >( K_F1 + ( scancode - SDL_SCANCODE_F1 ) );
	}
	return K_INVALID;
}

class idKeyboardSDL : public idKeyboard {
public:
	idKeyboardSDL() : active( true ) {}
	virtual ~idKeyboardSDL() {}

	virtual bool Init() override { return true; }
	virtual void Shutdown() override {}
	virtual void Activate() override { active = true; }
	virtual void Deactivate() override { active = false; }
	virtual int PollInputEvents( bool ) override { return 0; }
	virtual int ReturnInputEvent( const int, keyNum_t&, bool& ) override { return 0; }
	virtual void EndInputEvents() override {}

	virtual keyNum_t ConvertScanToKey( unsigned int scanCode ) const override {
		return static_cast< keyNum_t >( scanCode );
	}
	virtual keyNum_t ConvertCharToKey( char ch ) const override {
		return static_cast< keyNum_t >( Sys_CharToKeyNum( ch ) );
	}
	virtual char ConvertScanToChar( unsigned int scanCode ) const override {
		return Sys_KeyNumToChar( static_cast< int >( ConvertScanToKey( scanCode ) ) );
	}
	virtual unsigned int ConvertCharToScan( char ch ) const override {
		return static_cast< unsigned int >( Sys_CharToKeyNum( ch ) );
	}
	virtual char ConvertKeyToChar( const keyNum_t keyNum ) const override {
		return Sys_KeyNumToChar( keyNum );
	}
	virtual bool IsConsoleKey( const sdSysEvent& event ) const override {
		return event.GetKey() == K_CONSOLE;
	}

private:
	bool active;
};

/*
Capture follows three requests (#382). The console releases the mouse while it is open
(GrabCursor), and the game reports each frame whether a menu cursor shows
(SetMenuCursor). The mouse is captured, in relative mode with the system pointer
hidden, only while a grab is requested and no menu cursor shows. In a menu the system
pointer moves freely and the menu cursor is placed under it (GetPointerPosition), with
the system pointer hidden over the window so that only the menu cursor shows there.
*/
class idMouseSDL : public idMouse {
public:
	idMouseSDL() : active( true ), grabRequested( false ), menuCursor( false ) {}
	virtual ~idMouseSDL() {}

	virtual bool Init() override { return true; }
	virtual void Shutdown() override {}
	virtual bool IsActive() const override { return active; }
	virtual void Activate() override { active = true; }
	virtual void Deactivate() override { active = false; }

	virtual void GrabCursor( bool grab ) override {
		grabRequested = grab;
		ApplyCapture();
	}

	virtual void SetMenuCursor( bool show ) override {
		if ( show != menuCursor ) {
			menuCursor = show;
			ApplyCapture();
		}
	}

	virtual bool GetPointerPosition( float& x, float& y ) const override {
		x = 0.0f;
		y = 0.0f;
		SDL_Window* window = Sys_GetGameSDLWindow();
		if ( window == NULL || SDL_GetWindowRelativeMouseMode( window ) || SDL_GetMouseFocus() != window ) {
			return false;
		}
		int width = 0;
		int height = 0;
		if ( !SDL_GetWindowSize( window, &width, &height ) || width <= 0 || height <= 0 ) {
			return false;
		}
		float px = 0.0f;
		float py = 0.0f;
		SDL_GetMouseState( &px, &py );	// in window coordinates, as the window size
		x = idMath::ClampFloat( 0.0f, 1.0f, px / static_cast< float >( width ) );
		y = idMath::ClampFloat( 0.0f, 1.0f, py / static_cast< float >( height ) );
		return true;
	}

	virtual int PollInputEvents( bool ) override { return 0; }
	virtual int ReturnInputEvent( const int, int&, int& ) override { return 0; }
	virtual void EndInputEvents() override {}

private:
	void ApplyCapture() {
		SDL_Window* window = Sys_GetGameSDLWindow();
		if ( window != NULL ) {
			const bool capture = grabRequested && !menuCursor;
			SDL_SetWindowRelativeMouseMode( window, capture );
			if ( capture || !menuCursor ) {
				SDL_ShowCursor();
			} else {
				SDL_HideCursor();	// over the window only: the menu cursor stands in for it
			}
		}
		ResetMouseRemainder();
	}

	bool active;
	bool grabRequested;
	bool menuCursor;
};

class sdIMESDL : public sdIME {
public:
	virtual ~sdIMESDL() {}
	virtual bool Init() override { return true; }
	virtual void Shutdown() override {}
	virtual bool LangSupportsIME() const override { return false; }
	// the game's GUIs: an edit box has the focus (idGameLocal::UpdateTextInputFocus)
	virtual void Enable( bool enable ) override { guiEditFocused = enable; }
	virtual bool IsEnabled() const override { return guiEditFocused; }
	virtual void FinalizeString( bool ) override {}
	virtual int GetCursorChars() const override { return 0; }
	virtual bool IsReadingWindowActive() const override { return false; }
	virtual bool IsHorizontalReading() const override { return false; }
	virtual bool VerticalCandidateLine() const override { return false; }
	virtual state_e GetState() const override { return IME_STATE_OFF; }
	virtual const wchar_t* GetIndicator() const override { return L""; }
	virtual bool IsCandidateListActive() const override { return false; }
	virtual const wchar_t* GetCandidate( const unsigned int ) const override { return L""; }
	virtual int GetCandidateCount() const override { return 0; }
	virtual int GetCandidateSelection() const override { return 0; }
	virtual const wchar_t* GetCompositionString() const override { return L""; }
	virtual const byte* GetCompositionStringAttributes() const override { return NULL; }
	virtual const lang_e GetLanguage() const override { return IME_LANG_NEUTRAL; }
	virtual const lang_e GetPrimaryLanguage() const override { return IME_LANG_NEUTRAL; }
};

idKeyboardSDL sdlKeyboard;
idMouseSDL    sdlMouse;
sdIMESDL      sdlIME;

} // namespace

void IN_Frame( void ) {
}

#ifndef ID_DEDICATED
idKeyboard& idSysLocal::Keyboard() {
	return sdlKeyboard;
}

idMouse& idSysLocal::Mouse() {
	return sdlMouse;
}

sdIME& idSysLocal::IME() {
	return sdlIME;
}
#endif

void Sys_ProcessSDLEvents( void ) {
	// before the events are read: SDL makes text events only while text input is on
	Sys_UpdateSDLTextInput();
	SDL_Event event;
	while ( SDL_PollEvent( &event ) ) {
		switch ( event.type ) {
			case SDL_EVENT_QUIT:
			case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
				cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "quit\n" );
				break;

			case SDL_EVENT_WINDOW_FOCUS_GAINED:
				Sys_UpdateSDLTextInput();
				sys->Keyboard().Activate();
				IN_Frame();
				if ( session != NULL ) {
					session->SetPlayingSoundWorld();
				}
				break;

			case SDL_EVENT_WINDOW_FOCUS_LOST:
				sys->Keyboard().Deactivate();
				ResetMouseRemainder();
				IN_Frame();
				break;

			case SDL_EVENT_WINDOW_RESIZED:
			case SDL_EVENT_WINDOW_PIXEL_SIZE_CHANGED:
				if ( sys3D != NULL && event.window.data1 > 0 && event.window.data2 > 0 ) {
					sys3D->WindowSizeDragged( event.window.data1, event.window.data2 );
				}
				break;

			case SDL_EVENT_KEY_DOWN:
			case SDL_EVENT_KEY_UP: {
				// Alt+Return, or Command+Return on macOS (FullscreenChord.h)
				if ( event.key.down && Sys_IsFullscreenChord( event.key.key == SDLK_RETURN,
						( event.key.mod & SDL_KMOD_ALT ) != 0, ( event.key.mod & SDL_KMOD_GUI ) != 0 ) ) {
					const bool fullscreen = sys3D != NULL && sys3D->IsFullscreen();
					cvarSystem->SetCVarBool( "r_fullscreen", !fullscreen );
					cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "vid_restart\n" );
					break;
				}

				keyNum_t key = MapSDLScancode( event.key.scancode );
				const bool textInputActive = SDL_TextInputActive( Sys_GetGameSDLWindow() );
				// Backspace, Enter and Escape belong to an open composition (TextInputFocus.h)
				if ( composition.DropKey( CompositionKey( key ), event.key.down ) ) {
					if ( in_logTextInput.GetBool() ) {
						common->Printf( "TEXT_INPUT: key %s scancode %d dropped: the composition owns it\n",
							event.key.down ? "down" : "up", static_cast< int >( event.key.scancode ) );
					}
					break;
				}
				if ( key != K_INVALID ) {
					sys->QueEvent( SE_KEY, key, event.key.down ? 1 : 0, 0, NULL );
				}
				if ( event.key.down && in_logTextInput.GetBool() ) {
					// the scancode only: no typed character reaches the log (passwords)
					common->Printf( "TEXT_INPUT: key down scancode %d mod 0x%x, text input active %d\n",
						static_cast< int >( event.key.scancode ), static_cast< unsigned int >( event.key.mod ),
						textInputActive ? 1 : 0 );
				}
				if ( event.key.down ) {
					// the console key toggles the console; the text it types must not land in it.
					// With text input off (in play) it types none, and the flag would eat the
					// first character typed into the console instead
					swallowConsoleText = key == K_CONSOLE && textInputActive;
					// Backspace, Tab, Enter and Ctrl+letters reach edit fields as characters (TextInputChar.h)
					const bool ctrlOnly = ( event.key.mod & SDL_KMOD_CTRL ) != 0 && ( event.key.mod & ( SDL_KMOD_ALT | SDL_KMOD_GUI ) ) == 0;
					// the layout's key without Shift or Caps Lock, so Ctrl+Shift+V is still Ctrl+V (22), as WM_CHAR
					const int ch = Sys_ControlCharForKey( SDL_GetKeyFromScancode( event.key.scancode, SDL_KMOD_NONE, true ), ctrlOnly );
					if ( ch != 0 ) {
						sys->QueEvent( SE_CHAR, 0, ch, 0, NULL );
					}
				}
				break;
			}

			case SDL_EVENT_TEXT_INPUT: {
				if ( in_logTextInput.GetBool() ) {
					// its length only: no typed text reaches the log (passwords)
					common->Printf( "TEXT_INPUT: event, %d bytes%s\n", event.text.text != NULL ? static_cast< int >( strlen( event.text.text ) ) : -1,
						swallowConsoleText ? " (dropped: the console key's text)" : "" );
				}
				if ( event.text.text != NULL && !swallowConsoleText ) {
					const char* p = event.text.text;
					const int maxChar = sizeof( wchar_t ) == 2 ? 0xFFFF : 0x10FFFF;
					for ( int ch = Sys_NextUTF8Char( p, maxChar ); ch != 0; ch = Sys_NextUTF8Char( p, maxChar ) ) {
						if ( ch > 0 ) {
							sys->QueEvent( SE_CHAR, 0, ch, 0, NULL );
						}
					}
				}
				swallowConsoleText = false;
				composition.OnCommit();
				break;
			}

			case SDL_EVENT_TEXT_EDITING: {
				const bool hasText = event.edit.text != NULL && event.edit.text[ 0 ] != '\0';
				if ( in_logTextInput.GetBool() && hasText != composition.IsComposing() ) {
					common->Printf( "TEXT_INPUT: composition %s\n", hasText ? "open" : "closed" );
				}
				composition.OnEditing( hasText );
				break;
			}

			case SDL_EVENT_MOUSE_MOTION: {
				if ( !sys->Mouse().IsActive() ) {
					ResetMouseRemainder();
					break;
				}
				const int dx = Sys_ConsumeMouseDelta( event.motion.xrel, mouseRemainderX );
				const int dy = Sys_ConsumeMouseDelta( event.motion.yrel, mouseRemainderY );
				if ( dx != 0 || dy != 0 ) {
					sys->QueEvent( SE_MOUSE, dx, dy, 0, NULL );
				}
				break;
			}

			case SDL_EVENT_MOUSE_BUTTON_DOWN:
			case SDL_EVENT_MOUSE_BUTTON_UP: {
				if ( !sys->Mouse().IsActive() ) {
					break;
				}
				mouseButton_t btn = M_INVALID;
				switch ( event.button.button ) {
					case SDL_BUTTON_LEFT:   btn = M_MOUSE1; break;
					case SDL_BUTTON_RIGHT:  btn = M_MOUSE2; break;
					case SDL_BUTTON_MIDDLE: btn = M_MOUSE3; break;
					case SDL_BUTTON_X1:     btn = M_MOUSE4; break;
					case SDL_BUTTON_X2:     btn = M_MOUSE5; break;
					default: break;
				}
				if ( btn != M_INVALID ) {
					sys->QueEvent( SE_MOUSE_BUTTON, btn, event.button.down ? 1 : 0, 0, NULL );
				}
				break;
			}

			case SDL_EVENT_MOUSE_WHEEL: {
				if ( !sys->Mouse().IsActive() || event.wheel.y == 0.0f ) {
					break;
				}
				mouseButton_t btn = event.wheel.y > 0.0f ? M_MWHEELUP : M_MWHEELDOWN;
				sys->QueEvent( SE_MOUSE_BUTTON, btn, 1, 0, NULL );
				sys->QueEvent( SE_MOUSE_BUTTON, btn, 0, 0, NULL );
				break;
			}

			default:
				break;
		}
	}
}

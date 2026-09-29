// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <SDL3/SDL.h>
#include "sys_local.h"
#include "keynum.h"

SDL_Window* Sys_GetGameSDLWindow( void );

namespace {

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
		return static_cast< keyNum_t >( ch );
	}
	virtual char ConvertScanToChar( unsigned int scanCode ) const override {
		return static_cast< char >( scanCode );
	}
	virtual unsigned int ConvertCharToScan( char ch ) const override {
		return static_cast< unsigned int >( ch );
	}
	virtual char ConvertKeyToChar( const keyNum_t keyNum ) const override {
		return static_cast< char >( keyNum );
	}
	virtual bool IsConsoleKey( const sdSysEvent& event ) const override {
		return event.GetKey() == K_CONSOLE;
	}

private:
	bool active;
};

class idMouseSDL : public idMouse {
public:
	idMouseSDL() : active( true ) {}
	virtual ~idMouseSDL() {}

	virtual bool Init() override { return true; }
	virtual void Shutdown() override {}
	virtual bool IsActive() const override { return active; }
	virtual void Activate() override { active = true; }
	virtual void Deactivate() override { active = false; }

	virtual void GrabCursor( bool grab ) override {
		SDL_Window* window = Sys_GetGameSDLWindow();
		if ( window != NULL ) {
			SDL_SetWindowRelativeMouseMode( window, grab );
		}
	}

	virtual int PollInputEvents( bool ) override { return 0; }
	virtual int ReturnInputEvent( const int, int&, int& ) override { return 0; }
	virtual void EndInputEvents() override {}

private:
	bool active;
};

class sdIMESDL : public sdIME {
public:
	virtual ~sdIMESDL() {}
	virtual bool Init() override { return true; }
	virtual void Shutdown() override {}
	virtual bool LangSupportsIME() const override { return false; }
	virtual void Enable( bool ) override {}
	virtual bool IsEnabled() const override { return false; }
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
	SDL_Event event;
	while ( SDL_PollEvent( &event ) ) {
		switch ( event.type ) {
			case SDL_EVENT_QUIT:
			case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
				cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "quit\n" );
				break;

			case SDL_EVENT_WINDOW_FOCUS_GAINED:
				sys->Keyboard().Activate();
				IN_Frame();
				if ( session != NULL ) {
					session->SetPlayingSoundWorld();
				}
				break;

			case SDL_EVENT_WINDOW_FOCUS_LOST:
				sys->Keyboard().Deactivate();
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
				if ( event.key.down && event.key.key == SDLK_RETURN && ( event.key.mod & SDL_KMOD_ALT ) != 0 ) {
					const bool fullscreen = sys3D != NULL && sys3D->IsFullscreen();
					cvarSystem->SetCVarBool( "r_fullscreen", !fullscreen );
					cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "vid_restart\n" );
					break;
				}

				keyNum_t key = MapSDLScancode( event.key.scancode );
				if ( key != K_INVALID ) {
					sys->QueEvent( SE_KEY, key, event.key.down ? 1 : 0, 0, NULL );
				}
				break;
			}

			case SDL_EVENT_TEXT_INPUT: {
				if ( event.text.text != NULL ) {
					for ( const char* p = event.text.text; *p != '\0'; ++p ) {
						sys->QueEvent( SE_CHAR, 0, static_cast< unsigned char >( *p ), 0, NULL );
					}
				}
				break;
			}

			case SDL_EVENT_MOUSE_MOTION: {
				if ( sys->Mouse().IsActive() && ( event.motion.xrel != 0.0f || event.motion.yrel != 0.0f ) ) {
					sys->QueEvent( SE_MOUSE, static_cast< int >( event.motion.xrel ), static_cast< int >( event.motion.yrel ), 0, NULL );
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

// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>

#include "idlib/precompiled.h"
#include "posix_public.h"

#if defined( USE_SDL3 ) && !defined( ID_DEDICATED )
#include <SDL3/SDL.h>
#endif

namespace {

static const int POSIX_CONSOLE_BUFFER_SIZE = 32768;
static const int POSIX_CONSOLE_FATAL_SIZE = 4096;

struct posixConsoleBuffer_t {
	pthread_mutex_t mutex;
	char text[ POSIX_CONSOLE_BUFFER_SIZE + 1 ];
	char fatalText[ POSIX_CONSOLE_FATAL_SIZE + 1 ];
	int length;
	int fatalLength;

	posixConsoleBuffer_t() : length( 0 ), fatalLength( 0 ) {
		pthread_mutex_init( &mutex, NULL );
		text[0] = '\0';
		fatalText[0] = '\0';
	}
	~posixConsoleBuffer_t() {
		pthread_mutex_destroy( &mutex );
	}
};

static posixConsoleBuffer_t s_consoleBuffer;

} // namespace

void Posix_ConsoleAppendText( const char *message ) {
	if ( message == NULL || message[0] == '\0' ) {
		return;
	}

	pthread_mutex_lock( &s_consoleBuffer.mutex );
	int addLen = static_cast< int >( strlen( message ) );
	if ( addLen > POSIX_CONSOLE_BUFFER_SIZE ) {
		message += ( addLen - POSIX_CONSOLE_BUFFER_SIZE );
		addLen = POSIX_CONSOLE_BUFFER_SIZE;
	}

	if ( s_consoleBuffer.length + addLen > POSIX_CONSOLE_BUFFER_SIZE ) {
		int excess = ( s_consoleBuffer.length + addLen ) - POSIX_CONSOLE_BUFFER_SIZE;
		memmove( s_consoleBuffer.text, s_consoleBuffer.text + excess, s_consoleBuffer.length - excess );
		s_consoleBuffer.length -= excess;
	}

	memcpy( s_consoleBuffer.text + s_consoleBuffer.length, message, addLen );
	s_consoleBuffer.length += addLen;
	s_consoleBuffer.text[ s_consoleBuffer.length ] = '\0';
	pthread_mutex_unlock( &s_consoleBuffer.mutex );
}

void Posix_ConsoleSetFatalError( const char *message ) {
	if ( message == NULL ) {
		return;
	}

	pthread_mutex_lock( &s_consoleBuffer.mutex );
	idStr::Copynz( s_consoleBuffer.fatalText, message, sizeof( s_consoleBuffer.fatalText ) );
	s_consoleBuffer.fatalLength = static_cast< int >( strlen( s_consoleBuffer.fatalText ) );
	pthread_mutex_unlock( &s_consoleBuffer.mutex );
}

void Posix_ConsoleLateInit( void ) {
}

bool Posix_ConsoleNeedsEventPump( void ) {
	return false;
}

bool Posix_ConsoleProcessEvent( const void * ) {
	return false;
}

void Posix_ConsoleFrame( void ) {
}

void Posix_ConsoleFatalErrorWait( void ) {
	// In headless dedicated mode, fatal errors exit directly
}

void Posix_ShutdownConsole( void ) {
}

/*
==============================================================================

Launch splash (decision 0004)

The retail splash, assets/splash/etqw_splash.bmp, in a borderless window centred on
the screen from the start of common->Init until the renderer is up (Common.cpp calls
Sys_ShowSplashScreen( false ) after renderSystem->Init), as the retail Windows client
and openQ4 show theirs. The client only; the dedicated server shows none.

==============================================================================
*/

#if defined( USE_SDL3 ) && !defined( ID_DEDICATED )

namespace {

static const char POSIX_SPLASH_BMP[] = "assets/splash/etqw_splash.bmp";

struct posixSplash_t {
	SDL_Window *	window;
	SDL_Renderer *	renderer;
	bool			videoInitialized;
	bool			shown;
};

static posixSplash_t s_splash = { NULL, NULL, false, false };

// Opens directory/prefix/POSIX_SPLASH_BMP. It runs before common->Init has set up the
// engine's allocators, so no idStr: plain stack buffers only.
static SDL_Surface *Posix_SplashLoadFrom( const char *directory, size_t length, const char *prefix ) {
	if ( directory == NULL || length == 0 ) {
		return NULL;
	}
	char path[ MAX_OSPATH ];
	if ( length + 1 + strlen( prefix ) + sizeof( POSIX_SPLASH_BMP ) > sizeof( path ) ) {
		return NULL;
	}
	idStr::snPrintf( path, sizeof( path ), "%.*s/%s%s", static_cast< int >( length ), directory, prefix,
		POSIX_SPLASH_BMP );
	return SDL_LoadBMP( path );
}

// The splash beside the executable (an installed or packaged Linux engine), in the app's
// Contents/Resources (macOS, which SDL_GetBasePath names for an app bundle), or in the
// current directory (a development build run from the repository root)
static SDL_Surface *Posix_SplashLoad( void ) {
	const char *exePath = Sys_EXEPath();
	const char *slash = exePath != NULL ? strrchr( exePath, '/' ) : NULL;
	const size_t exeDirectoryLength = slash != NULL ? static_cast< size_t >( slash - exePath ) : 0;
	const char *basePath = SDL_GetBasePath();
	const char *cwd = Posix_Cwd();
	SDL_Surface *surface = Posix_SplashLoadFrom( exePath, exeDirectoryLength, "" );
	if ( surface == NULL ) {
		surface = Posix_SplashLoadFrom( exePath, exeDirectoryLength, "../Resources/" );
	}
	if ( surface == NULL && basePath != NULL ) {
		surface = Posix_SplashLoadFrom( basePath, strlen( basePath ), "" );
	}
	if ( surface == NULL && cwd != NULL ) {
		surface = Posix_SplashLoadFrom( cwd, strlen( cwd ), "" );
	}
	return surface;
}

static void Posix_SplashDestroy( void ) {
	if ( s_splash.window != NULL ) {
		// drop the splash window's own events; the first other one goes back for the game
		SDL_PumpEvents();
		SDL_Event event;
		while ( SDL_PollEvent( &event ) ) {
			if ( SDL_GetWindowFromEvent( &event ) != s_splash.window ) {
				SDL_PushEvent( &event );
				break;
			}
		}
	}
	if ( s_splash.renderer != NULL ) {
		SDL_DestroyRenderer( s_splash.renderer );
		s_splash.renderer = NULL;
	}
	if ( s_splash.window != NULL ) {
		SDL_DestroyWindow( s_splash.window );
		s_splash.window = NULL;
	}
	if ( s_splash.videoInitialized ) {
		// pairs with the splash's own SDL_InitSubSystem; the game window holds its own reference
		SDL_QuitSubSystem( SDL_INIT_VIDEO );
		s_splash.videoInitialized = false;
	}
}

static void Posix_SplashShow( void ) {
	if ( s_splash.shown ) {
		return;
	}
	s_splash.shown = true;
	SDL_Surface *image = Posix_SplashLoad();
	if ( image == NULL ) {
		Sys_Printf( "splash: %s not found\n", POSIX_SPLASH_BMP );
		return;
	}
	if ( !SDL_InitSubSystem( SDL_INIT_VIDEO ) ) {
		Sys_Printf( "splash: no video: %s\n", SDL_GetError() );
		SDL_DestroySurface( image );
		return;
	}
	s_splash.videoInitialized = true;
	s_splash.window = SDL_CreateWindow( GAME_NAME, image->w, image->h,
		SDL_WINDOW_HIDDEN | SDL_WINDOW_BORDERLESS | SDL_WINDOW_HIGH_PIXEL_DENSITY );
	if ( s_splash.window != NULL ) {
		s_splash.renderer = SDL_CreateRenderer( s_splash.window, NULL );
	}
	SDL_Texture *texture = s_splash.renderer != NULL ? SDL_CreateTextureFromSurface( s_splash.renderer, image ) : NULL;
	SDL_DestroySurface( image );
	if ( texture == NULL ) {
		Sys_Printf( "splash: not shown: %s\n", SDL_GetError() );
		Posix_SplashDestroy();
		return;
	}
	SDL_SetWindowPosition( s_splash.window, SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED );
	SDL_ShowWindow( s_splash.window );
	// NULL fills the whole drawable, so a HiDPI display scales the image rather than
	// drawing it in one corner
	SDL_RenderClear( s_splash.renderer );
	SDL_RenderTexture( s_splash.renderer, texture, NULL, NULL );
	SDL_RenderPresent( s_splash.renderer );
	SDL_DestroyTexture( texture );
	SDL_PumpEvents();
}

} // namespace

void Sys_ShowSplashScreen( bool show ) {
	if ( show ) {
		Posix_SplashShow();
	} else {
		Posix_SplashDestroy();
	}
}

#else

void Sys_ShowSplashScreen( bool ) {
}

#endif

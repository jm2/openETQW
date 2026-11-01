// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <ApplicationServices/ApplicationServices.h>
#include <SDL3/SDL.h>
#include "macosx_common.h"

// The Objective-C runtime, declared here so this file stays C++ (<objc/objc.h>
// would bring its own BOOL).
extern "C" void *objc_getClass( const char *name );
extern "C" void *sel_registerName( const char *name );
extern "C" void objc_msgSend( void );
extern "C" void *objc_autoreleasePoolPush( void );
extern "C" void objc_autoreleasePoolPop( void *pool );

SDL_Window* Sys_GetGameSDLWindow( void );

namespace {

template < typename R, typename... Args >
R ObjcSend( void *receiver, const char *selector, Args... args ) {
	return reinterpret_cast< R (*)( void *, void *, Args... ) >( objc_msgSend )( receiver, sel_registerName( selector ), args... );
}

}

/*
====================
Sys_KeepFullscreenComposited

In native (Spaces) fullscreen macOS shows an opaque layer that covers the
display straight on the panel, past the window compositor. With the Vulkan
drawables that path stays black until something forces compositing again
(Spotlight, a screenshot, leaving fullscreen), and it can hang WindowServer
(#385; KisakCOD found and fixed the same on its Vulkan driver). A non-opaque
layer always goes through the compositor. The Vulkan driver may make the layer
opaque again when the swapchain is rebuilt, so the event pump checks it every
time. Windowed keeps the opaque layer; it is composited anyway.
====================
*/
void Sys_KeepFullscreenComposited( void ) {
	SDL_Window *window = Sys_GetGameSDLWindow();
	if ( window == NULL ) {
		return;
	}
	void *nsWindow = SDL_GetPointerProperty( SDL_GetWindowProperties( window ), SDL_PROP_WINDOW_COCOA_WINDOW_POINTER, NULL );
	if ( nsWindow == NULL ) {
		return;
	}
	// The getters below can return autoreleased objects and nothing else drains
	// a pool on this thread, so each call gets its own.
	void *const pool = objc_autoreleasePoolPush();
	// Objective-C's BOOL is bool on arm64.
	const bool opaque = ( SDL_GetWindowFlags( window ) & SDL_WINDOW_FULLSCREEN ) == 0;
	void *const metalLayerClass = objc_getClass( "CAMetalLayer" );
	void *contentView = ObjcSend< void * >( nsWindow, "contentView" );
	void *subviews = contentView != NULL ? ObjcSend< void * >( contentView, "subviews" ) : NULL;
	const unsigned long count = subviews != NULL ? ObjcSend< unsigned long >( subviews, "count" ) : 0;
	for ( unsigned long i = 0; i < count; ++i ) {
		void *layer = ObjcSend< void * >( ObjcSend< void * >( subviews, "objectAtIndex:", i ), "layer" );
		if ( layer != NULL && ObjcSend< bool >( layer, "isKindOfClass:", metalLayerClass )
				&& ObjcSend< bool >( layer, "isOpaque" ) != opaque ) {
			ObjcSend< void >( layer, "setOpaque:", opaque );
		}
	}
	objc_autoreleasePoolPop( pool );
}

bool Sys_GetDesktopResolution( int *width, int *height ) {
	SDL_DisplayID displayID = SDL_GetPrimaryDisplay();
	if ( displayID == 0 ) {
		return false;
	}
	const SDL_DisplayMode *mode = SDL_GetCurrentDisplayMode( displayID );
	if ( mode == NULL ) {
		return false;
	}
	if ( width != NULL ) {
		*width = mode->w;
	}
	if ( height != NULL ) {
		*height = mode->h;
	}
	return true;
}

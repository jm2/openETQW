// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#include "sys_local.h"
#include "sys_render.h"

namespace {

class idRenderContextSDL3 : public idRenderContext {
public:
	virtual bool Create( const idRenderContextParms &p ) override {
		parms = p;
		return true;
	}
	virtual void Destroy( void ) override {}
	virtual bool MakeCurrent( dcHandle_t handle = NULL ) override {
		return true;
	}
	virtual bool IsValid( void ) const override {
		return true;
	}
	virtual void SetAdditionalDefaultState( void ) override {}
	virtual void ShowContext( void ) const override {}
};

class id3DContextSDL3 : public id3DContext {
public:
	id3DContextSDL3() :
		window( NULL ) {
		memset( &gameWindowParms, 0, sizeof( gameWindowParms ) );
		memset( &primaryMonitor, 0, sizeof( primaryMonitor ) );
	}

	virtual ~id3DContextSDL3() {
		Shutdown();
	}

	virtual void InitContext( const glimpParms_t& parms ) override {
#if defined( __APPLE__ )
		SDL_SetHint( SDL_HINT_VIDEO_DRIVER, "cocoa" );
		SDL_SetHint( SDL_HINT_RENDER_DRIVER, "metal,gpu,software" );
		SDL_SetHint( SDL_HINT_VIDEO_METAL_AUTO_RESIZE_DRAWABLE, "1" );
#endif

		if ( !SDL_InitSubSystem( SDL_INIT_VIDEO ) ) {
			common->FatalError( "SDL3: failed to initialize video subsystem: %s", SDL_GetError() );
		}

		SDL_WindowFlags flags = SDL_WINDOW_VULKAN | SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY;
		if ( parms.fullScreen ) {
			flags |= SDL_WINDOW_FULLSCREEN;
		}

		window = SDL_CreateWindow(
			"Enemy Territory: QUAKE Wars",
			parms.width,
			parms.height,
			flags
		);
		if ( window == NULL ) {
			common->FatalError( "SDL3: failed to create Vulkan window: %s", SDL_GetError() );
		}

		gameWindowParms = parms;

		idRenderContextParms rcParms;
		rcParms.width = parms.width;
		rcParms.height = parms.height;
		rcParms.isGameContext = true;
		renderContext.Create( rcParms );

		EnumerateMonitors();
		common->Printf( "SDL3 Vulkan window created: %dx%d (fullscreen: %d)\n",
			parms.width, parms.height, parms.fullScreen ? 1 : 0 );
	}

	virtual void Shutdown() override {
		if ( window != NULL ) {
			SDL_DestroyWindow( window );
			window = NULL;
		}
	}

	virtual void RecreateContext( const glimpParms_t& parms ) override {
		Shutdown();
		InitContext( parms );
	}

	virtual dcHandle_t GetGameWindow() override {
		return static_cast< dcHandle_t >( window );
	}

	virtual wndHandle_t GetGameWindowHandle() override {
		return static_cast< wndHandle_t >( window );
	}

	virtual idRenderContext* GetGameRenderContext() override {
		return &renderContext;
	}

	virtual bool SetGameWindowParms( const glimpParms_t& parms ) override {
		gameWindowParms = parms;
		return true;
	}

	virtual idRenderContext* GetCurrentRenderContext() override {
		return &renderContext;
	}

	virtual const glimpParms_t& GetGameWindowParms() const override {
		return gameWindowParms;
	}

	virtual void SetPixelFormat( dcHandle_t windowDC ) override {
		(void)windowDC;
	}

	virtual GLExtension_t ExtensionPointer( const char* name ) override {
		return reinterpret_cast< GLExtension_t >( SDL_GL_GetProcAddress( name ) );
	}

	virtual void ShowGameWindow() override {
		if ( window != NULL ) {
			SDL_ShowWindow( window );
		}
	}

	virtual void HideGameWindow() override {
		if ( window != NULL ) {
			SDL_HideWindow( window );
		}
	}

	virtual bool IsFullscreen() override {
		return window != NULL && ( SDL_GetWindowFlags( window ) & SDL_WINDOW_FULLSCREEN ) != 0;
	}

	virtual bool IsMinimized() override {
		return window != NULL && ( SDL_GetWindowFlags( window ) & SDL_WINDOW_MINIMIZED ) != 0;
	}

	virtual void WindowSizeDragged( int width, int height ) override {
		gameWindowParms.width = width;
		gameWindowParms.height = height;
	}

	virtual bool MakeCurrent( dcHandle_t windowDC ) override {
		(void)windowDC;
		return true;
	}

	virtual bool ReleaseCurrent( dcHandle_t windowDC ) override {
		(void)windowDC;
		return true;
	}

	virtual void ReleaseContext( dcHandle_t windowDC ) override {
		(void)windowDC;
	}

	virtual void SwapBuffers() override {
		// Handled directly by VulkanBackend swapchain presentation
	}

	virtual void SetGamma( unsigned short red[256], unsigned short green[256], unsigned short blue[256] ) override {
		(void)red;
		(void)green;
		(void)blue;
	}

	virtual bool IsDisplayModeAvailable( int width, int height ) override {
		(void)width;
		(void)height;
		return true;
	}

	virtual int GetNumMSAAModes() const override {
		return 1;
	}

	virtual const char* GetMSAAMode( int idx, int& val ) const override {
		(void)idx;
		val = 0;
		return "Off";
	}

	virtual bool IsMSAACountAvailable( int msaa ) const override {
		(void)msaa;
		return true;
	}

	virtual int GetNumMonitors() const override {
		return 1;
	}

	virtual const monitorInfo_t& GetMonitor( int index ) const override {
		(void)index;
		return primaryMonitor;
	}

	virtual const monitorInfo_t& GetPrimaryMonitor() const override {
		return primaryMonitor;
	}

	virtual void ConstrainToPrimaryMonitor( int& width, int& height ) override {
		EnumerateMonitors();
		if ( primaryMonitor.workArea.w > 0 && width > primaryMonitor.workArea.w ) {
			width = primaryMonitor.workArea.w;
		}
		if ( primaryMonitor.workArea.h > 0 && height > primaryMonitor.workArea.h ) {
			height = primaryMonitor.workArea.h;
		}
	}

	virtual void EnumerateMonitors() override {
		SDL_DisplayID displayID = SDL_GetPrimaryDisplay();
		const SDL_DisplayMode *mode = SDL_GetCurrentDisplayMode( displayID );
		int w = mode != NULL ? mode->w : 1920;
		int h = mode != NULL ? mode->h : 1080;
		primaryMonitor.monitor.x = 0;
		primaryMonitor.monitor.y = 0;
		primaryMonitor.monitor.w = w;
		primaryMonitor.monitor.h = h;
		primaryMonitor.workArea = primaryMonitor.monitor;
		primaryMonitor.primary = true;
	}

private:
	SDL_Window*				window;
	idRenderContextSDL3		renderContext;
	glimpParms_t			gameWindowParms;
	monitorInfo_t			primaryMonitor;
};

id3DContextSDL3 sys3DLocal;

} // namespace

id3DContext* sys3D = &sys3DLocal;

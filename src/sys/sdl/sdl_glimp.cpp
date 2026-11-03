// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#include "sys_local.h"
#include "sys_render.h"
#include "../renderer/VulkanBackend.h"
#include "VideoModeAvailability.h"

void Sys_SDL_SetVideoHints( void );
SDL_Window* Sys_GetGameSDLWindow( void );
void Sys_UpdateSDLTextInput( void );

namespace {

bool vidRestartRegistered = false;

/*
====================
VidRestart_f

Applies r_fullscreen to the game window ("vid_restart windowed" clears it first,
as retail's does). Alt+Return (Command+Return on macOS, FullscreenChord.h) flips
r_fullscreen and runs this. The window is
switched in place: the Vulkan swapchain follows the resize, so nothing is torn
down. Retail 1.5 also restarts the renderer here for r_mode changes; this does not.
====================
*/
void VidRestart_f( const idCmdArgs& args ) {
	SDL_Window* window = Sys_GetGameSDLWindow();
	if ( window == NULL ) {
		common->Printf( "vid_restart: no game window\n" );
		return;
	}
	if ( args.Argc() > 1 && idStr::Icmp( args.Argv( 1 ), "windowed" ) == 0 ) {
		cvarSystem->SetCVarBool( "r_fullscreen", false );
	}
	const bool fullscreen = cvarSystem->GetCVarBool( "r_fullscreen" );
	if ( !SDL_SetWindowFullscreen( window, fullscreen ) ) {
		common->Warning( "vid_restart: could not switch to %s: %s", fullscreen ? "fullscreen" : "a window", SDL_GetError() );
	}
	SDL_SyncWindow( window );
	const bool isFullscreen = ( SDL_GetWindowFlags( window ) & SDL_WINDOW_FULLSCREEN ) != 0;
	glimpParms_t parms = sys3D->GetGameWindowParms();
	parms.fullScreen = isFullscreen;
	sys3D->SetGameWindowParms( parms );
	common->Printf( "vid_restart: %s\n", isFullscreen ? "fullscreen" : "windowed" );
}

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
		window( NULL ), glContext( NULL ) {
		memset( &gameWindowParms, 0, sizeof( gameWindowParms ) );
		memset( &primaryMonitor, 0, sizeof( primaryMonitor ) );
	}

	virtual ~id3DContextSDL3() {
		Shutdown();
	}

	virtual void InitContext( const glimpParms_t& parms ) override {
		InitVideo();
		// The OpenGL path (explicit or the Vulkan fallback) draws stencil shadows
		// and assumes 24/8 (glConfig, as the old Win32 OpenGL setup requested); SDL's defaults
		// are a 16-bit depth buffer and no stencil. Set before any window exists.
		SDL_GL_SetAttribute( SDL_GL_RED_SIZE, 8 );
		SDL_GL_SetAttribute( SDL_GL_GREEN_SIZE, 8 );
		SDL_GL_SetAttribute( SDL_GL_BLUE_SIZE, 8 );
		SDL_GL_SetAttribute( SDL_GL_ALPHA_SIZE, 8 );
		SDL_GL_SetAttribute( SDL_GL_DEPTH_SIZE, 24 );
		SDL_GL_SetAttribute( SDL_GL_STENCIL_SIZE, 8 );
		SDL_GL_SetAttribute( SDL_GL_DOUBLEBUFFER, 1 );
		// r_multiSamples, as the old Win32 OpenGL setup requested it; without these the OpenGL
		// framebuffer was never multisampled, so glConfig.samples stayed 0 and the
		// sample-coverage fade could not run (#253).
		SDL_GL_SetAttribute( SDL_GL_MULTISAMPLEBUFFERS, parms.multiSamples.multi > 1 ? 1 : 0 );
		SDL_GL_SetAttribute( SDL_GL_MULTISAMPLESAMPLES, parms.multiSamples.multi > 1 ? parms.multiSamples.multi : 0 );

		SDL_WindowFlags flags = SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY;
		if ( R_UseVulkanBackend() ) {
			flags |= SDL_WINDOW_VULKAN;
		} else {
			flags |= SDL_WINDOW_OPENGL;
		}
		if ( parms.fullScreen ) {
			flags |= SDL_WINDOW_FULLSCREEN;
		}

		window = SDL_CreateWindow(
			"Enemy Territory: QUAKE Wars",
			parms.width,
			parms.height,
			flags
		);
		if ( window == NULL && R_UseVulkanBackend() ) {
			common->Printf(
				"SDL3: failed to create a Vulkan window (%s); falling back to OpenGL.\n",
				SDL_GetError() );
			R_NotifyVulkanUnavailable();
			flags = ( flags & ~SDL_WINDOW_VULKAN ) | SDL_WINDOW_OPENGL;
			window = SDL_CreateWindow(
				"Enemy Territory: QUAKE Wars",
				parms.width,
				parms.height,
				flags
			);
		}
		// A sample count the driver can't provide fails window creation (X11/GLX) or
		// context creation (Wayland/EGL, macOS). Drop multisampling and retry once,
		// so an archived r_multiSamples can't stop the game starting (#255).
		bool multisampled = !R_UseVulkanBackend() && parms.multiSamples.multi > 1;
		auto dropMultisampling = [&]() {
			common->Warning( "SDL3: failed with r_multiSamples %d (%s); retrying without multisampling",
				parms.multiSamples.multi, SDL_GetError() );
			SDL_GL_SetAttribute( SDL_GL_MULTISAMPLEBUFFERS, 0 );
			SDL_GL_SetAttribute( SDL_GL_MULTISAMPLESAMPLES, 0 );
			multisampled = false;
		};
		if ( window == NULL && multisampled ) {
			dropMultisampling();
			window = SDL_CreateWindow(
				"Enemy Territory: QUAKE Wars",
				parms.width,
				parms.height,
				flags
			);
		}
		if ( window == NULL ) {
			common->FatalError( "SDL3: failed to create window: %s", SDL_GetError() );
		}

		if ( !R_UseVulkanBackend() ) {
			glContext = SDL_GL_CreateContext( window );
			if ( glContext == NULL && multisampled ) {
				// the window's pixel format was chosen with the samples, so make a new one
				dropMultisampling();
				SDL_DestroyWindow( window );
				window = SDL_CreateWindow(
					"Enemy Territory: QUAKE Wars",
					parms.width,
					parms.height,
					flags
				);
				if ( window == NULL ) {
					common->FatalError( "SDL3: failed to create window: %s", SDL_GetError() );
				}
				glContext = SDL_GL_CreateContext( window );
			}
			if ( glContext == NULL ) {
				common->FatalError( "SDL3: failed to create OpenGL context: %s", SDL_GetError() );
			}
			if ( !SDL_GL_MakeCurrent( window, glContext ) ) {
				common->FatalError( "SDL3: failed to make the OpenGL context current: %s", SDL_GetError() );
			}
			int depthBits = 0;
			int stencilBits = 0;
			int samples = 0;
			SDL_GL_GetAttribute( SDL_GL_DEPTH_SIZE, &depthBits );
			SDL_GL_GetAttribute( SDL_GL_STENCIL_SIZE, &stencilBits );
			SDL_GL_GetAttribute( SDL_GL_MULTISAMPLESAMPLES, &samples );
			common->Printf( "SDL3 OpenGL context: depth %d, stencil %d, samples %d\n", depthBits, stencilBits, samples );
		}

		gameWindowParms = parms;

		idRenderContextParms rcParms;
		rcParms.width = parms.width;
		rcParms.height = parms.height;
		rcParms.isGameContext = true;
		renderContext.Create( rcParms );

		EnumerateMonitors();
		// typed text reaches the engine only while text input is on (sdl_events.cpp)
		Sys_UpdateSDLTextInput();
		// the game takes the mouse once its window is up, as on Windows (SystemBootstrap.cpp);
		// it stays uncaptured while a menu cursor shows (idMouseSDL, #382)
		if ( sys != NULL ) {
			sys->Mouse().GrabCursor( true );
		}
		if ( cmdSystem != NULL && !vidRestartRegistered ) {
			cmdSystem->AddCommand( "vid_restart", VidRestart_f, CMD_FL_RENDERER, "applies r_fullscreen to the game window (\"windowed\" leaves fullscreen)" );
			vidRestartRegistered = true;
		}
		common->Printf( "SDL3 %s window created: %dx%d (fullscreen: %d)\n",
			R_UseVulkanBackend() ? "Vulkan" : "OpenGL",
			parms.width, parms.height, parms.fullScreen ? 1 : 0 );
	}

	virtual void Shutdown() override {
		if ( glContext != NULL ) {
			if ( SDL_GL_GetCurrentContext() == glContext ) {
				SDL_GL_MakeCurrent( window, NULL );
			}
			SDL_GL_DestroyContext( glContext );
			glContext = NULL;
		}
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
#if defined( _WIN32 )
		if ( window == NULL ) {
			return NULL;
		}
		return reinterpret_cast< dcHandle_t >( SDL_GetPointerProperty(
			SDL_GetWindowProperties( window ), SDL_PROP_WINDOW_WIN32_HDC_POINTER, NULL ) );
#else
		return static_cast< dcHandle_t >( window );
#endif
	}

	virtual wndHandle_t GetGameWindowHandle() override {
#if defined( _WIN32 )
		if ( window == NULL ) {
			return NULL;
		}
		return reinterpret_cast< wndHandle_t >( SDL_GetPointerProperty(
			SDL_GetWindowProperties( window ), SDL_PROP_WINDOW_WIN32_HWND_POINTER, NULL ) );
#else
		return static_cast< wndHandle_t >( window );
#endif
	}

	virtual idRenderContext* GetGameRenderContext() override {
		return &renderContext;
	}

	SDL_Window* GetSDLWindow() const {
		return window;
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
		return glContext == NULL || SDL_GL_MakeCurrent( window, glContext );
	}

	virtual bool ReleaseCurrent( dcHandle_t windowDC ) override {
		(void)windowDC;
		return glContext == NULL || SDL_GL_MakeCurrent( window, NULL );
	}

	virtual void ReleaseContext( dcHandle_t windowDC ) override {
		(void)windowDC;
	}

	virtual void SwapBuffers() override {
		if ( glContext != NULL ) {
			SDL_GL_SwapWindow( window );
		}
		// Vulkan presentation is handled directly by VulkanBackend's swapchain
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
		// Common.cpp asks for the display (ConstrainToPrimaryMonitor) before InitContext.
		// Windows starts SDL video in SystemBootstrap; Linux and macOS do not, and without it
		// SDL reports no display, so every window used to be capped at the 1920x1080 below.
		InitVideo();
		SDL_DisplayID displayID = SDL_GetPrimaryDisplay();
		const SDL_DisplayMode *mode = SDL_GetCurrentDisplayMode( displayID );
		if ( mode == NULL ) {
			common->Printf( "SDL3: no mode for the primary display (%s); assuming 1920x1080\n", SDL_GetError() );
		}
		int w = mode != NULL ? mode->w : 1920;
		int h = mode != NULL ? mode->h : 1080;
		primaryMonitor.monitor.x = 0;
		primaryMonitor.monitor.y = 0;
		primaryMonitor.monitor.w = w;
		primaryMonitor.monitor.h = h;
		primaryMonitor.workArea = primaryMonitor.monitor;
		primaryMonitor.primary = true;
		MarkAvailableVideoModes( displayID );
		// SDL's headless drivers (the Linux tests and screenshot sets run on offscreen) report
		// a made-up 1024x768 display; a window there is not on any screen, so it is not capped.
		const char *driver = SDL_GetCurrentVideoDriver();
		if ( driver != NULL && ( SDL_strcmp( driver, "offscreen" ) == 0 || SDL_strcmp( driver, "dummy" ) == 0 ) ) {
			primaryMonitor.workArea.w = 0;
			primaryMonitor.workArea.h = 0;
			static bool logged = false;
			if ( !logged ) {
				logged = true;
				common->Printf( "SDL3: the '%s' video driver has no real display; the window size is not capped\n", driver );
			}
		}
	}

private:
	// Marks the r_mode table's modes the display can show fullscreen as available
	// (VideoModeAvailability.h); none were, so the menu's fullscreen mode list was empty.
	static void MarkAvailableVideoModes( SDL_DisplayID displayID ) {
		if ( common == NULL ) {
			return;
		}
		idList< int > sizes;
		int count = 0;
		SDL_DisplayMode **displayModes = SDL_GetFullscreenDisplayModes( displayID, &count );
		for ( int i = 0; displayModes != NULL && i < count; i++ ) {
			const SDL_DisplayMode *mode = displayModes[i];
			// 32 bpp only, as retail's mode check; Windows also lists 16 and 24 bpp modes
			if ( mode->format != SDL_PIXELFORMAT_UNKNOWN && SDL_BYTESPERPIXEL( mode->format ) != 4 ) {
				continue;
			}
			sizes.Append( mode->w );
			sizes.Append( mode->h );
			if ( mode->pixel_density > 1.0f ) {
				sizes.Append( idMath::Ftoi( mode->w * mode->pixel_density + 0.5f ) );
				sizes.Append( idMath::Ftoi( mode->h * mode->pixel_density + 0.5f ) );
			}
		}
		SDL_free( displayModes );
		idList< vidmode_t > modes;
		for ( int i = 0; i < common->GetNumVideoModes(); i++ ) {
			modes.Append( common->GetVideoMode( i ) );
		}
		const int available = Sys_MarkAvailableVideoModes( modes.Begin(), modes.Num(), sizes.Begin(), sizes.Num() / 2 );
		for ( int i = 0; i < modes.Num(); i++ ) {
			common->GetVideoMode( i ).available = modes[i].available;
		}
		static int logged = -1;
		if ( logged != available ) {
			logged = available;
			common->Printf( "SDL3: %d of %d video modes are available fullscreen (%d display modes)\n",
				available, modes.Num(), count );
		}
	}

	// Starts the video subsystem once; nothing quits it before SDL_Quit. The macOS hints are
	// set on every call, as InitContext always did, so they are set even when video is
	// already running (Sys_PromptForRetailData starts it without them; the launch splash
	// sets them first). The splash never quits its own reference.
	static void InitVideo() {
		Sys_SDL_SetVideoHints();

		if ( SDL_WasInit( SDL_INIT_VIDEO ) == 0 && !SDL_InitSubSystem( SDL_INIT_VIDEO ) ) {
			common->FatalError( "SDL3: failed to initialize video subsystem: %s", SDL_GetError() );
		}
	}

	SDL_Window*				window;
	SDL_GLContext			glContext;
	idRenderContextSDL3		renderContext;
	glimpParms_t			gameWindowParms;
	monitorInfo_t			primaryMonitor;
};

id3DContextSDL3 sys3DLocal;

} // namespace

id3DContext* sys3D = &sys3DLocal;

// The hints video needs before SDL starts it; also called by the launch splash
// (posix_syscon.cpp), which may start it first
void Sys_SDL_SetVideoHints( void ) {
#if defined( __APPLE__ )
	SDL_SetHint( SDL_HINT_VIDEO_DRIVER, "cocoa" );
	SDL_SetHint( SDL_HINT_RENDER_DRIVER, "metal,gpu,software" );
	SDL_SetHint( SDL_HINT_VIDEO_METAL_AUTO_RESIZE_DRAWABLE, "1" );
#endif
}

SDL_Window* Sys_GetGameSDLWindow( void ) {
	return sys3DLocal.GetSDLWindow();
}

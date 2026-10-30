// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include "framework/Common.h"
#include "../posix/posix_public.h"

#define SDL_MAIN_HANDLED
#include <SDL3/SDL_main.h>

static int SDLCALL OpenETQW_Main( int argc, char **argv ) {
	Posix_EarlyInit();
	// hidden again by common->Init once the renderer is up (decision 0004)
	Sys_ShowSplashScreen( true );

	if ( argc > 1 && argv != NULL ) {
		common->Init( argc - 1, const_cast< const char ** >( &argv[1] ), NULL );
	} else {
		common->Init( 0, NULL, NULL );
	}

	Sys_HandlePendingQuitSignal();
	Posix_LateInit();

	while ( 1 ) {
		Sys_HandlePendingQuitSignal();
		common->Frame();
	}

	return 0;
}

int main( int argc, char **argv ) {
	static char defaultArg0[] = "openETQW";
	char *defaultArgv[] = { defaultArg0, NULL };
	if ( argc <= 0 || argv == NULL ) {
		argc = 1;
		argv = defaultArgv;
	}
	return SDL_RunApp( argc, argv, OpenETQW_Main, NULL );
}

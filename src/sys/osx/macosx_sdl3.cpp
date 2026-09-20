// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <ApplicationServices/ApplicationServices.h>
#include <SDL3/SDL.h>
#include "macosx_common.h"

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

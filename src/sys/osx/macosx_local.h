// SPDX-License-Identifier: GPL-3.0-or-later
// Adapted from openQ4 src/sys/osx/macosx_local.h (jm2/openQ4 b37b1456; GPL-3.0-or-later, owner decision 2026-10-07).

#include <sys/wait.h>

#include "sys_public.h"

void	OutputDebugString( const char *text );

// input
void 	Sys_InitInput( void );
void 	Sys_ShutdownInput( void );

void	IN_DeactivateMouse( void);
void	IN_ActivateMouse( void);

void	IN_Activate (bool active);
void	IN_Frame (void);

void * wglGetProcAddress(const char *name);

void	Sleep( const int time );

void	Sys_UpdateWindowMouseInputRect( void );

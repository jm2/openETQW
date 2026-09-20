// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <signal.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdio.h>

#include "../../idlib/precompiled.h"
#include "posix_public.h"

struct posixSignalRoute_t {
	int signum;
	const char *name;
	bool graceful;
};

static const posixSignalRoute_t signalRoutes[] = {
	{ SIGHUP, "SIGHUP", true },
	{ SIGINT, "SIGINT", true },
	{ SIGTERM, "SIGTERM", true },
	{ SIGQUIT, "SIGQUIT", true },
	{ SIGILL, "SIGILL", false },
	{ SIGTRAP, "SIGTRAP", false },
	{ SIGBUS, "SIGBUS", false },
	{ SIGFPE, "SIGFPE", false },
	{ SIGSEGV, "SIGSEGV", false },
	{ SIGABRT, "SIGABRT", false },
	{ -1, NULL, false }
};

static volatile sig_atomic_t pendingQuitSignal = 0;
static volatile sig_atomic_t activeFatalSignal = 0;

static const posixSignalRoute_t *Posix_FindSignalRoute( int signum ) {
	for ( int i = 0; signalRoutes[ i ].signum != -1; ++i ) {
		if ( signalRoutes[ i ].signum == signum ) {
			return &signalRoutes[ i ];
		}
	}
	return NULL;
}

const char *Posix_SignalName( int signum ) {
	const posixSignalRoute_t *route = Posix_FindSignalRoute( signum );
	if ( route != NULL ) {
		return route->name;
	}
	return "unknown signal";
}

static void Posix_SignalHandler( int signum ) {
	const posixSignalRoute_t *route = Posix_FindSignalRoute( signum );
	if ( route != NULL && route->graceful ) {
		pendingQuitSignal = signum;
		return;
	}

	if ( activeFatalSignal ) {
		_exit( 1 );
	}
	activeFatalSignal = signum;

	const char *sigName = Posix_SignalName( signum );
	fprintf( stderr, "\nFATAL SIGNAL CAUGHT: %s (%d)\n", sigName, signum );
	_exit( 128 + signum );
}

int Posix_ConsumeQuitSignal( void ) {
	int sig = pendingQuitSignal;
	pendingQuitSignal = 0;
	return sig;
}

void Sys_HandlePendingQuitSignal( void ) {
	int sig = Posix_ConsumeQuitSignal();
	if ( sig != 0 ) {
		common->Printf( "Received signal %s (%d), shutting down...\n", Posix_SignalName( sig ), sig );
		if ( cmdSystem != NULL ) {
			cmdSystem->BufferCommandText( CMD_EXEC_NOW, "quit\n" );
		} else {
			common->Quit();
		}
	}
}

void Posix_InitSigs( void ) {
	struct sigaction act;
	memset( &act, 0, sizeof( act ) );
	act.sa_handler = Posix_SignalHandler;
	sigemptyset( &act.sa_mask );

	for ( int i = 0; signalRoutes[ i ].signum != -1; ++i ) {
		sigaction( signalRoutes[ i ].signum, &act, NULL );
	}

	signal( SIGPIPE, SIG_IGN );
}

void Posix_ClearSigs( void ) {
	struct sigaction act;
	memset( &act, 0, sizeof( act ) );
	act.sa_handler = SIG_DFL;
	sigemptyset( &act.sa_mask );

	for ( int i = 0; signalRoutes[ i ].signum != -1; ++i ) {
		sigaction( signalRoutes[ i ].signum, &act, NULL );
	}
}

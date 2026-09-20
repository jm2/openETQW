// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <stdio.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>

#include "idlib/precompiled.h"
#include "posix_public.h"

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

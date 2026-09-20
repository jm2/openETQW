// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <sys/types.h>
#include <sys/stat.h>
#include <errno.h>
#include <stdio.h>
#include <unistd.h>
#include <pthread.h>
#include <string.h>
#include <assert.h>

#include "idlib/precompiled.h"
#include "posix_public.h"

static pthread_mutex_t global_lock[ MAX_LOCAL_CRITICAL_SECTIONS ];
static bool global_lock_initialized[ MAX_LOCAL_CRITICAL_SECTIONS ];

static bool Sys_IsValidCriticalSectionIndex( int index ) {
	if ( index >= 0 && index < MAX_LOCAL_CRITICAL_SECTIONS ) {
		return true;
	}
	Sys_Printf( "Sys_Enter/LeaveCriticalSection: invalid index %d\n", index );
	return false;
}

static bool Sys_IsValidTriggerEventIndex( int index ) {
	if ( index >= 0 && index < MAX_TRIGGER_EVENTS ) {
		return true;
	}
	Sys_Printf( "Sys_Wait/TriggerEvent: invalid index %d\n", index );
	return false;
}

static bool Sys_IsCriticalSectionReady( int index, const char *operation ) {
	if ( !Sys_IsValidCriticalSectionIndex( index ) ) {
		return false;
	}
	if ( !global_lock_initialized[ index ] ) {
		return false;
	}
	return true;
}

static bool Sys_LockCriticalSection( int index, const char *operation ) {
	if ( !Sys_IsCriticalSectionReady( index, operation ) ) {
		return false;
	}
	const int result = pthread_mutex_lock( &global_lock[ index ] );
	return ( result == 0 );
}

static bool Sys_UnlockCriticalSection( int index, const char *operation ) {
	if ( !Sys_IsCriticalSectionReady( index, operation ) ) {
		return false;
	}
	const int result = pthread_mutex_unlock( &global_lock[ index ] );
	return ( result == 0 );
}

void Sys_EnterCriticalSection( int index ) {
	if ( index < 0 || index >= MAX_LOCAL_CRITICAL_SECTIONS ) {
		index = CRITICAL_SECTION_ZERO;
	}
	Sys_LockCriticalSection( index, "Sys_EnterCriticalSection" );
}

void Sys_LeaveCriticalSection( int index ) {
	if ( index < 0 || index >= MAX_LOCAL_CRITICAL_SECTIONS ) {
		index = CRITICAL_SECTION_ZERO;
	}
	Sys_UnlockCriticalSection( index, "Sys_LeaveCriticalSection" );
}

static pthread_cond_t	event_cond[ MAX_TRIGGER_EVENTS ];
static bool				signaled[ MAX_TRIGGER_EVENTS ];
static bool				waiting[ MAX_TRIGGER_EVENTS ];
static bool				event_cond_initialized[ MAX_TRIGGER_EVENTS ];

static bool Sys_IsTriggerEventReady( int index, const char *operation ) {
	if ( !Sys_IsValidTriggerEventIndex( index ) ) {
		return false;
	}
	return event_cond_initialized[ index ];
}

void Sys_WaitForEvent( int index ) {
	if ( !Sys_IsTriggerEventReady( index, "Sys_WaitForEvent" ) ) {
		return;
	}
	if ( !Sys_LockCriticalSection( MAX_LOCAL_CRITICAL_SECTIONS - 1, "Sys_WaitForEvent" ) ) {
		return;
	}
	if ( signaled[ index ] ) {
		signaled[ index ] = false;
	} else {
		waiting[ index ] = true;
		pthread_cond_wait( &event_cond[ index ], &global_lock[ MAX_LOCAL_CRITICAL_SECTIONS - 1 ] );
		waiting[ index ] = false;
	}
	Sys_UnlockCriticalSection( MAX_LOCAL_CRITICAL_SECTIONS - 1, "Sys_WaitForEvent" );
}

void Sys_TriggerEvent( int index ) {
	if ( !Sys_IsTriggerEventReady( index, "Sys_TriggerEvent" ) ) {
		return;
	}
	if ( !Sys_LockCriticalSection( MAX_LOCAL_CRITICAL_SECTIONS - 1, "Sys_TriggerEvent" ) ) {
		return;
	}
	if ( waiting[ index ] ) {
		pthread_cond_signal( &event_cond[ index ] );
	} else {
		signaled[ index ] = true;
	}
	Sys_UnlockCriticalSection( MAX_LOCAL_CRITICAL_SECTIONS - 1, "Sys_TriggerEvent" );
}

struct posixThreadEntry_t {
	pthread_t			thread;
	xthreadInfo *		info;
	volatile bool		stopRequested;
	bool				inUse;
};

static posixThreadEntry_t g_threadTable[ MAX_THREADS ];
xthreadInfo *g_threads[ MAX_THREADS ];
int g_thread_count = 0;

typedef void *(*pthread_function_t)( void * );

void Sys_CreateThread( xthread_t function, void *parms, threadPriority_e priority, xthreadInfo &info, const char *name, xthreadInfo *threads[], int *thread_count ) {
	const char *threadName = ( name != NULL && name[0] != '\0' ) ? name : "unnamed";
	(void)priority;

	info.threadHandle = 0;
	info.threadId = 0;
	info.name = threadName;

	if ( function == NULL || threads == NULL || thread_count == NULL ) {
		return;
	}

	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	int slot = -1;
	for ( int i = 0; i < MAX_THREADS; i++ ) {
		if ( !g_threadTable[ i ].inUse ) {
			slot = i;
			break;
		}
	}
	if ( slot == -1 ) {
		Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
		common->Printf( "Sys_CreateThread: MAX_THREADS (%d) reached for %s\n", MAX_THREADS, threadName );
		return;
	}

	pthread_attr_t attr;
	pthread_attr_init( &attr );
	pthread_attr_setdetachstate( &attr, PTHREAD_CREATE_JOINABLE );

	const size_t minWorkerStackSize = 4 * 1024 * 1024;
	size_t defaultStackSize = 0;
	if ( pthread_attr_getstacksize( &attr, &defaultStackSize ) == 0 && defaultStackSize < minWorkerStackSize ) {
		pthread_attr_setstacksize( &attr, minWorkerStackSize );
	}

	pthread_t thread;
	int result = pthread_create( &thread, &attr, (pthread_function_t)function, parms );
	pthread_attr_destroy( &attr );

	if ( result == 0 ) {
		g_threadTable[ slot ].thread = thread;
		g_threadTable[ slot ].info = &info;
		g_threadTable[ slot ].stopRequested = false;
		g_threadTable[ slot ].inUse = true;

		info.threadHandle = slot + 1;
		info.threadId = static_cast< unsigned long >( reinterpret_cast< uintptr_t >( thread ) );
		if ( *thread_count < MAX_THREADS ) {
			threads[ ( *thread_count )++ ] = &info;
		}
	} else {
		common->Printf( "Sys_CreateThread: pthread_create %s failed: %s\n", threadName, strerror( result ) );
	}
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
}

void Sys_RequestThreadStop( xthreadInfo& info ) {
	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	int slot = info.threadHandle - 1;
	if ( slot >= 0 && slot < MAX_THREADS && g_threadTable[ slot ].inUse && g_threadTable[ slot ].info == &info ) {
		g_threadTable[ slot ].stopRequested = true;
	}
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );

	for ( int i = 0; i < MAX_TRIGGER_EVENTS; i++ ) {
		Sys_TriggerEvent( i );
	}
}

bool Sys_IsThreadStopRequested( const xthreadInfo& info ) {
	bool stop = false;
	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	int slot = info.threadHandle - 1;
	if ( slot >= 0 && slot < MAX_THREADS && g_threadTable[ slot ].inUse && g_threadTable[ slot ].info == &info ) {
		stop = g_threadTable[ slot ].stopRequested;
	}
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
	return stop;
}

bool Sys_IsCurrentThreadStopRequested( void ) {
	const pthread_t current = pthread_self();
	bool stop = false;

	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	for ( int i = 0; i < MAX_THREADS; i++ ) {
		if ( g_threadTable[ i ].inUse && pthread_equal( current, g_threadTable[ i ].thread ) ) {
			stop = g_threadTable[ i ].stopRequested;
			break;
		}
	}
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
	return stop;
}

void Sys_DestroyThread( xthreadInfo& info ) {
	if ( info.threadHandle <= 0 || info.threadHandle > MAX_THREADS ) {
		return;
	}
	int slot = info.threadHandle - 1;
	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	if ( !g_threadTable[ slot ].inUse || g_threadTable[ slot ].info != &info ) {
		Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
		return;
	}
	pthread_t thread = g_threadTable[ slot ].thread;
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );

	if ( pthread_equal( thread, pthread_self() ) ) {
		common->Error( "ERROR: Sys_DestroyThread attempted to join current thread %s\n", info.name );
	}

	Sys_RequestThreadStop( info );
	pthread_join( thread, NULL );

	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	g_threadTable[ slot ].inUse = false;
	memset( &g_threadTable[ slot ].thread, 0, sizeof( pthread_t ) );
	g_threadTable[ slot ].info = NULL;
	g_threadTable[ slot ].stopRequested = false;

	for ( int i = 0; i < g_thread_count; i++ ) {
		if ( g_threads[ i ] == &info ) {
			for ( int j = i + 1; j < g_thread_count; j++ ) {
				g_threads[ j - 1 ] = g_threads[ j ];
			}
			g_threads[ g_thread_count - 1 ] = NULL;
			g_thread_count--;
			break;
		}
	}
	info.threadHandle = 0;
	info.threadId = 0;
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
}

const char* Sys_GetThreadName( int *index ) {
	const pthread_t current = pthread_self();
	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	for ( int i = 0; i < MAX_THREADS; i++ ) {
		if ( g_threadTable[ i ].inUse && pthread_equal( current, g_threadTable[ i ].thread ) ) {
			if ( index ) *index = i;
			const char *n = g_threadTable[ i ].info != NULL ? g_threadTable[ i ].info->name : "unnamed";
			Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
			return n;
		}
	}
	if ( index ) *index = -1;
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
	return "main";
}

void Posix_InitPThreads( void ) {
	for ( int i = 0; i < MAX_LOCAL_CRITICAL_SECTIONS; i++ ) {
		pthread_mutexattr_t attr;
		pthread_mutexattr_init( &attr );
		pthread_mutexattr_settype( &attr, PTHREAD_MUTEX_RECURSIVE );
		int res = pthread_mutex_init( &global_lock[ i ], &attr );
		pthread_mutexattr_destroy( &attr );
		global_lock_initialized[ i ] = ( res == 0 );
	}

	for ( int i = 0; i < MAX_TRIGGER_EVENTS; i++ ) {
		int res = pthread_cond_init( &event_cond[ i ], NULL );
		event_cond_initialized[ i ] = ( res == 0 );
		signaled[ i ] = false;
		waiting[ i ] = false;
	}

	for ( int i = 0; i < MAX_THREADS; i++ ) {
		g_threadTable[ i ].inUse = false;
		memset( &g_threadTable[ i ].thread, 0, sizeof( pthread_t ) );
		g_threadTable[ i ].info = NULL;
		g_threadTable[ i ].stopRequested = false;
		g_threads[ i ] = NULL;
	}
	g_thread_count = 0;
}

// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#ifndef __SYS_POSIX__
#define __SYS_POSIX__

#include <signal.h>
#include <time.h>
#include "sys_public.h"

typedef threadPriority_e xthreadPriority;

const int MAX_CRITICAL_SECTIONS = 8;
const int MAX_LOCAL_CRITICAL_SECTIONS = MAX_CRITICAL_SECTIONS + 1;
const int MAX_TRIGGER_EVENTS = 8;
const int MAX_THREADS = 16;

extern xthreadInfo *g_threads[MAX_THREADS];
extern int g_thread_count;

void		Sys_CreateThread( xthread_t function, void *parms, threadPriority_e priority, xthreadInfo &info, const char *name, xthreadInfo *threads[], int *thread_count );
void		Sys_DestroyThread( xthreadInfo& info );
void		Sys_RequestThreadStop( xthreadInfo& info );
bool		Sys_IsThreadStopRequested( const xthreadInfo& info );
bool		Sys_IsCurrentThreadStopRequested( void );
const char *Sys_GetThreadName( int *index = 0 );

void		Sys_EnterCriticalSection( int index = CRITICAL_SECTION_ZERO );
void		Sys_LeaveCriticalSection( int index = CRITICAL_SECTION_ZERO );
void		Sys_WaitForEvent( int index = 0 );
void		Sys_TriggerEvent( int index = 0 );

void		Posix_QueEvent( sysEventType_t type, int value, int value2, int ptrLength, void *ptr );
const char*	Posix_Cwd( void );

void		Posix_EarlyInit( void );
void		Posix_LateInit( void );
bool		Posix_IsMainThread( void );

void		Posix_InitPThreads( void );
void		Posix_InitSigs( void );
void		Posix_ClearSigs( void );
int			Posix_ConsumeQuitSignal( void );
const char *Posix_SignalName( int signum );
void		Sys_HandlePendingQuitSignal( void );

void		Posix_Exit( int ret );
void		Posix_SetExit( int ret );

void		Posix_InitConsoleInput( void );
void		Posix_ShutdownConsoleInput( void );
char*		Posix_ConsoleInput( void );
void		Posix_ConsoleAppendText( const char *message );
void		Posix_ConsoleSetFatalError( const char *message );
void		Posix_ConsoleLateInit( void );
bool		Posix_ConsoleNeedsEventPump( void );
bool		Posix_ConsoleProcessEvent( const void *eventData );
void		Posix_ConsoleFrame( void );
void		Posix_ConsoleFatalErrorWait( void );
void		Posix_ShutdownConsole( void );
void		Posix_Shutdown( void );

#endif /* !__SYS_POSIX__ */

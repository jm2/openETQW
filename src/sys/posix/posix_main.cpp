// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <sys/types.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/statvfs.h>
#include <errno.h>
#include <dirent.h>
#include <unistd.h>
#include <dlfcn.h>
#include <termios.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>

#if defined( __APPLE__ )
#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <sys/sysctl.h>
#endif

#include "idlib/precompiled.h"
#include "sys_local.h"
#include "sys_render.h"
#include "posix_public.h"

static pthread_t mainThread;
static bool mainThreadRecorded = false;
static int exitCode = 0;
static char s_exePath[ MAX_OSPATH ];
static char s_cwd[ MAX_OSPATH ];

bool Posix_IsMainThread( void ) {
	if ( !mainThreadRecorded ) {
		return true;
	}
	return pthread_equal( pthread_self(), mainThread ) != 0;
}

const char* Posix_Cwd( void ) {
	if ( s_cwd[0] == '\0' ) {
		if ( getcwd( s_cwd, sizeof( s_cwd ) ) == NULL ) {
			s_cwd[0] = '.';
			s_cwd[1] = '\0';
		}
	}
	return s_cwd;
}

void Posix_EarlyInit( void ) {
	mainThread = pthread_self();
	mainThreadRecorded = true;

	Posix_InitSigs();
	Posix_InitPThreads();
	Posix_Cwd();
}

void Posix_LateInit( void ) {
	Posix_InitConsoleInput();
	Posix_ConsoleLateInit();
}

void Posix_Shutdown( void ) {
	Posix_ShutdownConsole();
	Posix_ShutdownConsoleInput();
	Posix_ClearSigs();
}

void Posix_Exit( int ret ) {
	Posix_Shutdown();
	_exit( ret );
}

void Posix_SetExit( int ret ) {
	exitCode = ret;
}

void Sys_Quit( void ) {
	Posix_Exit( exitCode );
}

void Posix_QueEvent( sysEventType_t type, int value, int value2, int ptrLength, void *ptr ) {
	if ( sys != NULL ) {
		sys->QueEvent( type, value, value2, ptrLength, ptr );
	}
}

#define MAX_POSIX_PRINT_MSG 4096

void Sys_Printf( const char *msg, ... ) {
	char text[ MAX_POSIX_PRINT_MSG ];
	va_list argptr;

	if ( msg == NULL ) {
		return;
	}
	va_start( argptr, msg );
	idStr::vsnPrintf( text, sizeof( text ) - 1, msg, argptr );
	va_end( argptr );
	text[ sizeof( text ) - 1 ] = '\0';

	Posix_ConsoleAppendText( text );
	fputs( text, stdout );
	fflush( stdout );
}

void Sys_VPrintf( const char *msg, va_list arg ) {
	char text[ MAX_POSIX_PRINT_MSG ];

	if ( msg == NULL ) {
		return;
	}
	idStr::vsnPrintf( text, sizeof( text ) - 1, msg, arg );
	text[ sizeof( text ) - 1 ] = '\0';

	Posix_ConsoleAppendText( text );
	fputs( text, stdout );
	fflush( stdout );
}

void Sys_DebugPrintf( const char *fmt, ... ) {
	va_list argptr;
	if ( fmt == NULL ) {
		return;
	}
	va_start( argptr, fmt );
	Sys_VPrintf( fmt, argptr );
	va_end( argptr );
}

void Sys_DebugVPrintf( const char *fmt, va_list arg ) {
	Sys_VPrintf( fmt, arg );
}

void Sys_Error( const char *error, ... ) {
	char text[ MAX_POSIX_PRINT_MSG ];
	va_list argptr;

	if ( error == NULL ) {
		idStr::Copynz( text, "Unknown error", sizeof( text ) );
	} else {
		va_start( argptr, error );
		idStr::vsnPrintf( text, sizeof( text ) - 1, error, argptr );
		va_end( argptr );
		text[ sizeof( text ) - 1 ] = '\0';
	}

	Posix_ConsoleSetFatalError( text );
	fprintf( stderr, "Sys_Error: %s\n", text );
	Posix_Exit( EXIT_FAILURE );
}

/*
================
Timing Functions
================
*/
static uint64_t sys_timeBaseNs = 0;

int Sys_Milliseconds( void ) {
	struct timespec ts;
	if ( clock_gettime( CLOCK_MONOTONIC, &ts ) != 0 ) {
		return 0;
	}
	const uint64_t nowNs = static_cast< uint64_t >( ts.tv_sec ) * 1000000000ull + static_cast< uint64_t >( ts.tv_nsec );
	if ( sys_timeBaseNs == 0 ) {
		sys_timeBaseNs = nowNs;
		return 0;
	}
	return static_cast< int >( ( nowNs - sys_timeBaseNs ) / 1000000ull );
}

void Sys_Sleep( int msec ) {
	if ( msec <= 0 ) {
		sched_yield();
	} else {
		usleep( msec * 1000 );
	}
}

time_t Sys_RealTime( sysTime_t* sysTime ) {
	time_t t = time( NULL );
	if ( sysTime != NULL ) {
		struct tm localTm;
		localtime_r( &t, &localTm );
		sysTime->tm_sec = localTm.tm_sec;
		sysTime->tm_min = localTm.tm_min;
		sysTime->tm_hour = localTm.tm_hour;
		sysTime->tm_mday = localTm.tm_mday;
		sysTime->tm_mon = localTm.tm_mon;
		sysTime->tm_year = localTm.tm_year;
		sysTime->tm_wday = localTm.tm_wday;
		sysTime->tm_yday = localTm.tm_yday;
		sysTime->tm_isdst = localTm.tm_isdst;
	}
	return t;
}

#if !defined( MACOS_X )
double Sys_GetClockTicks( void ) {
#if defined( __APPLE__ )
	return static_cast< double >( mach_absolute_time() );
#else
	struct timespec ts;
	clock_gettime( CLOCK_MONOTONIC, &ts );
	return static_cast< double >( ts.tv_sec ) * 1000000000.0 + static_cast< double >( ts.tv_nsec );
#endif
}
#endif

double Sys_GetClockTicksNoFlush( void ) {
	return Sys_GetClockTicks();
}

#if !defined( MACOS_X )
double Sys_ClockTicksPerSecond( void ) {
#if defined( __APPLE__ )
	static double ticksPerSec = 0.0;
	if ( ticksPerSec == 0.0 ) {
		mach_timebase_info_data_t tb;
		mach_timebase_info( &tb );
		ticksPerSec = 1000000000.0 * ( static_cast< double >( tb.denom ) / static_cast< double >( tb.numer ) );
	}
	return ticksPerSec;
#else
	return 1000000000.0;
#endif
}
#endif

/*
================
System & Hardware Info
================
*/
void Sys_CPUInfo( cpuInfo_t& info ) {
	info.logicalNum = 1;
	info.physicalNum = 1;
	info.hyperThreadedStatus = HT_DISABLED;

#if defined( __APPLE__ )
	int count = 0;
	size_t size = sizeof( count );
	if ( sysctlbyname( "hw.logicalcpu", &count, &size, NULL, 0 ) == 0 && count > 0 ) {
		info.logicalNum = count;
	}
	if ( sysctlbyname( "hw.physicalcpu", &count, &size, NULL, 0 ) == 0 && count > 0 ) {
		info.physicalNum = count;
	}
	if ( info.logicalNum > info.physicalNum ) {
		info.hyperThreadedStatus = HT_ENABLED;
	}
#endif
}

#if !defined( MACOS_X )
cpuid_t Sys_GetProcessorId( void ) {
	return static_cast< cpuid_t >( 0 );
}

const char *Sys_GetProcessorString( void ) {
#if defined( __aarch64__ ) || defined( __arm64__ )
	return "ARM64";
#elif defined( __x86_64__ )
	return "x86_64";
#else
	return "Generic";
#endif
}
#endif

const char *Sys_FPU_GetState( void ) {
	return "OK";
}

bool Sys_FPU_StackIsEmpty( void ) {
	return true;
}

#if !defined( MACOS_X )
void Sys_FPU_SetFTZ( bool ) {}
void Sys_FPU_SetDAZ( bool ) {}
void Sys_FPU_EnableExceptions( int ) {}
#endif

const char *Sys_GetEnv( const char *name ) {
	return name != NULL ? getenv( name ) : NULL;
}

void Sys_GetCurrentMemoryStatus( sysMemoryStats_t &stats ) {
	memset( &stats, 0, sizeof( stats ) );
#if defined( __APPLE__ )
	int64_t memsize = 0;
	size_t len = sizeof( memsize );
	if ( sysctlbyname( "hw.memsize", &memsize, &len, NULL, 0 ) == 0 ) {
		stats.totalPhysical = static_cast< int >( memsize / ( 1024 * 1024 ) );
		stats.availPhysical = stats.totalPhysical / 2;
	}
#endif
}

void Sys_GetExeLaunchMemoryStatus( sysMemoryStats_t &stats ) {
	Sys_GetCurrentMemoryStatus( stats );
}

void Sys_GetProcessMemoryStatus( sysProcessMemoryStats_t &stats ) {
	memset( &stats, 0, sizeof( stats ) );
	Sys_GetCurrentMemoryStatus( stats.globalStats );
}

/*
================
Path & Dynamic Library Management
================
*/
#if !defined( __APPLE__ ) && ( defined( ID_DEDICATED ) || !defined( MACOS_X ) )
const char *Sys_EXEPath( void ) {
	if ( s_exePath[0] != '\0' ) {
		return s_exePath;
	}

#if defined( __APPLE__ )
	char rawPath[ MAX_OSPATH ];
	uint32_t size = sizeof( rawPath );
	if ( _NSGetExecutablePath( rawPath, &size ) == 0 ) {
		char resolved[ MAX_OSPATH ];
		if ( realpath( rawPath, resolved ) != NULL ) {
			idStr::Copynz( s_exePath, resolved, sizeof( s_exePath ) );
			return s_exePath;
		}
		idStr::Copynz( s_exePath, rawPath, sizeof( s_exePath ) );
		return s_exePath;
	}
#endif

	idStr::Copynz( s_exePath, Posix_Cwd(), sizeof( s_exePath ) );
	return s_exePath;
}

const char *Sys_DefaultBasePath( void ) {
	return Posix_Cwd();
}

const char *Sys_DefaultSavePath( void ) {
	return Posix_Cwd();
}
#endif

const char *Sys_DefaultUserPath( void ) {
	return Posix_Cwd();
}

void Sys_Mkdir( const char *path ) {
	if ( path == NULL || path[0] == '\0' ) {
		return;
	}
	mkdir( path, 0755 );
}

int Sys_Rmdir( const char *path ) {
	if ( path == NULL || path[0] == '\0' ) {
		return -1;
	}
	return rmdir( path );
}

#if !defined( __APPLE__ ) && ( defined( ID_DEDICATED ) || !defined( MACOS_X ) )
const char *Sys_DefaultCDPath( void ) {
	return "";
}
#endif

void Sys_Init( void ) {
}

void Sys_Shutdown( void ) {
	Posix_Shutdown();
}

void Sys_InitInput( void ) {
	Posix_InitConsoleInput();
}

void Sys_ShutdownInput( void ) {
	Posix_ShutdownConsole();
}

void Sys_PumpEvents( void ) {
	Sys_GenerateEvents();
}

void Sys_ShowWindow( bool show ) {
#ifndef ID_DEDICATED
	if ( sys3D != NULL ) {
		if ( show ) {
			sys3D->ShowGameWindow();
		} else {
			sys3D->HideGameWindow();
		}
	}
#endif
}

bool Sys_IsWindowVisible( void ) {
#ifndef ID_DEDICATED
	if ( sys3D != NULL && sys3D->GetGameWindow() != NULL ) {
		return !sys3D->IsMinimized();
	}
#endif
	return false;
}

void Sys_ShowConsole( int visLevel, bool quitOnClose ) {
}

void Sys_SetFatalError( const char *error ) {
	Posix_ConsoleSetFatalError( error );
}

wchar_t *Sys_GetClipboardData( void ) {
	return NULL;
}

void Sys_ShowSplashScreen( bool show ) {
}

long Sys_FileTimeStamp( FILE *f ) {
	if ( f == NULL ) {
		return -1;
	}
	struct stat st;
	if ( fstat( fileno( f ), &st ) == 0 ) {
		return st.st_mtime;
	}
	return -1;
}

int Sys_ListFiles( const char *directory, const char *extension, idList<idStr> &list ) {
	list.Clear();
	if ( directory == NULL || directory[0] == '\0' ) {
		return 0;
	}

	DIR *dir = opendir( directory );
	if ( dir == NULL ) {
		return -1;
	}

	struct dirent *entry;
	while ( ( entry = readdir( dir ) ) != NULL ) {
		if ( entry->d_name[0] == '.' ) {
			continue;
		}
		if ( extension != NULL && extension[0] != '\0' ) {
			const char *ext = strrchr( entry->d_name, '.' );
			if ( ext == NULL || idStr::Icmp( ext, extension ) != 0 ) {
				continue;
			}
		}
		list.Append( idStr( entry->d_name ) );
	}
	closedir( dir );
	return list.Num();
}

void *Sys_DLL_Load( const char *dllName, bool checkFullPathMatch ) {
	if ( dllName == NULL || dllName[0] == '\0' ) {
		return NULL;
	}
	void *handle = dlopen( dllName, RTLD_NOW | RTLD_LOCAL );
	if ( handle == NULL ) {
		common->DPrintf( "Sys_DLL_Load: dlopen('%s') failed: %s\n", dllName, dlerror() );
	}
	return handle;
}

void *Sys_DLL_GetProcAddress( void *dllHandle, const char *procName ) {
	if ( dllHandle == NULL || procName == NULL ) {
		return NULL;
	}
	return dlsym( dllHandle, procName );
}

void Sys_DLL_Unload( void *dllHandle ) {
	if ( dllHandle != NULL ) {
		dlclose( dllHandle );
	}
}

/*
================
Console Input & Event Pump
================
*/
static bool s_stdinEOF = false;
static bool s_ttyConfigured = false;
static struct termios s_origTermios;

void Posix_ShutdownConsoleInput( void ) {
	if ( s_ttyConfigured ) {
		tcsetattr( STDIN_FILENO, TCSANOW, &s_origTermios );
		s_ttyConfigured = false;
	}
}

void Posix_InitConsoleInput( void ) {
	if ( isatty( STDIN_FILENO ) ) {
		if ( tcgetattr( STDIN_FILENO, &s_origTermios ) == 0 ) {
			struct termios raw = s_origTermios;
			raw.c_lflag &= ~( ICANON | ECHO );
			raw.c_cc[ VMIN ] = 0;
			raw.c_cc[ VTIME ] = 0;
			tcsetattr( STDIN_FILENO, TCSANOW, &raw );
			s_ttyConfigured = true;
			atexit( Posix_ShutdownConsoleInput );
		}
	} else {
		// Non-tty / piped stdin: set O_NONBLOCK
		int flags = fcntl( STDIN_FILENO, F_GETFL, 0 );
		if ( flags != -1 ) {
			fcntl( STDIN_FILENO, F_SETFL, flags | O_NONBLOCK );
		}
	}
}

char* Posix_ConsoleInput( void ) {
	static char lineBuffer[ 1024 ];
	static int linePos = 0;

	if ( s_stdinEOF ) {
		return NULL;
	}

	struct pollfd pfd;
	pfd.fd = STDIN_FILENO;
	pfd.events = POLLIN;
	pfd.revents = 0;
	int pollRet = poll( &pfd, 1, 0 );
	if ( pollRet <= 0 || ( pfd.revents & POLLIN ) == 0 ) {
		if ( pollRet > 0 && ( pfd.revents & ( POLLERR | POLLHUP | POLLNVAL ) ) != 0 && !isatty( STDIN_FILENO ) ) {
			s_stdinEOF = true;
		}
		return NULL;
	}

	char ch;
	ssize_t n = 0;
	while ( ( n = read( STDIN_FILENO, &ch, 1 ) ) == 1 ) {
		if ( ch == '\r' || ch == '\n' ) {
			if ( linePos > 0 ) {
				lineBuffer[ linePos ] = '\0';
				linePos = 0;
				return lineBuffer;
			}
			continue;
		}
		if ( ch == '\b' || ch == 127 ) {
			if ( linePos > 0 ) {
				linePos--;
			}
			continue;
		}
		if ( linePos < static_cast< int >( sizeof( lineBuffer ) - 1 ) ) {
			lineBuffer[ linePos++ ] = ch;
		}
	}

	if ( n == 0 && !isatty( STDIN_FILENO ) ) {
		// EOF reached on piped input
		if ( linePos > 0 ) {
			lineBuffer[ linePos ] = '\0';
			linePos = 0;
			return lineBuffer;
		}
		s_stdinEOF = true;
	}

	return NULL;
}

void Sys_GenerateEvents( void ) {
#ifndef ID_DEDICATED
	void Sys_ProcessSDLEvents( void );
	Sys_ProcessSDLEvents();
#endif
	Posix_ConsoleFrame();

	char *cmd = Posix_ConsoleInput();
	if ( cmd != NULL && cmd[0] != '\0' ) {
		int len = static_cast< int >( strlen( cmd ) ) + 1;
		char *buf = static_cast< char* >( Mem_Alloc( len ) );
		if ( buf != NULL ) {
			memcpy( buf, cmd, len );
			Posix_QueEvent( SE_CONSOLE, 0, 0, len, buf );
		}
	}
}

/*
================
Main Entry Point
================
*/
#ifdef ID_DEDICATED
int main( int argc, const char **argv ) {
	Posix_EarlyInit();

	if ( argc > 1 ) {
		common->Init( argc - 1, &argv[ 1 ], NULL );
	} else {
		common->Init( 0, NULL, NULL );
	}

	Posix_LateInit();

	while ( 1 ) {
		Sys_HandlePendingQuitSignal();
		common->Frame();
		Sys_Sleep( 1 );
	}

	return 0;
}
#endif

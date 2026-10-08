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
#include <signal.h>
#include <atomic>

#if defined( __APPLE__ )
#include <mach-o/dyld.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <sys/sysctl.h>
#endif

#include "idlib/precompiled.h"
#include "sys_local.h"
#include "sys_render.h"
#include "../../framework/RetailDiscovery.h"
#include "posix_public.h"
#include "../sys_package.h"

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

/*
===============
Sys_FPU_SetFTZ / Sys_FPU_SetDAZ

Deliberately no-ops.  Retail set SSE flush-to-zero/denormals-are-zero, but its
x86 build did scalar float math on the x87 FPU, which ignores them.  On
x86-64 and AArch64 all float math would be affected, and the game's script VM
stores integers in float slots (sdProgram::ReturnBoolean returns the integer
1, and `!sys.isClient()` compiles to a float compare): with the flush modes
on, that value reads as 0 and a network client runs server-only map script.
===============
*/
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

#if defined( __linux__ )
	{
		char resolved[ MAX_OSPATH ];
		const ssize_t length = readlink( "/proc/self/exe", resolved, sizeof( resolved ) - 1 );
		// a result that fills the buffer may be truncated; s_exePath is no larger,
		// so fall back rather than cache a partial path
		if ( length > 0 && length < static_cast< ssize_t >( sizeof( resolved ) - 1 ) ) {
			resolved[ length ] = '\0';
			idStr::Copynz( s_exePath, resolved, sizeof( s_exePath ) );
			return s_exePath;
		}
	}
#endif

	idStr::Copynz( s_exePath, Posix_Cwd(), sizeof( s_exePath ) );
	return s_exePath;
}

static bool Posix_IsDirectory( const idStr &path ) {
	struct stat st;
	return stat( path.c_str(), &st ) == 0 && S_ISDIR( st.st_mode );
}

// The executable's directory when it holds relative (an installed or packaged
// engine: the binaries, the game module and base/ together), else the current
// directory (a development build runs from the repository root).
static const char *Posix_ExeDirectoryWith( idStr &storage, const char *relative ) {
	storage = Sys_EXEPath();
	if ( storage.Length() > 0 ) {
		storage.StripFilename();
		idStr probe = storage;
		probe.AppendPath( relative );
		if ( Posix_IsDirectory( probe ) ) {
			return storage.c_str();
		}
	}
	return Posix_Cwd();
}

const char *Sys_DefaultBasePath( void ) {
	// as openQ4: the executable's directory if it has base/, else the current one
	static idStr basepath;
	return Posix_ExeDirectoryWith( basepath, BASE_GAMEDIR );
}

/*
================
Sys_AppendRetailDataCandidates

Linux's table for RetailDiscovery (ledger 068), in the order probed:
  - the retail Linux client's usual install folders;
  - the Windows game in a Wine prefix: WINEPREFIX, ~/.wine, then the prefix folders of
    Lutris (~/Games/<game>), Bottles (native and Flatpak) and PlayOnLinux, each with the
    Windows install folders under drive_c's Program Files (x86) and Program Files;
  - the Steam roots, in this order: OPENETQW_STEAM_ROOT, the native and Flatpak Steam
    (whose libraries also hold a Proton install of the Windows game), then Steam inside
    each Wine prefix (only its own steamapps: a second library it lists is a Windows path,
    not mapped through drive_c).
Every candidate is checked for base/pak000.pk4 before it is used.
================
*/
void Sys_AppendRetailDataCandidates( idStrList &candidates, idStrList &steamRoots ) {
	const char *home = getenv( "HOME" );
	const bool haveHome = home != NULL && home[0] == '/';

	candidates.Append( "/usr/local/games/etqw" );
	candidates.Append( "/usr/games/etqw" );
	candidates.Append( "/opt/etqw" );
	if ( haveHome ) {
		idStr path = home;
		path.AppendPath( "etqw" );
		candidates.Append( path );
	}

	idStrList prefixes;
	const char *winePrefix = getenv( "WINEPREFIX" );
	if ( winePrefix != NULL && winePrefix[0] == '/' ) {
		prefixes.Append( winePrefix );
	}
	if ( haveHome ) {
		idStr wine = home;
		wine.AppendPath( ".wine" );
		prefixes.Append( wine );
		static const char *const prefixFolders[] = {
			"Games",												// Lutris's default game folder
			".local/share/bottles/bottles",							// Bottles
			".var/app/com.usebottles.bottles/data/bottles/bottles",	// Bottles (Flatpak)
			".PlayOnLinux/wineprefix",								// PlayOnLinux
		};
		for ( const char *folder : prefixFolders ) {
			idStr path = home;
			path.AppendPath( folder );
			RetailDiscovery::AppendWinePrefixesIn( path, prefixes );
		}
	}
	idStrList prefixSteamRoots;
	for ( int i = 0; i < prefixes.Num(); i++ ) {
		RetailDiscovery::AppendWineInstalls( prefixes[i], candidates, prefixSteamRoots );
	}

	RetailDiscovery::SplitPathList( getenv( "OPENETQW_STEAM_ROOT" ), steamRoots );
	const char *xdgDataHome = getenv( "XDG_DATA_HOME" );
	if ( xdgDataHome != NULL && xdgDataHome[0] == '/' ) {
		idStr path = xdgDataHome;
		path.AppendPath( "Steam" );
		steamRoots.Append( path );
	}
	if ( haveHome ) {
		static const char *const roots[] = {
			".steam/steam",
			".steam/root",
			".local/share/Steam",
			".var/app/com.valvesoftware.Steam/.local/share/Steam",	// Flatpak
		};
		for ( const char *root : roots ) {
			idStr path = home;
			path.AppendPath( root );
			steamRoots.Append( path );
		}
	}
	steamRoots.Append( prefixSteamRoots );
}

const char *Sys_DefaultSavePath( void ) {
	// per-user configs, logs and screenshots, as openQ4 (XDG data directory);
	// an installed engine's own directory is often not writable
	static idStr savepath;
	const char *xdgDataHome = getenv( "XDG_DATA_HOME" );
	if ( xdgDataHome != NULL && xdgDataHome[0] == '/' ) {
		savepath = xdgDataHome;
		savepath.StripTrailing( '/' );
		savepath += "/openetqw";
		return savepath.c_str();
	}
	const char *home = getenv( "HOME" );
	if ( home != NULL && home[0] != '\0' ) {
		savepath = home;
		savepath.StripTrailing( '/' );
		savepath += "/.local/share/openetqw";
		return savepath.c_str();
	}
	return Posix_Cwd();
}

bool Sys_GetGameModuleRootDirectory( char *moduleRoot, int moduleRootSize ) {
	// Linux packages keep the module next to the executable (FindDLL's first try)
	if ( moduleRoot != NULL && moduleRootSize > 0 ) {
		moduleRoot[0] = '\0';
	}
	return false;
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
	// the engine's own loose content (base/vkprogs/): next to an installed
	// executable, else in the repository root a development build runs from
	static idStr cdpath;
	return Posix_ExeDirectoryWith( cdpath, BASE_GAMEDIR "/vkprogs" );
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
	Posix_ShutdownConsoleInput();
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
// set (after s_origTermios) before the terminal goes raw, so a fatal signal on any
// thread from then on restores it; lock-free, so the signal handler may use it
static std::atomic<int> s_ttyConfigured( 0 );
static_assert( ATOMIC_INT_LOCK_FREE == 2, "the fatal signal handler reads s_ttyConfigured" );
static struct termios s_origTermios;

/*
==============
Posix_ShutdownConsoleInput

Puts the terminal back the way Posix_InitConsoleInput found it.  Only calls
tcsetattr, which is async-signal-safe, so the fatal signal handler uses it too.
==============
*/
void Posix_ShutdownConsoleInput( void ) {
	// cleared only after the restore, so a fatal signal meanwhile restores it
	// too (restoring twice is harmless)
	if ( s_ttyConfigured.load( std::memory_order_acquire ) ) {
		tcsetattr( STDIN_FILENO, TCSANOW, &s_origTermios );
		s_ttyConfigured.store( 0, std::memory_order_release );
	}
}

void Posix_InitConsoleInput( void ) {
	// Sys_InitInput (during InitGame) and Posix_LateInit both get here.  A
	// second tcgetattr would save the already raw attributes as the original
	// ones, and the terminal was left without echo/line editing after exit.
	if ( s_ttyConfigured.load( std::memory_order_acquire ) ) {
		return;
	}
	if ( isatty( STDIN_FILENO ) ) {
		if ( tcgetattr( STDIN_FILENO, &s_origTermios ) == 0 ) {
			s_ttyConfigured.store( 1, std::memory_order_release );
			struct termios raw = s_origTermios;
			raw.c_lflag &= ~( ICANON | ECHO );
			raw.c_cc[ VMIN ] = 0;
			raw.c_cc[ VTIME ] = 0;
			tcsetattr( STDIN_FILENO, TCSANOW, &raw );
			static bool atExitRegistered = false;
			if ( !atExitRegistered ) {
				atExitRegistered = true;
				atexit( Posix_ShutdownConsoleInput );
			}
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

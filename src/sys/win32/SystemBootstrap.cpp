// Copyright (C) 2007 Id Software, Inc.
//
// ETQW Win32 platform boundary reconstructed against the public SDK.  This
// replaces the incompatible Doom 3 Win32 implementation in the executable
// while the retail platform units are recovered from symbols.

#include "idlib/precompiled.h"
#pragma hdrstop

#include "sys_local.h"
#include "sys_render.h"
#define SDL_MAIN_HANDLED
#include <SDL3/SDL_main.h>
#include "win_local.h"
#include "win_soundthread.h"
#ifdef ETQW_WITH_RADIANT
#include "../../tools/radiant/RadiantImGui.h"
#endif

#include <winsock2.h>
#include <timeapi.h>
#include <direct.h>
#include <io.h>
#include <locale.h>
#include <sys/stat.h>
#include <process.h>
#include <shellapi.h>

// Instantiation of system localization and sysLocal interfaces for Windows builds
const char * sysLanguageNames[] = {
	"english", "spanish", "italian", "german", "french", "russian", 
	"polish", "korean", "japanese", "chinese", NULL
};

idCVar sys_lang( "sys_lang", "english", CVAR_SYSTEM | CVAR_ARCHIVE, "", sysLanguageNames, idCmdSystem::ArgCompletion_String<sysLanguageNames> );

idSysLocal			sysLocal;
idSys *				sys = &sysLocal;

Win32Vars_t win32 = {};

#ifdef ID_DEDICATED
void Sys_ProcessSDLEvents( void ) {}
void IN_Frame( void ) {}
#endif

// Win32 Threading Primitives Declarations & Constants
typedef threadPriority_e xthreadPriority;

const int MAX_CRITICAL_SECTIONS = 8;
const int MAX_LOCAL_CRITICAL_SECTIONS = MAX_CRITICAL_SECTIONS + 1;
const int MAX_TRIGGER_EVENTS = 8;
const int MAX_THREADS = 16;

extern xthreadInfo *g_threads[ MAX_THREADS ];
extern int g_thread_count;

void		Sys_CreateThread( xthread_t function, void *parms, threadPriority_e priority, xthreadInfo &info, const char *name, xthreadInfo *threads[], int *thread_count );
void		Sys_DestroyThread( xthreadInfo& info );
void		Sys_RequestThreadStop( xthreadInfo& info );
bool		Sys_IsThreadStopRequested( const xthreadInfo& info );
bool		Sys_IsCurrentThreadStopRequested( void );
const char *Sys_GetThreadName( int *index = NULL );

void		Sys_EnterCriticalSection( int index = CRITICAL_SECTION_ZERO );
void		Sys_LeaveCriticalSection( int index = CRITICAL_SECTION_ZERO );
void		Sys_WaitForEvent( int index = 0 );
void		Sys_TriggerEvent( int index = 0 );
#ifndef ID_DEDICATED
extern void Sys_CollectPerformanceData();
extern sdPerformanceQuery* Sys_GetPerformanceQuery( sdPerformanceQueryType pqType );
#endif
#ifdef ETQW_WITH_RADIANT
extern int com_editors;
#endif

namespace {

const int SYSTEM_MAX_PRINT_MSG = 4096;
const int SYSTEM_MAX_OS_PATH = 256;
#ifdef ETQW_WITH_RADIANT
const int SYSTEM_EDITOR_RADIANT = BIT( 1 );
#endif

LARGE_INTEGER timerFrequency;
LARGE_INTEGER timerBase;
bool networkingInitialized;

class sdIMEBootstrap : public sdIME {
public:
	sdIMEBootstrap() : enabled( false ) {}
	virtual bool Init() { return true; }
	virtual void Shutdown() { enabled = false; }
	virtual bool LangSupportsIME() const { return false; }
	virtual void Enable( bool value ) { enabled = value; }
	virtual bool IsEnabled() const { return enabled; }
	virtual void FinalizeString( bool ) {}
	virtual int GetCursorChars() const { return 0; }
	virtual bool IsReadingWindowActive() const { return false; }
	virtual bool IsHorizontalReading() const { return true; }
	virtual bool VerticalCandidateLine() const { return false; }
	virtual state_e GetState() const { return enabled ? IME_STATE_ON : IME_STATE_OFF; }
	virtual const wchar_t* GetIndicator() const { return L"A"; }
	virtual bool IsCandidateListActive() const { return false; }
	virtual const wchar_t* GetCandidate( const unsigned int ) const { return L""; }
	virtual int GetCandidateCount() const { return 0; }
	virtual int GetCandidateSelection() const { return 0; }
	virtual const wchar_t* GetCompositionString() const { return L""; }
	virtual const byte* GetCompositionStringAttributes() const { return NULL; }
	virtual const lang_e GetLanguage() const { return IME_LANG_NEUTRAL; }
	virtual const lang_e GetPrimaryLanguage() const { return IME_LANG_NEUTRAL; }
private:
	bool enabled;
};

sdIMEBootstrap ime;

void FillSysTime( const tm& source, sysTime_t& target ) {
	target.tm_sec = source.tm_sec;
	target.tm_min = source.tm_min;
	target.tm_hour = source.tm_hour;
	target.tm_mday = source.tm_mday;
	target.tm_mon = source.tm_mon;
	target.tm_year = source.tm_year;
	target.tm_wday = source.tm_wday;
	target.tm_yday = source.tm_yday;
	target.tm_isdst = source.tm_isdst;
}

time_t MakeTime( const sysTime_t& source ) {
	tm value;
	memset( &value, 0, sizeof( value ) );
	value.tm_sec = source.tm_sec;
	value.tm_min = source.tm_min;
	value.tm_hour = source.tm_hour;
	value.tm_mday = source.tm_mday;
	value.tm_mon = source.tm_mon;
	value.tm_year = source.tm_year;
	value.tm_isdst = source.tm_isdst;
	return mktime( &value );
}

void SockaddrFromNetadr( const netadr_t& address, sockaddr_in& socketAddress ) {
	memset( &socketAddress, 0, sizeof( socketAddress ) );
	socketAddress.sin_family = AF_INET;
	socketAddress.sin_port = htons( address.port );
	memcpy( &socketAddress.sin_addr, address.ip, sizeof( address.ip ) );
	if ( address.type == NA_BROADCAST ) {
		socketAddress.sin_addr.s_addr = INADDR_BROADCAST;
	} else if ( address.type == NA_LOOPBACK ) {
		socketAddress.sin_addr.s_addr = htonl( INADDR_LOOPBACK );
	}
}

void NetadrFromSockaddr( const sockaddr_in& socketAddress, netadr_t& address ) {
	address.type = NA_IP;
	memcpy( address.ip, &socketAddress.sin_addr, sizeof( address.ip ) );
	address.port = ntohs( socketAddress.sin_port );
}

}

sdSysEvent::~sdSysEvent() {
	FreeData();
}

void sdSysEvent::Init( sysEventType_t eventType, int eventValue, int eventValue2, int eventPtrLength, void* eventPtr ) {
	FreeData();
	type = eventType;
	value = eventValue;
	value2 = eventValue2;
	ptrLength = eventPtrLength;
	ptr = eventPtr;
	node.SetOwner( this );
}

void sdSysEvent::FreeData() {
	if ( ptr != NULL ) {
		Mem_Free( ptr );
		ptr = NULL;
	}
	ptrLength = 0;
}

static void Sys_EnsureThreadingInitialized( void );
static void Sys_ShutdownThreading( void );

idSysLocal::~idSysLocal() {
	ClearEvents();
	Sys_ShutdownThreading();
}

void idSysLocal::Init() {
	QueryPerformanceFrequency( &timerFrequency );
	QueryPerformanceCounter( &timerBase );
	Sys_EnsureThreadingInitialized();
	timeBeginPeriod( 1 );
#ifndef ID_DEDICATED
	SDL_SetMainReady();
	// SDL2's SDL_HINT_WINDOWS_DPI_AWARENESS ("permonitorv2") is gone in SDL3.
	// SetProcessDpiAwarenessContext exists from Windows 10 1703 on, so it is
	// resolved at run time; without it the process DPI mode stays as created.
	{
		typedef BOOL (WINAPI *SetProcessDpiAwarenessContext_t)( DPI_AWARENESS_CONTEXT );
		SetProcessDpiAwarenessContext_t setDpiContext =
			reinterpret_cast< SetProcessDpiAwarenessContext_t >( GetProcAddress(
				GetModuleHandleA( "user32.dll" ), "SetProcessDpiAwarenessContext" ) );
		if ( setDpiContext != NULL ) {
			setDpiContext( DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 );
		}
	}
	SDL_SetHint( SDL_HINT_MOUSE_FOCUS_CLICKTHROUGH, "1" );
	if ( !SDL_Init( SDL_INIT_VIDEO | SDL_INIT_EVENTS ) ) {
		Sys_Error( "SDL3 initialization failed: %s", SDL_GetError() );
	}
	PQ_Init();
	if ( sys_soundThread != NULL ) {
		sys_soundThread->StartThread();
	}
#endif
}

void idSysLocal::PostGameInit() {
}

void idSysLocal::Shutdown() {
	// Stop service workers before releasing any input, sound, event, or timer
	// state they can touch.
#ifndef ID_DEDICATED
	if ( sys_soundThread != NULL ) {
		sys_soundThread->StopThread();
	}
#endif
	ClearEvents();
#ifndef ID_DEDICATED
	PQ_ShutDown();
	SDL_Quit();
#endif
	timeEndPeriod( 1 );
	Sys_ShutdownThreading();
}

void idSysLocal::DebugPrintf( const char *fmt, ... ) {
	va_list argptr;
	va_start( argptr, fmt );
	Sys_DebugVPrintf( fmt, argptr );
	va_end( argptr );
}

void idSysLocal::DebugVPrintf( const char *fmt, va_list arg ) {
	Sys_DebugVPrintf( fmt, arg );
}

void idSysLocal::GetCPUInfo( cpuInfo_t& info ) {
	Sys_CPUInfo( info );
}

double idSysLocal::GetClockTicks( void ) {
	return Sys_GetClockTicks();
}

double idSysLocal::ClockTicksPerSecond( void ) {
	return Sys_ClockTicksPerSecond();
}

void idSysLocal::Sleep( int msec ) {
	Sys_Sleep( msec );
}

int idSysLocal::Milliseconds() {
	return Sys_Milliseconds();
}

time_t idSysLocal::RealTime( sysTime_t* value ) {
	return Sys_RealTime( value );
}

const char* idSysLocal::TimeToSystemStr( const sysTime_t& value ) {
	return Sys_TimeToSystemStr( value );
}

const char* idSysLocal::TimeAndDateToSystemStr( const sysTime_t& value ) {
	return Sys_TimeAndDateToSystemStr( value );
}

time_t idSysLocal::TimeDiff( const sysTime_t& from, const sysTime_t& to ) {
	return Sys_TimeDiff( from, to );
}

void idSysLocal::SecondsToTime( const time_t value, sysTime_t& out, bool localTime ) {
	Sys_SecondsToTime( value, out, localTime );
}

const char *idSysLocal::TimeToStr( const sysTime_t& value ) {
	return Sys_TimeToStr( value );
}

const char *idSysLocal::SecondsToStr( const time_t value, bool localTime ) {
	return Sys_SecondsToStr( value, localTime );
}

void idSysLocal::GetCurrentMemoryStatus( sysMemoryStats_t &stats ) {
	Sys_GetCurrentMemoryStatus( stats );
}

void idSysLocal::GetExeLaunchMemoryStatus( sysMemoryStats_t &stats ) {
	Sys_GetExeLaunchMemoryStatus( stats );
}

void idSysLocal::GetProcessMemoryStatus( sysProcessMemoryStats_t &stats ) {
	Sys_GetProcessMemoryStatus( stats );
}

cpuid_t idSysLocal::GetProcessorId( void ) {
	return Sys_GetProcessorId();
}

const char *idSysLocal::GetProcessorString( void ) {
	return Sys_GetProcessorString();
}

const char *idSysLocal::FPU_GetState( void ) {
	return Sys_FPU_GetState();
}

bool idSysLocal::FPU_StackIsEmpty( void ) {
	return Sys_FPU_StackIsEmpty();
}

void idSysLocal::FPU_SetFTZ( bool enable ) {
	Sys_FPU_SetFTZ( enable );
}

void idSysLocal::FPU_SetDAZ( bool enable ) {
	Sys_FPU_SetDAZ( enable );
}

void idSysLocal::FPU_EnableExceptions( int exceptions ) {
	Sys_FPU_EnableExceptions( exceptions );
}

void idSysLocal::GetCurCallStack( address_t *callStack, const int callStackSize ) {
	if ( callStack != NULL && callStackSize > 0 ) {
		memset( callStack, 0, callStackSize * sizeof( address_t ) );
	}
}

const char *idSysLocal::GetCurCallStackStr( int depth ) {
	return "";
}

const char *idSysLocal::GetCallStackStr( const address_t *callStack, const int callStackSize ) {
	return "";
}

void idSysLocal::ShutdownSymbols( void ) {
}

void *idSysLocal::DLL_Load( const char *dllName, bool checkFullPathMatch ) {
	return Sys_DLL_Load( dllName, checkFullPathMatch );
}

void *idSysLocal::DLL_GetProcAddress( void *dllHandle, const char *procName ) {
	return Sys_DLL_GetProcAddress( dllHandle, procName );
}

void idSysLocal::DLL_Unload( void *dllHandle ) {
	Sys_DLL_Unload( dllHandle );
}

void idSysLocal::DLL_GetFileName( const char *baseName, char *dllName, int maxLength ) {
#if defined( _M_ARM64 )
	#define SYS_ARCH_SUFFIX "_arm64"
#elif defined( _M_X64 )
	#define SYS_ARCH_SUFFIX "_x64"
#elif defined( _M_IX86 )
	#define SYS_ARCH_SUFFIX "_x86"
#else
	#define SYS_ARCH_SUFFIX ""
#endif
	idStr::snPrintf( dllName, maxLength, "%s%s.dll", baseName, SYS_ARCH_SUFFIX );
}

const char *idSysLocal::GetFunctionName( const address_t function ) {
	return Sys_GetFunctionName( function );
}

const char *idSysLocal::GetFunctionSourceFile( const address_t function ) {
	return Sys_GetFunctionSourceFile( function );
}

const char *idSysLocal::EXEPath() {
	return Sys_EXEPath();
}

long idSysLocal::File_TimeStamp( FILE* file ) {
	return Sys_FileTimeStamp( file );
}

int idSysLocal::File_Stat( const char* OSPath ) {
	struct _stat status;
	return OSPath != NULL ? _stat( OSPath, &status ) : -1;
}

const sdSysEvent* idSysLocal::GenerateBlankEvent() {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventAllocator.Alloc();
	event->Init( SE_NONE, 0, 0, 0, NULL );
	return event;
}

const sdSysEvent* idSysLocal::GenerateCharEvent( int ch ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventAllocator.Alloc();
	event->Init( SE_CHAR, 0, ch, 0, NULL );
	return event;
}

const sdSysEvent* idSysLocal::GenerateKeyEvent( keyNum_t key, bool down ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventAllocator.Alloc();
	event->Init( SE_KEY, SE_KEY_VALUE( key, key ), SE_KEY_VALUE2( down, false ), 0, NULL );
	return event;
}

const sdSysEvent *idSysLocal::GenerateMouseButtonEvent( int button, bool down ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( SE_MOUSE_BUTTON, button, down, 0, NULL );
	return event;
}

const sdSysEvent *idSysLocal::GenerateMouseMoveEvent( int deltax, int deltay ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( SE_MOUSE, deltax, deltay, 0, NULL );
	return event;
}

const sdSysEvent* idSysLocal::GenerateGuiEvent( int guiValue ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventAllocator.Alloc();
	event->Init( SE_GUI, guiValue, 0, 0, NULL );
	return event;
}

void idSysLocal::FreeEvent( const sdSysEvent* event ) {
	if ( event == NULL ) {
		return;
	}
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* mutableEvent = const_cast< sdSysEvent* >( event );
	mutableEvent->GetNode().Remove();
	mutableEvent->FreeData();
	eventAllocator.Free( mutableEvent );
}

const sdSysEvent* idSysLocal::GetEvent() {
	ProcessOSEvents();
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventQue.Next();
	if ( event == NULL ) {
		// The retail ETQW implementation returns NULL when the queue is empty.
		// GenerateBlankEvent is reserved for journal playback.  Returning a
		// blank event here keeps idEventLoop::RunEventLoop spinning forever.
		return NULL;
	}
	event->GetNode().Remove();
	return event;
}

void idSysLocal::QueEvent( sysEventType_t type, int value, int value2, int ptrLength, void *ptr ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent* event = eventAllocator.Alloc();
	event->Init( type, value, value2, ptrLength, ptr );
	event->GetNode().AddToEnd( eventQue );
}

void idSysLocal::ClearEvents() {
	sdScopedLock< true > queueLock( eventLock );
	// SE_CONSOLE events are command lines (stdin for the dedicated server) and
	// are never stale: dropping them loses the command written right after a
	// map-changing one ("nextMap\necho ..." lost the echo).  Same rule as the
	// POSIX Sys_FreeQueuedEvents( ..., keepConsoleEvents = true ).
	for ( sdSysEvent* event = eventQue.Next(); event != NULL; ) {
		sdSysEvent* next = event->GetNode().Next();
		if ( !event->IsConsoleEvent() ) {
			event->GetNode().Remove();
			event->FreeData();
			eventAllocator.Free( event );
		}
		event = next;
	}
}

void idSysLocal::OpenURL( const char *url, bool quit ) {
	if ( url != NULL ) {
		ShellExecuteA( NULL, "open", url, NULL, NULL, SW_SHOWNORMAL );
	}
	if ( quit ) {
		Sys_Quit();
	}
}

void idSysLocal::StartProcess( const char *exePath, bool quit ) {
	if ( exePath != NULL ) {
		STARTUPINFOA startup;
		PROCESS_INFORMATION process;
		memset( &startup, 0, sizeof( startup ) );
		memset( &process, 0, sizeof( process ) );
		startup.cb = sizeof( startup );
		char commandLine[ SYSTEM_MAX_OS_PATH * 2 ];
		idStr::Copynz( commandLine, exePath, sizeof( commandLine ) );
		if ( CreateProcessA( NULL, commandLine, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &process ) ) {
			CloseHandle( process.hThread );
			CloseHandle( process.hProcess );
		}
	}
	if ( quit ) {
		Sys_Quit();
	}
}

int idSysLocal::MessageBox( const char* title, const char* buffer, messageBoxType_t type ) {
	return Sys_MessageBox( title, buffer, type );
}

/*
==============================================================
Stdin console input

The dedicated server and the test harness drive commands through
stdin.  POSIX assembles them in Posix_ConsoleInput; this is the same
line assembly so commands like 'quit' and 'map valley' reach the
console parser as SE_CONSOLE events.
==============================================================
*/
static char consoleLine[ 1024 ];
static int consolePos = 0;
static bool consoleEOF = false;

static void Win_QueueConsoleLine() {
	const int len = consolePos + 1;
	char* buf = static_cast< char* >( Mem_Alloc( len ) );
	memcpy( buf, consoleLine, len );
	sysLocal.QueEvent( SE_CONSOLE, 0, 0, len, buf );
}

static bool Win_ConsoleReady( HANDLE stdinHandle ) {
	const DWORD type = GetFileType( stdinHandle );
	if ( type == FILE_TYPE_PIPE ) {
		DWORD available = 0;
		if ( !PeekNamedPipe( stdinHandle, NULL, 0, NULL, &available, NULL ) ) {
			// Broken pipe: ReadFile reports the EOF below.
			return true;
		}
		return available > 0;
	}
	return true;
}

/*
An interactive console cannot be polled like a pipe: its handle is signaled by
any input record (a focus change, a mouse move, the key-up of the Enter that
started the process), and a ReadFile in line mode then blocks until the user
presses Enter, which would stop the server's frames while an admin types.  A
reader thread does the blocking reads instead, keeping the console's own echo
and line editing, and hands the text to the main thread.
*/
static CRITICAL_SECTION consoleReaderLock;
static char consoleReaderText[ 4096 ];
static int consoleReaderLength = 0;
static bool consoleReaderEOF = false;
static bool consoleReaderStarted = false;

static DWORD WINAPI Win_ConsoleReaderThread( LPVOID parm ) {
	const HANDLE stdinHandle = static_cast< HANDLE >( parm );
	for ( ;; ) {
		char chunk[ 256 ];
		DWORD read = 0;
		SetLastError( ERROR_SUCCESS );
		const BOOL ok = ReadFile( stdinHandle, chunk, sizeof( chunk ), &read, NULL );
		if ( ( !ok || read == 0 ) && GetLastError() == ERROR_OPERATION_ABORTED ) {
			// Ctrl+C interrupted the read; the control handler deals with it.
			continue;
		}
		EnterCriticalSection( &consoleReaderLock );
		if ( !ok || read == 0 ) {
			// Ctrl+Z at the start of a line, or the console went away.
			consoleReaderEOF = true;
			LeaveCriticalSection( &consoleReaderLock );
			return 0;
		}
		// If the main thread has not drained for a while (a long map load), wait for
		// room instead of dropping what was typed or pasted.
		while ( consoleReaderLength + static_cast< int >( read ) > static_cast< int >( sizeof( consoleReaderText ) ) ) {
			LeaveCriticalSection( &consoleReaderLock );
			Sleep( 10 );
			EnterCriticalSection( &consoleReaderLock );
		}
		memcpy( consoleReaderText + consoleReaderLength, chunk, read );
		consoleReaderLength += static_cast< int >( read );
		LeaveCriticalSection( &consoleReaderLock );
	}
}

// Returns the number of bytes copied into chunk; sets eof once the reader has stopped.
static int Win_TakeConsoleReaderText( HANDLE stdinHandle, char* chunk, int chunkSize, bool& eof ) {
	if ( !consoleReaderStarted ) {
		consoleReaderStarted = true;
		InitializeCriticalSection( &consoleReaderLock );
		const HANDLE thread = CreateThread( NULL, 0, Win_ConsoleReaderThread, stdinHandle, 0, NULL );
		if ( thread == NULL ) {
			eof = true;
			return 0;
		}
		CloseHandle( thread );
	}
	EnterCriticalSection( &consoleReaderLock );
	const int count = Min( chunkSize, consoleReaderLength );
	memcpy( chunk, consoleReaderText, count );
	memmove( consoleReaderText, consoleReaderText + count, consoleReaderLength - count );
	consoleReaderLength -= count;
	eof = consoleReaderEOF && consoleReaderLength == 0;
	LeaveCriticalSection( &consoleReaderLock );
	return count;
}

void Win_ConsoleInput( void ) {
	if ( consoleEOF ) {
		return;
	}
	HANDLE stdinHandle = GetStdHandle( STD_INPUT_HANDLE );
	if ( stdinHandle == INVALID_HANDLE_VALUE || stdinHandle == NULL ) {
		consoleEOF = true;
		return;
	}
	char chunk[ 256 ];
	DWORD read = 0;
	bool eof = false;
	if ( GetFileType( stdinHandle ) == FILE_TYPE_CHAR ) {
		read = static_cast< DWORD >( Win_TakeConsoleReaderText( stdinHandle, chunk, sizeof( chunk ), eof ) );
	} else if ( Win_ConsoleReady( stdinHandle ) ) {
		if ( !ReadFile( stdinHandle, chunk, sizeof( chunk ), &read, NULL ) || read == 0 ) {
			read = 0;
			eof = true;
		}
	}
	for ( DWORD i = 0; i < read; i++ ) {
		const char ch = chunk[ i ];
		if ( ch == '\r' || ch == '\n' ) {
			if ( consolePos > 0 ) {
				consoleLine[ consolePos ] = '\0';
				Win_QueueConsoleLine();
				consolePos = 0;
			}
			continue;
		}
		if ( ch == '\b' || ch == 127 ) {
			if ( consolePos > 0 ) {
				consolePos--;
			}
			continue;
		}
		if ( consolePos < static_cast< int >( sizeof( consoleLine ) - 1 ) ) {
			consoleLine[ consolePos++ ] = ch;
		}
	}
	if ( eof ) {
		// EOF: flush any partial line, like Posix_ConsoleInput.
		if ( consolePos > 0 ) {
			consoleLine[ consolePos ] = '\0';
			Win_QueueConsoleLine();
			consolePos = 0;
		}
		consoleEOF = true;
	}
}

static sdLock inputPollLock;

sdLock& IN_GetPollLock() {
	return inputPollLock;
}

void Sys_InitInput( void ) {
	consolePos = 0;
	consoleEOF = false;
}

void Sys_ShutdownInput( void ) {
}

void idSysLocal::ProcessOSEvents() {
	Win_ConsoleInput();
#ifndef ID_DEDICATED
	Sys_ProcessSDLEvents();
#endif
}

sdPerformanceQuery* idSysLocal::GetPerformanceQuery( sdPerformanceQueryType pqType ) {
#ifndef ID_DEDICATED
	return Sys_GetPerformanceQuery( pqType );
#else
	return NULL;
#endif
}

void idSysLocal::CollectPerformanceData() {
#ifndef ID_DEDICATED
	Sys_CollectPerformanceData();
#endif
}

idWStr idSysLocal::GetClipboardData() {
	wchar_t* data = Sys_GetClipboardData();
	idWStr result = data != NULL ? data : L"";
	if ( data != NULL ) {
		Mem_Free( data );
	}
	return result;
}

void idSysLocal::SetClipboardData( const wchar_t *string ) {
	Sys_SetClipboardData( string );
}

void idSysLocal::SetServerInfo( const char*, const char* ) {
}

void idSysLocal::FlushServerInfo() {
}

void idSysLocal::InitInput() {
	Keyboard().Init();
	IME().Init();
	controllerManager.Init();
}

void idSysLocal::ShutdownInput() {
	controllerManager.Shutdown();
	IME().Shutdown();
	Keyboard().Shutdown();
}

#ifdef ID_DEDICATED
// The SDL3 layer (sdl_events.cpp) provides the real Keyboard/Mouse/IME for the
// client.  The dedicated build has no SDL input stack, so it gets headless
// implementations, mirroring sys_local.cpp.
namespace {

class idKeyboardHeadless : public idKeyboard {
public:
	virtual ~idKeyboardHeadless() {}
	virtual bool Init() { return true; }
	virtual void Shutdown() {}
	virtual void Activate() {}
	virtual void Deactivate() {}
	virtual int PollInputEvents( bool ) { return 0; }
	virtual int ReturnInputEvent( const int, keyNum_t&, bool& ) { return 0; }
	virtual void EndInputEvents() {}
	virtual keyNum_t ConvertScanToKey( unsigned int ) const { return K_INVALID; }
	virtual keyNum_t ConvertCharToKey( char ) const { return K_INVALID; }
	virtual char ConvertScanToChar( unsigned int ) const { return 0; }
	virtual unsigned int ConvertCharToScan( char ) const { return 0; }
	virtual char ConvertKeyToChar( const keyNum_t ) const { return 0; }
	virtual bool IsConsoleKey( const sdSysEvent& ) const { return false; }
};

class idMouseHeadless : public idMouse {
public:
	virtual ~idMouseHeadless() {}
	virtual bool Init() { return true; }
	virtual void Shutdown() {}
	virtual bool IsActive() const { return false; }
	virtual void Activate() {}
	virtual void Deactivate() {}
	virtual void GrabCursor( bool ) {}
	virtual int PollInputEvents( bool ) { return 0; }
	virtual int ReturnInputEvent( const int, int&, int& ) { return 0; }
	virtual void EndInputEvents() {}
};

idKeyboardHeadless headlessKeyboard;
idMouseHeadless headlessMouse;

} // namespace

idKeyboard& idSysLocal::Keyboard() {
	return headlessKeyboard;
}

idMouse& idSysLocal::Mouse() {
	return headlessMouse;
}

sdIME& idSysLocal::IME() {
	return ime;
}
#endif

void idSysLocal::SetSystemLocale() {
	Sys_SetSystemLocale();
}

void idSysLocal::SetDefaultLocale() {
	Sys_SetDefaultLocale();
}

sdLogitechLCDSystem* idSysLocal::GetLCDSystem() {
	return NULL;
}

const char *idSysLocal::NetAdrToString( const netadr_t& address ) const {
	return Sys_NetAdrToString( address );
}

bool idSysLocal::IsLANAddress( const netadr_t& address ) const {
	return Sys_IsLANAddress( address );
}

bool idSysLocal::StringToNetAdr( const char *text, netadr_t *address, bool doDNSResolve ) const {
	return Sys_StringToNetAdr( text, address, doDNSResolve );
}

int idSysLocal::GetGUID( unsigned char* guid, const int len ) const {
	if ( guid == NULL || len <= 0 ) {
		return 0;
	}
	LARGE_INTEGER counter;
	QueryPerformanceCounter( &counter );
	unsigned int state = static_cast< unsigned int >( counter.LowPart ^ GetCurrentProcessId() ^ GetCurrentThreadId() );
	for ( int i = 0; i < len; i++ ) {
		state = state * 1664525u + 1013904223u;
		guid[ i ] = static_cast< unsigned char >( state >> 24 );
	}
	return len;
}

void Sys_Init() {
	sysLocal.Init();
}

void Sys_Shutdown() {
	sysLocal.Shutdown();
}

void Sys_Error( const char *error, ... ) {
	char text[ SYSTEM_MAX_PRINT_MSG ];
	va_list args;
	va_start( args, error );
	idStr::vsnPrintf( text, sizeof( text ), error, args );
	va_end( args );
	OutputDebugStringA( text );
	fprintf( stderr, "%s\n", text );
	fflush( stderr );
#ifndef ID_DEDICATED
	// A launcher or test harness that captures stderr gets the message there; a
	// modal dialog would hold the process until the harness times out.
	const HANDLE errorHandle = GetStdHandle( STD_ERROR_HANDLE );
	const DWORD errorType = ( errorHandle != NULL && errorHandle != INVALID_HANDLE_VALUE ) ?
		GetFileType( errorHandle ) : FILE_TYPE_UNKNOWN;
	if ( errorType != FILE_TYPE_PIPE && errorType != FILE_TYPE_DISK ) {
		MessageBoxA( NULL, text, GAME_NAME, MB_OK | MB_ICONERROR );
	}
#endif
	fflush( NULL );
	ExitProcess( 1 );
}

// Does not return, like posix_main.cpp's _exit: idCommonLocal::Quit calls this
// after Shutdown, and the frame that ran the quit command must not continue on
// the freed state.
void Sys_Quit() {
	fflush( NULL );
	ExitProcess( 0 );
}

bool Sys_AlreadyRunning() {
	return false;
}

void Sys_CPUInfo( cpuInfo_t& info ) {
	SYSTEM_INFO systemInfo;
	GetSystemInfo( &systemInfo );
	info.logicalNum = Max( 1, static_cast< int >( systemInfo.dwNumberOfProcessors ) );
	info.physicalNum = info.logicalNum;
	info.hyperThreadedStatus = HT_CANNOT_DETECT;
}

void Sys_Printf( const char *message, ... ) {
	char text[ SYSTEM_MAX_PRINT_MSG ];
	va_list args;
	va_start( args, message );
	idStr::vsnPrintf( text, sizeof( text ), message, args );
	va_end( args );
	OutputDebugStringA( text );
	fputs( text, stdout );
	fflush( stdout );
}

void Sys_DebugPrintf( const char *message, ... ) {
	va_list args;
	va_start( args, message );
	Sys_DebugVPrintf( message, args );
	va_end( args );
}

void Sys_DebugVPrintf( const char *message, va_list args ) {
	char text[ SYSTEM_MAX_PRINT_MSG ];
	idStr::vsnPrintf( text, sizeof( text ), message, args );
	OutputDebugStringA( text );
}

void Sys_Sleep( int msec ) {
	Sleep( msec > 0 ? msec : 0 );
}

int Sys_Milliseconds() {
	if ( timerFrequency.QuadPart == 0 ) {
		QueryPerformanceFrequency( &timerFrequency );
		QueryPerformanceCounter( &timerBase );
	}
	LARGE_INTEGER now;
	QueryPerformanceCounter( &now );
	return static_cast< int >( ( now.QuadPart - timerBase.QuadPart ) * 1000 / timerFrequency.QuadPart );
}

unsigned long Sys_TimeBase() {
	return static_cast< unsigned long >( time( NULL ) );
}

double Sys_GetClockTicks() {
	LARGE_INTEGER value;
	QueryPerformanceCounter( &value );
	return static_cast< double >( value.QuadPart );
}

double Sys_GetClockTicksNoFlush() {
	return Sys_GetClockTicks();
}

double Sys_ClockTicksPerSecond() {
	if ( timerFrequency.QuadPart == 0 ) {
		QueryPerformanceFrequency( &timerFrequency );
	}
	return static_cast< double >( timerFrequency.QuadPart );
}

time_t Sys_RealTime( sysTime_t* value ) {
	const time_t now = time( NULL );
	if ( value != NULL ) {
		tm local;
		localtime_s( &local, &now );
		FillSysTime( local, *value );
	}
	return now;
}

time_t Sys_TimeDiff( const sysTime_t& from, const sysTime_t& to ) {
	return MakeTime( to ) - MakeTime( from );
}

void Sys_SecondsToTime( time_t value, sysTime_t& out, bool localTime ) {
	tm converted;
	if ( localTime ) {
		localtime_s( &converted, &value );
	} else {
		gmtime_s( &converted, &value );
	}
	FillSysTime( converted, out );
}

const char* Sys_TimeToStr( const sysTime_t& value ) {
	static char text[ 64 ];
	idStr::snPrintf( text, sizeof( text ), "%02d:%02d:%02d", value.tm_hour, value.tm_min, value.tm_sec );
	return text;
}

const char* Sys_SecondsToStr( const time_t value, bool localTime ) {
	sysTime_t converted;
	Sys_SecondsToTime( value, converted, localTime );
	return Sys_TimeToStr( converted );
}

const char* Sys_TimeToSystemStr( const sysTime_t& value ) {
	static char text[ 64 ];
	idStr::snPrintf( text, sizeof( text ), "%04d-%02d-%02d %02d:%02d:%02d", value.tm_year + 1900, value.tm_mon + 1, value.tm_mday, value.tm_hour, value.tm_min, value.tm_sec );
	return text;
}

const char* Sys_TimeAndDateToSystemStr( const sysTime_t& value ) {
	return Sys_TimeToSystemStr( value );
}

cpuid_t Sys_GetProcessorId() {
	return CPUID_GENERIC;
}

cpuid_t Sys_GetCPUId() {
	return Sys_GetProcessorId();
}

const char *Sys_GetProcessorString() {
	return "generic x86";
}

bool Sys_FPU_StackIsEmpty() { return true; }
void Sys_FPU_ClearStack() {}
const char *Sys_FPU_GetState() { return "FPU state unavailable"; }
void Sys_FPU_EnableExceptions( int ) {}
void Sys_FPU_SetPrecision( int ) {}
void Sys_FPU_SetRounding( int ) {}
void Sys_FPU_SetFTZ( bool ) {}
void Sys_FPU_SetDAZ( bool ) {}
int Sys_GetSystemRam() {
	MEMORYSTATUSEX status;
	status.dwLength = sizeof( status );
	GlobalMemoryStatusEx( &status );
	return static_cast< int >( status.ullTotalPhys >> 20 );
}
int Sys_GetVideoRam() { return 0; }
bool Sys_GetGfxDeviceIdentification( idStr &vendorID, idStr &deviceID ) { vendorID.Clear(); deviceID.Clear(); return false; }

int Sys_GetDriveFreeSpace( const char *path ) {
	ULARGE_INTEGER available;
	if ( path == NULL || !GetDiskFreeSpaceExA( path, &available, NULL, NULL ) ) {
		return 0;
	}
	return static_cast< int >( available.QuadPart >> 20 );
}

void Sys_GetCurrentMemoryStatus( sysMemoryStats_t &stats ) {
	MEMORYSTATUSEX status;
	status.dwLength = sizeof( status );
	GlobalMemoryStatusEx( &status );
	memset( &stats, 0, sizeof( stats ) );
	stats.memoryLoad = status.dwMemoryLoad;
	stats.totalPhysical = static_cast< int >( status.ullTotalPhys >> 10 );
	stats.availPhysical = static_cast< int >( status.ullAvailPhys >> 10 );
	stats.totalPageFile = static_cast< int >( status.ullTotalPageFile >> 10 );
	stats.availPageFile = static_cast< int >( status.ullAvailPageFile >> 10 );
	stats.totalVirtual = static_cast< int >( status.ullTotalVirtual >> 10 );
	stats.availVirtual = static_cast< int >( status.ullAvailVirtual >> 10 );
	stats.availExtendedVirtual = static_cast< int >( status.ullAvailExtendedVirtual >> 10 );
}

void Sys_GetExeLaunchMemoryStatus( sysMemoryStats_t &stats ) {
	Sys_GetCurrentMemoryStatus( stats );
}

void Sys_GetProcessMemoryStatus( sysProcessMemoryStats_t &stats ) {
	memset( &stats, 0, sizeof( stats ) );
	Sys_GetCurrentMemoryStatus( stats.globalStats );
}

void Sys_SetPhysicalWorkMemory( int, int ) {}

const char *Sys_GetCurCallStackAddressStr( int ) { return ""; }

void *Sys_DLL_Load( const char *dllName, bool ) {
	return dllName != NULL ? LoadLibraryA( dllName ) : NULL;
}

void *Sys_DLL_GetProcAddress( void* dllHandle, const char *procName ) {
	return dllHandle != NULL && procName != NULL ? reinterpret_cast< void* >( GetProcAddress( static_cast< HMODULE >( dllHandle ), procName ) ) : NULL;
}

void Sys_DLL_Unload( void* dllHandle ) {
	if ( dllHandle != NULL ) {
		FreeLibrary( static_cast< HMODULE >( dllHandle ) );
	}
}

void Sys_GenerateEvents() {
	sysLocal.ProcessOSEvents();
#ifndef ID_DEDICATED
	IN_Frame();
#endif
}

void Sys_PumpEvents() {
	sysLocal.ProcessOSEvents();
}

void Sys_ShowWindow( bool show ) {
	if ( sys3D == NULL ) {
		return;
	}
	if ( show ) {
		sys3D->ShowGameWindow();
	} else {
		sys3D->HideGameWindow();
	}
}

// The splash bitmap (IDB_ETQW_SPLASH) is purged retail content and cannot
// ship, so there is no splash to show on Windows either; the POSIX layer
// stubs this the same way.  win32/splashscreen.cpp keeps the window code.
void Sys_ShowSplashScreen( bool show ) {
}

bool Sys_IsWindowVisible() {
	if ( sys3D == NULL ) {
		return false;
	}
	const HWND window = sys3D->GetGameWindowHandle();
	return window != NULL && IsWindowVisible( window ) != FALSE && !sys3D->IsMinimized();
}

bool Sys_IsWindowFocused() {
	if ( sys3D == NULL ) {
		return false;
	}
	const HWND window = sys3D->GetGameWindowHandle();
	return window != NULL && GetForegroundWindow() == window;
}
void Sys_ShowConsole( int, bool ) {}
void Sys_UpdateConsole() {}

void Sys_Mkdir( const char *path ) {
	if ( path != NULL ) {
		_mkdir( path );
	}
}

int Sys_Rmdir( const char *path ) {
	return path != NULL ? _rmdir( path ) : -1;
}

bool Sys_CopyFile( const char* fromOSPath, const char* toOSPath, bool overwrite ) {
	return fromOSPath != NULL && toOSPath != NULL && CopyFileA( fromOSPath, toOSPath, overwrite ? FALSE : TRUE ) != FALSE;
}

long Sys_FileTimeStamp( FILE *file ) {
	if ( file == NULL ) {
		return 0;
	}
	struct _stat status;
	return _fstat( _fileno( file ), &status ) == 0 ? static_cast< long >( status.st_mtime ) : 0;
}

const char *Sys_DefaultCDPath() {
	static char path[ SYSTEM_MAX_OS_PATH ];
	_getcwd( path, sizeof( path ) );
	path[ sizeof( path ) - 1 ] = '\0';
	return path;
}

const char *Sys_DefaultBasePath() {
	static char path[ SYSTEM_MAX_OS_PATH ];
	_getcwd( path, sizeof( path ) );
	path[ sizeof( path ) - 1 ] = '\0';
	return path;
}

const char *Sys_DefaultSavePath() { return Sys_DefaultBasePath(); }
const char *Sys_DefaultUserPath() { return Sys_DefaultBasePath(); }

const char *Sys_EXEPath() {
	static char path[ SYSTEM_MAX_OS_PATH ];
	GetModuleFileNameA( NULL, path, sizeof( path ) );
	path[ sizeof( path ) - 1 ] = '\0';
	return path;
}

void Sys_GetDesktopSize( int& width, int& height ) {
	width = GetSystemMetrics( SM_CXSCREEN );
	height = GetSystemMetrics( SM_CYSCREEN );
}

int Sys_ListFiles( const char *directory, const char *extension, idList< idStr > &list ) {
	list.Clear();
	if ( directory == NULL ) {
		return -1;
	}
	const bool directoriesOnly = extension != NULL && extension[ 0 ] == '/' && extension[ 1 ] == '\0';
	idStr search = directory;
	search.AppendPath( directoriesOnly ? "*" : va( "*%s", extension != NULL ? extension : "" ) );
	// The CRT wildcard APIs do not consistently accept the mixed separators
	// produced when AppendPath is used with an absolute Windows path.
	search.SlashesToBackSlashes();
	_finddata_t findInfo;
	intptr_t findHandle = _findfirst( search.c_str(), &findInfo );
	if ( findHandle == -1 ) {
		return -1;
	}
	do {
		const bool isDirectory = ( findInfo.attrib & _A_SUBDIR ) != 0;
		if ( isDirectory == directoriesOnly && idStr::Cmp( findInfo.name, "." ) != 0 && idStr::Cmp( findInfo.name, ".." ) != 0 ) {
			list.Append( findInfo.name );
		}
	} while ( _findnext( findHandle, &findInfo ) == 0 );
	_findclose( findHandle );
	return list.Num();
}

void Sys_SetFatalError( const char * ) {}
void Sys_DoPreferences() {}
int Sys_CPUCount( int &logicalNum, int &physicalNum ) {
	cpuInfo_t info;
	Sys_CPUInfo( info );
	logicalNum = info.logicalNum;
	physicalNum = info.physicalNum;
	return info.hyperThreadedStatus;
}
int Sys_GenerateDiag( const char * ) { return 0; }
const char* Sys_GetEnv( const char* name ) { return name != NULL ? getenv( name ) : NULL; }
FILE* Sys_TempFile() { return tmpfile(); }

wchar_t *Sys_GetClipboardData() {
	if ( !OpenClipboard( NULL ) ) {
		return NULL;
	}
	HANDLE handle = GetClipboardData( CF_UNICODETEXT );
	const wchar_t* source = handle != NULL ? static_cast< const wchar_t* >( GlobalLock( handle ) ) : NULL;
	wchar_t* result = NULL;
	if ( source != NULL ) {
		const size_t bytes = ( wcslen( source ) + 1 ) * sizeof( wchar_t );
		result = static_cast< wchar_t* >( Mem_Alloc( bytes ) );
		memcpy( result, source, bytes );
		GlobalUnlock( handle );
	}
	CloseClipboard();
	return result;
}

void Sys_SetClipboardData( const wchar_t *string ) {
	if ( string == NULL || !OpenClipboard( NULL ) ) {
		return;
	}
	EmptyClipboard();
	const size_t bytes = ( wcslen( string ) + 1 ) * sizeof( wchar_t );
	HGLOBAL handle = GlobalAlloc( GMEM_MOVEABLE, bytes );
	if ( handle != NULL ) {
		void* target = GlobalLock( handle );
		memcpy( target, string, bytes );
		GlobalUnlock( handle );
		SetClipboardData( CF_UNICODETEXT, handle );
	}
	CloseClipboard();
}

idCVar net_forceDrop( "net_forceDrop", "0", CVAR_SYSTEM | CVAR_INTEGER, "percentage packet loss" );	// as win_net.cpp: outgoing packets
idCVar net_forceLatency( "net_forceLatency", "0", CVAR_SYSTEM | CVAR_INTEGER, "milliseconds latency added to received packets" );
idCVar net_forceJitter( "net_forceJitter", "0", CVAR_SYSTEM | CVAR_INTEGER, "up to this many more milliseconds of random latency per received packet (order kept)" );

// Received packets held back by net_forceLatency / net_forceJitter (network testing).
// Keyed by socket, so idPort (shared with the game module) is unchanged.
struct delayedPacket_t {
	int				socket;
	int				releaseTime;
	netadr_t		from;
	idList< byte >	data;
};
static idList< delayedPacket_t* > delayedPackets;

// last release time per socket: jitter varies the delay but keeps the arrival
// order, like queueing on a real path (independent per-packet delays reordered
// the fragments of every large message, which the channel then dropped)
struct socketRelease_t {
	int		socket;
	int		releaseTime;
};
static idList< socketRelease_t > socketReleases;

static int Net_ReleaseTime( int netSocket, int wanted ) {
	for ( int i = 0; i < socketReleases.Num(); i++ ) {
		if ( socketReleases[ i ].socket == netSocket ) {
			socketReleases[ i ].releaseTime = Max( wanted, socketReleases[ i ].releaseTime );
			return socketReleases[ i ].releaseTime;
		}
	}
	socketRelease_t release = { netSocket, wanted };
	socketReleases.Append( release );
	return wanted;
}

// a closed socket's descriptor is reused by the next one opened
static void Net_PurgeSocket( int netSocket ) {
	for ( int i = delayedPackets.Num() - 1; i >= 0; i-- ) {
		if ( delayedPackets[ i ]->socket == netSocket ) {
			delete delayedPackets[ i ];
			delayedPackets.RemoveIndex( i );
		}
	}
	for ( int i = socketReleases.Num() - 1; i >= 0; i-- ) {
		if ( socketReleases[ i ].socket == netSocket ) {
			socketReleases.RemoveIndex( i );
		}
	}
}

idPort::idPort() :
	netSocket( -1 ),
	silent( false ),
	packetsRead( 0 ),
	bytesRead( 0 ),
	packetsWritten( 0 ),
	bytesWritten( 0 ) {
	memset( &bound_to, 0, sizeof( bound_to ) );
	bound_to.type = NA_BAD;
}

idPort::~idPort() {
	Close();
}

bool idPort::InitForPort( int portNumber ) {
	Close();
	SOCKET socketHandle = socket( AF_INET, SOCK_DGRAM, IPPROTO_UDP );
	if ( socketHandle == INVALID_SOCKET ) {
		return false;
	}
	u_long nonBlocking = 1;
	ioctlsocket( socketHandle, FIONBIO, &nonBlocking );
	BOOL broadcast = TRUE;
	setsockopt( socketHandle, SOL_SOCKET, SO_BROADCAST, reinterpret_cast< const char* >( &broadcast ), sizeof( broadcast ) );
	sockaddr_in address;
	memset( &address, 0, sizeof( address ) );
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = INADDR_ANY;
	address.sin_port = htons( portNumber == PORT_ANY ? 0 : portNumber );
	if ( bind( socketHandle, reinterpret_cast< sockaddr* >( &address ), sizeof( address ) ) == SOCKET_ERROR ) {
		closesocket( socketHandle );
		return false;
	}
	int addressLength = sizeof( address );
	getsockname( socketHandle, reinterpret_cast< sockaddr* >( &address ), &addressLength );
	netSocket = static_cast< int >( socketHandle );
	bound_to.type = NA_IP;
	memset( bound_to.ip, 0, sizeof( bound_to.ip ) );
	bound_to.port = ntohs( address.sin_port );
	return true;
}

void idPort::Close() {
	if ( netSocket != -1 ) {
		Net_PurgeSocket( netSocket );
		closesocket( static_cast< SOCKET >( netSocket ) );
		netSocket = -1;
	}
	bound_to.type = NA_BAD;
	bound_to.port = 0;
}

bool idPort::GetPacket( netadr_t &from, void *data, int &size, int maxSize ) {
	size = 0;
	if ( netSocket == -1 || data == NULL || maxSize <= 0 ) {
		return false;
	}

	const int latency = Max( 0, net_forceLatency.GetInteger() );
	const int jitter = Max( 0, net_forceJitter.GetInteger() );
	if ( latency == 0 && jitter == 0 && delayedPackets.Num() == 0 ) {
		sockaddr_in address;
		int addressLength = sizeof( address );
		const int received = recvfrom( static_cast< SOCKET >( netSocket ), static_cast< char* >( data ), maxSize, 0, reinterpret_cast< sockaddr* >( &address ), &addressLength );
		if ( received == SOCKET_ERROR ) {
			return false;
		}
		NetadrFromSockaddr( address, from );
		size = received;
		packetsRead++;
		bytesRead += received;
		return true;
	}

	// queue everything that has arrived, each with its own release time
	const int now = Sys_Milliseconds();
	netadr_t packetFrom;
	int packetSize = 0;
	sockaddr_in address;
	int addressLength = sizeof( address );
	while ( ( packetSize = recvfrom( static_cast< SOCKET >( netSocket ), static_cast< char* >( data ), maxSize, 0, reinterpret_cast< sockaddr* >( &address ), &addressLength ) ) > 0 ) {
		delayedPacket_t* packet = new delayedPacket_t;
		packet->socket = netSocket;
		packet->releaseTime = Net_ReleaseTime( netSocket, now + latency + ( jitter > 0 ? rand() % ( jitter + 1 ) : 0 ) );
		NetadrFromSockaddr( address, packet->from );
		packet->data.SetNum( packetSize );
		memcpy( &packet->data[ 0 ], data, packetSize );
		delayedPackets.Append( packet );
		addressLength = sizeof( address );
	}

	// hand out the due packet of this socket that is released first
	int best = -1;
	for ( int i = 0; i < delayedPackets.Num(); i++ ) {
		const delayedPacket_t* packet = delayedPackets[ i ];
		if ( packet->socket == netSocket && packet->releaseTime <= now && ( best < 0 || packet->releaseTime < delayedPackets[ best ]->releaseTime ) ) {
			best = i;
		}
	}
	if ( best < 0 ) {
		return false;
	}
	delayedPacket_t* packet = delayedPackets[ best ];
	delayedPackets.RemoveIndex( best );
	from = packet->from;
	size = Min( packet->data.Num(), maxSize );
	memcpy( data, &packet->data[ 0 ], size );
	delete packet;
	packetsRead++;
	bytesRead += size;
	return true;
}

bool idPort::GetPacketBlocking( netadr_t &from, void *data, int &size, int maxSize, int timeout ) {
	if ( netSocket == -1 ) {
		return false;
	}

	// a packet held back by net_forceLatency / net_forceJitter can be due already,
	// and the socket does not wake select when one becomes due
	if ( GetPacket( from, data, size, maxSize ) ) {
		return true;
	}
	const int now = Sys_Milliseconds();
	for ( int i = 0; i < delayedPackets.Num(); i++ ) {
		if ( delayedPackets[ i ]->socket == netSocket ) {
			timeout = Min( timeout, Max( 0, delayedPackets[ i ]->releaseTime - now ) );
		}
	}
	fd_set readSet;
	FD_ZERO( &readSet );
	FD_SET( static_cast< SOCKET >( netSocket ), &readSet );
	timeval wait;
	wait.tv_sec = timeout / 1000;
	wait.tv_usec = ( timeout % 1000 ) * 1000;
	if ( select( 0, &readSet, NULL, NULL, &wait ) < 0 ) {
		return false;
	}
	// on a timeout too: the wait may have been cut short for a held-back packet
	return GetPacket( from, data, size, maxSize );
}

void idPort::SendPacket( const netadr_t to, const void *data, int size ) {
	if ( silent || netSocket == -1 || data == NULL || size <= 0 ) {
		return;
	}

	const int dropPercent = idMath::ClampInt( 0, 100, net_forceDrop.GetInteger() );
	if ( dropPercent > 0 && rand() < dropPercent * ( RAND_MAX / 100 ) ) {
		packetsWritten++;	// counted as sent, as a lost packet would be
		bytesWritten += size;
		return;
	}

	sockaddr_in address;
	SockaddrFromNetadr( to, address );
	const int sent = sendto( static_cast< SOCKET >( netSocket ), static_cast< const char* >( data ), size, 0, reinterpret_cast< sockaddr* >( &address ), sizeof( address ) );
	if ( sent != SOCKET_ERROR ) {
		packetsWritten++;
		bytesWritten += sent;
	}
}

bool Sys_StringToNetAdr( const char *text, netadr_t *address, bool doDNSResolve ) {
	if ( text == NULL || address == NULL || text[ 0 ] == '\0' ) {
		return false;
	}
	memset( address, 0, sizeof( *address ) );

	if ( idStr::Icmp( text, "localhost" ) == 0 || idStr::Icmp( text, "127.0.0.1" ) == 0 ) {
		address->type = NA_LOOPBACK;
		return true;
	}

	char host[ 256 ];
	idStr::Copynz( host, text, sizeof( host ) );
	char* portText = strrchr( host, ':' );
	int port = 0;
	if ( portText != NULL ) {
		*portText++ = '\0';
		port = atoi( portText );
	}
	address->port = static_cast< unsigned short >( port );

	unsigned long parsed = inet_addr( host );
	if ( parsed == INADDR_NONE && doDNSResolve ) {
		hostent* entry = gethostbyname( host );
		if ( entry != NULL && entry->h_addr_list[ 0 ] != NULL ) {
			memcpy( &parsed, entry->h_addr_list[ 0 ], sizeof( parsed ) );
		}
	}
	if ( parsed == INADDR_NONE ) {
		address->type = NA_BAD;
		return false;
	}
	address->type = NA_IP;
	memcpy( address->ip, &parsed, sizeof( address->ip ) );
	return true;
}

const char *Sys_NetAdrToString( const netadr_t& address ) {
	// rotating buffers, as posix_net.cpp: a caller may format two addresses in one print
	static char text[ 4 ][ 64 ];
	static int index = 0;
	index = ( index + 1 ) & 3;
	if ( address.type == NA_LOOPBACK ) {
		idStr::snPrintf( text[ index ], sizeof( text[ index ] ), "localhost:%u", address.port );
	} else {
		idStr::snPrintf( text[ index ], sizeof( text[ index ] ), "%u.%u.%u.%u:%u", address.ip[ 0 ], address.ip[ 1 ], address.ip[ 2 ], address.ip[ 3 ], address.port );
	}
	return text[ index ];
}

bool Sys_NetAdrToHostName( const netadr_t& address, char** text ) {
	if ( text == NULL ) {
		return false;
	}
	*text = const_cast< char* >( Sys_NetAdrToString( address ) );
	return true;
}

bool Sys_IsLANAddress( const netadr_t& address ) {
	if ( address.type == NA_LOOPBACK ) {
		return true;
	}
	// received addresses are NA_IP, so 127.x is the local host (as posix_net.cpp)
	return address.type == NA_IP &&
		( address.ip[ 0 ] == 10 ||
		  ( address.ip[ 0 ] == 172 && address.ip[ 1 ] >= 16 && address.ip[ 1 ] <= 31 ) ||
		  ( address.ip[ 0 ] == 192 && address.ip[ 1 ] == 168 ) ||
		  address.ip[ 0 ] == 127 );
}

bool Sys_CompareNetAdrBase( const netadr_t& a, const netadr_t& b ) {
	if ( a.type != b.type ) {
		return false;
	}
	if ( a.type == NA_LOOPBACK ) {
		return true;
	}
	return memcmp( a.ip, b.ip, sizeof( a.ip ) ) == 0;
}

int Sys_GetLocalIPCount() { return 1; }
const char *Sys_GetLocalIP( int i ) { return i == 0 ? "127.0.0.1" : NULL; }

void Sys_InitNetworking() {
	if ( networkingInitialized ) {
		return;
	}
	WSADATA data;
	networkingInitialized = WSAStartup( MAKEWORD( 2, 2 ), &data ) == 0;
}

void Sys_ShutdownNetworking() {
	if ( networkingInitialized ) {
		WSACleanup();
		networkingInitialized = false;
	}
}

int Sys_MessageBox( const char* title, const char* buffer, messageBoxType_t type ) {
	UINT flags = MB_OK;
	if ( type == MB_WARNING ) {
		flags |= MB_ICONWARNING;
	} else if ( type == MB_FATALERROR ) {
		flags |= MB_ICONERROR;
	} else {
		flags |= MB_ICONINFORMATION;
	}
	return MessageBoxA( NULL, buffer != NULL ? buffer : "", title != NULL ? title : GAME_NAME, flags );
}

sdLogitechLCDSystem* Sys_GetLogitechLCDSystem() { return NULL; }
void Sys_SetConsoleName( const char* ) {}

bool Sys_GetHTTPProxyAddress( char proxy[ MAX_PROXY_LENGTH ] ) {
	if ( proxy == NULL ) {
		return false;
	}
	const char* value = getenv( "HTTP_PROXY" );
	idStr::Copynz( proxy, value != NULL ? value : "", MAX_PROXY_LENGTH );
	return value != NULL && value[ 0 ] != '\0';
}

void Sys_SetSystemLocale() { setlocale( LC_ALL, "" ); }
void Sys_SetDefaultLocale() { setlocale( LC_ALL, "C" ); }

int Sys_GetLanguageIndex( const char* langName ) {
	for ( int i = 0; sysLanguageNames[ i ] != NULL; i++ ) {
		if ( idStr::Icmp( sysLanguageNames[ i ], langName ) == 0 ) {
			return i;
		}
	}
	return 0;
}

/*
==============================================================

	Win32 64-bit Threading & Synchronization Primitives

==============================================================
*/

struct win32ThreadEntry_t {
	HANDLE				handle;
	DWORD				threadId;
	xthreadInfo *		info;
	volatile bool		stopRequested;
	bool				inUse;
};

static CRITICAL_SECTION	g_criticalSections[ MAX_LOCAL_CRITICAL_SECTIONS ];
static HANDLE			g_triggerEvents[ MAX_TRIGGER_EVENTS ];
static win32ThreadEntry_t g_threadTable[ MAX_THREADS ];
static LONG				g_threadingInitState = 0;

xthreadInfo *			g_threads[ MAX_THREADS ];
int						g_thread_count = 0;

static void Sys_EnsureThreadingInitialized( void ) {
	if ( InterlockedCompareExchange( &g_threadingInitState, 1, 0 ) == 0 ) {
		for ( int i = 0; i < MAX_LOCAL_CRITICAL_SECTIONS; ++i ) {
			InitializeCriticalSectionAndSpinCount( &g_criticalSections[ i ], 4000 );
		}
		for ( int i = 0; i < MAX_TRIGGER_EVENTS; ++i ) {
			g_triggerEvents[ i ] = CreateEventA( NULL, FALSE, FALSE, NULL );
		}
		for ( int i = 0; i < MAX_THREADS; ++i ) {
			g_threadTable[ i ].handle = NULL;
			g_threadTable[ i ].threadId = 0;
			g_threadTable[ i ].info = NULL;
			g_threadTable[ i ].stopRequested = false;
			g_threadTable[ i ].inUse = false;
			g_threads[ i ] = NULL;
		}
		g_thread_count = 0;
		InterlockedExchange( &g_threadingInitState, 2 );
	} else {
		while ( InterlockedCompareExchange( &g_threadingInitState, 2, 2 ) != 2 ) {
			Sleep( 0 );
		}
	}
}

static void Sys_ShutdownThreading( void ) {
	if ( InterlockedCompareExchange( &g_threadingInitState, 0, 2 ) == 2 ) {
		for ( int i = 0; i < MAX_TRIGGER_EVENTS; ++i ) {
			if ( g_triggerEvents[ i ] != NULL ) {
				CloseHandle( g_triggerEvents[ i ] );
				g_triggerEvents[ i ] = NULL;
			}
		}
		for ( int i = 0; i < MAX_LOCAL_CRITICAL_SECTIONS; ++i ) {
			DeleteCriticalSection( &g_criticalSections[ i ] );
		}
	}
}

void Sys_EnterCriticalSection( int index ) {
	Sys_EnsureThreadingInitialized();
	if ( index < 0 || index >= MAX_LOCAL_CRITICAL_SECTIONS ) {
		index = CRITICAL_SECTION_ZERO;
	}
	EnterCriticalSection( &g_criticalSections[ index ] );
}

void Sys_LeaveCriticalSection( int index ) {
	if ( index < 0 || index >= MAX_LOCAL_CRITICAL_SECTIONS ) {
		index = CRITICAL_SECTION_ZERO;
	}
	LeaveCriticalSection( &g_criticalSections[ index ] );
}

void Sys_WaitForEvent( int index ) {
	Sys_EnsureThreadingInitialized();
	if ( index < 0 || index >= MAX_TRIGGER_EVENTS ) {
		index = 0;
	}
	if ( g_triggerEvents[ index ] != NULL ) {
		WaitForSingleObject( g_triggerEvents[ index ], INFINITE );
	}
}

void Sys_TriggerEvent( int index ) {
	Sys_EnsureThreadingInitialized();
	if ( index < 0 || index >= MAX_TRIGGER_EVENTS ) {
		index = 0;
	}
	if ( g_triggerEvents[ index ] != NULL ) {
		SetEvent( g_triggerEvents[ index ] );
	}
}

void Sys_CreateThread( xthread_t function, void *parms, threadPriority_e priority, xthreadInfo &info, const char *name, xthreadInfo *threads[], int *thread_count ) {
	const char *threadName = ( name != NULL && name[0] != '\0' ) ? name : "unnamed";

	info.threadHandle = 0;
	info.threadId = 0;
	info.name = threadName;

	if ( function == NULL || threads == NULL || thread_count == NULL ) {
		return;
	}

	Sys_EnsureThreadingInitialized();

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

	DWORD threadId = 0;
	HANDLE threadHandle = CreateThread(
		NULL,
		0,
		reinterpret_cast< LPTHREAD_START_ROUTINE >( function ),
		parms,
		0,
		&threadId
	);

	if ( threadHandle != NULL ) {
		if ( priority == THREAD_HIGHEST ) {
			SetThreadPriority( threadHandle, THREAD_PRIORITY_HIGHEST );
		} else if ( priority == THREAD_ABOVE_NORMAL ) {
			SetThreadPriority( threadHandle, THREAD_PRIORITY_ABOVE_NORMAL );
		} else if ( priority == THREAD_BELOW_NORMAL ) {
			SetThreadPriority( threadHandle, THREAD_PRIORITY_BELOW_NORMAL );
		} else if ( priority == THREAD_LOWEST ) {
			SetThreadPriority( threadHandle, THREAD_PRIORITY_LOWEST );
		}

		g_threadTable[ slot ].handle = threadHandle;
		g_threadTable[ slot ].threadId = threadId;
		g_threadTable[ slot ].info = &info;
		g_threadTable[ slot ].stopRequested = false;
		g_threadTable[ slot ].inUse = true;

		// 1-based index stored in integer threadHandle to eliminate 64-bit pointer truncation
		info.threadHandle = slot + 1;
		info.threadId = threadId;
		if ( *thread_count < MAX_THREADS ) {
			threads[ ( *thread_count )++ ] = &info;
		}
	} else {
		common->Printf( "Sys_CreateThread: CreateThread failed for %s (error %lu)\n", threadName, GetLastError() );
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
	DWORD currentId = GetCurrentThreadId();
	bool stop = false;

	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	for ( int i = 0; i < MAX_THREADS; i++ ) {
		if ( g_threadTable[ i ].inUse && g_threadTable[ i ].threadId == currentId ) {
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
	HANDLE handle = g_threadTable[ slot ].handle;
	DWORD tid = g_threadTable[ slot ].threadId;
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );

	if ( tid == GetCurrentThreadId() ) {
		common->Error( "Sys_DestroyThread: attempted to destroy calling thread '%s'", info.name ? info.name : "unnamed" );
		return;
	}

	Sys_RequestThreadStop( info );
	WaitForSingleObject( handle, INFINITE );
	CloseHandle( handle );

	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	g_threadTable[ slot ].handle = NULL;
	g_threadTable[ slot ].threadId = 0;
	g_threadTable[ slot ].info = NULL;
	g_threadTable[ slot ].stopRequested = false;
	g_threadTable[ slot ].inUse = false;

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
	DWORD currentId = GetCurrentThreadId();
	Sys_EnterCriticalSection( CRITICAL_SECTION_ZERO );
	for ( int i = 0; i < MAX_THREADS; i++ ) {
		if ( g_threadTable[ i ].inUse && g_threadTable[ i ].threadId == currentId ) {
			if ( index != NULL ) {
				*index = i;
			}
			const char *n = ( g_threadTable[ i ].info != NULL && g_threadTable[ i ].info->name != NULL ) ? g_threadTable[ i ].info->name : "unnamed";
			Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
			return n;
		}
	}
	if ( index != NULL ) {
		*index = -1;
	}
	Sys_LeaveCriticalSection( CRITICAL_SECTION_ZERO );
	return "main";
}

#ifndef ID_DEDICATED
int WINAPI WinMain( HINSTANCE hInstance, HINSTANCE, LPSTR commandLine, int ) {
	win32.hInstance = hInstance;
	Sys_ShowSplashScreen( true );
	const char* argv[] = { GAME_NAME };
	common->Init( 1, argv, commandLine != NULL ? commandLine : "" );
	// Fallback for command lines that deliberately skip game-DLL startup and
	// therefore never emit the normal "LOADING GAME: INIT UI" handoff.
	Sys_ShowSplashScreen( false );
	// The game window was normally revealed by the INIT UI loading message.
	// Once all common initialization is complete, activate it for input.  This
	// is especially important when Visual Studio owns the foreground window
	// while launching the process under F5.
	if ( sys3D != NULL && sys3D->GetGameWindowHandle() != NULL ) {
#ifdef ETQW_WITH_RADIANT
		if ( com_editors & SYSTEM_EDITOR_RADIANT ) {
			// +editor can finish creating Radiant during common initialization.
			// Do not unconditionally re-show the SDL game window over it here.
			sys3D->HideGameWindow();
			sys->Mouse().GrabCursor( false );
			RadiantImGuiFocus();
		} else
#endif
		{
			sys3D->ShowGameWindow();
			HWND gameWindow = reinterpret_cast< HWND >( sys3D->GetGameWindowHandle() );
			SetForegroundWindow( gameWindow );
			SetActiveWindow( gameWindow );
			SetFocus( gameWindow );
			// If the window already owned keyboard focus before Sys_InitInput ran,
			// SetFocus does not emit WM_SETFOCUS again.  Input initialization leaves
			// mouseReleased set deliberately, so clear it explicitly after the entire
			// engine is initialized instead of depending on another focus message.
			sys->Mouse().GrabCursor( true );
		}
	}
	// "quit" ends the process: idCommonLocal::Quit shuts down, then Sys_Quit exits.
	while ( 1 ) {
		common->Frame();
		Sys_Sleep( 1 );
	}
	return 0;
}
#endif

/*
==============================================================

	Console Dedicated Server Entry Point (Subsystem: Console)

==============================================================
*/

#ifdef ID_DEDICATED
// Console control events arrive on a thread the system creates for them.  As
// with the POSIX SIGINT/SIGTERM handler, the handler only records the request;
// the main loop turns it into a "quit" command, so the shutdown runs on the main
// thread between frames.
static volatile LONG pendingCtrlEvent = -1;

static const char *Sys_CtrlEventName( LONG ctrlType ) {
	switch ( ctrlType ) {
	case CTRL_C_EVENT:			return "CTRL_C_EVENT";
	case CTRL_BREAK_EVENT:		return "CTRL_BREAK_EVENT";
	case CTRL_CLOSE_EVENT:		return "CTRL_CLOSE_EVENT";
	case CTRL_SHUTDOWN_EVENT:	return "CTRL_SHUTDOWN_EVENT";
	default:					return "console control event";
	}
}

static BOOL WINAPI Sys_ConsoleCtrlHandler( DWORD ctrlType ) {
	switch ( ctrlType ) {
	case CTRL_C_EVENT:
	case CTRL_BREAK_EVENT:
		InterlockedCompareExchange( &pendingCtrlEvent, static_cast< LONG >( ctrlType ), -1 );
		return TRUE;
	case CTRL_CLOSE_EVENT:
	case CTRL_SHUTDOWN_EVENT:
		// The system ends the process as soon as this handler returns (or after
		// its timeout), so hold this thread while the main thread shuts down;
		// Sys_Quit's ExitProcess ends it.
		InterlockedCompareExchange( &pendingCtrlEvent, static_cast< LONG >( ctrlType ), -1 );
		Sleep( 10000 );
		return TRUE;
	default:
		return FALSE;
	}
}

static void Sys_HandlePendingCtrlEvent( void ) {
	const LONG ctrlType = InterlockedExchange( &pendingCtrlEvent, -1 );
	if ( ctrlType == -1 ) {
		return;
	}
	common->Printf( "Received %s, shutting down...\n", Sys_CtrlEventName( ctrlType ) );
	if ( cmdSystem != NULL ) {
		cmdSystem->BufferCommandText( CMD_EXEC_NOW, "quit\n" );
	} else {
		common->Quit();
	}
}

int main( int argc, char **argv ) {
	win32.hInstance = GetModuleHandleA( NULL );
	SetConsoleCtrlHandler( Sys_ConsoleCtrlHandler, TRUE );

	if ( argc > 1 ) {
		common->Init( argc - 1, const_cast< const char ** >( &argv[ 1 ] ), NULL );
	} else {
		common->Init( 0, NULL, NULL );
	}

	if ( sys3D != NULL && sys3D->GetGameWindowHandle() != NULL ) {
#ifdef ETQW_WITH_RADIANT
		if ( com_editors & SYSTEM_EDITOR_RADIANT ) {
			sys3D->HideGameWindow();
			sys->Mouse().GrabCursor( false );
			RadiantImGuiFocus();
		} else
#endif
		{
			sys3D->ShowGameWindow();
			HWND gameWindow = reinterpret_cast< HWND >( sys3D->GetGameWindowHandle() );
			SetForegroundWindow( gameWindow );
			SetActiveWindow( gameWindow );
			SetFocus( gameWindow );
			sys->Mouse().GrabCursor( true );
		}
	}

	// "quit" ends the process: idCommonLocal::Quit shuts down, then Sys_Quit exits.
	while ( 1 ) {
		Sys_HandlePendingCtrlEvent();
		common->Frame();
		Sys_Sleep( 1 );
	}
	return 0;
}
#endif


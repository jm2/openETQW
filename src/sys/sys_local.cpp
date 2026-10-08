// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "idlib/precompiled.h"
#pragma hdrstop

#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>

#include "sys_local.h"
#include "../framework/Session_local.h"
#include "posix/posix_public.h"

const char * sysLanguageNames[] = {
	"english", "spanish", "italian", "german", "french", "russian", 
	"polish", "korean", "japanese", "chinese", NULL
};

idCVar sys_lang( "sys_lang", "english", CVAR_SYSTEM | CVAR_ARCHIVE, "", sysLanguageNames, idCmdSystem::ArgCompletion_String<sysLanguageNames> );

idSysLocal			sysLocal;
idSys *				sys = &sysLocal;

/*
==============================================================
sdSysEvent
==============================================================
*/
sdSysEvent::~sdSysEvent() {
	FreeData();
}

void sdSysEvent::Init( sysEventType_t _type, int _value, int _value2, int _ptrLength, void* _ptr ) {
	FreeData();
	type = _type;
	value = _value;
	value2 = _value2;
	ptrLength = _ptrLength;
	ptr = _ptr;
	node.SetOwner( this );
}

void sdSysEvent::FreeData() {
	if ( ptr != NULL ) {
		Mem_Free( ptr );
		ptr = NULL;
	}
	ptrLength = 0;
}

/*
==============================================================
Headless Input Stubs
==============================================================
*/
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

class sdIMEHeadless : public sdIME {
public:
	virtual bool Init() { return true; }
	virtual void Shutdown() {}
	virtual bool LangSupportsIME() const { return false; }
	virtual void Enable( bool ) {}
	virtual bool IsEnabled() const { return false; }
	virtual void FinalizeString( bool ) {}
	virtual int GetCursorChars() const { return 0; }
	virtual bool IsReadingWindowActive() const { return false; }
	virtual bool IsHorizontalReading() const { return false; }
	virtual bool VerticalCandidateLine() const { return false; }
	virtual state_e GetState() const { return IME_STATE_OFF; }
	virtual const wchar_t* GetIndicator() const { return L""; }
	virtual bool IsCandidateListActive() const { return false; }
	virtual const wchar_t* GetCandidate( const unsigned int ) const { return L""; }
	virtual int GetCandidateCount() const { return 0; }
	virtual int GetCandidateSelection() const { return 0; }
	virtual const wchar_t* GetCompositionString() const { return L""; }
	virtual const byte* GetCompositionStringAttributes() const { return NULL; }
	virtual const lang_e GetLanguage() const { return IME_LANG_NEUTRAL; }
	virtual const lang_e GetPrimaryLanguage() const { return IME_LANG_NEUTRAL; }
};

static idKeyboardHeadless headlessKeyboard;
static idMouseHeadless headlessMouse;
static sdIMEHeadless headlessIME;

} // namespace

/*
==============
Sys_FreeQueuedEvents

Frees the queued events, or with keepConsoleEvents only the input events.
==============
*/
static void Sys_FreeQueuedEvents( idLinkList< sdSysEvent >& queue, idBlockAlloc< sdSysEvent, 128 >& allocator, bool keepConsoleEvents ) {
	sdSysEvent *event = queue.Next();
	while ( event != NULL ) {
		sdSysEvent *next = event->GetNode().Next();
		if ( !keepConsoleEvents || !event->IsConsoleEvent() ) {
			event->GetNode().Remove();
			event->FreeData();
			allocator.Free( event );
		}
		event = next;
	}
}

/*
==============================================================
idSysLocal
==============================================================
*/
idSysLocal::~idSysLocal( void ) {
	sdScopedLock< true > queueLock( eventLock );
	Sys_FreeQueuedEvents( eventQue, eventAllocator, false );
}

void idSysLocal::Init( void ) {
	{
		sdScopedLock< true > queueLock( eventLock );
		Sys_FreeQueuedEvents( eventQue, eventAllocator, false );
	}
	Sys_InitNetworking();
}

void idSysLocal::PostGameInit( void ) {
}

void idSysLocal::Shutdown( void ) {
	{
		sdScopedLock< true > queueLock( eventLock );
		Sys_FreeQueuedEvents( eventQue, eventAllocator, false );
	}
	Sys_ShutdownNetworking();
	Posix_ShutdownConsole();
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

time_t idSysLocal::RealTime( sysTime_t* sysTime ) {
	return Sys_RealTime( sysTime );
}

const char* idSysLocal::TimeToSystemStr( const sysTime_t& sysTime ) {
	static char buffer[ 64 ];
	idStr::snPrintf( buffer, sizeof( buffer ), "%02d:%02d:%02d", sysTime.tm_hour, sysTime.tm_min, sysTime.tm_sec );
	return buffer;
}

const char* idSysLocal::TimeAndDateToSystemStr( const sysTime_t& sysTime ) {
	static char buffer[ 128 ];
	idStr::snPrintf( buffer, sizeof( buffer ), "%04d-%02d-%02d %02d:%02d:%02d",
		sysTime.tm_year + 1900, sysTime.tm_mon + 1, sysTime.tm_mday,
		sysTime.tm_hour, sysTime.tm_min, sysTime.tm_sec );
	return buffer;
}

time_t idSysLocal::TimeDiff( const sysTime_t& from, const sysTime_t& to ) {
	struct tm tmFrom, tmTo;
	memset( &tmFrom, 0, sizeof( tmFrom ) );
	memset( &tmTo, 0, sizeof( tmTo ) );
	tmFrom.tm_sec = from.tm_sec; tmFrom.tm_min = from.tm_min; tmFrom.tm_hour = from.tm_hour;
	tmFrom.tm_mday = from.tm_mday; tmFrom.tm_mon = from.tm_mon; tmFrom.tm_year = from.tm_year;
	tmTo.tm_sec = to.tm_sec; tmTo.tm_min = to.tm_min; tmTo.tm_hour = to.tm_hour;
	tmTo.tm_mday = to.tm_mday; tmTo.tm_mon = to.tm_mon; tmTo.tm_year = to.tm_year;
	return static_cast< time_t >( difftime( mktime( &tmTo ), mktime( &tmFrom ) ) );
}

void idSysLocal::SecondsToTime( const time_t t, sysTime_t& out, bool localTime ) {
	struct tm result;
	if ( localTime ) {
		localtime_r( &t, &result );
	} else {
		gmtime_r( &t, &result );
	}
	out.tm_sec = result.tm_sec;
	out.tm_min = result.tm_min;
	out.tm_hour = result.tm_hour;
	out.tm_mday = result.tm_mday;
	out.tm_mon = result.tm_mon;
	out.tm_year = result.tm_year;
	out.tm_wday = result.tm_wday;
	out.tm_yday = result.tm_yday;
	out.tm_isdst = result.tm_isdst;
}

const char *idSysLocal::TimeToStr( const sysTime_t& t ) {
	return TimeToSystemStr( t );
}

const char *idSysLocal::SecondsToStr( const time_t t, bool localTime ) {
	sysTime_t st;
	SecondsToTime( t, st, localTime );
	return TimeAndDateToSystemStr( st );
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

const char *idSysLocal::GetFunctionName( const address_t function ) {
	return "";
}

const char *idSysLocal::GetFunctionSourceFile( const address_t function ) {
	return "";
}

void idSysLocal::ShutdownSymbols( void ) {
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
#if defined( __aarch64__ ) || defined( __arm64__ ) || defined( _M_ARM64 )
	#define SYS_ARCH_SUFFIX "_arm64"
#elif defined( __x86_64__ ) || defined( _M_X64 )
	#define SYS_ARCH_SUFFIX "_x64"
#elif defined( __i386__ ) || defined( _M_IX86 )
	#define SYS_ARCH_SUFFIX "_x86"
#else
	#define SYS_ARCH_SUFFIX ""
#endif
#ifdef _WIN32
	idStr::snPrintf( dllName, maxLength, "%s%s.dll", baseName, SYS_ARCH_SUFFIX );
#elif defined( __linux__ )
	idStr::snPrintf( dllName, maxLength, "%s%s.so", baseName, SYS_ARCH_SUFFIX );
#elif defined( MACOS_X )
	idStr::snPrintf( dllName, maxLength, "%s%s.dylib", baseName, SYS_ARCH_SUFFIX );
#else
#error OS define is required
#endif
}

const char *idSysLocal::EXEPath( void ) {
	return Sys_EXEPath();
}

long idSysLocal::File_TimeStamp( FILE* f ) {
	return Sys_FileTimeStamp( f );
}

int idSysLocal::File_Stat( const char* OSPath ) {
	struct stat st;
	return ( OSPath != NULL && stat( OSPath, &st ) == 0 ) ? 0 : -1;
}

const sdSysEvent *idSysLocal::GenerateBlankEvent( void ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( SE_NONE, 0, 0, 0, NULL );
	return event;
}

const sdSysEvent *idSysLocal::GenerateCharEvent( int ch ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( SE_CHAR, 0, ch, 0, NULL );
	return event;
}

const sdSysEvent *idSysLocal::GenerateKeyEvent( keyNum_t key, bool down ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
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

const sdSysEvent *idSysLocal::GenerateGuiEvent( int value ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( SE_GUI, value, 0, 0, NULL );
	return event;
}

const sdSysEvent *idSysLocal::GetEvent( void ) {
	ProcessOSEvents();
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventQue.Next();
	if ( event == NULL ) {
		return NULL;
	}
	event->GetNode().Remove();
	return event;
}

void idSysLocal::QueEvent( sysEventType_t type, int value, int value2, int ptrLength, void *ptr ) {
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *event = eventAllocator.Alloc();
	event->Init( type, value, value2, ptrLength, ptr );
	event->GetNode().AddToEnd( eventQue );
}

/*
==============
idSysLocal::ClearEvents

Session::ExecuteMapChange calls this to drop key, mouse and other input that
went stale during a load.  SE_CONSOLE events are command lines (stdin on the
dedicated server) and are never stale: the event pump reads one line ahead of
the command it returns, so dropping them silently lost the command written
right after a map-changing one ("nextMap\necho ..." lost the echo).
==============
*/
void idSysLocal::ClearEvents( void ) {
	sdScopedLock< true > queueLock( eventLock );
	Sys_FreeQueuedEvents( eventQue, eventAllocator, true );
}

void idSysLocal::FreeEvent( const sdSysEvent *event ) {
	if ( event == NULL ) {
		return;
	}
	sdScopedLock< true > queueLock( eventLock );
	sdSysEvent *mutableEvent = const_cast< sdSysEvent* >( event );
	mutableEvent->GetNode().Remove();
	mutableEvent->FreeData();
	eventAllocator.Free( mutableEvent );
}

#if !defined( MACOS_X )
void idSysLocal::OpenURL( const char *url, bool quit ) {
	if ( quit ) {
		Sys_Quit();
	}
}
#endif

void idSysLocal::StartProcess( const char *exeName, bool quit ) {
	if ( quit ) {
		Sys_Quit();
	}
}

int idSysLocal::MessageBox( const char* title, const char* buffer, messageBoxType_t type ) {
	return Sys_MessageBox( title, buffer, type );
}

void idSysLocal::ProcessOSEvents() {
	Sys_GenerateEvents();
}

sdPerformanceQuery* idSysLocal::GetPerformanceQuery( sdPerformanceQueryType pqType ) {
	return NULL;
}

void idSysLocal::CollectPerformanceData( void ) {
}

idWStr idSysLocal::GetClipboardData( void ) {
	return idWStr();
}

void idSysLocal::SetClipboardData( const wchar_t *string ) {
}

// the game's server info writes: the shared engine helpers (Session.cpp), as on Windows
void idSysLocal::SetServerInfo( const char* key, const char* value ) {
	Session_SetServerInfoFromGame( key, value );
}

void idSysLocal::FlushServerInfo( void ) {
	Session_FlushServerInfoFromGame();
}

void idSysLocal::InitInput() {
}

void idSysLocal::ShutdownInput() {
}

#ifdef ID_DEDICATED
idKeyboard& idSysLocal::Keyboard() {
	return headlessKeyboard;
}

idMouse& idSysLocal::Mouse() {
	return headlessMouse;
}

sdIME& idSysLocal::IME() {
	return headlessIME;
}
#endif

void idSysLocal::SetSystemLocale() {
}

void idSysLocal::SetDefaultLocale() {
}

sdLogitechLCDSystem* idSysLocal::GetLCDSystem( void ) {
	return NULL;
}

const char *idSysLocal::NetAdrToString( const netadr_t& a ) const {
	return Sys_NetAdrToString( a );
}

bool idSysLocal::IsLANAddress( const netadr_t& a ) const {
	return Sys_IsLANAddress( a );
}

bool idSysLocal::StringToNetAdr( const char *s, netadr_t *a, bool doDNSResolve ) const {
	return Sys_StringToNetAdr( s, a, doDNSResolve );
}

int idSysLocal::GetGUID( unsigned char* guid, const int len ) const {
	if ( guid == NULL || len <= 0 ) {
		return 0;
	}
	unsigned int state = static_cast< unsigned int >( Sys_Milliseconds() ^ getpid() );
	for ( int i = 0; i < len; i++ ) {
		state = state * 1664525u + 1013904223u;
		guid[ i ] = static_cast< unsigned char >( state >> 24 );
	}
	return len;
}

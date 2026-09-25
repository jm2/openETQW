// Copyright (C) 2007 Id Software, Inc.
//
// ETQW common-system reconstruction.  The public interface comes from the
// released SDK; the private object layout and behaviour are backed by the
// Microsoft PDB and the matching retail executable disassembly.

#include "../idlib/precompiled.h"
#pragma hdrstop

#if !defined( _WIN32 )
#include <execinfo.h>	// backtrace() in FatalError
#include <unistd.h>
#endif
#include <algorithm>
#include <chrono>
#include <mutex>
#include <thread>
#include <vector>
#include "precompiled.h"
#include "AdManager.h"
#include "GraphManager.h"
#include "GameModuleCallbacks.h"
#include "NotificationSystem.h"
#include "../decllib/declLocStr.h"
#include "../renderer/DeviceContext.h"
#include "../renderer/renderbindingmanager.h"
#include "../bse/BSEInterface.h"
#include "../navigation/Navigation.h"
#include "../sdnet/SDNet.h"
#include "../sys/sys_render.h"
#include "../tools/compilers/compiler_public.h"
#ifdef ETQW_WITH_RADIANT
#include "../tools/edit_public.h"
#endif

#define MAX_PRINT_MSG_SIZE 4096
#define MAX_WARNING_LIST 256
#define MAX_CONSOLE_LINES 32

enum errorParm_t {
	ERP_NONE,
	ERP_FATAL,
	ERP_DROP,
	ERP_DISCONNECT
};

struct versionString_t {
	versionString_t() {
		idStr::snPrintf(
			string,
			sizeof( string ),
			"%s %d.%d.%d.%d %s %s",
			GAME_NAME,
			ENGINE_VERSION_MAJOR,
			ENGINE_VERSION_MINOR,
			ENGINE_SRC_REVISION,
			ENGINE_MEDIA_REVISION,
			BUILD_STRING,
			__DATE__
		);
	}

	char string[ 256 ];
};

static versionString_t versionString;

idCVar com_version( "si_version", versionString.string, CVAR_SYSTEM | CVAR_ROM | CVAR_SERVERINFO, "engine version" );
idCVar com_skipRenderer( "com_skipRenderer", "0", CVAR_BOOL | CVAR_SYSTEM, "skip renderer initialization" );
idCVar com_machineSpec( "com_machineSpec", "-1", CVAR_INTEGER | CVAR_ARCHIVE | CVAR_SYSTEM, "hardware quality classification", -1, 3 );
idCVar com_gpuSpec( "com_gpuSpec", "3", CVAR_INTEGER | CVAR_ARCHIVE | CVAR_SYSTEM,
	"hardware classification, -1 = not detected, 0 = low quality, 1 = medium quality, 2 = high quality, 3 = ultra quality" );
idCVar com_purgeAll( "com_purgeAll", "0", CVAR_BOOL | CVAR_ARCHIVE | CVAR_SYSTEM, "purge all media between level loads" );
idCVar com_memoryMarker( "com_memoryMarker", "-1", CVAR_INTEGER | CVAR_SYSTEM | CVAR_INIT, "memory statistics marker" );
idCVar com_preciseTic( "com_preciseTic", "1", CVAR_BOOL | CVAR_SYSTEM, "run exact user command ticks" );
idCVar com_asyncInput( "com_asyncInput", "1", CVAR_BOOL | CVAR_SYSTEM | CVAR_ROM, "input is sampled by the dedicated input thread" );
idCVar com_asyncSound( "com_asyncSound", "1", CVAR_INTEGER | CVAR_SYSTEM | CVAR_ROM, "sound is serviced by the dedicated sound thread", 1, 1 );
idCVar com_unlockFPS( "com_unlockFPS", "1", CVAR_BOOL | CVAR_SYSTEM | CVAR_ARCHIVE | CVAR_PROFILE,
	"allow renderer to go faster than game ticks" );
idCVar com_unlock_avgFrames( "com_unlock_avgFrames", "4", CVAR_INTEGER | CVAR_SYSTEM | CVAR_NOCHEAT,
	"average out render times over n frames for extra renders management", 1, 10 );
idCVar com_unlock_safetyMargin( "com_unlock_safetyMargin", "0.1", CVAR_FLOAT | CVAR_SYSTEM | CVAR_NOCHEAT,
	"percentage safety margin under which not to trigger an extra render" );
idCVar com_unlock_timingMethod( "com_unlock_timingMethod", "2", CVAR_INTEGER | CVAR_SYSTEM | CVAR_NOCHEAT,
	"method of timing interpolated/extrapolated frames: 0 = whenever they fit;  1 = on boundaries of framerate multiples of 30;  2 = like 1, but using total previous everything time to estimate, not just render time" );
idCVar com_maxFPS( "com_maxFPS", "60", CVAR_INTEGER | CVAR_SYSTEM | CVAR_ARCHIVE,
	"maximum frames per second presented (0 = unlimited / VSync-only)" );
idCVar com_unlock_maxFPS( "com_unlock_maxFPS", "60", CVAR_INTEGER | CVAR_SYSTEM | CVAR_NOCHEAT,
	"maximum FPS unlock will try to reach (only works in timing methods 1 & 2) - zero = no limit" );
idCVar com_logFrameTiming( "com_logFrameTiming", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT,
	"print a FRAME_TIMING line every second: frames, game ticks and the distribution of frame intervals and frame work times" );

int com_frameTicks = 0;

idCVar com_forceGenericSIMD( "com_forceGenericSIMD", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "force generic SIMD" );
idCVar com_developer( "developer", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "developer mode" );
idCVar com_allowConsole( "com_allowConsole", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "allow console toggling" );
idCVar com_speeds( "com_speeds", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "show engine timings" );
idCVar com_showFPS( "com_showFPS", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_ARCHIVE | CVAR_NOCHEAT, "show frame rate" );
idCVar com_showMemoryUsage( "com_showMemoryUsage", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "show memory usage" );
idCVar com_showAsyncStats( "com_showAsyncStats", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "show async statistics" );
idCVar com_showSoundDecoders( "com_showSoundDecoders", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "show sound decoders" );
idCVar com_timestampPrints( "com_timestampPrints", "0", CVAR_INTEGER | CVAR_SYSTEM, "prefix prints with a timestamp", 0, 2 );
idCVar com_timescale( "timescale", "1", CVAR_FLOAT | CVAR_SYSTEM, "scales game time", 0.1f, 10.0f );
idCVar com_logFile( "logFile", "0", CVAR_INTEGER | CVAR_SYSTEM | CVAR_NOCHEAT, "1: log, 2: force flush", 0, 2 );
idCVar com_logFileName( "logFileName", "console.log", CVAR_SYSTEM | CVAR_NOCHEAT, "console log file name" );
idCVar com_logTimeStamps( "logTimeStamps", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "timestamp console log lines" );
idCVar com_makingBuild( "com_makingBuild", "0", CVAR_BOOL | CVAR_SYSTEM, "building generated data" );
idCVar com_makingRC( "com_makingRC", "0", CVAR_BOOL | CVAR_SYSTEM, "1 when making a release candidate" );
idCVar com_useBinaryDecls( "com_useBinaryDecls", "2", CVAR_INTEGER | CVAR_SYSTEM | CVAR_ARCHIVE,
	"1 = Load binary versions of declarations, falling back to text if not found\n2 = Allow text to override binary versions" );
idCVar com_writeBinaryDecls( "com_writeBinaryDecls", "0", CVAR_BOOL | CVAR_SYSTEM, "Write binary versions of declarations" );
idCVar com_updateLoadSize( "com_updateLoadSize", "0", CVAR_BOOL | CVAR_SYSTEM | CVAR_NOCHEAT, "update map load sizes" );
idCVar com_videoRam( "com_videoRam", "64", CVAR_INTEGER | CVAR_SYSTEM | CVAR_NOCHEAT | CVAR_ARCHIVE, "detected video memory" );
idCVar com_product_lang_ext( "com_product_lang_ext", "1", CVAR_INTEGER | CVAR_SYSTEM | CVAR_ARCHIVE, "language file extension" );

int time_gameFrame;
int time_gameDraw;
int time_frontend;
int time_backend;

int com_frameTime;
int com_frameNumber;
volatile int com_ticNumber;
int com_editors;
bool com_editorActive;

// Fixed simulation time is owned exclusively by the foreground thread.  Keep
// this state outside idCommonLocal so the public/private object layout remains
// compatible with the SDK ABI.
static int mainThreadLastTicMsec;
static double mainThreadTicResidual;

#ifdef _WIN32
HWND com_hwndMsg = NULL;
bool com_outputMsg = false;
unsigned int com_msgID = static_cast< unsigned int >( -1 );
#endif

idGame* game = NULL;
idGameEdit* gameEdit = NULL;

static idCmdArgs com_consoleLines[ MAX_CONSOLE_LINES ];
static int com_numConsoleLines;

/*
==================
Com_PrintMutex

The bot thread, the file-system thread, the MegaTexture threads and the
Vulkan debug callback all print through common while the main thread does.
This serializes everything the print paths share: the console buffer, the
log file, the rcon redirect buffer and the warning/error lists.  It is
recursive because printing re-enters (opening the log file prints, an rcon
flush prints) and Error() can be raised inside a print; the guards are RAII,
so an exception thrown through them releases the lock.  Nothing that joins or
waits for another thread (session->Stop, Shutdown, UpdateScreen) runs while
it is held.  Signal handlers never print through common (posix_signal.cpp).
Never destroyed: other threads may still print while the process exits.
==================
*/
static std::recursive_mutex& Com_PrintMutex() {
	static std::recursive_mutex* printMutex = new std::recursive_mutex;
	return *printMutex;
}
typedef std::lock_guard< std::recursive_mutex > comPrintLock_t;

// Only the main thread may refresh the screen from a print.
static std::thread::id com_mainThreadId;

// The retail table is a 26-entry, 20-byte array at r_vidModes.  Aspect values
// are the SDK's 4:3, 16:9, 16:10 and 5:4 indices.
static vidmode_t r_vidModes[ 26 ] = {
	{ "Mode  0: 320x240",   320,  240, 0, false },
	{ "Mode  1: 400x300",   400,  300, 0, false },
	{ "Mode  2: 512x384",   512,  384, 0, false },
	{ "Mode  3: 640x480",   640,  480, 0, false },
	{ "Mode  4: 800x600",   800,  600, 0, false },
	{ "Mode  5: 1024x768", 1024,  768, 0, false },
	{ "Mode  6: 1152x864", 1152,  864, 0, false },
	{ "Mode  7: 1280x960", 1280,  960, 0, false },
	{ "Mode  8: 1600x1200",1600, 1200, 0, false },
	{ "Mode  9: 2048x1536",2048, 1536, 0, false },
	{ "Mode 10: 856x480",   856,  480, 1, false },
	{ "Mode 11: 1024x576", 1024,  576, 1, false },
	{ "Mode 12: 1280x720", 1280,  720, 1, false },
	{ "Mode 13: 1360x768", 1360,  768, 1, false },
	{ "Mode 14: 1366x768", 1366,  768, 1, false },
	{ "Mode 15: 1600x900", 1600,  900, 1, false },
	{ "Mode 16: 1920x1080",1920, 1080, 1, false },
	{ "Mode 17: 2560x1440",2560, 1440, 1, false },
	{ "Mode 18: 1024x640", 1024,  640, 2, false },
	{ "Mode 19: 1280x800", 1280,  800, 2, false },
	{ "Mode 20: 1440x900", 1440,  900, 2, false },
	{ "Mode 21: 1680x1050",1680, 1050, 2, false },
	{ "Mode 22: 1920x1200",1920, 1200, 2, false },
	{ "Mode 23: 2560x1600",2560, 1600, 2, false },
	{ "Mode 24: 1280x1024",1280, 1024, 3, false },
	{ "Mode 25: 2560x2048",2560, 2048, 3, false }
};

class idCommonLocal : public idCommon {
public:
	idCommonLocal();

	virtual void			Init( int argc, const char** argv, const char* cmdline );
	virtual void			Shutdown();
	virtual void			Quit();
	virtual bool			IsInitialized() const;
	virtual void			Frame();
	virtual void			Async();
	virtual bool			StartupVariable( const char* match );
	virtual void			ClearStartupVariable( const char* match );
	virtual void			WriteConfigToFile( const char* filename, bool writeBindings, bool writeCVars );
	virtual void			WriteFlaggedCVarsToFile( const char* filename, int flags, const char* setCmd );
	virtual void			BeginRedirect( char* buffer, int buffersize, void* user, void ( *flush )( void*, const char* ) );
	virtual void			EndRedirect();
	virtual void			SetRefreshOnPrint( bool set );
	virtual void			Printf( const char* fmt, ... );
	virtual void			TPrintf( const char* fmt, ... );
	virtual void			VPrintf( const char* fmt, va_list args );
	virtual void			DPrintf( const char* fmt, ... );
	virtual void			Warning( const char* fmt, ... );
	virtual void			TWarning( const char* fmt, ... );
	virtual void			DWarning( const char* fmt, ... );
	virtual void			PrintWarnings();
	virtual void			ClearWarnings( const char* reason );
	virtual void			Error( const char* fmt, ... );
	virtual void			FatalError( const char* fmt, ... );
	virtual void			PrintLoadingMessage( const char* msg );
	virtual void			EnableWarnings();
	virtual void			DisableWarnings();
	virtual void			PacifierUpdate();
	virtual void			UpdateLevelLoadScreen( const wchar_t* status );
	virtual const idLangDict* GetLanguageDict();
	virtual idWStr			LocalizeText( const char* declName, const idWStrList& arguments );
	virtual idWStr			LocalizeText( const sdDeclLocStr* loc, const idWStrList& arguments );
	virtual int				GetNumVideoModes() const;
	virtual vidmode_t&		GetVideoMode( int index ) const;
	virtual idSoundWorld*	GetGameSoundWorld();
	virtual idSoundWorld*	GetMenuSoundWorld();
	virtual void			WriteConfigs();

private:
	void					ParseCommandLine( int argc, const char** argv );
	bool					AddStartupCommands();
	bool					SafeMode();
	void					InitCommands();
	void					InitGame( bool resetConfigs );
	void					LoadGameDLL();
	void					UnloadGameDLL();
	void					InitLanguageDict( bool reload );
	void					InitSIMD();
	void					WriteConfiguration();
	void					CloseLogFile();
	void					DumpWarnings();
	void					VPrintfInternal( const char* fmt, va_list args, bool allowRefresh );
	void					PrintfNoRefresh( const char* fmt, ... );

	bool					com_fullyInitialized;
	bool					refreshOnPrint;
	int						com_errorEntered;
	bool					com_shuttingDown;
	idFile*					logFile;
	char					errorMessage[ MAX_PRINT_MSG_SIZE ];
	char*					rd_buffer;
	int						rd_buffersize;
	void*					rd_user;
	void					( *rd_flush )( void*, const char* );
	idStr					warningCaption;
	idStrList				warningList;
	idStrList				errorList;
	bool					noWarnings;
	void*					gameDLL;
	idLangDict				languageDict;
	int						nextConfigWriteTime;
};

#if defined( _WIN32 ) && !defined( _WIN64 )
assert_sizeof( idCommonLocal, 0x109c );
#endif

static idCommonLocal commonLocal;
idCommon* common = &commonLocal;

idCommonLocal::idCommonLocal() :
	com_fullyInitialized( false ),
	refreshOnPrint( false ),
	com_errorEntered( ERP_NONE ),
	com_shuttingDown( false ),
	logFile( NULL ),
	rd_buffer( NULL ),
	rd_buffersize( 0 ),
	rd_user( NULL ),
	rd_flush( NULL ),
	noWarnings( false ),
	gameDLL( NULL ),
	nextConfigWriteTime( 0 ) {
	errorMessage[ 0 ] = '\0';
	warningList.SetGranularity( 1 );
	errorList.SetGranularity( 1 );
}

void idCommonLocal::BeginRedirect( char* buffer, int buffersize, void* user, void ( *flush )( void*, const char* ) ) {
	if ( buffer == NULL || buffersize <= 0 || flush == NULL ) {
		return;
	}
	comPrintLock_t lock( Com_PrintMutex() );
	rd_buffer = buffer;
	rd_buffersize = buffersize;
	rd_user = user;
	rd_flush = flush;
	rd_buffer[ 0 ] = '\0';
}

void idCommonLocal::EndRedirect() {
	comPrintLock_t lock( Com_PrintMutex() );
	if ( rd_flush != NULL && rd_buffer != NULL && rd_buffer[ 0 ] != '\0' ) {
		rd_flush( rd_user, rd_buffer );
	}
	rd_user = NULL;
	rd_buffer = NULL;
	rd_buffersize = 0;
	rd_flush = NULL;
}

void idCommonLocal::SetRefreshOnPrint( bool set ) {
	refreshOnPrint = set;
}

void idCommonLocal::CloseLogFile() {
	comPrintLock_t lock( Com_PrintMutex() );
	if ( logFile != NULL && fileSystem != NULL ) {
		com_logFile.SetInteger( 0 );
		fileSystem->CloseFile( logFile );
		logFile = NULL;
	}
}

void idCommonLocal::VPrintf( const char* fmt, va_list args ) {
	VPrintfInternal( fmt, args, true );
}

/*
==================
idCommonLocal::VPrintfInternal

allowRefresh false is TPrintf/DPrintf/TWarning: never refresh the screen,
without toggling the shared refreshOnPrint flag under other threads.
==================
*/
void idCommonLocal::VPrintfInternal( const char* fmt, va_list args, bool allowRefresh ) {
	if ( fmt == NULL ) {
		return;
	}

	char msg[ MAX_PRINT_MSG_SIZE ];
	int prefixLength = 0;
	if ( cvarSystem != NULL && cvarSystem->IsInitialized() && com_timestampPrints.GetInteger() != 0 ) {
		int stamp = Sys_Milliseconds();
		if ( com_timestampPrints.GetInteger() == 1 ) {
			stamp /= 1000;
		}
		prefixLength = idStr::snPrintf( msg, sizeof( msg ), "[%i]", stamp );
		if ( prefixLength < 0 ) {
			prefixLength = 0;
		}
	}
	if ( idStr::vsnPrintf( msg + prefixLength, sizeof( msg ) - prefixLength, fmt, args ) < 0 ) {
		msg[ sizeof( msg ) - 2 ] = '\n';
		msg[ sizeof( msg ) - 1 ] = '\0';
	}

	bool refresh = false;
	{
		comPrintLock_t lock( Com_PrintMutex() );
		if ( rd_buffer != NULL ) {
			const int messageLength = idStr::Length( msg );
			const int currentLength = idStr::Length( rd_buffer );
			if ( currentLength + messageLength > rd_buffersize - 1 ) {
				rd_flush( rd_user, rd_buffer );
				rd_buffer[ 0 ] = '\0';
			}
			if ( messageLength <= rd_buffersize - 1 ) {
				idStr::Append( rd_buffer, rd_buffersize, msg );
			} else {
				rd_flush( rd_user, msg );
			}
			return;
		}

		if ( console != NULL ) {
			console->Print( msg );
		}

		char plain[ MAX_PRINT_MSG_SIZE ];
		idStr::Copynz( plain, msg, sizeof( plain ) );
		idStr::RemoveColors( plain );
		Sys_Printf( "%s", plain );

		static bool logFileFailed = false;
		static bool openingLogFile = false;
		if ( cvarSystem != NULL && cvarSystem->IsInitialized() &&
				com_logFile.GetInteger() != 0 && !logFileFailed &&
				fileSystem != NULL && fileSystem->IsInitialized() ) {
			if ( logFile == NULL && !openingLogFile ) {
				openingLogFile = true;
				const char* name = com_logFileName.GetString()[ 0 ] != '\0' ? com_logFileName.GetString() : "console.log";
				logFile = fileSystem->OpenFileWrite( name, "fs_userpath" );
				openingLogFile = false;
				if ( logFile == NULL ) {
					logFileFailed = true;
					Sys_Printf( "failed to open log file '%s'\n", name );
				} else if ( com_logFile.GetInteger() > 1 ) {
					logFile->ForceFlush();
				}
			}
			if ( logFile != NULL ) {
				logFile->Write( plain, idStr::Length( plain ) );
				logFile->Flush();
			}
		}

		refresh = allowRefresh && com_errorEntered != ERP_FATAL && refreshOnPrint && session != NULL &&
			std::this_thread::get_id() == com_mainThreadId;
	}

	// Outside the lock: rendering a frame can wait on threads that print.
	if ( refresh ) {
		session->UpdateScreen( true );
	}
}

void idCommonLocal::Printf( const char* fmt, ... ) {
	va_list args;
	va_start( args, fmt );
	VPrintf( fmt, args );
	va_end( args );
}

void idCommonLocal::TPrintf( const char* fmt, ... ) {
	va_list args;
	va_start( args, fmt );
	VPrintfInternal( fmt, args, false );
	va_end( args );
}

void idCommonLocal::PrintfNoRefresh( const char* fmt, ... ) {
	va_list args;
	va_start( args, fmt );
	VPrintfInternal( fmt, args, false );
	va_end( args );
}

void idCommonLocal::DPrintf( const char* fmt, ... ) {
	if ( cvarSystem == NULL || !cvarSystem->IsInitialized() || !com_developer.GetBool() ) {
		return;
	}
	char msg[ MAX_PRINT_MSG_SIZE ];
	va_list args;
	va_start( args, fmt );
	idStr::vsnPrintf( msg, sizeof( msg ), fmt, args );
	va_end( args );
	PrintfNoRefresh( S_COLOR_GRAY "%s", msg );
}

static void FormatCommonMessage( char* target, int targetSize, const char* fmt, va_list args ) {
	idStr::vsnPrintf( target, targetSize, fmt, args );
	target[ targetSize - 1 ] = '\0';
}

void idCommonLocal::Warning( const char* fmt, ... ) {
	if ( noWarnings ) {
		return;
	}
	char msg[ MAX_PRINT_MSG_SIZE ];
	va_list args;
	va_start( args, fmt );
	FormatCommonMessage( msg, sizeof( msg ), fmt, args );
	va_end( args );
	Printf( S_COLOR_YELLOW "WARNING: %s\n", msg );
	comPrintLock_t lock( Com_PrintMutex() );
	if ( warningList.Num() < MAX_WARNING_LIST ) {
		warningList.AddUnique( msg );
	}
}

void idCommonLocal::TWarning( const char* fmt, ... ) {
	if ( noWarnings ) {
		return;
	}
	char msg[ MAX_PRINT_MSG_SIZE ];
	va_list args;
	va_start( args, fmt );
	FormatCommonMessage( msg, sizeof( msg ), fmt, args );
	va_end( args );
	PrintfNoRefresh( S_COLOR_YELLOW "WARNING: %s\n", msg );
	comPrintLock_t lock( Com_PrintMutex() );
	if ( warningList.Num() < MAX_WARNING_LIST ) {
		warningList.AddUnique( msg );
	}
}

void idCommonLocal::DWarning( const char* fmt, ... ) {
	if ( cvarSystem == NULL || !cvarSystem->IsInitialized() || !com_developer.GetBool() || noWarnings ) {
		return;
	}
	char msg[ MAX_PRINT_MSG_SIZE ];
	va_list args;
	va_start( args, fmt );
	FormatCommonMessage( msg, sizeof( msg ), fmt, args );
	va_end( args );
	Printf( S_COLOR_YELLOW "WARNING: %s\n", msg );
}

void idCommonLocal::PrintWarnings() {
	// Print a copy: a warning raised meanwhile (another thread) must not
	// reallocate the list being walked, and printing may refresh the screen,
	// which must not happen under the print lock.
	idStrList warnings;
	idStr caption;
	{
		comPrintLock_t lock( Com_PrintMutex() );
		if ( warningList.Num() == 0 ) {
			return;
		}
		warningList.Sort();
		warnings = warningList;
		caption = warningCaption;
	}
	Printf( "------------- Warnings ---------------\n" );
	Printf( "during %s...\n", caption.c_str() );
	for ( int i = 0; i < warnings.Num(); ++i ) {
		Printf( S_COLOR_YELLOW "WARNING: %s\n", warnings[ i ].c_str() );
	}
	Printf( "%s%d warnings\n", warnings.Num() >= MAX_WARNING_LIST ? "more than " : "", warnings.Num() );
}

void idCommonLocal::ClearWarnings( const char* reason ) {
	comPrintLock_t lock( Com_PrintMutex() );
	warningCaption = reason != NULL ? reason : "";
	warningList.Clear();
}

void idCommonLocal::DumpWarnings() {
	comPrintLock_t lock( Com_PrintMutex() );
	if ( warningList.Num() == 0 || fileSystem == NULL || !fileSystem->IsInitialized() ) {
		return;
	}
	idFile* file = fileSystem->OpenFileWrite( "warnings.txt", "fs_savepath" );
	if ( file == NULL ) {
		return;
	}
	file->Printf( "Warnings during %s\n\n", warningCaption.c_str() );
	warningList.Sort();
	for ( int i = 0; i < warningList.Num(); ++i ) {
		file->Printf( "WARNING: %s\n", warningList[ i ].c_str() );
	}
	errorList.Sort();
	for ( int j = 0; j < errorList.Num(); ++j ) {
		file->Printf( "ERROR: %s\n", errorList[ j ].c_str() );
	}
	file->ForceFlush();
	fileSystem->CloseFile( file );
}

void idCommonLocal::Error( const char* fmt, ... ) {
	refreshOnPrint = false;
	if ( com_errorEntered == ERP_FATAL ) {
		Sys_Quit();
	}
	com_errorEntered = ERP_DROP;
	char msg[ MAX_PRINT_MSG_SIZE ];
	va_list args;
	va_start( args, fmt );
	FormatCommonMessage( msg, sizeof( msg ), fmt, args );
	va_end( args );
	{
		comPrintLock_t lock( Com_PrintMutex() );
		idStr::Copynz( errorMessage, msg, sizeof( errorMessage ) );
		errorList.AddUnique( msg );
	}
	// Not under the print lock: stopping the session joins the bot thread.
	if ( session != NULL ) {
		session->Stop();
	}
	Printf( "********************\nERROR: %s\n********************\n", msg );
	com_errorEntered = ERP_NONE;
	// idException strcpy()s into a fixed buffer smaller than msg.
	char exceptionText[ sizeof( static_cast< idException* >( NULL )->error ) ];
	idStr::Copynz( exceptionText, msg, sizeof( exceptionText ) );
	throw idException( exceptionText );
}

void idCommonLocal::FatalError( const char* fmt, ... ) {
	if ( com_errorEntered != ERP_NONE ) {
		Sys_Printf( "FATAL: recursive error:\n%s\n", errorMessage );
		Sys_Quit();
	}
	com_errorEntered = ERP_FATAL;
	{
		comPrintLock_t lock( Com_PrintMutex() );
		va_list args;
		va_start( args, fmt );
		FormatCommonMessage( errorMessage, sizeof( errorMessage ), fmt, args );
		va_end( args );
	}
	Printf( "********************\nFATAL ERROR: %s\n********************\n", errorMessage );
#if !defined( _WIN32 )
	void *callstack[ 64 ];
	int frames = backtrace( callstack, 64 );
	backtrace_symbols_fd( callstack, frames, STDERR_FILENO );
#endif
	Sys_SetFatalError( errorMessage );
	Shutdown();
	Sys_Error( "%s", errorMessage );
}

void idCommonLocal::EnableWarnings() {
	noWarnings = false;
}

void idCommonLocal::DisableWarnings() {
	noWarnings = true;
}

void idCommonLocal::ParseCommandLine( int argc, const char** argv ) {
	for ( int i = 0; i < MAX_CONSOLE_LINES; ++i ) {
		com_consoleLines[ i ].Clear();
	}
	com_numConsoleLines = 0;
	for ( int arg = 0; arg < argc && com_numConsoleLines < MAX_CONSOLE_LINES; ++arg ) {
		const char* value = argv[ arg ];
		if ( value == NULL ) {
			continue;
		}
		if ( value[ 0 ] == '+' ) {
			++com_numConsoleLines;
			if ( com_numConsoleLines > MAX_CONSOLE_LINES ) {
				com_numConsoleLines = MAX_CONSOLE_LINES;
				break;
			}
			com_consoleLines[ com_numConsoleLines - 1 ].AppendArg( value + 1 );
		} else {
			if ( com_numConsoleLines == 0 ) {
				com_numConsoleLines = 1;
			}
			com_consoleLines[ com_numConsoleLines - 1 ].AppendArg( value );
		}
	}
}

bool idCommonLocal::StartupVariable( const char* match ) {
	bool found = false;
	if ( cvarSystem == NULL ) {
		return false;
	}
	for ( int i = 0; i < com_numConsoleLines; ++i ) {
		idCmdArgs& args = com_consoleLines[ i ];
		if ( args.Argc() < 3 || idStr::Icmp( args.Argv( 0 ), "set" ) != 0 ) {
			continue;
		}
		if ( match == NULL || idStr::Icmp( args.Argv( 1 ), match ) == 0 ) {
			cvarSystem->SetCVarString( args.Argv( 1 ), args.Argv( 2 ) );
			found = true;
		}
	}
	return found;
}

void idCommonLocal::ClearStartupVariable( const char* match ) {
	if ( match == NULL ) {
		return;
	}
	for ( int i = 0; i < com_numConsoleLines; ++i ) {
		idCmdArgs& args = com_consoleLines[ i ];
		if ( args.Argc() >= 2 && idStr::Icmp( args.Argv( 0 ), "set" ) == 0 &&
				idStr::Icmp( args.Argv( 1 ), match ) == 0 ) {
			args.Clear();
		}
	}
}

bool idCommonLocal::AddStartupCommands() {
	bool added = false;
	for ( int i = 0; i < com_numConsoleLines; ++i ) {
		if ( com_consoleLines[ i ].Argc() == 0 ) {
			continue;
		}
		if ( idStr::Icmpn( com_consoleLines[ i ].Argv( 0 ), "set", 3 ) != 0 ) {
			added = true;
		}
		// Preserve the command name and the already-tokenized arguments.  Using
		// Args() here drops Argv( 0 ), turning "+spawnServer valley" into the
		// unknown command "valley".
		cmdSystem->BufferCommandArgs( CMD_EXEC_APPEND, com_consoleLines[ i ] );
	}
	return added;
}

bool idCommonLocal::SafeMode() {
	for ( int i = 0; i < com_numConsoleLines; ++i ) {
		const char* command = com_consoleLines[ i ].Argv( 0 );
		if ( idStr::Icmp( command, "safe" ) == 0 || idStr::Icmp( command, "cvar_restart" ) == 0 ) {
			com_consoleLines[ i ].Clear();
			return true;
		}
	}
	return false;
}

static void WriteCFGHeader( idFile* file ) {
	file->Printf( "// *********************************************************\n" );
	file->Printf( "// This file is managed by ETQW and will be overwritten\n" );
	file->Printf( "// Put custom commands and bindings in autoexec.cfg\n" );
	file->Printf( "// *********************************************************\n\n" );
}

void idCommonLocal::WriteFlaggedCVarsToFile( const char* filename, int flags, const char* setCmd ) {
	idFile* file = fileSystem->OpenFileWrite( filename, "fs_savepath" );
	if ( file == NULL ) {
		Printf( "Couldn't write %s.\n", filename );
		return;
	}
	cvarSystem->WriteFlaggedVariables( flags, setCmd, file );
	fileSystem->CloseFile( file );
}

void idCommonLocal::WriteConfigToFile( const char* filename, bool writeBindings, bool writeCVars ) {
	idFile* file = fileSystem->OpenFileWrite( filename, "fs_userpath" );
	if ( file == NULL ) {
		Printf( "Couldn't write %s.\n", filename );
		return;
	}
	if ( writeBindings ) {
		WriteCFGHeader( file );
		idKeyInput::WriteBindings( file );
	}
	if ( writeCVars ) {
		WriteCFGHeader( file );
		cvarSystem->WriteFlaggedVariables( CVAR_ARCHIVE, "seta", file );
	}
	fileSystem->CloseFile( file );
}

void idCommonLocal::WriteConfiguration() {
	if ( !com_fullyInitialized || cvarSystem == NULL || fileSystem == NULL ) {
		return;
	}
	if ( ( cvarSystem->GetModifiedFlags() & CVAR_ARCHIVE ) == 0 ) {
		return;
	}
	cvarSystem->ClearModifiedFlags( CVAR_ARCHIVE );
	WriteConfigToFile( BINDING_FILE, true, false );
	WriteConfigToFile( CONFIG_FILE, false, true );
}

void idCommonLocal::WriteConfigs() {
	WriteConfiguration();
}

const idLangDict* idCommonLocal::GetLanguageDict() {
	return &languageDict;
}

void idCommonLocal::InitLanguageDict( bool reload ) {
	languageDict.Clear();
	if ( fileSystem == NULL || !fileSystem->IsInitialized() || cvarSystem == NULL ) {
		return;
	}
	StartupVariable( "sys_lang" );
	const char* language = cvarSystem->GetCVarString( "sys_lang" );
	idStr path = va( "localization/%s/strings", language != NULL && language[ 0 ] != '\0' ? language : "english" );
	idFileList* files = fileSystem->ListFilesTree( path, ".lang", true );
	if ( files != NULL ) {
		for ( int i = 0; i < files->GetNumFiles(); ++i ) {
			languageDict.Load( files->GetFile( i ), false );
		}
		fileSystem->FreeFileList( files );
	}
	if ( reload && game != NULL ) {
		game->OnLanguageInit();
	}
}

idWStr idCommonLocal::LocalizeText( const sdDeclLocStr* loc, const idWStrList& arguments ) {
	if ( loc == NULL ) {
		return idWStr();
	}
	idWStr result;
	if ( !loc->Format( result, arguments ) ) {
		Warning( "Failed to localize '%s'", loc->GetName() );
	}
	return result;
}

idWStr idCommonLocal::LocalizeText( const char* declName, const idWStrList& arguments ) {
	if ( declName == NULL || declName[ 0 ] == '\0' ) {
		return idWStr();
	}
	const sdDeclLocStr* loc = declHolder.FindLocStr( declName, false );
	if ( loc == NULL ) {
		return idWStr( va( L"###%hs###", declName ) );
	}
	return LocalizeText( loc, arguments );
}

int idCommonLocal::GetNumVideoModes() const {
	return _arraycount( r_vidModes );
}

vidmode_t& idCommonLocal::GetVideoMode( int index ) const {
	if ( index < 0 || index >= _arraycount( r_vidModes ) ) {
		index = 3;
	}
	return r_vidModes[ index ];
}

idSoundWorld* idCommonLocal::GetGameSoundWorld() {
	return session != NULL ? session->sw : NULL;
}

idSoundWorld* idCommonLocal::GetMenuSoundWorld() {
	return session != NULL ? session->sw : NULL;
}

void idCommonLocal::PacifierUpdate() {
	if ( session != NULL ) {
		session->PacifierUpdate();
	}
}

void idCommonLocal::UpdateLevelLoadScreen( const wchar_t* status ) {
	if ( game != NULL ) {
		game->UpdateLevelLoadScreen( status );
	}
}

void idCommonLocal::PrintLoadingMessage( const char* msg ) {
	if ( msg == NULL || msg[ 0 ] == '\0' ) {
		return;
	}

	Printf( "%s\n", msg );

	if ( com_skipRenderer.GetBool() || renderSystem == NULL || deviceContext == NULL ||
		sys3D == NULL || sys3D->GetGameRenderContext() == NULL ) {
		return;
	}

	// Retail ETQW renders a complete frame for every loading message.  These
	// calls are intentionally independent of Session::PacifierUpdate: during
	// game-DLL initialization there is no map-change pacifier frame to draw it.
	Sys_PumpEvents();
	if ( !sys3D->MakeCurrent( sys3D->GetGameWindow() ) ) {
		return;
	}

	const glimpParms_t& parms = sys3D->GetGameWindowParms();
	renderSystem->BeginFrame( parms.width, parms.height );
	deviceContext->BeginEmitFullScreen();

	const float aspectCorrection = Max( deviceContext->GetAspectRatioCorrection(), 0.001f );
	const float screenWidth = SCREEN_WIDTH / aspectCorrection;
	const float splashWidth = 853.33331298828125f;

	deviceContext->SetColor( colorBlack );
	deviceContext->DrawRect(
		0.0f, 0.0f, screenWidth, SCREEN_HEIGHT,
		0.0f, 0.0f, 1.0f, 1.0f,
		declHolder.FindMaterial( "_black", true )
	);

	deviceContext->SetColor( colorWhite );
	deviceContext->DrawRect(
		( screenWidth - splashWidth ) * 0.5f, 0.0f, splashWidth, SCREEN_HEIGHT,
		0.0f, 0.0f, 1.0f, 1.0f,
		declHolder.FindMaterial( "splashScreen", true )
	);

	const int textX = static_cast< int >( ( screenWidth - strlen( msg ) * SMALLCHAR_WIDTH ) * 0.5f );
	const idMaterial* bigChars = declHolder.FindMaterial( "textures/bigchars", true );
	renderSystem->DrawSmallStringExt( textX + 1, 385, msg, colorBlack, true, bigChars );
	renderSystem->DrawSmallStringExt( textX, 384, msg, idVec4( 1.0f, 0.5f, 0.0f, 0.85f ), true, bigChars );

	deviceContext->End();
	renderSystem->EndFrame( true );
}

void idCommonLocal::InitSIMD() {
	idSIMD::InitProcessor( GAME_NAME, com_forceGenericSIMD.GetBool() );
	com_forceGenericSIMD.ClearModified();
}

void idCommonLocal::Async() {
	// Retained as an ABI-compatible no-op.  The former common-services worker
	// no longer exists; Frame owns simulation time and the platform owns the
	// dedicated input and sound workers.
}

void Com_UpdateGameTime() {
	const int now = Sys_Milliseconds();
	if ( mainThreadLastTicMsec == 0 ) {
		mainThreadLastTicMsec = now;
		mainThreadTicResidual = USERCMD_MSEC;
	}

	int elapsedMsec = now - mainThreadLastTicMsec;
	mainThreadLastTicMsec = now;
	if ( elapsedMsec < 0 ) {
		mainThreadTicResidual = 0.0;
		return;
	}

	if ( !com_preciseTic.GetBool() ) {
		++com_ticNumber;
		mainThreadTicResidual = 0.0;
		return;
	}

	// Bound catch-up after loading or debugger stalls.  The session consumes at
	// most ten fixed steps, so retaining more debt would leave it permanently
	// behind the authoritative clock.
	double scaledMsec = elapsedMsec * static_cast< double >( com_timescale.GetFloat() );
	const double maxCatchupMsec = 10.0 * USERCMD_MSEC;
	if ( scaledMsec > maxCatchupMsec ) {
		scaledMsec = maxCatchupMsec;
		mainThreadTicResidual = 0.0;
	}

	mainThreadTicResidual += scaledMsec;
	const int elapsedTics = static_cast< int >( mainThreadTicResidual / USERCMD_MSEC );
	if ( elapsedTics > 0 ) {
		com_ticNumber += elapsedTics;
		mainThreadTicResidual -= elapsedTics * USERCMD_MSEC;
	}
}

int Com_GetGameTimeLeft() {
	if ( idAsyncNetwork::client.IsActive() ) {
		return idAsyncNetwork::client.GetGameTimeLeft();
	}
	// ClientUpdateView interprets this as the time remaining before the next
	// usercmd.  It cannot accept a completely unadvanced interval, but the
	// foreground loop can render twice within the same millisecond.  Treat that
	// case as the first millisecond of the interval so the frame is still marked
	// as an unlocked draw and cannot reapply the weapon-origin correction.
	int elapsedInTic = static_cast< int >( mainThreadTicResidual );
	if ( elapsedInTic < 1 ) {
		elapsedInTic = 1;
	} else if ( elapsedInTic >= USERCMD_MSEC ) {
		elapsedInTic = USERCMD_MSEC - 1;
	}
	return USERCMD_MSEC - elapsedInTic;
}

/*
==================
FrameTimingLog

com_logFrameTiming: every host frame adds its interval (from the end of the previous
frame's pacing to the end of its own) and its work time (from its start to the pacing
wait). Once a second one line reports how many frames and game ticks that second had and
the distribution of both times, so a test can tell the frame rate it got during play from
the cap it asked for.
==================
*/
namespace {

uint64_t MonotonicUsec() {
	return static_cast< uint64_t >( std::chrono::duration_cast< std::chrono::microseconds >(
		std::chrono::steady_clock::now().time_since_epoch() ).count() );
}

class FrameTimingLog {
public:
	void	AddFrame( uint64_t frameStartUsec, uint64_t workEndUsec, uint64_t frameEndUsec, int ticks, int cap );
	void	Reset() { windowStartUsec = 0; lastFrameEndUsec = 0; intervals.clear(); works.clear(); windowTicks = 0; }

private:
	static float	PercentileMs( std::vector< uint32_t >& usec, float p );

	uint64_t				windowStartUsec = 0;
	uint64_t				lastFrameEndUsec = 0;
	std::vector< uint32_t >	intervals;
	std::vector< uint32_t >	works;
	int						windowTicks = 0;
};

float FrameTimingLog::PercentileMs( std::vector< uint32_t >& usec, float p ) {
	if ( usec.empty() ) {
		return 0.0f;
	}
	const size_t index = std::min( usec.size() - 1, static_cast< size_t >( p * static_cast< float >( usec.size() - 1 ) + 0.5f ) );
	std::nth_element( usec.begin(), usec.begin() + index, usec.end() );
	return static_cast< float >( usec[ index ] ) * 0.001f;
}

void FrameTimingLog::AddFrame( uint64_t frameStartUsec, uint64_t workEndUsec, uint64_t frameEndUsec, int ticks, int cap ) {
	if ( windowStartUsec == 0 ) {
		// the first frame only opens the window: it has no previous frame end
		windowStartUsec = frameEndUsec;
		lastFrameEndUsec = frameEndUsec;
		return;
	}
	intervals.push_back( static_cast< uint32_t >( std::min< uint64_t >( frameEndUsec - lastFrameEndUsec, UINT32_MAX ) ) );
	works.push_back( static_cast< uint32_t >( std::min< uint64_t >( workEndUsec - frameStartUsec, UINT32_MAX ) ) );
	windowTicks += ticks;
	lastFrameEndUsec = frameEndUsec;

	const uint64_t windowUsec = frameEndUsec - windowStartUsec;
	if ( windowUsec < 1000000 ) {
		return;
	}
	const int frames = static_cast< int >( intervals.size() );
	common->Printf( "FRAME_TIMING: window_ms=%.1f frames=%d ticks=%d cap=%d"
		" dt_p50=%.2f dt_p95=%.2f dt_p99=%.2f dt_max=%.2f work_p50=%.2f work_p95=%.2f work_max=%.2f\n",
		static_cast< float >( windowUsec ) * 0.001f, frames, windowTicks, cap,
		PercentileMs( intervals, 0.5f ), PercentileMs( intervals, 0.95f ), PercentileMs( intervals, 0.99f ), PercentileMs( intervals, 1.0f ),
		PercentileMs( works, 0.5f ), PercentileMs( works, 0.95f ), PercentileMs( works, 1.0f ) );
	windowStartUsec = frameEndUsec;
	intervals.clear();
	works.clear();
	windowTicks = 0;
}

FrameTimingLog frameTimingLog;

}	// namespace

void idCommonLocal::Frame() {
	if ( !com_fullyInitialized || com_shuttingDown ) {
		return;
	}
	const uint64_t frameStartUsec = MonotonicUsec();
	com_frameTicks = 0;
	try {
		Sys_FPU_EnableExceptions( 0 );
		Sys_GenerateEvents();
		Com_UpdateGameTime();
		const int now = Sys_Milliseconds();
		if ( now >= nextConfigWriteTime ) {
			WriteConfiguration();
			nextConfigWriteTime = now + 2000;
		}
		if ( com_forceGenericSIMD.IsModified() ) {
			InitSIMD();
		}
		if ( eventLoop != NULL ) {
			eventLoop->RunEventLoop( true );
		}
		com_frameTime = com_ticNumber * USERCMD_MSEC;
		if ( networkService != NULL ) {
			networkService->RunFrame();
		}
		if ( game != NULL ) {
			// ETQW has a per-host-frame game callback in addition to the
			// fixed-tic RunFrame overload driven by idSessionLocal.
			game->RunFrame();
		}
		idAsyncNetwork::RunFrame();
		if ( session != NULL ) {
			if ( !idAsyncNetwork::IsActive() ) {
				session->Frame();
			} else if ( networkSystem != NULL && networkSystem->IsDedicated() && renderSystem != NULL ) {
				renderSystem->SyncRenderSystem();
			}
			session->UpdateScreen( false );
		}
		++com_frameNumber;
		idLib::frameNumber = com_frameNumber;

		// Dynamic sync between com_maxFPS and com_unlock_maxFPS
		if ( com_maxFPS.IsModified() ) {
			com_maxFPS.ClearModified();
			com_unlock_maxFPS.SetInteger( com_maxFPS.GetInteger() );
			com_unlock_maxFPS.ClearModified();
		} else if ( com_unlock_maxFPS.IsModified() ) {
			com_unlock_maxFPS.ClearModified();
			com_maxFPS.SetInteger( com_unlock_maxFPS.GetInteger() );
			com_maxFPS.ClearModified();
		} else if ( com_maxFPS.GetInteger() != com_unlock_maxFPS.GetInteger() ) {
			com_unlock_maxFPS.SetInteger( com_maxFPS.GetInteger() );
		}

		// Frame pacing using monotonic microsecond timing
		static uint64_t lastFrameUsec = 0;
		const uint64_t workEndUsec = MonotonicUsec();
		const int maxFPS = com_maxFPS.GetInteger() > 0 ? com_maxFPS.GetInteger() : com_unlock_maxFPS.GetInteger();
		if ( maxFPS > 0 ) {
			const uint64_t targetFrameUsec = 1000000ull / static_cast< uint64_t >( maxFPS );
			const auto monotonicUsec = MonotonicUsec;
			uint64_t nowUsec = workEndUsec;
			if ( lastFrameUsec != 0 && nowUsec > lastFrameUsec ) {
				uint64_t elapsedUsec = nowUsec - lastFrameUsec;
				if ( elapsedUsec < targetFrameUsec ) {
					uint64_t waitUsec = targetFrameUsec - elapsedUsec;
					if ( networkSystem != NULL && networkSystem->IsDedicated() ) {
						// a dedicated binary (server, or headless client) never busy-waits:
						// sleep the whole remainder; its 30 Hz ticks do not need sub-ms pacing
						std::this_thread::sleep_for( std::chrono::microseconds( waitUsec ) );
						nowUsec = monotonicUsec();
					} else {
						// A client sleeps until shortly before the frame is due, then spins for exact
						// high-refresh pacing. How late a sleep wakes depends on the OS: macOS wakes
						// about 2 ms late (with the 1.5 ms margin used before, a 60 fps cap ran at
						// 58 fps), so the spin margin follows the lateness measured on recent sleeps:
						// it rises to a late wake at once and decays over about a hundred frames.
						static uint64_t sleepLatenessUsec = 2000;
						const uint64_t spinUsec = std::min< uint64_t >( sleepLatenessUsec + 500, 6000 );
						if ( waitUsec > spinUsec ) {
							const uint64_t sleepUsec = waitUsec - spinUsec;
							const uint64_t sleepStartUsec = monotonicUsec();
							std::this_thread::sleep_for( std::chrono::microseconds( sleepUsec ) );
							const uint64_t sleptUsec = monotonicUsec() - sleepStartUsec;
							const uint64_t latenessUsec = sleptUsec > sleepUsec ? sleptUsec - sleepUsec : 0;
							sleepLatenessUsec = std::max( latenessUsec, sleepLatenessUsec - sleepLatenessUsec / 64 );
						}
						do {
							nowUsec = monotonicUsec();
						} while ( nowUsec - lastFrameUsec < targetFrameUsec );
					}
				}
			}
			lastFrameUsec = nowUsec;
		} else {
			lastFrameUsec = 0;
		}

		if ( com_logFrameTiming.GetBool() ) {
			frameTimingLog.AddFrame( frameStartUsec, workEndUsec, MonotonicUsec(), com_frameTicks, maxFPS );
		} else if ( com_logFrameTiming.IsModified() ) {
			com_logFrameTiming.ClearModified();
			frameTimingLog.Reset();
		}
	} catch ( idException& ) {
	}
}

static void Com_Quit_f( const idCmdArgs& ) {
	common->Quit();
}

static void Com_Status_f( const idCmdArgs& ) {
	common->Printf( "ETQW Dedicated Server Status:\n" );
	common->Printf( "  Version: %s\n", versionString.string );
	common->Printf( "  Dedicated: %s\n", cvarSystem->GetCVarBool( "dedicated" ) ? "Yes" : "No" );
	common->Printf( "  State: Running (Headless Dedicated)\n" );
	idAsyncNetwork::server.PrintClientSlots();
}

static void Com_Help_f( const idCmdArgs& ) {
	common->Printf( "Commonly used commands:\n" );
	common->Printf( "  status       - display server status\n" );
	common->Printf( "  listDecls    - list parsed declarations\n" );
	common->Printf( "  listCmds     - list all console commands\n" );
	common->Printf( "  listCvars    - list all console variables\n" );
	common->Printf( "  quit         - shutdown and exit server\n" );
}

static void Com_WriteConfig_f( const idCmdArgs& args ) {
	if ( args.Argc() != 2 ) {
		common->Printf( "Usage: writeConfig <filename>\n" );
		return;
	}
	idStr filename = args.Argv( 1 );
	filename.DefaultFileExtension( ".cfg" );
	common->WriteConfigToFile( filename, true, true );
}

static void Com_ListModes_f( const idCmdArgs& ) {
	common->Printf( "\n" );
	for ( int i = 0; i < common->GetNumVideoModes(); ++i ) {
		const vidmode_t& mode = common->GetVideoMode( i );
		common->Printf( "%s%s\n", mode.description, mode.available ? "" : " - Not available for fullscreen" );
	}
	common->Printf( "\n" );
}

#ifdef ETQW_WITH_RADIANT
static void Com_Editor_f( const idCmdArgs& args ) {
	com_editors |= EDITOR_RADIANT;
	RadiantInit();
	if ( args.Argc() > 1 ) {
		RadiantOpenMap( args.Argv( 1 ) );
	}
}

#endif

void idCommonLocal::InitCommands() {
	cmdSystem->AddCommand( "quit", Com_Quit_f, CMD_FL_SYSTEM, "quits the game" );
	cmdSystem->AddCommand( "exit", Com_Quit_f, CMD_FL_SYSTEM, "exits the game" );
	cmdSystem->AddCommand( "status", Com_Status_f, CMD_FL_SYSTEM, "displays server status" );
	cmdSystem->AddCommand( "help", Com_Help_f, CMD_FL_SYSTEM, "displays commonly used commands" );
	cmdSystem->AddCommand( "writeConfig", Com_WriteConfig_f, CMD_FL_SYSTEM, "writes a configuration file" );
	cmdSystem->AddCommand( "listModes", Com_ListModes_f, CMD_FL_RENDERER, "lists video modes" );
#ifdef ETQW_WITH_RADIANT
	cmdSystem->AddCommand( "editor", Com_Editor_f, CMD_FL_TOOL, "opens the ETQW Radiant level editor" );
#endif
#if defined( ETQW_WITH_TOOLS ) && !defined( ID_DEDICATED )
	cmdSystem->AddCommand( "dmap", Dmap_f, CMD_FL_TOOL, "compiles an ETQW map" );
	cmdSystem->AddCommand( "navBuild", NavBuild_f, CMD_FL_TOOL, "builds Recast/Detour navigation for an ETQW map" );
	cmdSystem->AddCommand( "navVerify", NavVerify_f, CMD_FL_TOOL, "validates a compiled ETQW navmesh" );
	cmdSystem->AddCommand( "navTest", NavTest_f, CMD_FL_TOOL, "loads and smoke-tests compiled ETQW navigation" );
	cmdSystem->AddCommand( "megaCreate", MegaTextureCreate_f, CMD_FL_TOOL, "creates a MegaTexture terrain project" );
	cmdSystem->AddCommand( "megaCompile", MegaTextureCompile_f, CMD_FL_TOOL, "compiles a MegaTexture terrain project" );
	cmdSystem->AddCommand( "megaVerify", MegaTextureVerify_f, CMD_FL_TOOL, "validates a compiled MegaTexture" );
#endif
}

void idCommonLocal::LoadGameDLL() {
	if ( gameDLL != NULL || game != NULL ) {
		return;
	}

	char dllPath[ MAX_OSPATH ];
	dllPath[ 0 ] = '\0';
	fileSystem->FindDLL( "game", dllPath, true, true );
	if ( dllPath[ 0 ] == '\0' ) {
		char defaultDllName[ MAX_OSPATH ];
		sys->DLL_GetFileName( "game", defaultDllName, sizeof( defaultDllName ) );
		idStr localDLL = Sys_EXEPath();
		localDLL.StripFilename();
		localDLL.AppendPath( defaultDllName );
		if ( fileSystem->FileExistsExplicit( localDLL ) ) {
			idStr::Copynz( dllPath, localDLL, sizeof( dllPath ) );
		}
	}
	if ( dllPath[ 0 ] == '\0' ) {
		FatalError( "couldn't find game dynamic library" );
		return;
	}

	DPrintf( "Loading game DLL: '%s'\n", dllPath );
	gameDLL = sys->DLL_Load( dllPath, true );
	if ( gameDLL == NULL ) {
		FatalError( "couldn't load game dynamic library '%s'", dllPath );
		return;
	}
	// Before GetGameAPI registers the module's cvars and commands; restored by
	// every UnloadGameDLL (GameModuleCallbacks.h).
	CVar_CaptureModuleCallbacks();
	Cmd_CaptureModuleCommands();

	GetGameAPI_t getGameAPI = reinterpret_cast< GetGameAPI_t >( sys->DLL_GetProcAddress( gameDLL, "GetGameAPI" ) );
	if ( getGameAPI == NULL ) {
		UnloadGameDLL();
		FatalError( "couldn't find game DLL API" );
		return;
	}

	gameImport_t gameImport;
	memset( &gameImport, 0, sizeof( gameImport ) );
	gameImport.version = GAME_API_VERSION;
	gameImport.sys = sys;
	gameImport.common = common;
	gameImport.cmdSystem = cmdSystem;
	gameImport.cvarSystem = cvarSystem;
	gameImport.fileSystem = fileSystem;
	gameImport.networkSystem = networkSystem;
	gameImport.renderSystem = renderSystem;
	gameImport.deviceContext = deviceContext;
	gameImport.soundSystem = soundSystem;
	gameImport.renderModelManager = renderModelManager;
	gameImport.declManager = declManager;
	gameImport.collisionModelManager = collisionModelManager;
	gameImport.navigationSystem = navigationSystem;
	gameImport.bse = bse;
#ifndef _XENON
	gameImport.networkService = networkService;
#endif
	gameImport.adManager = adManager;
	gameImport.keyInputManager = keyInputManager;
	gameImport.notificationSystem = notificationSystem;
	idDict::GetGlobalPools( gameImport.globalKeys, gameImport.globalValues );
	gameImport.stringAllocator = idStr::GetStringAllocator();
	gameImport.wideStringAllocator = idWStr::GetStringAllocator();
	gameImport.graphManager = graphManager;

	gameExport_t* gameExport = getGameAPI( &gameImport );
	if ( gameExport == NULL || gameExport->version != GAME_API_VERSION ) {
		const int foundVersion = gameExport != NULL ? gameExport->version : -1;
		UnloadGameDLL();
		FatalError(
			"Wrong Game DLL API version (expected '%i' but found '%i').",
			GAME_API_VERSION,
			foundVersion
		);
		return;
	}

	game = gameExport->game;
	gameEdit = gameExport->gameEdit;
	if ( game == NULL ) {
		UnloadGameDLL();
		FatalError( "game DLL returned a NULL game interface" );
		return;
	}

	try {
		game->Init();
	} catch ( const idException &e ) {
		Warning( "idCommonLocal::LoadGameDLL: game->Init error: %s", e.error );
	}
	idKeyInput::SetupBinds();
}

void idCommonLocal::UnloadGameDLL() {
	game = NULL;
	gameEdit = NULL;
	if ( gameDLL != NULL ) {
		// Nothing in the engine may keep pointing into the module's code.
		const int cvarsRestored = CVar_RestoreModuleCallbacks();
		const int commandsRemoved = Cmd_RemoveModuleCommands();
		if ( cvarsRestored != 0 || commandsRemoved != 0 ) {
			DPrintf( "unloading game module: restored the completion/callbacks of %d cvar%s and removed %d command%s it left registered\n",
				cvarsRestored, cvarsRestored == 1 ? "" : "s", commandsRemoved, commandsRemoved == 1 ? "" : "s" );
		}
		sys->DLL_Unload( gameDLL );
		gameDLL = NULL;
	}
}

void idCommonLocal::InitGame( bool resetConfigs ) {
	( void )resetConfigs;

	fileSystem->Init( false );
	declManager->Init();
	renderBindingManager->Init();

	cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "exec default.cfg\n" );
	if ( !SafeMode() ) {
		cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "exec " BINDING_FILE "\n" );
		cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "exec " CONFIG_FILE "\n" );
	}
	cmdSystem->BufferCommandText( CMD_EXEC_APPEND, "exec autoexec.cfg\n" );
	cmdSystem->ExecuteCommandBuffer();
	StartupVariable( NULL );
	cvarSystem->ClearModifiedFlags( CVAR_ARCHIVE );

	if ( networkService != NULL ) {
		networkService->Init();
	}
	if ( !com_skipRenderer.GetBool() && ( networkSystem == NULL || !networkSystem->IsDedicated() ) ) {
		glimpParms_t parms;
		memset( &parms, 0, sizeof( parms ) );
		const int modeIndex = cvarSystem->GetCVarInteger( "r_mode" );
		if ( modeIndex >= 0 && modeIndex < GetNumVideoModes() ) {
			const vidmode_t& mode = GetVideoMode( modeIndex );
			parms.width = mode.width;
			parms.height = mode.height;
		} else {
			parms.width = Max( 320, cvarSystem->GetCVarInteger( "r_customWidth" ) );
			parms.height = Max( 240, cvarSystem->GetCVarInteger( "r_customHeight" ) );
		}
		parms.fullScreen = cvarSystem->GetCVarBool( "r_fullscreen" );
		parms.stereo = false;
		parms.displayHz = cvarSystem->GetCVarInteger( "r_displayRefresh" );
		parms.multiSamples = multiSampleParms( Max( 0, cvarSystem->GetCVarInteger( "r_multiSamples" ) ) );
		parms.pixelAspect = 1.0f;
		parms.fullscreenAvail = true;
		if ( !parms.fullScreen ) {
			sys3D->ConstrainToPrimaryMonitor( parms.width, parms.height );
		}
		sys3D->InitContext( parms );
		if ( sys3D->GetGameRenderContext() == NULL || sys3D->GetGameWindowHandle() == NULL ) {
			common->FatalError(
				"Failed to create the ETQW game window/OpenGL context (%dx%d, fullscreen %d)",
				parms.width,
				parms.height,
				parms.fullScreen ? 1 : 0
			);
		}
		Sys_PumpEvents();
	}

	renderSystem->Init();
	InitLanguageDict( false );
	Sys_ShowSplashScreen(false);
	Sys_ShowWindow(true);
	PrintLoadingMessage( "INITIALIZING COLLISION MANAGER..." );
	collisionModelManager->Init();
	PrintLoadingMessage( "INITIALIZING CONSOLE GRAPHICS..." );
	console->LoadGraphics();
	PrintLoadingMessage( "INITIALIZING EVENTS..." );
	eventLoop->Init();
	PrintLoadingMessage( "INITIALIZING USER COMMAND INPUT..." );
	usercmdGen->Init();
	PrintLoadingMessage( "INITIALIZING SOUND..." );
	soundSystem->Init();
	if ( networkSystem == NULL || !networkSystem->IsDedicated() ) {
		soundSystem->InitHW();
	}
	PrintLoadingMessage( "INITIALIZING INPUT..." );
	Sys_InitInput();
	PrintLoadingMessage( "INITIALIZING NETWORK..." );
	idAsyncNetwork::Init();
	PrintLoadingMessage( "INITIALIZING SESSION..." );
	session->Init();
	if ( graphManager != NULL ) {
		graphManager->Init();
	}
	PrintLoadingMessage( "INITIALIZING GAME..." );
	if ( adManager != NULL ) {
		adManager->Init();
	}
	if ( bse != NULL ) {
		bse->Init();
	}

	LoadGameDLL();
	sys->PostGameInit();
	nextConfigWriteTime = Sys_Milliseconds() + 2000;
}

void idCommonLocal::Init( int argc, const char** argv, const char* cmdline ) {
	if ( com_fullyInitialized ) {
		return;
	}
	com_mainThreadId = std::this_thread::get_id();

	try {
		idLib::sys = sys;
		idLib::common = common;
		idLib::cvarSystem = cvarSystem;
		idLib::fileSystem = fileSystem;
		idLib::Init();
		mainThreadLastTicMsec = 0;
		mainThreadTicResidual = 0.0;
		com_ticNumber = 0;
		ClearWarnings( GAME_NAME " initialization" );

		idCmdArgs commandLine;
		if ( cmdline != NULL && cmdline[ 0 ] != '\0' ) {
			commandLine.TokenizeString( cmdline, true );
			argv = commandLine.GetArgs( &argc );
		}
		ParseCommandLine( argc, argv );

		cmdSystem->Init();
		cvarSystem->Init();
		idCVar::RegisterStaticVars();
		// The main-menu GUI installs an onCVarChanged callback for this engine
		// variable during game DLL initialization.  Keep the retail ownership in
		// Common and make the cross-DLL registration explicit.
		cvarSystem->Register( &com_machineSpec );
		idKeyInput::Init();
		console->Init();
		Sys_Init();
		Sys_InitNetworking();
		StartupVariable( NULL );
		Printf( "%s\n", versionString.string );
		InitCommands();
		InitSIMD();
		InitGame( false );
	} catch ( idException& ) {
		// Error() has already printed the error banner.
		Sys_Error( "Error during initialization" );
	}

	// Startup commands (+spawnServer <map>, +exec ...) run before the ready
	// banner so the first map is loaded when it prints.  An error in one of
	// them is handled as it is when the same command fails later in Frame():
	// Error() has printed the ERROR banner and stopped the session, and the
	// engine keeps running (a dedicated server stays up without a map until
	// another one is started), as idTech 4 does for a failing +map.  Letting
	// the exception out of Init aborted the process (SIGABRT, exit 134).
	AddStartupCommands();
	try {
		cmdSystem->ExecuteCommandBuffer();
	} catch ( idException& ) {
	}
	com_fullyInitialized = true;
	Printf( "------------- ETQW initialized -------------\n" );
}

void idCommonLocal::Shutdown() {
	if ( com_shuttingDown ) {
		return;
	}
	com_shuttingDown = true;
	WriteConfiguration();
	com_fullyInitialized = false;

	if ( session != NULL ) {
		session->Shutdown();
	}
	if ( game != NULL ) {
		game->Shutdown();
	}
	UnloadGameDLL();
	if ( bse != NULL ) {
		bse->Shutdown();
	}
	if ( adManager != NULL ) {
		adManager->Shutdown();
	}
	if ( graphManager != NULL ) {
		graphManager->Shutdown();
	}
	if ( networkService != NULL ) {
		networkService->Shutdown();
	}
	idAsyncNetwork::Shutdown();
	Sys_ShutdownInput();
	if ( soundSystem != NULL ) {
		sdScopedLock< true > soundLock( soundSystem->GetLock() );
		soundSystem->ShutdownHW();
		soundSystem->Shutdown();
	}
	if ( usercmdGen != NULL ) {
		usercmdGen->Shutdown();
	}
	if ( eventLoop != NULL ) {
		eventLoop->Shutdown();
	}
	if ( collisionModelManager != NULL ) {
		collisionModelManager->Shutdown();
	}
	if ( renderSystem != NULL ) {
		renderSystem->Shutdown();
	}
	if ( sys3D != NULL ) {
		sys3D->Shutdown();
	}
	if ( declManager != NULL ) {
		renderBindingManager->Shutdown();
		declManager->Shutdown();
	}
	if ( fileSystem != NULL && fileSystem->IsInitialized() ) {
		DumpWarnings();
		CloseLogFile();
		fileSystem->Shutdown( false );
	}
	Sys_ShutdownNetworking();
	Sys_Shutdown();
	if ( console != NULL ) {
		console->Shutdown();
	}
	idKeyInput::Shutdown();
	if ( cvarSystem != NULL && cvarSystem->IsInitialized() ) {
		cvarSystem->Shutdown();
	}
	if ( cmdSystem != NULL ) {
		cmdSystem->Shutdown();
	}
	languageDict.Clear();
	idLib::ShutDown();
}

void idCommonLocal::Quit() {
	if ( com_errorEntered == ERP_NONE ) {
		Shutdown();
	}
	Sys_Quit();
}

bool idCommonLocal::IsInitialized() const {
	return com_fullyInitialized;
}

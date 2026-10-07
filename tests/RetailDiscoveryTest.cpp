// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: RetailDiscoveryTest.cpp
Retail data discovery (src/framework/RetailDiscovery.h, ledger 068) on
synthetic trees in a temporary directory: the base/pak000.pk4 marker in any
letter case, the path lists, the stored path file, Steam's libraryfolders.vdf
(both formats) and steamapps/common, and the search order with the platform
hooks stubbed here.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "RetailDiscovery.h"

#include <filesystem>
#include <fstream>
#include <string>

namespace fs = std::filesystem;

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

// The platform hooks, set by each case
static idStr g_defaultBasePath;
static idStrList g_platformCandidates;
static idStrList g_steamRoots;

const char *Sys_DefaultBasePath( void ) {
	return g_defaultBasePath.c_str();
}

void Sys_AppendRetailDataCandidates( idStrList &candidates, idStrList &steamRoots ) {
	for ( int i = 0; i < g_platformCandidates.Num(); i++ ) {
		candidates.Append( g_platformCandidates[ i ] );
	}
	for ( int i = 0; i < g_steamRoots.Num(); i++ ) {
		steamRoots.Append( g_steamRoots[ i ] );
	}
}

static int g_failures = 0;
#define CHECK( cond, ... ) do { if ( !( cond ) ) { printf( "FAIL: " __VA_ARGS__ ); printf( "\n" ); g_failures++; } } while ( 0 )

static void SetEnv( const char *name, const char *value ) {
#ifdef _WIN32
	_putenv_s( name, value != NULL ? value : "" );
#else
	if ( value != NULL ) {
		setenv( name, value, 1 );
	} else {
		unsetenv( name );
	}
#endif
}

static void WriteFile( const fs::path &file, const std::string &text ) {
	fs::create_directories( file.parent_path() );
	std::ofstream out( file, std::ios::binary );
	out << text;
}

// an install root holding the retail marker, with the names in the given case
static fs::path MakeInstall( const fs::path &root, const char *baseName = "base", const char *pakName = "pak000.pk4" ) {
	WriteFile( root / baseName / pakName, "PK" );
	return root;
}

static std::string Str( const fs::path &path ) {
	return path.string();
}

static bool SameDir( const char *a, const fs::path &b ) {
	std::error_code error;
	return fs::equivalent( fs::path( a ), b, error );
}

static void ResetHooks( const fs::path &engineDir ) {
	g_defaultBasePath = Str( engineDir ).c_str();
	g_platformCandidates.Clear();
	g_steamRoots.Clear();
	SetEnv( "OPENETQW_BASEPATH", NULL );
}

int main() {
	idStr::InitMemory();	// as idLib::Init: an idStr past its base buffer allocates from it

	const fs::path tmp = fs::temp_directory_path() / ( "openetqw-retail-discovery-" + std::to_string( ( unsigned long long )time( NULL ) ) );
	std::error_code error;
	fs::remove_all( tmp, error );
	fs::create_directories( tmp );

	// --- the marker ---
	const fs::path lower = MakeInstall( tmp / "lower" );
	const fs::path upper = MakeInstall( tmp / "upper", "BASE", "PAK000.PK4" );
	WriteFile( tmp / "nopak" / "base" / "pak001.pk4", "PK" );
	fs::create_directories( tmp / "pakdir" / "base" / "pak000.pk4" );	// a directory, not the pak
	CHECK( RetailDiscovery::HasRetailData( Str( lower ).c_str() ), "lower-case base/pak000.pk4 not found" );
	CHECK( RetailDiscovery::HasRetailData( Str( upper ).c_str() ), "upper-case BASE/PAK000.PK4 not found" );
	CHECK( !RetailDiscovery::HasRetailData( Str( tmp / "nopak" ).c_str() ), "a base/ without pak000.pk4 counted" );
	CHECK( !RetailDiscovery::HasRetailData( Str( tmp / "pakdir" ).c_str() ), "a pak000.pk4 directory counted" );
	CHECK( !RetailDiscovery::HasRetailData( Str( tmp / "missing" ).c_str() ), "a missing root counted" );
	CHECK( !RetailDiscovery::HasRetailData( "" ) && !RetailDiscovery::HasRetailData( NULL ), "an empty root counted" );

	// --- path lists ---
	{
		idStrList paths;
#ifdef _WIN32
		// ';' only: ':' is a drive letter's; backslashes, quotes and a trailing slash go
		RetailDiscovery::SplitPathList( " \"C:\\Games\\ETQW\\\" ;C:/Games/ETQW;;D:/Other/ ", paths );
		const char *first = "C:/Games/ETQW";
		const char *second = "D:/Other";
#else
		// ':' and ';' both separate
		RetailDiscovery::SplitPathList( " \"/games/etqw/\" :/games/etqw;;/other/ ", paths );
		const char *first = "/games/etqw";
		const char *second = "/other";
#endif
		CHECK( paths.Num() == 2, "path list: %d entries, want 2", paths.Num() );
		CHECK( paths.Num() > 0 && idStr::Cmp( paths[ 0 ], first ) == 0, "path list: first '%s'", paths.Num() > 0 ? paths[ 0 ].c_str() : "" );
		CHECK( paths.Num() > 1 && idStr::Cmp( paths[ 1 ], second ) == 0, "path list: second '%s'", paths.Num() > 1 ? paths[ 1 ].c_str() : "" );
		paths.Clear();
		RetailDiscovery::SplitPathList( NULL, paths );
		CHECK( paths.Num() == 0, "a NULL path list gave %d entries", paths.Num() );
	}

	// --- the stored path: a byte order mark, CRLF and a trailing slash ---
	{
		const fs::path stored = tmp / "save" / "basepath.txt";
		WriteFile( stored, std::string( "\xEF\xBB\xBF" ) + Str( lower ) + "/\r\nignored second line\r\n" );
		idStr path;
		CHECK( RetailDiscovery::ReadStoredPath( Str( stored ).c_str(), path ) && SameDir( path, lower ),
			"stored path read as '%s'", path.c_str() );
		WriteFile( tmp / "empty" / "basepath.txt", "\r\n" );
		CHECK( !RetailDiscovery::ReadStoredPath( Str( tmp / "empty" / "basepath.txt" ).c_str(), path ), "an empty stored path read" );
		CHECK( !RetailDiscovery::ReadStoredPath( Str( tmp / "none.txt" ).c_str(), path ), "a missing stored path read" );
	}

	// --- Steam: libraryfolders.vdf, new and old format, and steamapps/common ---
	const fs::path steam = tmp / "Steam";
	const fs::path library2 = tmp / "SteamLibrary2";
	const fs::path library3 = tmp / "SteamLibrary3";
	{
		// vdf escapes backslashes
		auto vdfString = []( const fs::path &path ) {
			std::string escaped;
			for ( char c : path.string() ) {
				escaped += c;
				if ( c == '\\' ) {
					escaped += '\\';
				}
			}
			return escaped;
		};
		WriteFile( steam / "steamapps" / "libraryfolders.vdf",
			"\"libraryfolders\"\n{\n"
			"\t\"0\"\n\t{\n\t\t\"path\"\t\t\"" + vdfString( library2 ) + "\"\n\t\t\"label\"\t\t\"\"\n\t}\n"
			"\t\"1\"\t\t\"" + vdfString( library3 ) + "/\"\n"	// the pre-2021 form
			"}\n" );
		fs::create_directories( steam / "steamapps" / "common" / "Half-Life" );
		fs::create_directories( library2 / "steamapps" / "common" / "Another Game" );
		MakeInstall( library2 / "steamapps" / "common" / "Enemy Territory Quake Wars" );
		fs::create_directories( library3 / "steamapps" / "common" );

		idStrList libraries;
		RetailDiscovery::AppendSteamLibraries( Str( steam ).c_str(), libraries );
		RetailDiscovery::AppendSteamLibraries( Str( steam ).c_str(), libraries );	// a second root listing the same ones
		CHECK( libraries.Num() == 3, "Steam libraries: %d, want 3 (the root, the path entry, the old entry)", libraries.Num() );
		if ( libraries.Num() == 3 ) {
			CHECK( SameDir( libraries[ 0 ], steam ), "Steam library 0 '%s'", libraries[ 0 ].c_str() );
			CHECK( SameDir( libraries[ 1 ], library2 ), "Steam library 1 '%s'", libraries[ 1 ].c_str() );
			CHECK( SameDir( libraries[ 2 ], library3 ), "Steam library 2 '%s'", libraries[ 2 ].c_str() );
		}

		idStrList folders;
		RetailDiscovery::AppendSteamCommonFolders( Str( library2 ).c_str(), folders );
		CHECK( folders.Num() == 2, "steamapps/common folders: %d, want 2", folders.Num() );
		if ( folders.Num() == 2 ) {
			CHECK( folders[ 0 ].Find( "Another Game" ) >= 0 && folders[ 1 ].Find( "Enemy Territory" ) >= 0,
				"steamapps/common not in name order: '%s', '%s'", folders[ 0 ].c_str(), folders[ 1 ].c_str() );
		}
	}

	// --- the search order ---
	const fs::path engineNoData = tmp / "engine";
	fs::create_directories( engineNoData / "base" / "vkprogs" );	// the engine's own base/: no retail data
	const fs::path envInstall = MakeInstall( tmp / "env" );
	const fs::path storedInstall = MakeInstall( tmp / "stored" );
	const fs::path engineInstall = MakeInstall( tmp / "engine-with-data" );
	const fs::path recordInstall = MakeInstall( tmp / "record" );
	const fs::path savePath = tmp / "save2";
	WriteFile( savePath / "basepath.txt", Str( storedInstall ) + "\n" );
	{
		idStr root;
		idList<retailProbe_t> probes;

		// everything present: the environment wins
		ResetHooks( engineInstall );
		g_platformCandidates.Append( Str( recordInstall ).c_str() );
		g_steamRoots.Append( Str( steam ).c_str() );
		SetEnv( "OPENETQW_BASEPATH", ( Str( tmp / "env-missing" ) + ";" + Str( envInstall ) ).c_str() );
		CHECK( RetailDiscovery::Discover( Str( savePath ).c_str(), root, probes ) && SameDir( root, envInstall ),
			"order 1: '%s', want the environment's", root.c_str() );
		CHECK( probes.Num() == 2 && !probes[ 0 ].found && probes[ 1 ].found, "order 1: %d probes", probes.Num() );

		// without the environment: the stored path
		SetEnv( "OPENETQW_BASEPATH", NULL );
		probes.Clear();
		CHECK( RetailDiscovery::Discover( Str( savePath ).c_str(), root, probes ) && SameDir( root, storedInstall ),
			"order 2: '%s', want the stored path", root.c_str() );

		// without a stored path: the engine's directory, when it holds the data
		probes.Clear();
		CHECK( RetailDiscovery::Discover( Str( tmp / "no-save" ).c_str(), root, probes ) && SameDir( root, engineInstall ),
			"order 3: '%s', want the engine directory", root.c_str() );

		// an engine directory with only its own base/: the platform's records
		g_defaultBasePath = Str( engineNoData ).c_str();
		probes.Clear();
		CHECK( RetailDiscovery::Discover( Str( tmp / "no-save" ).c_str(), root, probes ) && SameDir( root, recordInstall ),
			"order 4: '%s', want the install record", root.c_str() );

		// no records: the Steam library folder that holds the data, whatever its name
		g_platformCandidates.Clear();
		probes.Clear();
		CHECK( RetailDiscovery::Discover( Str( tmp / "no-save" ).c_str(), root, probes ) &&
			SameDir( root, library2 / "steamapps" / "common" / "Enemy Territory Quake Wars" ),
			"order 5: '%s', want the Steam library's", root.c_str() );
		CHECK( probes.Num() > 0 && idStr::Cmp( probes[ probes.Num() - 1 ].source, "Steam library" ) == 0,
			"order 5: the last probe is '%s'", probes.Num() > 0 ? probes[ probes.Num() - 1 ].source.c_str() : "" );

		// nothing anywhere: false, and every probe says so
		g_steamRoots.Clear();
		g_platformCandidates.Append( Str( tmp / "nopak" ).c_str() );
		probes.Clear();
		root = "unchanged";
		CHECK( !RetailDiscovery::Discover( Str( tmp / "no-save" ).c_str(), root, probes ), "nothing to find, but '%s' found", root.c_str() );
		int found = 0;
		for ( int i = 0; i < probes.Num(); i++ ) {
			found += probes[ i ].found ? 1 : 0;
		}
		CHECK( probes.Num() == 2 && found == 0, "nothing to find: %d probes, %d found (want 2, 0)", probes.Num(), found );
	}

	SetEnv( "OPENETQW_BASEPATH", NULL );
	fs::remove_all( tmp, error );
	printf( "retail-discovery: %d failure(s)\n", g_failures );
	return g_failures == 0 ? 0 : 1;
}

// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: RetailDiscoveryTest.cpp
Retail data discovery (src/framework/RetailDiscovery.h, ledger 068) on
synthetic trees in a temporary directory: the base/pak000.pk4 marker in any
letter case, the path lists, the stored path file (UTF-8), Steam's
libraryfolders.vdf (both formats, UTF-8, a symlinked root) and app manifests,
names outside every code page, Wine prefixes (the prefix folders, one level
down too, and the install folders and Steam under drive_c that exist, without
case), and the search order with the platform hooks stubbed here.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "RetailDiscovery.h"

#include <filesystem>
#include <chrono>
#include <fstream>
#include <string>
#ifndef _WIN32
#include <unistd.h>	// geteuid, for the read-only folder case
#endif

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

// a real directory, for WriteStoredPath (the engine's Sys_Mkdir makes one level)
void Sys_Mkdir( const char *path ) {
	std::error_code error;
	fs::create_directory( fs::u8path( path ), error );
}

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
	return path.string();	// the engine's form; only for names every code page holds
}

// a path in the engine's form when the code page can name it (path.string() converts to the
// ANSI code page on Windows and throws for a name it cannot hold)
static bool Narrow( const fs::path &path, idStr &name ) {
	try {
		name = path.string().c_str();
		return true;
	} catch ( const std::exception & ) {
		return false;
	}
}

// a path as UTF-8 (what Steam's files and Notepad write)
static std::string Utf8( const fs::path &path ) {
	const std::u8string utf8 = path.u8string();
	return std::string( utf8.begin(), utf8.end() );
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

	// a directory of this run's own (two runs at once must not share one); not
	// <random>: libc++'s needs INFINITY, which idLib's Math.h undefines
	const fs::path tmp = fs::temp_directory_path() / ( "openetqw-retail-discovery-" +
		std::to_string( ( unsigned long long )std::chrono::steady_clock::now().time_since_epoch().count() ) );
	std::error_code error;
	fs::remove_all( tmp, error );
	fs::create_directories( tmp );

	// --- the marker ---
	const fs::path lower = MakeInstall( tmp / "lower" );
	const fs::path upper = MakeInstall( tmp / "upper", "BASE", "PAK000.PK4" );
	// names no code page may hold beside the marker: skipped, never a crash
	// (std::filesystem's own conversion throws on them on Windows)
	const fs::path unicode = MakeInstall( tmp / "unicode" );
	WriteFile( unicode / "base" / fs::path( u8"ゲーム.pk4" ), "PK" );
	fs::create_directories( unicode / fs::path( u8"データ" ) );
	CHECK( RetailDiscovery::HasRetailData( Str( unicode ).c_str() ), "an install with non-ANSI names beside the marker not found" );
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
		// a first-run choice is written under a save path that does not exist yet, and read back
		const fs::path savePath = tmp / "save" / "nested";
		CHECK( RetailDiscovery::WriteStoredPath( Str( savePath ).c_str(), Str( lower ).c_str() ), "the stored path was not written" );
		CHECK( RetailDiscovery::ReadStoredPath( Str( savePath / "basepath.txt" ).c_str(), path ) && SameDir( path, lower ),
			"the written stored path read back as '%s'", path.c_str() );
		CHECK( !RetailDiscovery::WriteStoredPath( "", Str( lower ).c_str() ) && !RetailDiscovery::WriteStoredPath( Str( savePath ).c_str(), "" ),
			"an empty save path or root was written" );
		// a non-ASCII root the code page can name ("Jeux vidéo") round-trips: written as
		// UTF-8, read back as the engine's own name
		const fs::path accented = tmp / fs::u8path( "Jeux vid\xC3\xA9o" );
		idStr accentedName;
		if ( Narrow( accented, accentedName ) ) {
			const fs::path accentedSave = tmp / "save-accented";
			CHECK( RetailDiscovery::WriteStoredPath( Str( accentedSave ).c_str(), accentedName.c_str() ), "a non-ASCII stored path was not written" );
			// the read path is normalised (forward slashes on Windows): compare as directories
			std::error_code error;
			fs::create_directories( accented, error );
			CHECK( RetailDiscovery::ReadStoredPath( Str( accentedSave / "basepath.txt" ).c_str(), path ) && SameDir( path, accented ),
				"a non-ASCII stored path read back as '%s', want '%s'", path.c_str(), accentedName.c_str() );
		}
	}

	// --- the Linux folder-picker routes (ledger 068) ---
	{
		idList< RetailDiscovery::retailFolderChooser_t > choosers;
		RetailDiscovery::AppendLinuxFolderChoosers( choosers );
		const char *const programs[] = { "zenity", "kdialog", "yad" };
		CHECK( choosers.Num() == 3, "expected three folder-picker routes, got %d", choosers.Num() );
		for ( int i = 0; i < choosers.Num() && i < 3; i++ ) {
			const RetailDiscovery::retailFolderChooser_t &chooser = choosers[ i ];
			CHECK( !idStr::Icmp( chooser.program.c_str(), programs[ i ] ),
				"folder-picker route %d is '%s', want '%s'", i, chooser.program.c_str(), programs[ i ] );
			CHECK( chooser.folderArgs.Num() > 1 && chooser.messageArgs.Num() > 1,
				"folder-picker route %s is missing its arguments", chooser.program.c_str() );
			bool asksForFolder = false;
			for ( int a = 0; a < chooser.folderArgs.Num(); a++ ) {
				if ( !idStr::Icmp( chooser.folderArgs[ a ].c_str(), "--directory" ) ||
					 !idStr::Icmp( chooser.folderArgs[ a ].c_str(), "--getexistingdirectory" ) ) {
					asksForFolder = true;
				}
			}
			CHECK( asksForFolder, "folder-picker route %s does not ask for a folder", chooser.program.c_str() );
			// the refusal text is the last argument, so the flag that takes it comes last too
			const idStr &last = chooser.messageArgs[ chooser.messageArgs.Num() - 1 ];
			CHECK( !idStr::Icmp( last.c_str(), "--text" ) || !idStr::Icmp( last.c_str(), "--sorry" ),
				"folder-picker route %s's refusal dialog does not take the text last ('%s')", chooser.program.c_str(), last.c_str() );
		}
	}
	// a chooser's answer: its first line, trimmed, absolute; a message or nothing is no choice
	{
		idStr path;
		CHECK( RetailDiscovery::ParseChooserOutput( "/games/ETQW\n", path ) && !idStr::Icmp( path.c_str(), "/games/ETQW" ),
			"a chooser's answer read as '%s'", path.c_str() );
		CHECK( RetailDiscovery::ParseChooserOutput( "/games/ETQW  \r\n", path ) && !idStr::Icmp( path.c_str(), "/games/ETQW" ),
			"a padded chooser answer read as '%s'", path.c_str() );
		CHECK( RetailDiscovery::ParseChooserOutput( "/a b/c\n/other\n", path ) && !idStr::Icmp( path.c_str(), "/a b/c" ),
			"a chooser that printed two lines read as '%s'", path.c_str() );
		CHECK( !RetailDiscovery::ParseChooserOutput( "", path ) && !RetailDiscovery::ParseChooserOutput( "\n", path ) &&
			   !RetailDiscovery::ParseChooserOutput( "no folder chosen\n", path ) && !RetailDiscovery::ParseChooserOutput( NULL, path ),
			"a message or an empty answer counted as a folder" );
	}
	// what a finished helper means: the rule the chooser sequence acts on
	{
		idStr answer;
		CHECK( RetailDiscovery::ClassifyChooser( 127, false, "", answer ) == RetailDiscovery::CHOOSER_ABSENT,
			"a helper that could not be started was not skipped" );
		CHECK( RetailDiscovery::ClassifyChooser( 0, false, "/games/ETQW\n", answer ) == RetailDiscovery::CHOOSER_PICKED &&
			   !idStr::Icmp( answer.c_str(), "/games/ETQW" ),
			"a chosen folder was read as '%s'", answer.c_str() );
		CHECK( RetailDiscovery::ClassifyChooser( 1, false, "", answer ) == RetailDiscovery::CHOOSER_CANCELLED,
			"a helper's Cancel (non-zero exit) did not end the sequence" );
		CHECK( RetailDiscovery::ClassifyChooser( -15, false, "", answer ) == RetailDiscovery::CHOOSER_CANCELLED,
			"a helper killed by a signal did not end the sequence" );
		CHECK( RetailDiscovery::ClassifyChooser( 0, false, "no folder chosen\n", answer ) == RetailDiscovery::CHOOSER_CANCELLED,
			"a helper that printed no folder did not end the sequence" );
		CHECK( RetailDiscovery::ClassifyChooser( 0, true, "/games/ETQW\n", answer ) == RetailDiscovery::CHOOSER_CANCELLED,
			"output too long for a folder was taken as a choice" );
	}
	// a write that cannot happen leaves the previous choice, never a truncated one
	{
		const fs::path savePath = tmp / "save-keep";
		WriteFile( savePath / "basepath.txt", "/previous/install\n" );
#ifndef _WIN32
		if ( geteuid() != 0 ) {	// root ignores the read-only folder: the case cannot be built
			std::error_code chmodError;
			fs::permissions( savePath, fs::perms::owner_read | fs::perms::owner_exec, fs::perm_options::replace, chmodError );
			const bool refused = !RetailDiscovery::WriteStoredPath( Str( savePath ).c_str(), Str( lower ).c_str() );
			fs::permissions( savePath, fs::perms::owner_all, fs::perm_options::replace, chmodError );
			idStr kept;
			CHECK( refused, "a stored path was written to a folder that cannot hold it" );
			CHECK( RetailDiscovery::ReadStoredPath( Str( savePath / "basepath.txt" ).c_str(), kept ) && !idStr::Icmp( kept.c_str(), "/previous/install" ),
				"a failed write replaced the previous stored path with '%s'", kept.c_str() );
			CHECK( !fs::exists( savePath / "basepath.txt.tmp" ), "a failed write left its temporary file behind" );
		}
#endif
		CHECK( RetailDiscovery::WriteStoredPath( Str( savePath ).c_str(), Str( lower ).c_str() ), "a stored path was not replaced" );
		CHECK( !fs::exists( savePath / "basepath.txt.tmp" ), "a write left its temporary file behind" );
		idStr replaced;
		CHECK( RetailDiscovery::ReadStoredPath( Str( savePath / "basepath.txt" ).c_str(), replaced ) && SameDir( replaced, lower ),
			"the replaced stored path read back as '%s'", replaced.c_str() );
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
		// app manifests: Doom 3 (base/pak000.pk4 too, and its manifest first) is not the
		// game; ET:QW's is; one that would leave steamapps/common is refused
		auto manifest = []( const fs::path &library, const char *appId, const char *name, const char *installDir ) {
			WriteFile( library / "steamapps" / ( std::string( "appmanifest_" ) + appId + ".acf" ),
				std::string( "\"AppState\"\n{\n\t\"appid\"\t\t\"" ) + appId + "\"\n\t\"name\"\t\t\"" + name +
				"\"\n\t\"installdir\"\t\t\"" + installDir + "\"\n}\n" );
		};
		fs::create_directories( steam / "steamapps" / "common" / "Half-Life" );
		manifest( steam, "70", "Half-Life", "Half-Life" );
		MakeInstall( library2 / "steamapps" / "common" / "Doom 3" );
		manifest( library2, "1", "DOOM 3", "Doom 3" );	// appmanifest_1.acf sorts before appmanifest_10000.acf
		MakeInstall( library2 / "steamapps" / "common" / "Enemy Territory Quake Wars" );
		manifest( library2, "10000", "Enemy Territory: QUAKE Wars", "Enemy Territory Quake Wars" );
		manifest( library2, "99999", "Enemy Territory: QUAKE Wars (bad manifest)", "../../outside" );
		manifest( library2, "99998", "Enemy Territory: QUAKE Wars (bad manifest)", ".." );
		manifest( library2, "99997", "Enemy Territory: QUAKE Wars (bad manifest)", "C:" );
		fs::create_directories( library2 / "steamapps" / fs::path( u8"ゲーム" ) );	// a non-ANSI name in steamapps
		fs::create_directories( library3 / "steamapps" / "common" );

		idStrList libraries;
		idStrList keys;
		RetailDiscovery::AppendSteamLibraries( Str( steam ).c_str(), libraries, keys );
		RetailDiscovery::AppendSteamLibraries( Str( steam ).c_str(), libraries, keys );	// a second root listing the same ones
		RetailDiscovery::AppendSteamLibraries( ( Str( steam ) + "/" ).c_str(), libraries, keys );	// another spelling of it
		CHECK( libraries.Num() == 3, "Steam libraries: %d, want 3 (the root, the path entry, the old entry)", libraries.Num() );
		if ( libraries.Num() == 3 ) {
			CHECK( SameDir( libraries[ 0 ], steam ), "Steam library 0 '%s'", libraries[ 0 ].c_str() );
			CHECK( SameDir( libraries[ 1 ], library2 ), "Steam library 1 '%s'", libraries[ 1 ].c_str() );
			CHECK( SameDir( libraries[ 2 ], library3 ), "Steam library 2 '%s'", libraries[ 2 ].c_str() );
		}

		// a symlinked root (~/.steam/steam on Linux) is the same library, where links can be made
		std::error_code linkError;
		fs::create_directory_symlink( steam, tmp / "SteamLink", linkError );
		if ( !linkError ) {
			const int before = libraries.Num();
			RetailDiscovery::AppendSteamLibraries( Str( tmp / "SteamLink" ).c_str(), libraries, keys );
			CHECK( libraries.Num() == before, "a symlinked Steam root listed again (%d libraries, want %d)", libraries.Num(), before );
		} else {
			printf( "note: no symlink could be made here (%s); the symlinked-root case is skipped\n", linkError.message().c_str() );
		}

		idStrList installs;
		RetailDiscovery::AppendSteamInstalls( Str( library2 ).c_str(), installs );
		CHECK( installs.Num() == 1, "Steam installs of the game in library 2: %d, want 1", installs.Num() );
		if ( installs.Num() == 1 ) {
			CHECK( SameDir( installs[ 0 ], library2 / "steamapps" / "common" / "Enemy Territory Quake Wars" ),
				"Steam install '%s'", installs[ 0 ].c_str() );
		}
		installs.Clear();
		RetailDiscovery::AppendSteamInstalls( Str( steam ).c_str(), installs );
		RetailDiscovery::AppendSteamInstalls( Str( library3 ).c_str(), installs );
		CHECK( installs.Num() == 0, "Steam installs of the game where there are none: %d", installs.Num() );

		// UTF-8 in Steam's files: a library named outside the code page is skipped
		// (Windows) or read (POSIX), never the end of the search; an accented one
		// the engine can name is searched
		const fs::path steamU = tmp / "SteamU";
		const fs::path libraryJa = tmp / fs::path( u8"ライブラリ" );
		const fs::path libraryE = tmp / fs::path( u8"Bibliothèque" );
		fs::create_directories( steamU / "steamapps" );
		fs::create_directories( libraryJa / "steamapps" / "common" );
		MakeInstall( libraryE / "steamapps" / "common" / "ETQW" );
		manifest( libraryE, "10000", "Enemy Territory: QUAKE Wars", "ETQW" );
		auto vdfUtf8 = []( const fs::path &path ) {
			std::string escaped;
			for ( char c : Utf8( path ) ) {
				escaped += c;
				if ( c == '\\' ) {
					escaped += '\\';
				}
			}
			return escaped;
		};
		WriteFile( steamU / "steamapps" / "libraryfolders.vdf",
			"\"libraryfolders\"\n{\n"
			"\t\"0\"\n\t{\n\t\t\"path\"\t\t\"" + vdfUtf8( libraryJa ) + "\"\n\t}\n"
			"\t\"1\"\n\t{\n\t\t\"path\"\t\t\"" + vdfUtf8( libraryE ) + "\"\n\t}\n"
			"}\n" );
		idStrList librariesU;
		idStrList keysU;
		RetailDiscovery::AppendSteamLibraries( Str( steamU ).c_str(), librariesU, keysU );
		idStrList installsU;
		for ( int i = 0; i < librariesU.Num(); i++ ) {
			RetailDiscovery::AppendSteamInstalls( librariesU[ i ], installsU );
		}
		bool foundAccented = false;
		for ( int i = 0; i < installsU.Num(); i++ ) {
			foundAccented = foundAccented || SameDir( installsU[ i ], libraryE / "steamapps" / "common" / "ETQW" );
		}
		CHECK( foundAccented, "the accented Steam library's install not found (%d libraries, %d installs)",
			librariesU.Num(), installsU.Num() );

		// a UTF-8 stored path (Notepad's) with an accent
		const fs::path storedAccented = MakeInstall( tmp / fs::path( u8"Données ETQW" ) );
		WriteFile( tmp / "saveE" / "basepath.txt", "\xEF\xBB\xBF" + Utf8( storedAccented ) + "\r\n" );
		idStr storedPath;
		CHECK( RetailDiscovery::ReadStoredPath( Str( tmp / "saveE" / "basepath.txt" ).c_str(), storedPath ) &&
			SameDir( storedPath, storedAccented ), "a UTF-8 stored path with an accent read as '%s'", storedPath.c_str() );
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

	// Wine prefixes (Linux's table): the prefix folders of a Lutris-, Bottles- or
	// PlayOnLinux-style parent, one level down too, and the install folders and Steam
	// under drive_c that exist, matched without case
	{
		const fs::path wine = tmp / "wine";
		const fs::path pfx1 = wine / "pfx1";
		const fs::path pfx2 = wine / "pfx2";
		const fs::path nested = wine / "lutrisgame" / "pfx";
		const fs::path umu = wine / "umu" / "12345";
		const fs::path game = MakeInstall( pfx1 / "drive_c" / "Program Files (x86)" / "id Software" / "Enemy Territory - QUAKE Wars" );
		const fs::path steam = pfx1 / "drive_c" / "Program Files (x86)" / "Steam";
		fs::create_directories( steam / "steamapps" );
		const fs::path lowerGame = MakeInstall( nested / "drive_c" / "program files" / "etqw" );
		fs::create_directories( pfx2 / "drive_c" / "windows" );
		fs::create_directories( umu / "drive_c" );
		fs::create_directories( wine / "notpfx" / "something" );
		WriteFile( wine / "a-file", "not a prefix" );

		idStrList prefixes, keys;
		RetailDiscovery::AppendWinePrefixesIn( Str( wine ).c_str(), prefixes, keys );
		CHECK( prefixes.Num() == 4, "wine: %d prefixes, want 4 (lutrisgame/pfx, pfx1, pfx2, umu/12345)", prefixes.Num() );
		CHECK( prefixes.Num() == 4 && SameDir( prefixes[ 0 ], nested ) && SameDir( prefixes[ 1 ], pfx1 ) &&
			SameDir( prefixes[ 2 ], pfx2 ) && SameDir( prefixes[ 3 ], umu ), "wine: prefixes not the four in name order" );
		RetailDiscovery::AppendWinePrefix( Str( pfx1 ).c_str(), prefixes, keys );
		RetailDiscovery::AppendWinePrefix( Str( wine / "notpfx" ).c_str(), prefixes, keys );
#ifndef _WIN32
		std::error_code linkError;
		fs::create_directory_symlink( pfx1, tmp / "wine-link", linkError );
		if ( !linkError ) {
			RetailDiscovery::AppendWinePrefix( Str( tmp / "wine-link" ).c_str(), prefixes, keys );
		}
#endif
		CHECK( prefixes.Num() == 4, "wine: %d prefixes after the same prefix, a symlink to it and a non-prefix, want 4", prefixes.Num() );
		idStrList none, noKeys;
		RetailDiscovery::AppendWinePrefixesIn( Str( tmp / "missing" ).c_str(), none, noKeys );
		RetailDiscovery::AppendWinePrefixesIn( NULL, none, noKeys );
		CHECK( none.Num() == 0, "wine: %d prefixes in a missing folder", none.Num() );

		idStrList candidates, steamRoots;
		RetailDiscovery::AppendWineInstalls( Str( pfx1 ).c_str(), candidates, steamRoots );
		CHECK( candidates.Num() == 1 && SameDir( candidates[ 0 ], game ), "wine: %d install folders from pfx1, want the one that exists",
			candidates.Num() );
		CHECK( steamRoots.Num() == 1 && SameDir( steamRoots[ 0 ], steam ), "wine: %d Steam roots from pfx1, want its Steam", steamRoots.Num() );
		idStrList lowerCandidates, lowerSteam;
		RetailDiscovery::AppendWineInstalls( Str( nested ).c_str(), lowerCandidates, lowerSteam );
		CHECK( lowerCandidates.Num() == 1 && SameDir( lowerCandidates[ 0 ], lowerGame ) && lowerSteam.Num() == 0,
			"wine: 'program files/etqw' in another case not found (%d candidates)", lowerCandidates.Num() );
		idStr root;
		idList<retailProbe_t> probes;
		CHECK( RetailDiscovery::FindFirst( candidates, "platform list", root, probes ) && SameDir( root, game ),
			"wine: the game under drive_c not found ('%s')", root.c_str() );

		idStrList noCandidates, noSteam;
		RetailDiscovery::AppendWineInstalls( Str( pfx2 ).c_str(), noCandidates, noSteam );
		RetailDiscovery::AppendWineInstalls( Str( wine / "notpfx" ).c_str(), noCandidates, noSteam );
		RetailDiscovery::AppendWineInstalls( NULL, noCandidates, noSteam );
		CHECK( noCandidates.Num() == 0 && noSteam.Num() == 0, "wine: an empty prefix or a folder without drive_c gave %d candidates, %d Steam roots",
			noCandidates.Num(), noSteam.Num() );

		// through the search: a platform table built from the prefixes finds the first install
		ResetHooks( tmp / "engine-no-data" );
		for ( int i = 0; i < prefixes.Num(); i++ ) {
			RetailDiscovery::AppendWineInstalls( prefixes[ i ], g_platformCandidates, g_steamRoots );
		}
		probes.Clear();
		CHECK( RetailDiscovery::Discover( Str( tmp / "no-save" ).c_str(), root, probes ) && SameDir( root, lowerGame ),
			"wine: the search found '%s', want the first prefix's install", root.c_str() );
		CHECK( probes.Num() > 0 && idStr::Cmp( probes[ probes.Num() - 1 ].source, "platform list" ) == 0,
			"wine: the last probe is '%s', want the platform list", probes.Num() > 0 ? probes[ probes.Num() - 1 ].source.c_str() : "" );
	}

	SetEnv( "OPENETQW_BASEPATH", NULL );
	fs::remove_all( tmp, error );
	printf( "retail-discovery: %d failure(s)\n", g_failures );
	return g_failures == 0 ? 0 : 1;
}

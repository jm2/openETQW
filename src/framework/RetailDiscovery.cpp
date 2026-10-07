// SPDX-License-Identifier: GPL-3.0-or-later
// Retail data discovery (RetailDiscovery.h; ledger 068). After openQ4's
// fs_basepath auto-discovery (FileSystem.cpp FS_AutoDiscoverBasePath), with the
// ET:QW marker base/pak000.pk4 and Steam installs found from Steam's own app
// manifests, not from a folder name.
//
// Nothing here may throw into idFileSystemLocal::Init: directories are walked
// with error codes, and on Windows a name is compared in its native (UTF-16)
// form and converted to the ANSI code page the file system uses without
// std::filesystem's throwing conversion; an entry that cannot be named is skipped.

#include "RetailDiscovery.h"

#include <algorithm>
#include <exception>
#include <filesystem>
#include <system_error>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

namespace fs = std::filesystem;

static const char RETAIL_MARKER_DIR[] = BASE_GAMEDIR;
static const char RETAIL_MARKER_PAK[] = "pak000.pk4";
static const char STEAM_GAME_NAME[] = "QUAKE Wars";	// in an app manifest's "name"

// a path in the engine's form: forward slashes, no trailing slash (a bare root
// such as "C:/" or "/" keeps its slash)
static idStr NormalizePath( const char *path ) {
	idStr normalized = path;
	normalized.Strip( ' ' );
	normalized.Strip( '\t' );
	normalized.Strip( '"' );
	normalized.BackSlashesToSlashes();
	while ( normalized.Length() > 1 && normalized[ normalized.Length() - 1 ] == '/' &&
			!( normalized.Length() == 3 && normalized[ 1 ] == ':' ) ) {
		normalized.CapLength( normalized.Length() - 1 );
	}
	return normalized;
}

static bool SamePath( const char *a, const char *b ) {
#ifdef _WIN32
	return idStr::Icmp( a, b ) == 0;
#else
	return idStr::Cmp( a, b ) == 0;
#endif
}

// A path as the engine's file system can open it (the ANSI code page on
// Windows); false when it cannot be named that way
static bool NarrowPath( const fs::path &path, idStr &narrow ) {
#ifdef _WIN32
	const std::wstring &wide = path.native();
	const UINT codePage = GetACP();
	const bool utf8 = codePage == CP_UTF8;
	const int length = WideCharToMultiByte( codePage, utf8 ? 0 : WC_NO_BEST_FIT_CHARS, wide.c_str(), -1, NULL, 0, NULL, NULL );
	if ( length <= 0 ) {
		return false;
	}
	std::vector< char > buffer( static_cast< size_t >( length ) );
	BOOL lossy = FALSE;
	if ( WideCharToMultiByte( codePage, utf8 ? 0 : WC_NO_BEST_FIT_CHARS, wide.c_str(), -1, buffer.data(), length,
			NULL, utf8 ? NULL : &lossy ) <= 0 || lossy ) {
		return false;
	}
	narrow = buffer.data();
	return true;
#else
	narrow = path.native().c_str();
	return true;
#endif
}

// a file name against an ASCII name, without case and without converting it
static bool NameIs( const fs::path &name, const char *ascii ) {
	const fs::path::string_type &native = name.native();
	size_t i = 0;
	for ( ; ascii[ i ] != '\0'; i++ ) {
		if ( i >= native.size() || native[ i ] > 127 ||
			 idStr::ToLower( static_cast< char >( native[ i ] ) ) != idStr::ToLower( ascii[ i ] ) ) {
			return false;
		}
	}
	return i == native.size();
}

// The entries of a directory, walked with error codes; false when it cannot be read
template< typename Visit >
static bool ForEachEntry( const fs::path &directory, Visit visit ) {
	std::error_code error;
	fs::directory_iterator it( directory, fs::directory_options::skip_permission_denied, error );
	if ( error ) {
		return false;
	}
	for ( fs::directory_iterator end; it != end; it.increment( error ) ) {
		if ( error ) {
			break;
		}
		if ( !visit( *it ) ) {
			break;
		}
	}
	return true;
}

// the entry of directory whose name matches an ASCII name without case, if any
static bool FindEntryNoCase( const fs::path &directory, const char *name, bool wantDirectory, fs::path &entry ) {
	bool found = false;
	ForEachEntry( directory, [&]( const fs::directory_entry &candidate ) {
		if ( !NameIs( candidate.path().filename(), name ) ) {
			return true;
		}
		std::error_code typeError;
		const bool isDirectory = candidate.is_directory( typeError );
		const bool isFile = !isDirectory && candidate.is_regular_file( typeError );
		if ( ( wantDirectory && isDirectory ) || ( !wantDirectory && isFile ) ) {
			entry = candidate.path();
			found = true;
			return false;
		}
		return true;
	} );
	return found;
}

// The key two spellings of one directory share (a symlinked Steam root, a
// trailing slash): its canonical form where it exists, else its normalized text
static idStr PathKey( const char *path ) {
	std::error_code error;
	const fs::path canonical = fs::weakly_canonical( fs::path( path ), error );
	idStr key;
	if ( !error && NarrowPath( canonical, key ) ) {
		return NormalizePath( key );
	}
	return NormalizePath( path );
}

static void AddUnique( idStrList &paths, const char *path ) {
	const idStr normalized = NormalizePath( path );
	if ( normalized.Length() == 0 ) {
		return;
	}
	for ( int i = 0; i < paths.Num(); i++ ) {
		if ( SamePath( paths[ i ], normalized ) ) {
			return;
		}
	}
	paths.Append( normalized );
}

// as AddUnique, also against the canonical forms of the paths already listed
static void AddUniqueDirectory( idStrList &paths, idStrList &keys, const char *path ) {
	const idStr normalized = NormalizePath( path );
	if ( normalized.Length() == 0 ) {
		return;
	}
	const idStr key = PathKey( normalized );
	for ( int i = 0; i < keys.Num(); i++ ) {
		if ( SamePath( keys[ i ], key ) ) {
			return;
		}
	}
	keys.Append( key );
	paths.Append( normalized );
}

bool RetailDiscovery::HasRetailData( const char *root ) {
	if ( root == NULL || root[ 0 ] == '\0' ) {
		return false;
	}
	fs::path baseDir;
	fs::path pak;
	return FindEntryNoCase( fs::path( root ), RETAIL_MARKER_DIR, true, baseDir ) &&
		   FindEntryNoCase( baseDir, RETAIL_MARKER_PAK, false, pak );
}

void RetailDiscovery::SplitPathList( const char *list, idStrList &paths ) {
	if ( list == NULL ) {
		return;
	}
	idStr current;
	for ( const char *c = list; ; c++ ) {
#ifdef _WIN32
		const bool separator = *c == ';';	// ':' is a drive letter's
#else
		const bool separator = *c == ';' || *c == ':';
#endif
		if ( *c == '\0' || separator ) {
			AddUnique( paths, current );
			current.Clear();
			if ( *c == '\0' ) {
				break;
			}
			continue;
		}
		current.Append( *c );
	}
}

// the double-quoted tokens of one line of a Valve KeyValues (.vdf, .acf) file
static void QuotedTokens( const char *line, idStrList &tokens ) {
	tokens.Clear();
	const char *c = line;
	while ( *c != '\0' ) {
		if ( *c != '"' ) {
			c++;
			continue;
		}
		idStr token;
		c++;
		while ( *c != '\0' && *c != '"' ) {
			if ( *c == '\\' && c[ 1 ] != '\0' ) {	// \\ and \" escapes
				c++;
			}
			token.Append( *c );
			c++;
		}
		tokens.Append( token );
		if ( *c == '"' ) {
			c++;
		}
	}
}

// the key-value pairs of a KeyValues file, one pair per line, in file order
template< typename Visit >
static void ForEachKeyValue( const fs::path &file, Visit visit ) {
	idStr narrow;
	if ( !NarrowPath( file, narrow ) ) {
		return;
	}
	FILE *f = fopen( narrow.c_str(), "rb" );
	if ( f == NULL ) {
		return;
	}
	char line[ 4096 ];
	idStrList tokens;
	while ( fgets( line, sizeof( line ), f ) != NULL ) {
		QuotedTokens( line, tokens );
		if ( tokens.Num() >= 2 ) {
			visit( tokens[ 0 ], tokens[ 1 ] );
		}
	}
	fclose( f );
}

void RetailDiscovery::AppendSteamLibraries( const char *steamRoot, idStrList &libraries, idStrList &keys ) {
	if ( steamRoot == NULL || steamRoot[ 0 ] == '\0' ) {
		return;
	}
	AddUniqueDirectory( libraries, keys, steamRoot );

	fs::path steamapps;
	fs::path vdf;
	if ( !FindEntryNoCase( fs::path( steamRoot ), "steamapps", true, steamapps ) ||
		 !FindEntryNoCase( steamapps, "libraryfolders.vdf", false, vdf ) ) {
		return;
	}
	ForEachKeyValue( vdf, [&]( const idStr &key, const idStr &value ) {
		// "path" "<library>" (since 2021), or "<n>" "<library>" (older clients)
		if ( idStr::Icmp( key, "path" ) == 0 ||
			 ( idStr::IsNumeric( key ) && ( value.Find( '/' ) >= 0 || value.Find( '\\' ) >= 0 ) ) ) {
			AddUniqueDirectory( libraries, keys, value );
		}
	} );
}

void RetailDiscovery::AppendSteamInstalls( const char *library, idStrList &candidates ) {
	if ( library == NULL || library[ 0 ] == '\0' ) {
		return;
	}
	fs::path steamapps;
	if ( !FindEntryNoCase( fs::path( library ), "steamapps", true, steamapps ) ) {
		return;
	}
	// steamapps/appmanifest_<appid>.acf names each installed app ("name") and its
	// folder under steamapps/common ("installdir")
	std::vector< fs::path > manifests;
	ForEachEntry( steamapps, [&]( const fs::directory_entry &entry ) {
		const fs::path::string_type name = entry.path().filename().native();	// a copy: filename() is a temporary
		const fs::path::string_type prefix = fs::path( "appmanifest_" ).native();
		const fs::path::string_type suffix = fs::path( ".acf" ).native();
		if ( name.size() > prefix.size() + suffix.size() &&
			 name.compare( 0, prefix.size(), prefix ) == 0 &&
			 name.compare( name.size() - suffix.size(), suffix.size(), suffix ) == 0 ) {
			manifests.push_back( entry.path() );
		}
		return true;
	} );
	std::sort( manifests.begin(), manifests.end() );	// a stable order across runs
	for ( const fs::path &manifest : manifests ) {
		idStr gameName;
		idStr installDir;
		ForEachKeyValue( manifest, [&]( const idStr &key, const idStr &value ) {
			if ( idStr::Icmp( key, "name" ) == 0 && gameName.Length() == 0 ) {
				gameName = value;
			} else if ( idStr::Icmp( key, "installdir" ) == 0 && installDir.Length() == 0 ) {
				installDir = value;
			}
		} );
		if ( gameName.Find( STEAM_GAME_NAME, false ) < 0 || installDir.Length() == 0 ||
			 installDir.Find( '/' ) >= 0 || installDir.Find( '\\' ) >= 0 || installDir.Find( ".." ) >= 0 ) {
			continue;
		}
		idStr path = library;
		path.AppendPath( "steamapps/common" );
		path.AppendPath( installDir );
		AddUnique( candidates, path );
	}
}

bool RetailDiscovery::ReadStoredPath( const char *file, idStr &path ) {
	if ( file == NULL || file[ 0 ] == '\0' ) {
		return false;
	}
	FILE *f = fopen( file, "rb" );
	if ( f == NULL ) {
		return false;
	}
	char line[ 4096 ];
	const bool read = fgets( line, sizeof( line ), f ) != NULL;
	fclose( f );
	if ( !read ) {
		return false;
	}
	// a UTF-8 byte order mark (Notepad) and the line ending are not part of it
	const char *start = line;
	if ( ( unsigned char )start[ 0 ] == 0xEF && ( unsigned char )start[ 1 ] == 0xBB && ( unsigned char )start[ 2 ] == 0xBF ) {
		start += 3;
	}
	idStr value = start;
	value.StripTrailingWhiteSpace();
	path = NormalizePath( value );
	return path.Length() > 0;
}

bool RetailDiscovery::FindFirst( const idStrList &candidates, const char *source, idStr &root, idList<retailProbe_t> &probes ) {
	for ( int i = 0; i < candidates.Num(); i++ ) {
		retailProbe_t probe;
		probe.path = candidates[ i ];
		probe.source = source;
		probe.found = HasRetailData( candidates[ i ] );
		probes.Append( probe );
		if ( probe.found ) {
			root = candidates[ i ];
			return true;
		}
	}
	return false;
}

static bool DiscoverInternal( const char *savePath, idStr &root, idList<retailProbe_t> &probes ) {
	idStrList candidates;

	// 1. the environment
	RetailDiscovery::SplitPathList( getenv( "OPENETQW_BASEPATH" ), candidates );
	if ( RetailDiscovery::FindFirst( candidates, "OPENETQW_BASEPATH", root, probes ) ) {
		return true;
	}

	// 2. the stored path
	if ( savePath != NULL && savePath[ 0 ] != '\0' ) {
		idStr storedFile = savePath;
		storedFile.AppendPath( "basepath.txt" );
		idStr stored;
		candidates.Clear();
		if ( RetailDiscovery::ReadStoredPath( storedFile, stored ) ) {
			AddUnique( candidates, stored );
		}
		if ( RetailDiscovery::FindFirst( candidates, "stored path", root, probes ) ) {
			return true;
		}
	}

	// 3. next to the engine
	candidates.Clear();
	AddUnique( candidates, Sys_DefaultBasePath() );
	if ( RetailDiscovery::FindFirst( candidates, "engine directory", root, probes ) ) {
		return true;
	}

	// 4. the platform's list, then 5. the Steam installs of the game
	idStrList platformCandidates;
	idStrList steamRoots;
	Sys_AppendRetailDataCandidates( platformCandidates, steamRoots );
	candidates.Clear();
	for ( int i = 0; i < platformCandidates.Num(); i++ ) {
		AddUnique( candidates, platformCandidates[ i ] );
	}
	if ( RetailDiscovery::FindFirst( candidates, "platform list", root, probes ) ) {
		return true;
	}
	idStrList libraries;
	idStrList libraryKeys;
	for ( int i = 0; i < steamRoots.Num(); i++ ) {
		RetailDiscovery::AppendSteamLibraries( steamRoots[ i ], libraries, libraryKeys );
	}
	candidates.Clear();
	for ( int i = 0; i < libraries.Num(); i++ ) {
		RetailDiscovery::AppendSteamInstalls( libraries[ i ], candidates );
	}
	return RetailDiscovery::FindFirst( candidates, "Steam library", root, probes );
}

bool RetailDiscovery::Discover( const char *savePath, idStr &root, idList<retailProbe_t> &probes ) {
	// the search is best effort: whatever goes wrong in it, the engine starts
	// with its default base path and the retail-install check reports
	try {
		return DiscoverInternal( savePath, root, probes );
	} catch ( const std::exception & ) {
		return false;
	}
}

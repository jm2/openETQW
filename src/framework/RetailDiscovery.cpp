// SPDX-License-Identifier: GPL-3.0-or-later
// Retail data discovery (RetailDiscovery.h; ledger 068). After openQ4's
// fs_basepath auto-discovery (FileSystem.cpp FS_AutoDiscoverBasePath), with the
// ET:QW marker base/pak000.pk4 and Steam folders found by content, not by name.

#include "RetailDiscovery.h"

#include <algorithm>
#include <filesystem>
#include <system_error>
#include <vector>

namespace fs = std::filesystem;

static const char RETAIL_MARKER_DIR[] = BASE_GAMEDIR;
static const char RETAIL_MARKER_PAK[] = "pak000.pk4";

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

// the entry of directory whose name matches name without case, if any
static bool FindEntryNoCase( const fs::path &directory, const char *name, bool wantDirectory, fs::path &entry ) {
	std::error_code error;
	fs::directory_iterator it( directory, fs::directory_options::skip_permission_denied, error );
	if ( error ) {
		return false;
	}
	for ( const fs::directory_entry &candidate : it ) {
		const std::string candidateName = candidate.path().filename().string();
		if ( idStr::Icmp( candidateName.c_str(), name ) != 0 ) {
			continue;
		}
		std::error_code typeError;
		const bool isDirectory = candidate.is_directory( typeError );
		const bool isFile = candidate.is_regular_file( typeError );
		if ( ( wantDirectory && isDirectory ) || ( !wantDirectory && isFile ) ) {
			entry = candidate.path();
			return true;
		}
	}
	return false;
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

// the double-quoted tokens of one line of a Valve KeyValues (.vdf) file
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

void RetailDiscovery::AppendSteamLibraries( const char *steamRoot, idStrList &libraries ) {
	if ( steamRoot == NULL || steamRoot[ 0 ] == '\0' ) {
		return;
	}
	AddUnique( libraries, steamRoot );

	fs::path steamapps;
	fs::path vdf;
	if ( !FindEntryNoCase( fs::path( steamRoot ), "steamapps", true, steamapps ) ||
		 !FindEntryNoCase( steamapps, "libraryfolders.vdf", false, vdf ) ) {
		return;
	}
	FILE *f = fopen( vdf.string().c_str(), "rb" );
	if ( f == NULL ) {
		return;
	}
	char line[ 4096 ];
	idStrList tokens;
	while ( fgets( line, sizeof( line ), f ) != NULL ) {
		QuotedTokens( line, tokens );
		if ( tokens.Num() < 2 ) {
			continue;
		}
		// "path" "<library>" (since 2021), or "<n>" "<library>" (older clients)
		if ( idStr::Icmp( tokens[ 0 ], "path" ) == 0 ||
			 ( idStr::IsNumeric( tokens[ 0 ] ) && ( tokens[ 1 ].Find( '/' ) >= 0 || tokens[ 1 ].Find( '\\' ) >= 0 ) ) ) {
			AddUnique( libraries, tokens[ 1 ] );
		}
	}
	fclose( f );
}

void RetailDiscovery::AppendSteamCommonFolders( const char *library, idStrList &candidates ) {
	if ( library == NULL || library[ 0 ] == '\0' ) {
		return;
	}
	fs::path steamapps;
	fs::path common;
	if ( !FindEntryNoCase( fs::path( library ), "steamapps", true, steamapps ) ||
		 !FindEntryNoCase( steamapps, "common", true, common ) ) {
		return;
	}
	std::error_code error;
	fs::directory_iterator it( common, fs::directory_options::skip_permission_denied, error );
	if ( error ) {
		return;
	}
	std::vector< std::string > folders;
	for ( const fs::directory_entry &entry : it ) {
		std::error_code typeError;
		if ( entry.is_directory( typeError ) ) {
			folders.push_back( entry.path().string() );
		}
	}
	std::sort( folders.begin(), folders.end() );	// a stable order across runs
	for ( const std::string &folder : folders ) {
		AddUnique( candidates, folder.c_str() );
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

bool RetailDiscovery::Discover( const char *savePath, idStr &root, idList<retailProbe_t> &probes ) {
	idStrList candidates;

	// 1. the environment
	SplitPathList( getenv( "OPENETQW_BASEPATH" ), candidates );
	if ( FindFirst( candidates, "OPENETQW_BASEPATH", root, probes ) ) {
		return true;
	}

	// 2. the stored path
	if ( savePath != NULL && savePath[ 0 ] != '\0' ) {
		idStr storedFile = savePath;
		storedFile.AppendPath( "basepath.txt" );
		idStr stored;
		candidates.Clear();
		if ( ReadStoredPath( storedFile, stored ) ) {
			AddUnique( candidates, stored );
		}
		if ( FindFirst( candidates, "stored path", root, probes ) ) {
			return true;
		}
	}

	// 3. next to the engine
	candidates.Clear();
	AddUnique( candidates, Sys_DefaultBasePath() );
	if ( FindFirst( candidates, "engine directory", root, probes ) ) {
		return true;
	}

	// 4. the platform's table, then 5. the Steam libraries
	idStrList platformCandidates;
	idStrList steamRoots;
	Sys_AppendRetailDataCandidates( platformCandidates, steamRoots );
	candidates.Clear();
	for ( int i = 0; i < platformCandidates.Num(); i++ ) {
		AddUnique( candidates, platformCandidates[ i ] );
	}
	if ( FindFirst( candidates, "platform list", root, probes ) ) {
		return true;
	}
	idStrList libraries;	// AppendSteamLibraries drops a root or library seen before
	for ( int i = 0; i < steamRoots.Num(); i++ ) {
		AppendSteamLibraries( steamRoots[ i ], libraries );
	}
	candidates.Clear();
	for ( int i = 0; i < libraries.Num(); i++ ) {
		AppendSteamCommonFolders( libraries[ i ], candidates );
	}
	return FindFirst( candidates, "Steam library", root, probes );
}

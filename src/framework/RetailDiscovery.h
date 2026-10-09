// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RETAILDISCOVERY_H__
#define __RETAILDISCOVERY_H__

/*
===============================================================================

	Retail data discovery: where the retail ET:QW install is when fs_basepath is
	not given (ledger 068). A candidate counts when <root>/base holds pak000.pk4,
	whatever the letter case of the two names. The order, first match wins:

		1. OPENETQW_BASEPATH, one or more install roots (';' separated; ':' too
		   on POSIX)
		2. the stored path: the first line of <fs_savepath>/basepath.txt
		3. Sys_DefaultBasePath(): the executable's directory, else the current one
		4. the platform's list (Sys_AppendRetailDataCandidates): installer
		   records and common install folders
		5. the Steam installs of the game: in each library of the platform's
		   Steam roots, the folder an app manifest whose name holds "QUAKE Wars"
		   installs to

	An explicit +set fs_basepath is never second-guessed. When nothing matches,
	fs_basepath stays Sys_DefaultBasePath() and the retail-install check fails;
	the content search diagnostics list what was probed. The search never
	throws: a directory entry that cannot be named in the file system's code
	page is skipped.

===============================================================================
*/

// one probed root and whether it held the retail data
struct retailProbe_t {
	idStr	path;
	idStr	source;		// "OPENETQW_BASEPATH", "stored path", "engine directory", "platform list", "Steam library"
	bool	found;
};

namespace RetailDiscovery {
	// true when <root>/base/pak000.pk4 exists (names compared without case)
	bool	HasRetailData( const char *root );

	// the paths in an environment-style list, unquoted and trimmed
	void	SplitPathList( const char *list, idStrList &paths );

	// the library roots a Steam root lists in steamapps/libraryfolders.vdf, plus
	// the root itself; keys (the canonical forms) drop a library already listed
	// under another spelling, such as a symlinked root
	void	AppendSteamLibraries( const char *steamRoot, idStrList &libraries, idStrList &keys );

	// the game's install folders in one library: steamapps/common/<installdir>
	// of each steamapps/appmanifest_*.acf whose "name" holds "QUAKE Wars"
	void	AppendSteamInstalls( const char *library, idStrList &candidates );

	// the first line of a stored-path file, trimmed; false when there is none
	bool	ReadStoredPath( const char *file, idStr &path );

	// probe the candidates in order; true and the root at the first match
	bool	FindFirst( const idStrList &candidates, const char *source, idStr &root, idList<retailProbe_t> &probes );

	// the whole search above; savePath is fs_savepath
	bool	Discover( const char *savePath, idStr &root, idList<retailProbe_t> &probes );

	// writes root as the stored path (<savePath>/basepath.txt), creating savePath if needed
	bool	WriteStoredPath( const char *savePath, const char *root );
}

// Platform lists (src/sys/<platform>), in the order to probe:
//	candidates	install roots: installer records, common install folders
//	steamRoots	Steam installations, whose libraries are searched after them
void	Sys_AppendRetailDataCandidates( idStrList &candidates, idStrList &steamRoots );

// When the search finds nothing: the platform's way to ask the player for the install (a
// folder picker). problem is NULL on the first ask, else why the last choice was refused.
// true and the chosen folder when the player chose one, false when there is no such way or
// the player gave up. Only UI: FileSystem checks the choice for base/pak000.pk4, asks again
// if it fails, and stores an accepted one (basepath.txt). FileSystem never asks in a
// dedicated server, with fs_retailPrompt 0, or with stdin a pipe (a test harness).
bool	Sys_PromptForRetailData( idStr &root, const char *problem );

// true when stdin is a pipe or a socket (a test harness or a script drives the engine)
bool	Sys_StdinIsPipe( void );

#endif /* !__RETAILDISCOVERY_H__ */

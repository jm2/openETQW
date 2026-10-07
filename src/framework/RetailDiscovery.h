// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RETAILDISCOVERY_H__
#define __RETAILDISCOVERY_H__

/*
===============================================================================

	Retail data discovery: where the retail ET:QW install is when fs_basepath is
	not given (ledger 068). A candidate counts when <root>/base holds pak000.pk4,
	whatever the letter case of the two names. The order, first match wins:

		1. OPENETQW_BASEPATH, one or more install roots (';' or ':' separated
		   on POSIX, ';' on Windows)
		2. the stored path: the first line of <fs_savepath>/basepath.txt
		3. Sys_DefaultBasePath(): the executable's directory, else the current one
		4. the platform's table (Sys_AppendRetailDataCandidates): installer
		   records and common install folders
		5. every Steam library of the platform's Steam roots: each folder under
		   steamapps/common that holds the retail data

	An explicit +set fs_basepath is never second-guessed. When nothing matches,
	fs_basepath stays Sys_DefaultBasePath() and the retail-install check fails,
	listing what was probed.

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
	// the root itself
	void	AppendSteamLibraries( const char *steamRoot, idStrList &libraries );

	// every folder under <library>/steamapps/common, sorted by name
	void	AppendSteamCommonFolders( const char *library, idStrList &candidates );

	// the first line of a stored-path file, trimmed; false when there is none
	bool	ReadStoredPath( const char *file, idStr &path );

	// probe the candidates in order; true and the root at the first match
	bool	FindFirst( const idStrList &candidates, const char *source, idStr &root, idList<retailProbe_t> &probes );

	// the whole search above; savePath is fs_savepath
	bool	Discover( const char *savePath, idStr &root, idList<retailProbe_t> &probes );
}

// Platform tables (src/sys/<platform>), in the order to probe:
//	candidates	install roots: installer records, common install folders
//	steamRoots	Steam installations, whose libraries are searched after them
void	Sys_AppendRetailDataCandidates( idStrList &candidates, idStrList &steamRoots );

#endif /* !__RETAILDISCOVERY_H__ */

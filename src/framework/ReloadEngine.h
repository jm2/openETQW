// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __FRAMEWORK_RELOADENGINE_H__
#define __FRAMEWORK_RELOADENGINE_H__

/*
The command line reloadEngine starts the engine again with (#420): the one it started
with. With "menu" (the settings menu and the mod chooser pass it) the commands that
start or join a game are left out, so the engine comes back at the main menu. fs_game,
which the mod chooser sets just before, is passed on. The command line's own
reloadEngine, if any, is left out, or the engine would restart without end.
*/

// true for a command-line command that starts or joins a game
inline bool Sys_IsGameStartCommand( const char* command ) {
	static const char* const starts[] = {
		"connect", "spawnServer", "map", "devmap", "devmapLocal", "testmap", "playDemo", "timeDemo",
	};
	for ( size_t i = 0; i < sizeof( starts ) / sizeof( starts[ 0 ] ); i++ ) {
		if ( idStr::Icmp( command, starts[ i ] ) == 0 ) {
			return true;
		}
	}
	return false;
}

// true for a command line's own reloadEngine, which would restart without end
inline bool Sys_IsReloadEngineCommand( const char* command ) {
	return idStr::Icmp( command, "reloadEngine" ) == 0;
}

// true for a command line's "+set fs_game x" (or seta, sets, setu)
inline bool Sys_IsFsGameSet( const idStrList& line ) {
	return line.Num() >= 2 && idStr::Icmpn( line[ 0 ], "set", 3 ) == 0 && line[ 0 ].Length() <= 4 &&
		idStr::Icmp( line[ 1 ], "fs_game" ) == 0;
}

inline void Sys_ReloadEngineArgs( const idStrList& launchArgs, bool menu, const char* fsGame, idStrList& out ) {
	out.Clear();
	// split into the command line's commands: each starts at a "+". Anything before the
	// first one is left out: it is no command (Windows' WinMain passes the game's name
	// there when the command line is empty, macOS a -psn_ argument)
	idList< idStrList > lines;
	for ( int i = 0; i < launchArgs.Num(); i++ ) {
		const idStr& arg = launchArgs[ i ];
		if ( arg.Length() > 0 && arg[ 0 ] == '+' ) {
			idStrList line;
			line.Append( arg.c_str() + 1 );
			lines.Append( line );
		} else if ( lines.Num() > 0 ) {
			lines[ lines.Num() - 1 ].Append( arg );
		}
	}
	for ( int i = 0; i < lines.Num(); i++ ) {
		const idStrList& line = lines[ i ];
		if ( line.Num() == 0 ) {
			continue;
		}
		if ( menu && Sys_IsGameStartCommand( line[ 0 ] ) ) {
			continue;
		}
		if ( Sys_IsFsGameSet( line ) || Sys_IsReloadEngineCommand( line[ 0 ] ) ) {
			continue;
		}
		out.Append( "+" + line[ 0 ] );
		for ( int j = 1; j < line.Num(); j++ ) {
			out.Append( line[ j ] );
		}
	}
	if ( fsGame != NULL && fsGame[ 0 ] != '\0' ) {
		out.Append( "+set" );
		out.Append( "fs_game" );
		out.Append( fsGame );
	}
}

#endif /* !__FRAMEWORK_RELOADENGINE_H__ */

// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: ReloadEngineTest.cpp
Sys_ReloadEngineArgs (src/framework/ReloadEngine.h), the command line reloadEngine
starts the engine again with (#420): the one it started with, without the commands
that start or join a game when the menu asks for it, and with the current fs_game.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "ReloadEngine.h"

#include <cstdio>

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

static idStrList Args( const char* const* list ) {
	idStrList out;
	for ( ; *list != NULL; list++ ) {
		out.Append( *list );
	}
	return out;
}

static idStr Join( const idStrList& list ) {
	idStr out;
	for ( int i = 0; i < list.Num(); i++ ) {
		if ( i > 0 ) {
			out += " ";
		}
		out += list[ i ];
	}
	return out;
}

int main() {
	idStr::InitMemory();	// as idLib::Init: an idStr past its base buffer allocates from it
	static const char* const launch[] = {
		"+set", "fs_basepath", "/data", "+set", "fs_game", "base", "+set", "ui_name", "Player One",
		"+spawnServer", "valley", "+connect", "127.0.0.1:27733", "+exec", "autoexec.cfg", NULL,
	};
	idStrList out;

	Sys_ReloadEngineArgs( Args( launch ), true, "base", out );
	Expect( Join( out ) == "+set fs_basepath /data +set ui_name Player One +exec autoexec.cfg +set fs_game base",
		va( "menu: the game-starting commands are left out, fs_game is passed on (%s)", Join( out ).c_str() ) );
	Expect( out.Num() == 11 && out[ 5 ] == "Player One", "an argument with a space stays one argument" );

	Sys_ReloadEngineArgs( Args( launch ), false, "mymod", out );
	Expect( Join( out ) == "+set fs_basepath /data +set ui_name Player One +spawnServer valley "
		"+connect 127.0.0.1:27733 +exec autoexec.cfg +set fs_game mymod",
		va( "without menu the game-starting commands stay; the mod chooser's fs_game wins (%s)", Join( out ).c_str() ) );

	static const char* const lead[] = { "-psn_0_1234", "+devmap", "valley", "+seta", "fs_game", "x", NULL };
	Sys_ReloadEngineArgs( Args( lead ), true, "", out );
	Expect( Join( out ) == "-psn_0_1234", va( "arguments before the first + stay; no fs_game, none added (%s)", Join( out ).c_str() ) );

	Expect( Sys_IsGameStartCommand( "SPAWNSERVER" ) && Sys_IsGameStartCommand( "connect" ) &&
		Sys_IsGameStartCommand( "devmap" ) && !Sys_IsGameStartCommand( "exec" ) && !Sys_IsGameStartCommand( "set" ),
		"the game-starting commands, without case" );

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: reloadEngine starts the engine again with the command line it started with\n" );
	return 0;
}

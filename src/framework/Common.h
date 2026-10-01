/*
===========================================================================

Doom 3 GPL Source Code
Copyright (C) 1999-2011 id Software LLC, a ZeniMax Media company. 

This file is part of the Doom 3 GPL Source Code (?Doom 3 Source Code?).  

Doom 3 Source Code is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Doom 3 Source Code is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with Doom 3 Source Code.  If not, see <http://www.gnu.org/licenses/>.

In addition, the Doom 3 Source Code is also subject to certain additional terms. You should have received a copy of these additional terms immediately following the terms and conditions of the GNU General Public License which accompanied the Doom 3 Source Code.  If not, please request a copy in writing from id Software at the address below.

If you have questions concerning this license or the applicable additional terms, you may contact in writing id Software LLC, c/o ZeniMax Media Inc., Suite 120, Rockville, Maryland 20850 USA.

===========================================================================
*/

#ifndef __COMMON_H__
#define __COMMON_H__

/*
==============================================================

  Common

==============================================================
*/

typedef enum {
	EDITOR_NONE					= 0,
	EDITOR_RADIANT				= BIT(1),
	EDITOR_GUI					= BIT(2),
	EDITOR_DEBUGGER				= BIT(3),
	EDITOR_SCRIPT				= BIT(4),
	EDITOR_LIGHT				= BIT(5),
	EDITOR_SOUND				= BIT(6),
	EDITOR_DECL					= BIT(7),
	EDITOR_AF					= BIT(8),
	EDITOR_PARTICLE				= BIT(9),
	EDITOR_PDA					= BIT(10),
	EDITOR_AAS					= BIT(11),
	EDITOR_MATERIAL				= BIT(12)
} toolFlag_t;

#define STRTABLE_ID				"#str_"
#define STRTABLE_ID_LENGTH		5

extern idCVar		com_version;
extern idCVar		com_skipRenderer;
extern idCVar		com_asyncInput;
extern idCVar		com_asyncSound;
extern idCVar		com_machineSpec;
extern idCVar		com_gpuSpec;
extern idCVar		com_purgeAll;
extern idCVar		com_developer;
extern idCVar		com_allowConsole;
extern idCVar		com_speeds;
extern idCVar		com_showFPS;
extern idCVar		com_showMemoryUsage;
extern idCVar		com_showAsyncStats;
extern idCVar		com_showSoundDecoders;
extern idCVar		com_makingBuild;
extern idCVar		com_makingRC;
extern idCVar		com_useBinaryDecls;
extern idCVar		com_writeBinaryDecls;
extern idCVar		com_updateLoadSize;
extern idCVar		com_videoRam;

// game ticks run in the current host frame: idSessionLocal::Frame, idAsyncClient::RunFrame
// and idAsyncServer::RunFrame add to it; idCommonLocal::Frame resets it and reports it
// (com_logFrameTiming)
extern int			com_frameTicks;
// game->ClientUpdateView calls in the current host frame (render-only frames prepared for
// drawing): idSessionLocal::Frame, idAsyncClient::RunFrame and idAsyncServer::RunFrame add
// to it; idCommonLocal::Frame resets it and reports it (com_logFrameTiming cuv=)
extern int			com_frameUpdateViews;
// a game ran in the current host frame, so its ticks fall due as time passes: set by
// idSessionLocal::Frame (single player), idAsyncClient::RunFrame (in game) and a listen
// host's idAsyncServer::RunFrame; idCommonLocal::Frame resets it. With com_unlockFPS 0
// such a frame is drawn only when it ran a tick (M5 plan step 11, D8)
extern bool			com_frameInGame;
extern idCVar		com_unlockFPS;
extern idCVar		com_maxFPS;
extern idCVar		com_unlock_maxFPS;

extern int			time_gameFrame;			// game logic time
extern int			time_gameDraw;			// game present time
extern int			time_frontend;			// renderer frontend time
extern int			time_backend;			// renderer backend time

extern int			com_frameTime;			// time for the current frame in milliseconds
extern volatile int	com_ticNumber;			// 30 Hz fixed tics, advanced on the main thread
extern int			com_editors;			// current active editor(s)
extern bool			com_editorActive;		// true if an editor has focus

// Services the fixed-step clock from foreground-only blocking loops such as
// transition wipes. This never runs game code and must not be called by a
// worker thread.
void				Com_UpdateGameTime();

// Returns the amount of the current 30 Hz command interval that remains.  The
// session uses this to drive ETQW's unlocked-view interpolation on render-only
// frames.
int					Com_GetGameTimeLeft();

#ifdef _WIN32
const char			DMAP_MSGID[] = "DMAPOutput";
const char			DMAP_DONE[] = "DMAPDone";
extern HWND			com_hwndMsg;
extern bool			com_outputMsg;
#endif

struct MemInfo_t {
	idStr			filebase;

	int				total;
	int				assetTotals;

	// memory manager totals
	int				memoryManagerTotal;

	// subsystem totals
	int				gameSubsystemTotal;
	int				renderSubsystemTotal;

	// asset totals
	int				imageAssetsTotal;
	int				modelAssetsTotal;
	int				soundAssetsTotal;
};

#endif /* !__COMMON_H__ */

// SPDX-License-Identifier: GPL-3.0-or-later
//
// Keeps the engine's cvar and command tables from pointing into an unloaded
// game module.
//
// The idCmdSystem::ArgCompletion_* helpers are inline, so the game module has
// its own copies, and registering a static cvar hands the engine's internal
// cvar the declaring module's completion function; that includes the cvars
// the game declares again after the engine (developer, com_*, net_*, ...).
// The game registers change callbacks on cvars (objects in its own image) and
// adds console commands whose handlers live in its image.  Its Shutdown clears
// only the CVAR_GAME completions and the CMD_FL_GAME commands, and does not
// unregister every callback, so after the module is unloaded a completion
// (typing the name and a space), a cvar change or a leftover command would
// jump into unmapped code.
//
// Common captures the tables right after the module is loaded, before
// GetGameAPI registers anything, and restores them before every unload:
// each cvar gets back the completion and callbacks it had at capture, cvars
// created since get none, and commands added since are removed.  Anything the
// engine itself registers while the module is loaded is reverted as well; it
// registers nothing then (the renderer and input callbacks are installed
// before the game module loads).  The pattern follows openQ4's
// idCVarCompletionSnapshot (openQ4 commit 63756efb).

#ifndef __GAMEMODULECALLBACKS_H__
#define __GAMEMODULECALLBACKS_H__

void	CVar_CaptureModuleCallbacks( void );
int		CVar_RestoreModuleCallbacks( void );	// returns the number of cvars changed
void	Cmd_CaptureModuleCommands( void );
int		Cmd_RemoveModuleCommands( void );		// returns the number of commands removed

#endif /* !__GAMEMODULECALLBACKS_H__ */

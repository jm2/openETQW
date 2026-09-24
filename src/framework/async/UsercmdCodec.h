// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __USERCMDCODEC_H__
#define __USERCMDCODEC_H__

/*
===============================================================================

	The usercmd wire codec and duplication rule.

	Kept apart from idAsyncNetwork so the unit tests (tests/UserCmdDeltaTest.cpp,
	tests/test_usercmd_history.cpp) compile and exercise the code the engine runs
	instead of a copy of it. Needs idBitMsg and usercmd_t (idlib/precompiled.h).

===============================================================================
*/

// usercmds a server repeats for a client whose commands are late before it drops the
// attack button and halves the movement
const int MAX_USERCMD_DUPLICATION	= 25;

void	Net_WriteUserCmdDelta( idBitMsg &msg, const usercmd_t &cmd, const usercmd_t *base, int timeOffset );
void	Net_ReadUserCmdDelta( const idBitMsg &msg, usercmd_t &cmd, const usercmd_t *base );
bool	Net_DuplicateUsercmd( const usercmd_t &previousUserCmd, usercmd_t &currentUserCmd, int frame, int time );

#endif /* !__USERCMDCODEC_H__ */

// SPDX-License-Identifier: GPL-3.0-or-later
// The usercmd wire codec and duplication rule (see UsercmdCodec.h). Moved unchanged
// from AsyncNetwork.cpp so the unit tests run the engine's code.

#include "idlib/precompiled.h"
#pragma hdrstop

// usercmd_t: precompiled.h leaves out the framework headers when built for the unit tests (__IDLIB__)
#include "framework/UsercmdGen.h"
#include "UsercmdCodec.h"

/*
==================
Net_WriteUserCmdDelta
==================
*/
void Net_WriteUserCmdDelta( idBitMsg &msg, const usercmd_t &cmd, const usercmd_t *base, int timeOffset ) {
	const int gameTime = cmd.gameTime - timeOffset;

	if ( base ) {
		msg.WriteDeltaLongCounter( base->gameTime, gameTime );

		const bool movementChanged =
			base->buttons.btnValue != cmd.buttons.btnValue ||
			base->forwardmove != cmd.forwardmove ||
			base->rightmove != cmd.rightmove ||
			base->upmove != cmd.upmove;

		msg.WriteBits( movementChanged ? 1 : 0, 1 );
		if ( movementChanged ) {
			msg.WriteDelta( base->buttons.btnValue, cmd.buttons.btnValue, USERCMD_NUM_BUTTONS );
			msg.WriteDelta( base->forwardmove, cmd.forwardmove, -8 );
			msg.WriteDelta( base->rightmove, cmd.rightmove, -8 );
			msg.WriteDelta( base->upmove, cmd.upmove, -8 );
		}
		msg.WriteDelta( base->angles[0], cmd.angles[0], -16 );
		msg.WriteDelta( base->angles[1], cmd.angles[1], -16 );
		return;
	}

	msg.WriteBits( gameTime, 32 );
	msg.WriteBits( cmd.buttons.btnValue, USERCMD_NUM_BUTTONS );
	msg.WriteDelta( 0, cmd.forwardmove, -8 );
	msg.WriteDelta( 0, cmd.rightmove, -8 );
	msg.WriteDelta( 0, cmd.upmove, -8 );
	msg.WriteBits( cmd.angles[0], -16 );
	msg.WriteBits( cmd.angles[1], -16 );
}

/*
==================
Net_ReadUserCmdDelta
==================
*/
void Net_ReadUserCmdDelta( const idBitMsg &msg, usercmd_t &cmd, const usercmd_t *base ) {
	memset( &cmd, 0, sizeof( cmd ) );

	if ( base ) {
		cmd.gameTime = msg.ReadDeltaLongCounter( base->gameTime );
		if ( msg.ReadBits( 1 ) != 0 ) {
			cmd.buttons.btnValue = msg.ReadDelta( base->buttons.btnValue, USERCMD_NUM_BUTTONS );
			cmd.forwardmove = msg.ReadDelta( base->forwardmove, -8 );
			cmd.rightmove = msg.ReadDelta( base->rightmove, -8 );
			cmd.upmove = msg.ReadDelta( base->upmove, -8 );
		} else {
			cmd.buttons.btnValue = base->buttons.btnValue;
			cmd.forwardmove = base->forwardmove;
			cmd.rightmove = base->rightmove;
			cmd.upmove = base->upmove;
		}
		cmd.angles[0] = msg.ReadDelta( base->angles[0], -16 );
		cmd.angles[1] = msg.ReadDelta( base->angles[1], -16 );
		return;
	}

	cmd.gameTime = msg.ReadBits( 32 );
	cmd.buttons.btnValue = msg.ReadBits( USERCMD_NUM_BUTTONS );
	cmd.forwardmove = msg.ReadDelta( 0, -8 );
	cmd.rightmove = msg.ReadDelta( 0, -8 );
	cmd.upmove = msg.ReadDelta( 0, -8 );
	cmd.angles[0] = msg.ReadBits( -16 );
	cmd.angles[1] = msg.ReadBits( -16 );
}

/*
==================
Net_DuplicateUsercmd
==================
*/
bool Net_DuplicateUsercmd( const usercmd_t &previousUserCmd, usercmd_t &currentUserCmd, int frame, int time ) {

	if ( currentUserCmd.gameTime <= previousUserCmd.gameTime ) {

		currentUserCmd = previousUserCmd;
		currentUserCmd.gameFrame = frame;
		currentUserCmd.gameTime = time;
		currentUserCmd.duplicateCount++;

		if ( currentUserCmd.duplicateCount > MAX_USERCMD_DUPLICATION ) {
			currentUserCmd.buttons.btn.attack = false;
			if ( abs( currentUserCmd.forwardmove ) > 2 ) currentUserCmd.forwardmove >>= 1;
			if ( abs( currentUserCmd.rightmove ) > 2 ) currentUserCmd.rightmove >>= 1;
			if ( abs( currentUserCmd.upmove ) > 2 ) currentUserCmd.upmove >>= 1;
		}

		return true;
	}
	return false;
}

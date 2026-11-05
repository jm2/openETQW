// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RENDERER_EFFECTGAMECLOCK_H__
#define __RENDERER_EFFECTGAMECLOCK_H__

/*
====================
sdEffectGameClock

When a stopped, non-looping effect is over, by game time alone (#433). The game removes
its client effect on that answer (rvClientEffect::Think), and the removal is a game
event, so it must not depend on when frames are drawn. The stop time used to be the last
drawn frame's time (currentRenderView.time), a time between ticks that the host's frame
schedule decided, or unknown (-1) until the next draw.

The clock keeps the last game time the game gave the effect (AddEffectDef's start time,
then each UpdateEffectDef's time) and takes the stop time from it. The draw keeps its
own stop time, from the drawn frame, for the particle fade.
====================
*/
class sdEffectGameClock {
public:
	sdEffectGameClock() : lastGameTime( 0 ), stopTime( 0.0f ), stopped( false ) {}

	// AddEffectDef's start time
	void Start( int gameTime ) {
		lastGameTime = gameTime;
		stopTime = 0.0f;
		stopped = false;
	}

	// each UpdateEffectDef's time
	void Update( int gameTime ) {
		lastGameTime = gameTime;
	}

	// StopEffectDef: the effect stopped at the last game time it was given; timeOffset is
	// the effect's SHADERPARM_TIMEOFFSET, as for its other times
	void Stop( float timeOffset ) {
		stopped = true;
		stopTime = Max( 0.0001f, lastGameTime * 0.001f + timeOffset );
	}

	// RestartEffectDef
	void Restart() {
		stopped = false;
		stopTime = 0.0f;
	}

	bool IsStopped() const { return stopped; }
	float GetStopTime() const { return stopTime; }

	// a stopped effect is over once its last particles' lifetime (duration) has passed
	// since the stop; effectTime and duration in seconds
	bool StoppedEffectOver( float effectTime, float duration ) const {
		return stopped && stopTime > 0.0f && effectTime > stopTime + duration;
	}

private:
	int		lastGameTime;	// milliseconds
	float	stopTime;		// seconds, with the effect's time offset
	bool	stopped;
};

#endif /* !__RENDERER_EFFECTGAMECLOCK_H__ */

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

The clock keeps the newest game time the game gave the effect (AddEffectDef's start
time, then each UpdateEffectDef's time) and takes the stop time from it. It never goes
back: a network client's re-prediction rewinds gameLocal.time and hands it older ticks.
The draw takes its particle fade from the same stop time, so the fade and the removal
agree.
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

	// each UpdateEffectDef's time; an older tick (a re-predicted one) leaves it
	void Update( int gameTime ) {
		lastGameTime = Max( lastGameTime, gameTime );
	}

	// StopEffectDef: the effect stopped at the newest game time it was given; timeOffset is
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

	int GetLastGameTime() const { return lastGameTime; }

private:
	int		lastGameTime;	// milliseconds
	float	stopTime;		// seconds, with the effect's time offset
	bool	stopped;
};

/*
====================
EffectOver

idRenderWorldLocal::UpdateEffectDef's answer for a non-looping effect: whether it is
over at game time gameTime (ms), given its time offset and its template's longest
duration (s). A running effect is over after its duration; a stopped one, after its
duration from the stop. Decided by game time only (#433).
====================
*/
inline bool EffectOver( const sdEffectGameClock& clock, int gameTime, float timeOffset, float duration ) {
	if ( duration <= 0.0f ) {
		return false;
	}
	const float effectTime = gameTime * 0.001f + timeOffset;
	if ( clock.IsStopped() ) {
		return clock.StoppedEffectOver( effectTime, duration );
	}
	return effectTime > duration;
}

/*
====================
EffectDrawStopTime

the stop time the draw fades a stopped effect's particles from (rvBSEOwner::stopTime),
0 for a running effect: the game clock's, so the fade and the removal agree (#433)
====================
*/
inline float EffectDrawStopTime( const sdEffectGameClock& clock ) {
	return clock.IsStopped() ? clock.GetStopTime() : 0.0f;
}

#endif /* !__RENDERER_EFFECTGAMECLOCK_H__ */

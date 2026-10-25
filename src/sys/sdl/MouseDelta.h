// SPDX-License-Identifier: GPL-3.0-or-later
// Adapted from openQ4: jm2/openQ4 b37b1456f3e400fd4b523090402a6dd1fae2af1b,
// src/sys/sdl3/sdl3_backend.cpp, SDL3_ConsumeMouseDelta (without idMath).
#ifndef __SYS_SDL_MOUSEDELTA_H__
#define __SYS_SDL_MOUSEDELTA_H__

#include <cmath>

/*
SDL3 reports mouse motion as floats, and on high-DPI displays (a Retina Mac above all)
slow movement arrives as many sub-unit events. SE_MOUSE carries whole units, so each
event's delta is added to the fraction left over from the previous ones, the whole part
is sent and the rest is kept. Truncating each event on its own lost slow motion entirely.
A non-finite delta drops the remainder; the sum is clamped so the int conversion is defined.
*/
static const float SYS_MAX_MOUSE_DELTA_PER_EVENT = 32767.0f;

inline int Sys_ConsumeMouseDelta( float delta, float& remainder ) {
	if ( !std::isfinite( delta ) || !std::isfinite( remainder ) ) {
		remainder = 0.0f;
		return 0;
	}
	float accumulated = delta + remainder;
	if ( !std::isfinite( accumulated ) ) {
		remainder = 0.0f;
		return 0;
	}
	if ( accumulated > SYS_MAX_MOUSE_DELTA_PER_EVENT ) {
		accumulated = SYS_MAX_MOUSE_DELTA_PER_EVENT;
	} else if ( accumulated < -SYS_MAX_MOUSE_DELTA_PER_EVENT ) {
		accumulated = -SYS_MAX_MOUSE_DELTA_PER_EVENT;
	}
	const int whole = static_cast< int >( accumulated );
	remainder = accumulated - static_cast< float >( whole );
	return whole;
}

#endif /* !__SYS_SDL_MOUSEDELTA_H__ */

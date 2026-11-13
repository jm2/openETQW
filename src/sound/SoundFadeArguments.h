// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SOUND_SOUNDFADEARGUMENTS_H__
#define __SOUND_SOUNDFADEARGUMENTS_H__

#include <cmath>

/*
====================
Snd_FadeArgumentsFinite

Whether idSoundWorld::FadeSoundClasses or idSoundEmitter::FadeSound can take a fade
to `to` dB over `over` seconds (#458). An infinite target makes idSoundFade::
FadeDbAt44kHz compute inf - inf, a NaN volume (the kind that crashed dB2Scale, #453),
and a non-finite length overflows the conversion to samples. Both come from game code,
GUI scripts and the soundFadeClass command, so they are checked where they come in.
====================
*/
inline bool Snd_FadeArgumentsFinite( float to, float over ) {
	return std::isfinite( to ) && std::isfinite( over );
}

#endif /* !__SOUND_SOUNDFADEARGUMENTS_H__ */

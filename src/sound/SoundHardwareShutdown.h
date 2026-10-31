// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SOUNDHARDWARESHUTDOWN_H__
#define __SOUNDHARDWARESHUTDOWN_H__

#include "../idlib/threading/Lock.h"

/*
====================
Snd_ShutdownHardwareLocked

Frees the sound system's audio hardware under the sound system's lock. On Windows the
sound thread (win_soundthread.cpp) holds that lock around every AsyncUpdate, which
writes to the hardware's stream and mix buffer; freeing them without the lock could
free them while that write is still using them (#374). A caller that already holds
the lock (idCommonLocal's shutdown) calls ShutdownHW directly: the lock is not
recursive on POSIX.
====================
*/
template< class soundSystem_t >
inline bool Snd_ShutdownHardwareLocked( soundSystem_t& sound ) {
	sdScopedLock< true > soundLock( sound.GetLock() );
	return sound.ShutdownHW();
}

#endif // !__SOUNDHARDWARESHUTDOWN_H__

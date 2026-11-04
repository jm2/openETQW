// SPDX-License-Identifier: GPL-3.0-or-later
//
// The client's sound thread on Linux and macOS (#436): every USERCMD_MSEC it runs
// soundSystem->AsyncUpdate under the sound system's lock, which mixes the playing
// sound world into the audio device, as win_soundthread.cpp does on Windows.
// Sys_Init starts it and Sys_Shutdown stops it (posix_main.cpp), as on Windows.

#include "../../idlib/precompiled.h"
#pragma hdrstop

#include "posix_public.h"
#include "../../framework/UsercmdGen.h"
#include "../../sound/SoundSystem.h"
#include "../../idlib/threading/Lock.h"
#include "../../idlib/threading/ThreadProcess.h"
#include "../../idlib/threading/Thread.h"

namespace {

class sdPosixSoundThread : public sdThreadProcess {
public:
	sdPosixSoundThread() : thread( NULL ) {}

	// not Start/Stop: sdThreadProcess declares those virtual, and sdThread::Stop calls
	// the process's Stop, so an override named Stop recursed until it crashed (#439)
	void Launch() {
		if ( thread != NULL ) {
			return;
		}
		// normal priority: POSIX threads here can only lower theirs (SysThread_posix.cpp)
		thread = new sdThread( this, THREAD_NORMAL, 0x100000 );
		thread->SetName( "Sound" );
		if ( !thread->Start( NULL, 0 ) ) {
			common->Error( "Posix_StartSoundThread: failed to start the sound thread" );
		}
	}

	void Shutdown() {
		if ( thread != NULL ) {
			thread->Stop();
			thread->Join();
			thread->Destroy();
			thread = NULL;
		}
	}

	virtual unsigned int Run( void* ) {
		int next = Sys_Milliseconds();
		while ( !Terminating() ) {
			// one update every USERCMD_MSEC, as Windows' waitable timer; a thread that
			// fell more than a tick behind starts counting again from now
			next += USERCMD_MSEC;
			const int now = Sys_Milliseconds();
			if ( next > now ) {
				Sys_Sleep( next - now );
			} else if ( now - next > USERCMD_MSEC ) {
				next = now;
			}
			if ( common == NULL || !common->IsInitialized() || soundSystem == NULL ) {
				continue;
			}

			// the same lock guards the hardware's shutdown (Snd_ShutdownHardwareLocked)
			sdScopedLock< true > soundLock( soundSystem->GetLock() );
			soundSystem->AsyncUpdate( Sys_Milliseconds() );
		}
		return 0;
	}

private:
	sdThread*	thread;
};

sdPosixSoundThread soundThread;

} // namespace

void Posix_StartSoundThread( void ) {
	soundThread.Launch();
}

void Posix_StopSoundThread( void ) {
	soundThread.Shutdown();
}

// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: SoundHardwareShutdownTest.cpp
Snd_ShutdownHardwareLocked (src/sound/SoundHardwareShutdown.h), which frees the
audio hardware under the sound system's lock (#374). Windows' sound thread holds
that lock around AsyncUpdate, which writes to the hardware; freeing it without the
lock (net_serverDedicated 1 in idAsyncNetwork::SpawnServer) could free it mid-write.

A writer thread takes the lock and stays "inside the write" until told to leave. A
second thread then shuts the hardware down. It must not free the hardware until the
writer has released the lock.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "../src/sound/SoundHardwareShutdown.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <thread>

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	std::printf( "  %s %s\n", ok ? "ok  " : "FAIL", what );
	if ( !ok ) {
		failures++;
	}
}

// The parts of idSoundSystem the helper uses: the lock and the hardware shutdown
class FakeSoundSystem {
public:
	sdLock& GetLock() { return lock; }
	bool ShutdownHW() {
		if ( writing.load() ) {
			freedDuringWrite.store( true );
		}
		freed.store( true );
		return true;
	}

	sdLock				lock;
	std::atomic< bool >	writing{ false };
	std::atomic< bool >	freed{ false };
	std::atomic< bool >	freedDuringWrite{ false };
};

static bool WaitFor( const std::atomic< bool >& flag, int milliseconds ) {
	for ( int i = 0; i < milliseconds && !flag.load(); i++ ) {
		std::this_thread::sleep_for( std::chrono::milliseconds( 1 ) );
	}
	return flag.load();
}

int main() {
	FakeSoundSystem sound;
	std::atomic< bool > leave{ false };

	// the sound thread: AsyncUpdate under the lock, still inside Write
	std::thread writer( [&]() {
		sdScopedLock< true > soundLock( sound.GetLock() );
		sound.writing.store( true );
		WaitFor( leave, 5000 );
		sound.writing.store( false );
	} );
	Expect( WaitFor( sound.writing, 5000 ), "the writer holds the lock, inside the write" );

	// the main thread: net_serverDedicated 1 shuts the hardware down
	std::atomic< bool > started{ false };
	std::atomic< bool > returned{ false };
	std::thread shutdown( [&]() {
		started.store( true );
		Snd_ShutdownHardwareLocked( sound );
		returned.store( true );
	} );

	// give the shutdown thread, once it runs, time to get past the lock if nothing stops it
	Expect( WaitFor( started, 5000 ), "the shutdown thread is running" );
	std::this_thread::sleep_for( std::chrono::milliseconds( 300 ) );
	Expect( !sound.freed.load(), "the hardware is not freed while the writer is inside the write (300 ms)" );
	Expect( !returned.load(), "the shutdown waits for the lock" );

	leave.store( true );
	writer.join();
	shutdown.join();
	Expect( sound.freed.load(), "the hardware is freed once the writer releases the lock" );
	Expect( !sound.freedDuringWrite.load(), "it was never freed during a write" );

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: the audio hardware is freed only under the sound lock\n" );
	return 0;
}

// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "framework/precompiled.h"
#include <SDL3/SDL.h>
#include "sound/AudioHardware.h"

namespace {

class idAudioHardwareSDL3 : public idAudioHardware {
public:
	idAudioHardwareSDL3() :
		audioStream( NULL ),
		numSpeakers( 2 ),
		sampleRate( 44100 ),
		mixBuffer( NULL ),
		mixBufferSize( 0 ) {
	}

	virtual ~idAudioHardwareSDL3() {
		if ( audioStream != NULL ) {
			SDL_DestroyAudioStream( audioStream );
			audioStream = NULL;
		}
		if ( mixBuffer != NULL ) {
			delete[] mixBuffer;
			mixBuffer = NULL;
		}
	}

	virtual bool Initialize() override {
		if ( !SDL_InitSubSystem( SDL_INIT_AUDIO ) ) {
			common->Warning( "SDL3: failed to initialize audio subsystem: %s", SDL_GetError() );
			return false;
		}

		numSpeakers = 2;
		sampleRate = 44100;

		SDL_AudioSpec spec;
		memset( &spec, 0, sizeof( spec ) );
		spec.format = SDL_AUDIO_S16;
		spec.channels = numSpeakers;
		spec.freq = sampleRate;

		audioStream = SDL_OpenAudioDeviceStream(
			SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK,
			&spec,
			NULL,
			NULL
		);
		if ( audioStream == NULL ) {
			common->Warning( "SDL3: failed to open audio stream: %s", SDL_GetError() );
			return false;
		}

		mixBufferSize = MIXBUFFER_SAMPLES * numSpeakers * sizeof( short );
		mixBuffer = new short[ MIXBUFFER_SAMPLES * numSpeakers ];
		memset( mixBuffer, 0, mixBufferSize );

		if ( !SDL_ResumeAudioStreamDevice( audioStream ) ) {
			common->Warning( "SDL3: failed to resume audio device: %s", SDL_GetError() );
			return false;
		}

		common->Printf( "SDL3 Audio initialized: %d Hz, %d channels, 16-bit PCM\n", sampleRate, numSpeakers );
		return true;
	}

	virtual bool Lock( void **pDSLockedBuffer, ulong *dwDSLockedBufferSize ) override {
		(void)pDSLockedBuffer;
		(void)dwDSLockedBufferSize;
		return false;
	}

	virtual bool Unlock( void *pDSLockedBuffer, dword dwDSLockedBufferSize ) override {
		(void)pDSLockedBuffer;
		(void)dwDSLockedBufferSize;
		return false;
	}

	virtual bool GetCurrentPosition( ulong *pdwCurrentWriteCursor ) override {
		(void)pdwCurrentWriteCursor;
		return false;
	}

	virtual bool Flush( void ) override {
		return true;
	}

	// The caller writes on its own schedule, not when the device needs data: Windows' sound
	// thread writes one mix buffer every USERCMD_MSEC, about three times what 44.1 kHz plays,
	// and a device that has gone away (a disconnected remote session) plays nothing. SDL
	// keeps all that is put, so the queue grew by 18-28 MB a minute (#367). Only queue
	// while less than MAX_QUEUED_MIX_BUFFERS are waiting. Today the buffer is always
	// silence (the bootstrap sound system mixes nothing). A real mixer should check this
	// gate before it mixes, or the buffers dropped here would be lost audio.
	virtual void Write( bool flushing ) override {
		(void)flushing;
		if ( audioStream == NULL || mixBuffer == NULL ) {
			return;
		}
		const int queued = SDL_GetAudioStreamQueued( audioStream );
		if ( queued < 0 || queued >= MAX_QUEUED_MIX_BUFFERS * mixBufferSize ) {
			return;
		}
		SDL_PutAudioStreamData( audioStream, mixBuffer, mixBufferSize );
	}

	virtual int GetNumberOfSpeakers( void ) override {
		return numSpeakers;
	}

	virtual int GetMixBufferSize( void ) override {
		return mixBufferSize;
	}

	virtual short* GetMixBuffer( void ) override {
		return mixBuffer;
	}

private:
	static const int	MAX_QUEUED_MIX_BUFFERS = 2;

	SDL_AudioStream*	audioStream;
	int					numSpeakers;
	int					sampleRate;
	short*				mixBuffer;
	int					mixBufferSize;
};

} // namespace

idAudioHardware *idAudioHardware::Alloc() {
	return new idAudioHardwareSDL3();
}

idAudioHardware::~idAudioHardware() {
}

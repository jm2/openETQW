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

	virtual void Write( bool flushing ) override {
		(void)flushing;
		if ( audioStream != NULL && mixBuffer != NULL ) {
			SDL_PutAudioStreamData( audioStream, mixBuffer, mixBufferSize );
		}
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

// SPDX-License-Identifier: GPL-3.0-or-later
//
// The OpenAL entry points idal.h routes al* calls through (precompiled.h), left NULL:
// the sound mixer writes through idAudioHardware and never uses OpenAL (#436;
// idSoundSystemLocal::useOpenAL stays false). src/openal/idal.cpp, which loads them
// from a DLL on Windows, isn't built.

#include "../idlib/precompiled.h"
#pragma hdrstop

ALenum ( ALAPIENTRY * idalGetError )( ALvoid ) = NULL;
ALvoid ( ALAPIENTRY * idalGenBuffers )( ALsizei, ALuint * ) = NULL;
ALboolean ( ALAPIENTRY * idalIsSource )( ALuint ) = NULL;
ALvoid ( ALAPIENTRY * idalSourceStop )( ALuint ) = NULL;
ALvoid ( ALAPIENTRY * idalGetSourcei )( ALuint, ALenum, ALint * ) = NULL;
ALint ( ALAPIENTRY * idalGetInteger )( ALenum ) = NULL;
ALCvoid ( ALAPIENTRY * idalcSuspendContext )( ALCcontext * ) = NULL;
ALCboolean ( ALAPIENTRY * idalcMakeContextCurrent )( ALCcontext * ) = NULL;
ALCvoid ( ALAPIENTRY * idalcProcessContext )( ALCcontext * ) = NULL;
ALCvoid ( ALAPIENTRY * idalcDestroyContext )( ALCcontext * ) = NULL;
ALCubyte * ( ALAPIENTRY * idalcGetString )( ALCdevice *, ALCenum ) = NULL;
ALvoid ( ALAPIENTRY * idalBufferData )( ALuint, ALenum, ALvoid *, ALsizei, ALsizei ) = NULL;
ALvoid ( ALAPIENTRY * idalDeleteBuffers )( ALsizei, ALuint * ) = NULL;
ALboolean ( ALAPIENTRY * idalIsExtensionPresent )( ALubyte * ) = NULL;
ALvoid ( ALAPIENTRY * idalDeleteSources )( ALsizei, ALuint * ) = NULL;
ALenum ( ALAPIENTRY * idalGetEnumValue )( ALubyte * ) = NULL;
ALvoid * ( ALAPIENTRY * idalGetProcAddress )( ALubyte * ) = NULL;
ALCcontext * ( ALAPIENTRY * idalcCreateContext )( ALCdevice *, ALCint * ) = NULL;
ALCdevice * ( ALAPIENTRY * idalcOpenDevice )( ALubyte * ) = NULL;
ALvoid ( ALAPIENTRY * idalListenerfv )( ALenum, ALfloat* ) = NULL;
ALvoid ( ALAPIENTRY * idalSourceQueueBuffers )( ALuint, ALsizei, ALuint * ) = NULL;
ALvoid ( ALAPIENTRY * idalSourcei )( ALuint, ALenum, ALint ) = NULL;
ALvoid ( ALAPIENTRY * idalListenerf )( ALenum, ALfloat ) = NULL;
ALCvoid ( ALAPIENTRY * idalcCloseDevice )( ALCdevice * ) = NULL;
ALboolean ( ALAPIENTRY * idalIsBuffer )( ALuint ) = NULL;
ALvoid ( ALAPIENTRY * idalSource3f )( ALuint, ALenum, ALfloat, ALfloat, ALfloat ) = NULL;
ALvoid ( ALAPIENTRY * idalGenSources )( ALsizei, ALuint * ) = NULL;
ALvoid ( ALAPIENTRY * idalSourcef )( ALuint, ALenum, ALfloat ) = NULL;
ALvoid ( ALAPIENTRY * idalSourceUnqueueBuffers )( ALuint, ALsizei, ALuint * ) = NULL;
ALvoid ( ALAPIENTRY * idalSourcePlay )( ALuint ) = NULL;

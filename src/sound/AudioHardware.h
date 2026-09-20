// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#ifndef __AUDIO_HARDWARE_H__
#define __AUDIO_HARDWARE_H__

class idAudioHardware {
public:
	static idAudioHardware *Alloc();

	virtual ~idAudioHardware();

	virtual bool Initialize() = 0;
	virtual bool Lock( void **pDSLockedBuffer, ulong *dwDSLockedBufferSize ) = 0;
	virtual bool Unlock( void *pDSLockedBuffer, dword dwDSLockedBufferSize ) = 0;
	virtual bool GetCurrentPosition( ulong *pdwCurrentWriteCursor ) = 0;
	virtual bool Flush() = 0;
	virtual void Write( bool flushing ) = 0;
	virtual int GetNumberOfSpeakers() = 0;
	virtual int GetMixBufferSize() = 0;
	virtual short* GetMixBuffer() = 0;
};

#endif

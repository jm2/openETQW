// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SOUNDBINK_H__
#define __SOUNDBINK_H__

#include "../renderer/Cinematic.h"

class idSoundShader;

/*
Bink cinematics that sound shaders name: the GUI movies (#190). An emitter whose
shader names a .bik sample plays its frames through ImageForTime (#436).
*/

// a cinData_t with no image
cinData_t Sound_EmptyCinematicData();

// the .bik sample the shader names, if any
bool Sound_FindBinkPath( const idSoundShader* shader, idStr& samplePath );

class sdSoundBinkMovie {
public:
					sdSoundBinkMovie();
					~sdSoundBinkMovie();

	bool			Open( const char* samplePath );
	void			Close();
	bool			IsPlaying() const;
	cinData_t		ImageForTime();

private:
	bool			DecodeFrame();

	void*			bink;
	byte*			videoBuffer;
	int				width;
	int				height;
	int				frameCount;
	int				framesDecoded;
	int				frameMsec;
	int				nextFrameTime;
	bool			playing;
	bool			frameReady;
};

#endif /* !__SOUNDBINK_H__ */

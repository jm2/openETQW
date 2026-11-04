// Copyright (C) 2007 Id Software, Inc.
//
// Bink cinematics that sound shaders name (GUI movies, #190). They were played by the
// compile-first sound placeholder; the mixer's emitters play them now (#436).

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "SoundShader.h"
#include "SoundBink.h"

// SoundShader.cpp: the Bink sample a shader's parse found (#190)
bool SoundShader_GetBinkSample( const idSoundShader* shader, idStr& samplePath );

cinData_t Sound_EmptyCinematicData() {
	cinData_t data;
	memset( &data, 0, sizeof( data ) );
	return data;
}

namespace {

struct binkHeaderBootstrap_t {
	unsigned int width;
	unsigned int height;
	unsigned int frames;
	unsigned int frameRate;
	unsigned int frameRateDiv;
};

typedef void* ( *open_t )( const char*, unsigned int );
typedef void ( *close_t )( void* );
typedef int ( *doFrame_t )( void* );
typedef int ( *copyToBuffer_t )( void*, void*, int, unsigned int, unsigned int, unsigned int, unsigned int );
typedef void ( *nextFrame_t )( void* );
typedef const char* ( *getError_t )( void );

struct binkApiBootstrap_t {
	binkApiBootstrap_t() :
		module( NULL ),
		open( NULL ),
		close( NULL ),
		doFrame( NULL ),
		copyToBuffer( NULL ),
		nextFrame( NULL ),
		getError( NULL ),
		attemptedLoad( false ) {
	}

	bool Load() {
		if ( attemptedLoad ) {
			return module != NULL;
		}
		attemptedLoad = true;
#if !defined( _WIN32 )
		return false;
#else
		char executablePath[ MAX_OSPATH ];
		const DWORD pathLength = GetModuleFileNameA( NULL, executablePath, sizeof( executablePath ) );
		if ( pathLength > 0 && pathLength < sizeof( executablePath ) ) {
			for ( int i = static_cast< int >( pathLength ) - 1; i >= 0; i-- ) {
				if ( executablePath[ i ] == '\\' || executablePath[ i ] == '/' ) {
					executablePath[ i + 1 ] = '\0';
					break;
				}
			}
			idStr dllPath = executablePath;
			dllPath += "binkw32.dll";
			module = LoadLibraryA( dllPath.c_str() );
		}
		if ( module == NULL ) {
			module = LoadLibraryA( "binkw32.dll" );
		}
		if ( module == NULL ) {
			common->Warning( "Bink playback unavailable: could not load binkw32.dll" );
			return false;
		}

		open = reinterpret_cast< open_t >( GetProcAddress( module, "_BinkOpen@8" ) );
		close = reinterpret_cast< close_t >( GetProcAddress( module, "_BinkClose@4" ) );
		doFrame = reinterpret_cast< doFrame_t >( GetProcAddress( module, "_BinkDoFrame@4" ) );
		copyToBuffer = reinterpret_cast< copyToBuffer_t >( GetProcAddress( module, "_BinkCopyToBuffer@28" ) );
		nextFrame = reinterpret_cast< nextFrame_t >( GetProcAddress( module, "_BinkNextFrame@4" ) );
		getError = reinterpret_cast< getError_t >( GetProcAddress( module, "_BinkGetError@0" ) );
		if ( open == NULL || close == NULL || doFrame == NULL || copyToBuffer == NULL || nextFrame == NULL ) {
			common->Warning( "Bink playback unavailable: binkw32.dll has an incompatible API" );
			FreeLibrary( module );
			module = NULL;
			return false;
		}
		return true;
#endif
	}

#if defined( _WIN32 )
	HMODULE module;
#else
	void* module;
#endif
	open_t open;
	close_t close;
	doFrame_t doFrame;
	copyToBuffer_t copyToBuffer;
	nextFrame_t nextFrame;
	getError_t getError;
	bool attemptedLoad;
};

binkApiBootstrap_t binkApi;

} // namespace

bool Sound_FindBinkPath( const idSoundShader* shader, idStr& samplePath ) {
	samplePath.Clear();
	if ( shader == NULL ) {
		return false;
	}
	// a shader from a binary decl has no text: the parsed sample (#190)
	if ( shader->GetTextLength() <= 0 ) {
		return SoundShader_GetBinkSample( shader, samplePath );
	}

	const int textLength = shader->GetTextLength();
	char* text = static_cast< char* >( Mem_Alloc( textLength + 1 ) );
	shader->GetText( text );
	idParser src;
	src.SetFlags( LEXFL_NOSTRINGCONCAT | LEXFL_ALLOWPATHNAMES );
	src.LoadMemory( text, textLength, shader->GetName() );
	idToken token;
	while ( src.ReadToken( &token ) ) {
		if ( token.Find( ".bik", false ) != -1 ) {
			samplePath = token;
			break;
		}
	}
	Mem_Free( text );
	return !samplePath.IsEmpty();
}

static bool ResolveBinkOSPath( const char* samplePath, idStr& osPath ) {
	osPath.Clear();
	if ( samplePath == NULL || samplePath[ 0 ] == '\0' ) {
		return false;
	}

#if !defined( _WIN32 )
	return false;
#else
	char executablePath[ MAX_OSPATH ];
	const DWORD pathLength = GetModuleFileNameA( NULL, executablePath, sizeof( executablePath ) );
	if ( pathLength == 0 || pathLength >= sizeof( executablePath ) ) {
		return false;
	}
	for ( int i = static_cast< int >( pathLength ) - 1; i >= 0; i-- ) {
		if ( executablePath[ i ] == '\\' || executablePath[ i ] == '/' ) {
			executablePath[ i + 1 ] = '\0';
			break;
		}
	}

	idStr normalized = samplePath;
	normalized.BackSlashesToSlashes();
	osPath = executablePath;
	osPath += "base/";
	osPath += normalized;
	if ( GetFileAttributesA( osPath.c_str() ) != INVALID_FILE_ATTRIBUTES ) {
		return true;
	}

	// SDK development layouts can put loose assets below runtime/base.
	osPath = executablePath;
	osPath += "runtime/base/";
	osPath += normalized;
	return GetFileAttributesA( osPath.c_str() ) != INVALID_FILE_ATTRIBUTES;
#endif
}

sdSoundBinkMovie::sdSoundBinkMovie() :
	bink( NULL ),
	videoBuffer( NULL ),
	width( 0 ),
	height( 0 ),
	frameCount( 0 ),
	framesDecoded( 0 ),
	frameMsec( 33 ),
	nextFrameTime( 0 ),
	playing( false ),
	frameReady( false ) {
}

sdSoundBinkMovie::~sdSoundBinkMovie() {
	Close();
}

bool sdSoundBinkMovie::Open( const char* samplePath ) {
	Close();
	idStr osPath;
	if ( !ResolveBinkOSPath( samplePath, osPath ) ) {
		common->Warning( "Bink cinematic '%s' was not found below the game base directory", samplePath );
		return false;
	}
	if ( !binkApi.Load() ) {
		return false;
	}

	// 0x4000 is the flag used by the retail sdBinkFile::Open path.
	bink = binkApi.open( osPath.c_str(), 0x4000 );
	if ( bink == NULL ) {
		const char* error = binkApi.getError != NULL ? binkApi.getError() : "unknown error";
		common->Warning( "BinkOpen failed for '%s': %s", osPath.c_str(), error != NULL ? error : "unknown error" );
		return false;
	}

	const binkHeaderBootstrap_t* header = static_cast< const binkHeaderBootstrap_t* >( bink );
	width = static_cast< int >( header->width );
	height = static_cast< int >( header->height );
	frameCount = static_cast< int >( header->frames );
	if ( width <= 0 || height <= 0 || width > 8192 || height > 8192 || frameCount <= 0 ) {
		common->Warning( "Bink cinematic '%s' has invalid dimensions or frame count", osPath.c_str() );
		Close();
		return false;
	}
	if ( header->frameRate > 0 && header->frameRateDiv > 0 ) {
		frameMsec = Max( 1, idMath::Ftoi( 1000.0f * header->frameRateDiv / header->frameRate ) );
	}

	const int lumaSize = width * height;
	const int chromaSize = ( width >> 1 ) * ( height >> 1 );
	videoBuffer = static_cast< byte* >( Mem_Alloc( lumaSize + chromaSize * 2 ) );
	memset( videoBuffer, 0, lumaSize + chromaSize * 2 );
	playing = true;
	nextFrameTime = Sys_Milliseconds();
	if ( !DecodeFrame() ) {
		Close();
		return false;
	}
	common->Printf( "Bink cinematic opened: %s (%dx%d, %d frames)\n", samplePath, width, height, frameCount );
	return true;
}

void sdSoundBinkMovie::Close() {
	if ( bink != NULL && binkApi.close != NULL ) {
		binkApi.close( bink );
	}
	bink = NULL;
	if ( videoBuffer != NULL ) {
		Mem_Free( videoBuffer );
	}
	videoBuffer = NULL;
	width = height = frameCount = framesDecoded = 0;
	playing = false;
	frameReady = false;
}

bool sdSoundBinkMovie::IsPlaying() const {
	return playing;
}

cinData_t sdSoundBinkMovie::ImageForTime() {
	if ( !playing || bink == NULL || videoBuffer == NULL ) {
		return Sound_EmptyCinematicData();
	}
	const int now = Sys_Milliseconds();
	if ( frameReady && now >= nextFrameTime ) {
		if ( framesDecoded >= frameCount ) {
			playing = false;
			return Sound_EmptyCinematicData();
		}
		if ( !DecodeFrame() ) {
			playing = false;
			return Sound_EmptyCinematicData();
		}
	}

	cinData_t data = Sound_EmptyCinematicData();
	data.imageWidth = width;
	data.imageHeight = height;
	const int lumaSize = width * height;
	const int chromaSize = ( width >> 1 ) * ( height >> 1 );
	data.image[ 0 ] = videoBuffer;
	// BINKSURFACEYV12 stores Y, V, U. cinData_t is Y, U, V.
	data.image[ 1 ] = videoBuffer + lumaSize + chromaSize;
	data.image[ 2 ] = videoBuffer + lumaSize;
	return data;
}

bool sdSoundBinkMovie::DecodeFrame() {
	binkApi.doFrame( bink );
	const unsigned int BINK_SURFACE_YV12 = 15;
	const unsigned int BINK_COPY_ALL = 0x80000000u;
	binkApi.copyToBuffer( bink, videoBuffer, width, height, 0, 0, BINK_COPY_ALL | BINK_SURFACE_YV12 );
	binkApi.nextFrame( bink );
	framesDecoded++;
	frameReady = true;
	nextFrameTime = Sys_Milliseconds() + frameMsec;
	return true;
}


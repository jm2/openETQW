// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include "idlib/precompiled.h"
#pragma hdrstop

#include "../../renderer/RenderSystem.h"
#include "../../renderer/RenderWorld.h"
#include "../../renderer/ModelManager.h"
#include "../../renderer/DeviceContext.h"
#include "../../sound/SoundSystem.h"
#include "../../sound/SoundWorld.h"
#include "sys_render.h"
#include "../../renderer/renderbindingmanager.h"
#include "../../renderer/renderbindings.h"
#include "../../renderer/megatexture/megatexture.h"
#include "../../renderer/Image.h"
#include "../../renderer/ImageParse.h"
#include <BSEInterface.h>

extern glconfig_t glConfig;

namespace {

/*
==============================================================
idRenderWorldHeadless
==============================================================
*/
class idRenderWorldHeadless : public idRenderWorld {
public:
	virtual ~idRenderWorldHeadless() {}

	virtual bool InitFromMap( const char * ) { return true; }
	virtual void LinkCullSectorsToArea( int ) {}

	virtual qhandle_t AddEntityDef( const renderEntity_t * ) { return 0; }
	virtual void UpdateEntityDef( qhandle_t, const renderEntity_t * ) {}
	virtual void FreeEntityDef( qhandle_t ) {}
	virtual const renderEntity_t *GetRenderEntity( qhandle_t ) const { return NULL; }
	virtual renderEntity_t *GetRenderEntity( qhandle_t ) { return NULL; }

	virtual qhandle_t AddLightDef( const renderLight_t * ) { return 0; }
	virtual void UpdateLightDef( qhandle_t, const renderLight_t * ) {}
	virtual void FreeLightDef( qhandle_t ) {}
	virtual const renderLight_t *GetRenderLight( qhandle_t ) const { return NULL; }

	virtual qhandle_t AddEffectDef( const renderEffect_t *, int ) { return 0; }
	virtual bool UpdateEffectDef( qhandle_t, const renderEffect_t *, int ) { return false; }
	virtual void StopEffectDef( qhandle_t ) {}
	virtual void RestartEffectDef( qhandle_t ) {}
	virtual void FreeEffectDef( qhandle_t ) {}
	virtual void FreeStoppedEffectDefs( void ) {}

	virtual qhandle_t AddOcclusionTestDef( const occlusionTest_t * ) { return 0; }
	virtual void UpdateOcclusionTestDef( qhandle_t, const occlusionTest_t * ) {}
	virtual void UpdateOcclusionTestDefViewID( qhandle_t, int ) {}
	virtual bool IsVisibleOcclusionTestDef( qhandle_t ) { return false; }
	virtual void FreeOcclusionTestDef( qhandle_t ) {}
	virtual int CountVisibleOcclusionTestDef( qhandle_t ) { return 0; }

	virtual bool IsVisibleEntity( int, int ) { return false; }
	virtual void UpdateOcclusionTests( void ) {}

	virtual void GenerateAllInteractions() {}
	virtual idRenderModel* GetEntityHandleDynamicModel( qhandle_t ) { return NULL; }

	virtual idRenderModel* CreateDecalModel() { return NULL; }
	virtual void AddToProjectedDecal( const idFixedWinding&, const idVec3 &, const bool, const idVec4&, idRenderModel*, int, const idMaterial** = NULL, const int = 1 ) {}
	virtual void ResetDecalModel( idRenderModel* ) {}
	virtual void FinishDecal( idRenderModel* ) {}

	virtual void ProjectDecalOntoWorld( const idFixedWinding &, const idVec3 &, const bool, const float, const idMaterial *, const int, const int ) {}
	virtual void ProjectDecal( qhandle_t, const idFixedWinding &, const idVec3 &, const bool, const float, const idMaterial *, const int, const int ) {}
	virtual void ProjectOverlay( qhandle_t, const idPlane[2], const idMaterial * ) {}
	virtual void RemoveDecals( qhandle_t ) {}
	virtual void AddCheapDecal( qhandle_t, const cheapDecalParameters_t &, float ) {}
	virtual void ClearDecals( void ) {}

	virtual void AddEnvBounds( idVec3 const &, idVec3 const &, const char * ) {}

	virtual void SetRenderView( const renderView_t * ) {}
	virtual void RenderScene( const renderView_t * ) {}
	virtual void PerformRenderScene( const renderView_t * ) {}

	virtual int NumPortals( void ) const { return 0; }
	virtual qhandle_t FindPortal( const idBounds & ) const { return 0; }
	virtual void SetPortalState( qhandle_t, int ) {}
	virtual int GetPortalState( qhandle_t ) { return 0; }
	virtual void UpdatePortalOccTestView( int ) {}

	virtual bool AreasAreConnected( int, int, portalConnection_t ) { return true; }
	virtual bool AreasAreConnected( int, int, portalFlags_t ) { return true; }
	virtual bool AreasAreConnected( int, int ) { return true; }

	virtual int NumAreas( void ) const { return 1; }
	virtual int PointInArea( const idVec3 & ) const { return 0; }
	virtual int BoundsInAreas( const idBounds &, int *areas, int maxAreas ) const {
		if ( maxAreas > 0 && areas != NULL ) {
			areas[0] = 0;
			return 1;
		}
		return 0;
	}

	virtual int NumPortalsInArea( int ) { return 0; }
	virtual exitPortal_t GetPortal( int, int ) {
		exitPortal_t ep;
		memset( &ep, 0, sizeof( ep ) );
		return ep;
	}

	virtual void SetAreaPortalFlags( int, int ) {}
	virtual int GetAreaPortalFlags( int ) const { return 0; }
	virtual void SetAreaAmbientCubeMap( int, const sdDeclAmbientCubeMap * ) {}
	virtual const sdDeclAmbientCubeMap *GetAreaAmbientCubeMap( int ) { return NULL; }
	virtual void SetCubemapSunProperties( const sdDeclAmbientCubeMap *, const idVec3 &, const idVec3 & ) {}

	virtual bool ModelTrace( modelTrace_t &trace, qhandle_t, const idVec3 &, const idVec3 &, const float, int = SURF_COLLISION ) const {
		memset( &trace, 0, sizeof( trace ) );
		trace.fraction = 1.0f;
		return false;
	}
	virtual bool Trace( modelTrace_t &trace, const idVec3 &, const idVec3 &, const float, bool = true ) const {
		memset( &trace, 0, sizeof( trace ) );
		trace.fraction = 1.0f;
		return false;
	}
	virtual bool FastWorldTrace( modelTrace_t &trace, const idVec3 &, const idVec3 & ) const {
		memset( &trace, 0, sizeof( trace ) );
		trace.fraction = 1.0f;
		return false;
	}

	virtual void StartWritingDemo( idDemoFile * ) {}
	virtual void StopWritingDemo() {}
	virtual bool ProcessDemoCommand( idDemoFile *, renderView_t *, int * ) { return false; }

	virtual void RegenerateWorld() {}

	virtual void DebugClearLines( int ) {}
	virtual void DebugLine( const idVec4 &, const idVec3 &, const idVec3 &, const int = 0, const bool = false ) {}
	virtual void DebugArrow( const idVec4 &, const idVec3 &, const idVec3 &, int, const int = 0, bool = false ) {}
	virtual void DebugWinding( const idVec4 &, const idWinding &, const idVec3 &, const idMat3 &, const int = 0, const bool = false ) {}
	virtual void DebugCircle( const idVec4 &, const idVec3 &, const idVec3 &, const float, const int, const int = 0, const bool = false ) {}
	virtual void DebugSphere( const idVec4 &, const idSphere &, const int = 0, const bool = false ) {}
	virtual void DebugBounds( const idVec4 &, const idBounds &, const idVec3 & = vec3_origin, const idMat3& = mat3_identity, const int = 0 ) {}
	virtual void DebugBox( const idVec4 &, const idBox &, const int = 0 ) {}
	virtual void DebugFrustum( const idVec4 &, const idFrustum &, const bool = false, const int = 0 ) {}
	virtual void DebugCone( const idVec4 &, const idVec3 &, const idVec3 &, float, float, const int = 0 ) {}
	virtual void DebugAxis( const idVec3 &, const idMat3 &, int = 0 ) {}

	virtual void DebugClearPolygons( int ) {}
	virtual void DebugPolygon( const idVec4 &, const idWinding &, const int = 0, const bool = false, idImage* = NULL ) {}
	virtual void DrawText( const char *, const idVec3 &, float, const idVec4 &, const idMat3 &, const int = 1, const int = 0 ) {}

	virtual void SetAtmosphere( const sdDeclAtmosphere* ) {}
	virtual const sdDeclAtmosphere* GetAtmosphere() const { return NULL; }
	virtual void SetupMatrices( const renderView_t*, float*, float*, const bool ) {}
	virtual void SetMegaTextureSTGrid( const idBounds&, const idVec2*, int, int ) {}
	virtual struct atmosLightProjection_t *FindAtmosLightProjection( int ) { return NULL; }
};

static idRenderWorldHeadless headlessRenderWorld;

/*
==============================================================
idRenderModelManagerHeadless
==============================================================
*/
class idRenderModelManagerHeadless : public idRenderModelManager {
public:
	virtual ~idRenderModelManagerHeadless() {}

	virtual void Init() {}
	virtual void Shutdown() {}
	virtual void BeginLevelLoad() {}
	virtual void EndLevelLoad() {}

	virtual idRenderModel *AllocModel() { return NULL; }
	virtual void FreeModel( idRenderModel * ) {}
	virtual idRenderModel *FindModel( const char * ) { return NULL; }
	virtual idRenderModel *CheckModel( const char * ) { return NULL; }
	virtual idRenderModel *GetModel( const char * ) { return NULL; }
	virtual idRenderModel *DefaultModel() { return NULL; }

	virtual void AddModel( idRenderModel * ) {}
	virtual void RemoveModel( idRenderModel * ) {}
	virtual void ReloadModels( bool = false ) {}
	virtual void WritePrecacheCommands( idFile * ) {}
	virtual void FreeModelVertexCaches() {}

	virtual bool WriteSurfaceModel( const char*, idList< idSurface* >&, idStrList& ) { return false; }
	virtual bool WriteTriangleModelB( const char *, idRenderModel * ) { return false; }
	virtual bool WriteTriangleModel( const char *, idRenderModel * ) { return false; }
};

static idRenderModelManagerHeadless headlessRenderModelManager;

/*
==============================================================
idRenderSystemHeadless
==============================================================
*/
class idRenderSystemHeadless : public idRenderSystem {
public:
	virtual ~idRenderSystemHeadless() {}

	// The client resolves the built-in render bindings and programs in
	// idRenderSystemLocal::Init.  Material parsing compares stage bindings
	// against them (implicit interaction/depth stages, perforated materials),
	// so the dedicated server must resolve them too or those materials default.
	virtual void Init( void ) { rbinds->Init(); }
	virtual void Shutdown( void ) {}
	virtual void ShutdownOpenGL( void ) {}
	virtual bool IsOpenGLRunning( void ) const { return false; }

	virtual int GetScreenWidth( void ) const { return 640; }
	virtual int GetScreenHeight( void ) const { return 480; }

	virtual idRenderWorld *AllocRenderWorld( void ) {
		return &headlessRenderWorld;
	}
	virtual void FreeRenderWorld( idRenderWorld * ) {}

	virtual void BeginLevelLoad( void ) {}
	virtual void EndLevelLoad( void ) {}
	virtual void LevelStart( void ) {}

	virtual void DrawChar( int, int, int, int, int, const idMaterial * ) {}
	virtual void DrawStringExt( int, int, int, int, const char *, const idVec4 &, bool, const idMaterial * ) {}
	virtual void DrawSmallChar( int, int, int, const idMaterial * ) {}
	virtual void DrawSmallStringExt( int, int, const char *, const idVec4 &, bool, const idMaterial * ) {}
	virtual void DrawBigChar( int, int, int, const idMaterial * ) {}
	virtual void DrawBigStringExt( int, int, const char *, const idVec4 &, bool, const idMaterial * ) {}

	virtual void WriteDemoPics() {}
	virtual void DrawDemoPics() {}

	virtual void BeginFrame( int, int ) {}
	virtual void EndFrame( bool = true ) {}

	virtual void SetCaptureBuffer( sdFrameBuffer* ) {}
	virtual sdFrameBuffer* GetCaptureBuffer() { return NULL; }

	virtual bool TakeScreenshot( int, int, const char *, int, struct renderView_s *, bool = false, bool = false ) { return false; }
	virtual void CropRenderSize( int, int, bool = false ) {}
	virtual void CaptureRenderToImage( const char *, int = -1, copyBuffer_t = CB_COLOR ) {}
	virtual void SetFrameBuffer( class sdFrameBuffer * ) {}
	virtual void UnCrop() {}

	virtual void GetCardCaps( bool &oldCard ) { oldCard = false; }
	virtual bool UploadImage( const char*, const byte*, int, int, bool = false, bool = true ) { return false; }

	virtual void BindImage( textureType_t, GLuint ) {}
	virtual void SetGLState( int ) {}
	virtual void SetGLTexEnv( int ) {}
	virtual void SelectTextureUnit( int ) {}
	virtual void SetDefaultGLState( void ) {}
	virtual void SetGL2D( void ) {}
	virtual void SetCull( int ) {}

	virtual FILE* GetLogFileHandle() { return NULL; }
	virtual void SetLogFileHandle( FILE* ) {}

	virtual void LoadImage( const char *, byte **, int *, int *, unsigned *, bool ) {}
	virtual void FlushGLErrors( bool = false ) {}
	virtual int CheckGLForErrors( bool = false ) { return 0; }

	virtual class idRenderModel* InstantiateDynamicModel( class idRenderModel*, struct renderEntity_t* ) { return NULL; }

	virtual const glconfig_t& GLConfig() const {
		return glConfig;
	}

	virtual void SyncRenderSystem( void ) {}
	virtual bool BeginRenderSync( void ) { return true; }
	virtual void EndRenderSync( void ) {}

	virtual idImage *LoadImageFromFile( const char *, struct imageParams_t & ) { return NULL; }
	virtual bool IsDisplayModeAvailable( int, int ) const { return true; }
	virtual int GetNumMSAAModes( void ) const { return 0; }
	virtual const char *GetMSAAMode( int, int &val ) const { val = 0; return ""; }
	virtual bool IsMSAACountAvailable( int ) const { return false; }

	virtual void LockThreads( void ) {}
	virtual void UnlockThreads( void ) {}
	virtual int GetDoubleBufferIndex( void ) { return 0; }
	virtual int GetSyncNum( void ) { return 0; }
	virtual bool IsSMPEnabled( void ) { return false; }

	virtual void FreeOcclussionQueries( void ) {}
	virtual int RegisterPtr( void * ) { return 0; }
	virtual void UnregisterPtr( int ) {}
	virtual void* PtrForUID( int ) { return NULL; }
};

static idRenderSystemHeadless headlessRenderSystem;

/*
==============================================================
idSoundWorldHeadless
==============================================================
*/
class idSoundWorldHeadless : public idSoundWorld {
public:
	virtual ~idSoundWorldHeadless() {}

	virtual void ClearAllSoundEmitters( void ) {}
	virtual void StopAllSounds( void ) {}
	virtual idSoundEmitter* AllocSoundEmitter( void ) { return NULL; }
	virtual idSoundEmitter* EmitterForIndex( int ) { return NULL; }
	virtual void MixLoop( int ) {}
	virtual float CurrentShakeAmplitudeForPosition( const int, const idVec3& ) { return 0.0f; }
	virtual void PlaceListener( const idVec3&, const idMat3&, const int, const int ) {}

	virtual const idVec3& GetListenerPosition() const { return vec3_origin; }
	virtual const idMat3& GetListenerAxis() const { return mat3_identity; }

	virtual void FadeSoundClasses( const int, const float, const float ) {}
	virtual void PlayShaderDirectly( const idSoundShader*, const soundChannel_t = SCHANNEL_ANY, int* = NULL ) {}

	virtual void StartWritingDemo( idDemoFile * ) {}
	virtual void StopWritingDemo( void ) {}
	virtual void ProcessDemoCommand( idDemoFile * ) {}

	virtual void Pause( void ) {}
	virtual void UnPause( void ) {}
	virtual bool IsPaused( void ) { return false; }
	virtual bool IsMuted() const { return true; }

	virtual void AVIOpen( const char*, const char* ) {}
	virtual void AVIClose( void ) {}

	virtual void WriteToSaveGame( idFile* ) {}
	virtual void ReadFromSaveGame( idFile* ) {}
	virtual void BeginLevelLoad( void ) {}
};

static idSoundWorldHeadless headlessSoundWorld;

/*
==============================================================
idSoundSystemHeadless
==============================================================
*/
class idSoundSystemHeadless : public idSoundSystem {
private:
	sdLock lock;

public:
	virtual ~idSoundSystemHeadless() {}

	virtual void Init( void ) {}
	virtual void Shutdown( void ) {}
	virtual void ClearBuffer( void ) {}

	virtual bool InitHW( void ) { return true; }
	virtual bool ShutdownHW( void ) { return true; }

	virtual sdLock& GetLock() { return lock; }
	virtual int AsyncUpdate( int ) { return 0; }

	virtual void SetMute( bool ) {}
	virtual bool IsMuted() const { return true; }

	virtual cinData_t ImageForTime( const int, const bool ) {
		cinData_t d;
		memset( &d, 0, sizeof( d ) );
		return d;
	}

	virtual int GetSoundDecoderInfo( int, soundDecoderInfo_t& ) { return 0; }

	virtual idSoundWorld* AllocSoundWorld( idRenderWorld* ) {
		return &headlessSoundWorld;
	}
	virtual void FreeSoundWorld( idSoundWorld* ) {}
	virtual void SetPlayingSoundWorld( idSoundWorld* ) {}
	virtual idSoundWorld *GetPlayingSoundWorld( void ) { return NULL; }

	virtual void BeginLevelLoad( void ) {}
	virtual void EndLevelLoad( void ) {}

	virtual void StartCapture() {}
	virtual void StopCapture() {}
	virtual int GetCaptureRate( void ) const { return 0; }

	virtual bool QuerySpeakers( int ) const { return false; }
	virtual void RefreshSoundDevices( void ) {}

	virtual const idWStrList* ListSoundPlaybackDevices() const { return NULL; }
	virtual const idWStrList* ListSoundCaptureDevices() const { return NULL; }
	virtual void FreeDeviceList( const idWStrList* ) const {}

	virtual int GetAudioDeviceHash( const wchar_t* ) const { return 0; }
	virtual int GetAudioDeviceHash( const char* ) const { return 0; }
};

static idSoundSystemHeadless headlessSoundSystem;

/*
==============================================================
sdDeviceContextHeadless
==============================================================
*/
class sdDeviceContextHeadless : public sdDeviceContext {
public:
	virtual void Reset() {}
	virtual void BeginEmitToCurrentView( const float[16], const int, const bool ) {}
	virtual void BeginEmitFullScreen() {}
	virtual void End() {}

	virtual void SetColor( const idVec4& ) {}
	virtual void SetColor( const float, const float, const float, const float ) {}
	virtual idVec4 SetColorMultiplier( const idVec4& c ) { return c; }
	virtual void SetRegister( const int, const float ) {}
	virtual void SetRegisters( const float* ) {}

	virtual void EnableClipping( bool ) {}
	virtual void PushClipRect( const sdBounds2D& ) {}
	virtual void PopClipRect() {}

	virtual void DrawRect( float, float, float, float, float, float, float, float, const idMaterial*, float = 0.0f ) {}
	virtual void DrawClippedRect( float, float, float, float, float, float, float, float, const idMaterial*, float = 0.0f ) {}
	virtual void DrawMaskedClippedRect( float, float, float, float, float, float, float, float, float, float, float, float, const idMaterial*, float = 0.0f ) {}

	virtual void DrawCinematic( float, float, float, float, float, float, float, float, const idMaterial*, idSoundEmitter*, float = 0.0f ) {}
	virtual void DrawClippedWinding( const idWinding2D&, const idMaterial* ) {}
	virtual void DrawClippedWindingMasked( const idWinding2D&, const idMaterial*, float, float, float, float ) {}

	virtual void DrawMaskedMaterial( float, float, float, float, float, float, float, float, const idMaterial*, const idVec4 &, float = 1.0f, float = 1.0f, float = 0.0f, float = 0.0f, float = 0.0f ) {}
	virtual void DrawMaterial( float, float, float, float, const idMaterial*, const idVec4 &, float = 1.0f, float = 1.0f, float = 0.0f, float = 0.0f, float = 0.0f ) {}
	virtual void DrawMaterial( const idVec4&, const idMaterial *, const idVec4 &, const idVec2& = idVec2( 1.0f, 1.0f ), const idVec2& = vec2_origin, float = 0.0f ) {}
	virtual void DrawMaterial( const sdBounds2D&, const idMaterial *, const idVec4 &, const idVec2& = idVec2( 1.0f, 1.0f ), const idVec2& = vec2_origin, float = 0.0f ) {}
	virtual void DrawMaterial( float, float, float, float, const idMaterial*, const idVec4 &, const idVec2&, const idVec2& ) {}
	virtual void DrawRotatedMaterial( float, idVec2, idVec2, const idMaterial*, const idVec4& ) {}

	virtual void DrawWindingMaterial( const idWinding2D&, const idMaterial*, const idVec4& ) {}
	virtual void DrawRect( float, float, float, float, const idVec4 & ) {}
	virtual void DrawClippedRect( float, float, float, float, const idVec4 & ) {}
	virtual void DrawBox( float, float, float, float, float, const idVec4 & ) {}
	virtual void DrawClippedBox( float, float, float, float, float, const idVec4 & ) {}

	virtual void DrawCircleMaterial( const float, const float, const idVec2&, const int, const idVec4&, const idMaterial*, const idVec4&, float ) {}
	virtual void DrawCircleMaterialMasked( const float, const float, const idVec2&, const int, const idVec4&, const idMaterial*, const idVec4&, float, float, float, float, float ) {}
	virtual void DrawCircle( const float, const float, const idVec2&, const float, const int, const idVec4& ) {}

	virtual void DrawLineMaterial( const idVec2&, const idVec2&, const float, const idMaterial*, const idVec4& ) {}
	virtual void DrawLine( const idVec2&, const idVec2&, const float, const idVec4 & ) {}

	virtual void DrawFilledArc( const float, const float, const float, int, float, const idVec4 &, float = 0.0f, const idMaterial * = NULL ) {}
	virtual void DrawFilledArcMasked( const float, const float, const float, int, float, const idVec4 &, float, float, float, float, float = 0.0f, const idMaterial * = NULL ) {}
	virtual void DrawArc( const float, const float, const float, const float, const int, const float, const idVec4 &, const float = 0.0f ) {}
	virtual void DrawTimer( const float, const float, const float, const float, float, const idVec4 &, const idMaterial*, bool, const idVec2& = vec2_zero, const idVec2& = vec2_one ) {}

	virtual qhandle_t FindFont( const char* ) { return 0; }
	virtual void FreeFont( const qhandle_t ) {}
	virtual const int GetFontHeight( const qhandle_t, const int ) { return 12; }
	virtual void SetFont( const qhandle_t ) {}
	virtual void SetFontSize( const int ) {}
	virtual void DrawText( const wchar_t*, const sdBounds2D&, unsigned int ) {}
	virtual void GetTextDimensions( const wchar_t*, const sdBounds2D&, unsigned int, const qhandle_t, const int, int& width, int& height, float* = NULL, int** = NULL, idList< int >* = NULL ) {
		width = 0;
		height = 0;
	}

	virtual void OverrideAspectRationCorrection( bool ) {}
	virtual float GetAspectRatioCorrection() const { return 1.0f; }
};

static sdDeviceContextHeadless headlessDeviceContext;

/*
==============================================================
rvBSEManagerHeadless
==============================================================
*/
class rvBSEManagerHeadless : public rvBSEManager {
public:
	virtual ~rvBSEManagerHeadless() {}

	virtual bool Init() { return true; }
	virtual bool Shutdown() { return true; }
	virtual bool PlayEffect( rvRenderEffectLocal*, float ) { return false; }
	virtual bool ServiceEffect( rvRenderEffectLocal*, float, bool& forcePush ) { forcePush = false; return false; }
	virtual void StopEffect( rvRenderEffectLocal* ) {}
	virtual void RestartEffect( rvRenderEffectLocal* ) {}
	virtual void FreeEffect( rvRenderEffectLocal* ) {}
	virtual float EffectDuration( const rvRenderEffectLocal* ) { return 0.0f; }
	virtual void BeginLevelLoad() {}
	virtual void EndLevelLoad() {}
	virtual void StartFrame() {}
	virtual void EndFrame() {}
	virtual bool Filtered( const char*, effectCategory_t ) { return false; }
	virtual void UpdateRateTimes() {}
	virtual bool CanPlayRateLimited( effectCategory_t ) { return true; }
	virtual int AddTraceModel( idTraceModel* ) { return -1; }
	virtual idTraceModel* GetTraceModel( int ) { return NULL; }
	virtual void FreeTraceModel( int ) {}
	virtual const idVec3& GetCubeNormals( int ) { return vec3_origin; }
	virtual void SetShakeParms( float, float ) {}
	virtual void SetTunnelParms( float, float ) {}
	virtual const idMat3& GetModelToBSE() { return mat3_identity; }
	virtual bool IsTimeLocked() const { return false; }
	virtual float GetLockedTime() const { return 0.0f; }
	virtual void MakeEditable( rvParticleTemplate* ) {}
	virtual void CopySegment( rvSegmentTemplate*, rvSegmentTemplate* ) {}

	virtual void BeginFrame() {}
	virtual void ServiceEffect( rvBSE&, const rvBSEOwner&, idList<rvBSEParticle>& ) {}
	virtual void PrepareRender( const rvBSEOwner&, const idList<rvBSEParticle>&, idList<rvBSEParticle>& ) {}
	virtual const rvBSEStats& GetStats() const { static rvBSEStats stats; return stats; }
};

static rvBSEManagerHeadless headlessBSEManager;

} // namespace

/*
==============================================================
sdRenderBindingManager
==============================================================
*/
void sdRenderBindingManager::Init() {}
void sdRenderBindingManager::Shutdown() {}
void sdRenderBindingManager::UpdateInfrequentRenderBindings() {}
void sdRenderBindingManager::UpdateParameter( const sdDeclRenderBinding* const ) {}
void sdRenderBindingManager::RenderBindingPostParseCallback( idDecl* ) {}
void sdRenderBindingManager::RenderBindingPostParse( idDecl* ) {}

static sdRenderBindingManager headlessRenderBindingManager;

/*
==============================================================
idImage / idImageManager

The dedicated server never uploads or samples a texture, but material and
renderBinding decls must parse exactly as they do on the client.  Retail
material stages hold an image pointer for every texture reference, and a NULL
ParseImage result defaults the whole material: its contents become plain
CONTENTS_SOLID (no water, no CONTENTS_OPAQUE), its surface flags (nonsolid,
ladder, slick, noimpact, ...) are cleared and its surfaceType/surfaceTypeMap
are dropped.  The collision model takes polygon contents from the materials,
so collision on the server differed from the client and from what the AAS was
compiled against (e.g. most world polygons lost CONTENTS_OPAQUE).

ParseImage therefore consumes the reference with the same parser as the
client (R_ParseImageReference) and, like the client, returns NULL only for a
malformed reference.  Every well-formed reference gets one shared placeholder
image; its GPU operations below do nothing and it reports itself unloaded
(sourceWidth/uploadWidth 0), which is also what a client image looks like
before it is loaded.
==============================================================
*/
void idImage::Bind() {}
void idImage::BindFragment() {}
void idImage::BindFragment( const int ) {}
void idImage::Purge() {}
bool idImage::IsLoaded() const { return false; }
void idImage::SetMipmapLevel( byte*, int, int, int, mipmapState_t& ) {}
void idImage::GenerateImage( const byte*, int, int, textureFilter_t, bool, textureRepeat_t, textureDepth_t, mipmapState_t ) {}
void idImage::GenerateImageEx( const byte*, int, int, textureFilter_t, bool, textureRepeat_t, textureDepth_t, int, int ) {}
void idImage::Generate3DImage( const byte*, int, int, int, textureFilter_t, bool, textureRepeat_t, textureDepth_t ) {}
void idImage::GenerateCubeImage( const byte*[ 6 ], int, textureFilter_t, bool, textureDepth_t ) {}
void idImage::GenerateMipmaps() {}
void idImage::MakeDefault() {}
void idImage::Reload( bool, bool ) {}

static idImage* R_HeadlessPlaceholderImage() {
	// Never freed: materials keep pointing at it until process exit.
	static idImage* placeholder = NULL;
	if ( placeholder == NULL ) {
		placeholder = new idImage;
		placeholder->imgName = "_headlessPlaceholder";
	}
	return placeholder;
}

idImage* idImageManager::ParseImage( idParser& src, const imageParams_t& defaultParms ) {
	imageParams_t parms = defaultParms;
	idStr imageProgram;
	if ( !R_ParseImageReference( src, parms, false, imageProgram ) ) {
		return NULL;
	}
	return R_HeadlessPlaceholderImage();
}

// Exported Globals
glconfig_t				glConfig;
idCVar					r_lightScale( "r_lightScale", "2", CVAR_RENDERER | CVAR_FLOAT, "all light intensities are multiplied by this" );
idCVar					r_shaderQuality( "r_shaderQuality", "2", CVAR_RENDERER | CVAR_INTEGER, "" );
idCVar					r_useConstantMaterials( "r_useConstantMaterials", "0", CVAR_RENDERER | CVAR_BOOL, "" );

sdRenderBindings		renderBindings;
sdRenderBindings*		rbinds = &renderBindings;

const renderView_t*		RB_GetDrawView() { return NULL; }
idRenderWorldLocal* RB_GetDrawWorld() { return NULL; }

void idMegaTexture::UpdateMapping( const idRenderWorldLocal* ) {}
void idMegaTexture::UpdateForViewOrigin( const idVec3&, int ) {}

idRenderSystem*			renderSystem = &headlessRenderSystem;
idRenderModelManager*	renderModelManager = &headlessRenderModelManager;
idSoundSystem*			soundSystem = &headlessSoundSystem;
sdDeviceContext*		deviceContext = &headlessDeviceContext;
sdRenderBindingManager*	renderBindingManager = &headlessRenderBindingManager;
rvBSEManager*			bse = &headlessBSEManager;
idImageManager*			globalImages = NULL;
id3DContext*			sys3D = NULL;



// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#ifndef __RENDER_BSE_H__
#define __RENDER_BSE_H__

#include "../idlib/precompiled.h"

// Define rvBSEOwner and rvBSEParticle before BSE.h
typedef bool (*bseModelSample_t)( const char*, idRandom&, idVec3&, idVec3& );

struct rvBSEOwner {
	float			time;
	float			stopTime;
	float			diversity;
	float			brightness;
	float			attenuation;
	idVec4			color;
	idVec4			materialColor;
	idVec3			gravity;
	idVec3			wind;
	idVec3			endOrigin;
	idVec3			viewOrigin;
	bool			hasEndOrigin;
	bseModelSample_t modelSampler;
};

struct rvBSEParticle {
	int				type;
	int				segmentIndex;
	const void*		particleTemplate;
	idStr			materialName;
	idStr			modelName;
	idStr			entityDefName;
	idVec3			position;
	idVec3			velocity;
	idVec3			length;
	idVec3			angles;
	idVec3			size;
	idVec4			color;
	idVec3			jitterSize;
	float			jitterRate;
	const idDeclTable* jitterTable;
	idVec3			forkMins;
	idVec3			forkMaxs;
	int				numForks;
	int				seed;
	float			age;
	float			life;
	float			textureS0;
	float			textureS1;
	float			tiling;
	float			trailTime;
	float			trailScale;
	int				trailCount;
	int				trailType;
	idStr			trailMaterialName;
	bool			useEndOrigin;
	bool			depthSort;
	bool			inverseDrawOrder;
};

#include "../bse/BSE.h"

class rvBSEManagerClient : public rvBSEManager {
public:
	virtual ~rvBSEManagerClient() {}

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

inline rvBSEManagerClient s_clientBSEManager;
inline rvBSEManager* bse = &s_clientBSEManager;

class rvRenderBSE {
public:
	rvRenderBSE() {}
	~rvRenderBSE() {}
	void Init( const rvDeclEffect* ) {}
	void Service( const rvBSEOwner&, idList<rvBSEParticle>& ) {}
};

#define rvBSE rvRenderBSE

inline void BSE_BuildRenderModel( idRenderModel *snapshot, const char *snapshotName,
	const idList<rvBSEParticle> &particles, const rvBSEOwner &owner,
	const idVec3 &viewOrigin, const idVec3 &viewRight, const idVec3 &viewUp ) {
	if ( snapshot != NULL ) {
		snapshot->InitEmpty( snapshotName );
	}
}

#endif

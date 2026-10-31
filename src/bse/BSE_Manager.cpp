// Copyright (C) 2007 Id Software, Inc.
//
// ETQW BSE manager and renderer-neutral particle preparation.

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "BSESim.h"

static rvBSEManagerClient bseManagerLocal;
rvBSEManager *bse = &bseManagerLocal;

rvBSEManagerClient &BSE_ClientManager() {
	return bseManagerLocal;
}

static void BSE_Stats_f( const idCmdArgs & ) {
	BSE_ClientManager().PrintStats();
}

void rvBSEManagerClient::PrintStats() const {
	common->Printf( "BSE: %lld effects serviced, %lld segments, %lld particles evaluated, %lld rendered, %lld capped; "
		"%lld effect models drawn with %lld surfaces\n",
		totalEffectsServiced + stats.effectsServiced, totalSegmentsServiced + stats.segmentsServiced,
		totalParticlesEvaluated + stats.particlesEvaluated, totalParticlesRendered + stats.particlesRendered,
		totalParticlesCapped + stats.particlesCapped, drawnEffects, drawnSurfaces );
}

void rvBSEManagerClient::AddFrameToTotals() {
	totalEffectsServiced += stats.effectsServiced;
	totalSegmentsServiced += stats.segmentsServiced;
	totalParticlesEvaluated += stats.particlesEvaluated;
	totalParticlesRendered += stats.particlesRendered;
	totalParticlesCapped += stats.particlesCapped;
}

bool rvBSEManagerClient::Init() {
	totalEffectsServiced = totalSegmentsServiced = totalParticlesEvaluated = 0;
	totalParticlesRendered = totalParticlesCapped = 0;
	drawnEffects = 0;
	drawnSurfaces = 0;
	cmdSystem->AddCommand( "bseStats", BSE_Stats_f, CMD_FL_RENDERER, "prints the BSE particle counts since start" );
	BeginFrame();
	return true;
}

bool rvBSEManagerClient::Shutdown() {
	cmdSystem->RemoveCommand( "bseStats" );
	BeginFrame();
	return true;
}

// These entries retain the retail game-DLL ABI.  Render-world effect defs are
// serviced by the renderer-neutral overload below, so no rvRenderEffectLocal
// object crosses the reconstructed renderer boundary.
bool rvBSEManagerClient::PlayEffect( rvRenderEffectLocal *, float ) { return false; }
bool rvBSEManagerClient::ServiceEffect( rvRenderEffectLocal *, float, bool &forcePush ) {
	forcePush = false;
	return false;
}
void rvBSEManagerClient::StopEffect( rvRenderEffectLocal * ) {}
void rvBSEManagerClient::RestartEffect( rvRenderEffectLocal * ) {}
void rvBSEManagerClient::FreeEffect( rvRenderEffectLocal * ) {}
float rvBSEManagerClient::EffectDuration( const rvRenderEffectLocal * ) { return 0.0f; }
void rvBSEManagerClient::BeginLevelLoad() {}
void rvBSEManagerClient::EndLevelLoad() {}
void rvBSEManagerClient::StartFrame() { BeginFrame(); }
void rvBSEManagerClient::EndFrame() {}
bool rvBSEManagerClient::Filtered( const char *, effectCategory_t ) { return false; }
void rvBSEManagerClient::UpdateRateTimes() {}
bool rvBSEManagerClient::CanPlayRateLimited( effectCategory_t ) { return true; }
int rvBSEManagerClient::AddTraceModel( idTraceModel * ) { return -1; }
idTraceModel *rvBSEManagerClient::GetTraceModel( int ) { return NULL; }
void rvBSEManagerClient::FreeTraceModel( int ) {}
const idVec3 &rvBSEManagerClient::GetCubeNormals( int ) { return vec3_origin; }
void rvBSEManagerClient::SetShakeParms( float, float ) {}
void rvBSEManagerClient::SetTunnelParms( float, float ) {}
const idMat3 &rvBSEManagerClient::GetModelToBSE() { return mat3_identity; }
bool rvBSEManagerClient::IsTimeLocked() const { return false; }
float rvBSEManagerClient::GetLockedTime() const { return 0.0f; }
void rvBSEManagerClient::MakeEditable( ::rvParticleTemplate * ) {}
void rvBSEManagerClient::CopySegment( ::rvSegmentTemplate *, ::rvSegmentTemplate * ) {}
// the game-facing entry; the engine's own effects go through the bseSimulation overload
void rvBSEManagerClient::ServiceEffect( ::rvBSE &, const rvBSEOwner &, idList<rvBSEParticle> &particles ) {
	particles.Clear();
}

void rvBSEManagerClient::BeginFrame() {
	AddFrameToTotals();
	memset( &stats, 0, sizeof( stats ) );
}

void rvBSEManagerClient::ServiceEffect( bseSimulation &effect, const rvBSEOwner &owner,
		idList<rvBSEParticle> &particles ) {
	particles.Clear();
	effect.ServiceInternal( owner, particles, 0, &stats );
}

void rvBSEManagerClient::PrepareRender( const rvBSEOwner &owner,
		const idList<rvBSEParticle> &particles, idList<rvBSEParticle> &renderParticles ) {
	renderParticles.Clear();
	for ( int i = 0; i < particles.Num(); i++ ) {
		const rvBSEParticle &particle = particles[i];
		if ( particle.type == PTYPE_ELECTRICITY ) {
			BSE_CreateElectricity( owner, particle, renderParticles );
		} else {
			renderParticles.Append( particle );
		}
		BSE_CreateTrail( particle, renderParticles );
	}
	BSE_SortParticles( owner, renderParticles );
	stats.particlesRendered += renderParticles.Num();
}

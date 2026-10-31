/*
===========================================================================

DarklightNG Source Code
Copyright (C) 2026 - Justin Marshall(aka IceColdDuke).

This file is part of the DarklightNG GPL source code.
This file is part of the Doom 3 GPL Source Code (?Doom 3 Source Code?).

DarklightNG is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

DarklightNG is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

===========================================================================
*/

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "BSESim.h"

bseSimulation::bseSimulation() {
	effect = NULL;
	seedBase = 0;
}

void bseSimulation::Init( const bseEffectTemplate *newEffect ) {
	effect = newEffect;
	seedBase = effect != NULL ? idStr::IHash( effect->GetName() ) : 0;
	segments.Clear();
	if ( effect != NULL ) {
		segments.SetNum( effect->GetNumSegmentTemplates() );
		for ( int i = 0; i < segments.Num(); i++ ) {
			segments[i].Init( effect->GetSegmentTemplate( i ), i, seedBase );
		}
	}
}

void bseSimulation::Service( const rvBSEOwner &owner, idList<rvBSEParticle> &particles ) {
	BSE_ClientManager().ServiceEffect( *this, owner, particles );
}

void bseSimulation::ServiceInternal( const rvBSEOwner &owner, idList<rvBSEParticle> &particles,
		int depth, rvBSEStats *stats ) {
	if ( effect == NULL || owner.time < 0.0f ) return;
	if ( stats != NULL ) stats->effectsServiced++;
	const int maxEffectParticles = 16384;
	const int diversitySeed = idMath::Ftoi( owner.diversity * idRandom::MAX_RAND );

	for ( int i = 0; i < effect->GetNumSegmentTemplates(); i++ ) {
		const bseSegmentTemplate &segmentTemplate = *effect->GetSegmentTemplate( i );
		if ( segmentTemplate.type != SEG_EFFECT ) {
			segments[i].Service( owner, particles, maxEffectParticles, stats );
			continue;
		}
		if ( stats != NULL ) stats->segmentsServiced++;
		if ( depth >= 8 || segmentTemplate.spawnEffects.Num() == 0 ) continue;
		idRandom random( seedBase ^ diversitySeed ^ ( i * 7919 ) );
		const float segmentStart = segmentTemplate.startTime.x + random.RandomFloat() *
			( segmentTemplate.startTime.y - segmentTemplate.startTime.x );
		if ( owner.time < segmentStart ) continue;

		const idStr &spawnName = segmentTemplate.spawnEffects[random.RandomInt( segmentTemplate.spawnEffects.Num() )];
		idStr declName = spawnName;
		declName.StripFileExtension();
		const qhandle_t effectType = declManager->GetDeclTypeHandle( declEffectsIdentifier );
		const bseEffectTemplate *nestedDecl = BSE_EffectTemplateForDecl( BSE_EffectDeclOf(
			declManager->FindType( effectType, declName, false ) ) );
		if ( nestedDecl == NULL || nestedDecl == effect ) continue;

		rvBSEOwner nestedOwner = owner;
		nestedOwner.time -= segmentStart;
		if ( nestedOwner.stopTime > 0.0f ) nestedOwner.stopTime -= segmentStart;
		nestedOwner.diversity += i * ( 1.0f / 1024.0f );
		bseSimulation nested;
		nested.Init( nestedDecl );
		nested.ServiceInternal( nestedOwner, particles, depth + 1, stats );
		if ( particles.Num() >= maxEffectParticles ) return;
	}
}

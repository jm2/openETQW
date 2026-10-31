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
#include "../framework/declManager.h"

#include <unordered_map>

bseEffectTemplate::bseEffectTemplate() {
	size = 8.0f;
	cutOffDistance = 0.0f;
	minDuration = 0.0f;
	maxDuration = 0.0f;
	bounds.Zero();
	bounds.ExpandSelf( size );
}

bseEffectTemplate::~bseEffectTemplate() {
	FreeData();
}

size_t bseEffectTemplate::Size() const {
	int allocated = segments.Allocated();
	for ( int i = 0; i < segments.Num(); i++ ) {
		allocated += segments[i].Allocated();
	}
	return sizeof( *this ) + allocated;
}

void bseEffectTemplate::FreeData() {
	segments.Clear();
	size = 8.0f;
	cutOffDistance = 0.0f;
	minDuration = 0.0f;
	maxDuration = 0.0f;
	bounds.Zero();
	bounds.ExpandSelf( size );
}

// The effect decl (src/decllib/declEffect.cpp) opens the text or binary source and
// hands the lexer over; this parses the braced body into segments.
bool bseEffectTemplate::Parse( idParser &src ) {
	FreeData();
	idToken token;
	if ( !src.SkipUntilString( "{", &token ) ) {
		return false;
	}

	while ( src.ReadToken( &token ) ) {
		if ( token == "}" ) {
			Finish();
			return true;
		}
		if ( !token.Icmp( "size" ) ) {
			size = Max( 0.0f, src.ParseFloat() );
			continue;
		}
		if ( !token.Icmp( "cutOffDistance" ) ) {
			cutOffDistance = Max( 0.0f, src.ParseFloat() );
			continue;
		}

		const int segmentType = BSE_SegmentTypeForToken( token );
		if ( segmentType != SEG_NONE ) {
			if ( !ParseSegment( src, segmentType ) ) {
				return false;
			}
		} else {
			BSE_SkipUnknown( src, token );
		}
	}
	return false;
}

void bseEffectTemplate::Finish() {
	minDuration = 0.0f;
	maxDuration = 0.0f;
	for ( int i = 0; i < segments.Num(); i++ ) {
		const bseSegmentTemplate &segment = segments[i];
		float segmentMin = segment.startTime.x + segment.duration.x;
		float segmentMax = segment.startTime.y + segment.duration.y;
		if ( segment.HasVisualParticle() ) {
			segmentMin += segment.particle.duration.x;
			segmentMax += segment.particle.duration.y;
		}
		if ( segment.constant || segment.looping ) {
			segmentMax = 300.0f;
		}
		minDuration = Max( minDuration, segmentMin );
		maxDuration = Max( maxDuration, segmentMax );
	}
	CalculateBounds();
}

/*
================
The parsed templates of the SDK effect decls (#368)

The decl type is the SDK's rvDeclEffect, whose layout the game module shares, so
the simulation's template lives beside it, keyed by the decl. rvDeclEffect::Parse
fills it and FreeData drops it (src/decllib/declEffect.cpp); both run on the main
thread while decls load. The renderer looks a template up by decl.
================
*/
static std::unordered_map< const rvDeclEffect *, bseEffectTemplate * > bseEffectTemplates;

bool BSE_ParseEffectTemplate( const rvDeclEffect *decl, const char *name, idParser &src, float &minDuration, float &maxDuration,
		float &cutOffDistance, float &size ) {
	BSE_FreeEffectTemplate( decl );
	bseEffectTemplate *effectTemplate = new bseEffectTemplate;
	effectTemplate->SetName( name );
	if ( !effectTemplate->Parse( src ) ) {
		delete effectTemplate;
		return false;
	}
	minDuration = effectTemplate->GetMinDuration();
	maxDuration = effectTemplate->GetMaxDuration();
	cutOffDistance = effectTemplate->GetCutOffDistance();
	size = effectTemplate->GetSize();
	bseEffectTemplates[ decl ] = effectTemplate;
	return true;
}

void BSE_FreeEffectTemplate( const rvDeclEffect *decl ) {
	auto it = bseEffectTemplates.find( decl );
	if ( it != bseEffectTemplates.end() ) {
		delete it->second;
		bseEffectTemplates.erase( it );
	}
}

const bseEffectTemplate *BSE_EffectTemplateForDecl( const rvDeclEffect *decl ) {
	if ( decl == NULL ) {
		return NULL;
	}
	auto it = bseEffectTemplates.find( decl );
	return it != bseEffectTemplates.end() ? it->second : NULL;
}

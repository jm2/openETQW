// Copyright (C) 2007 Id Software, Inc.
//
// ETQW render-world implementation reconstructed under the retail PDB path.

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "RenderWorld_local.h"
#include "Image.h"
#include "Material.h"
#include "Model.h"
#include "ModelManager.h"
#include "RenderSystemBackend.h"
#include "GuiModel.h"
#include "DeviceContext.h"
#include "tr_render.h"
#include "../decllib/declTypeHolder.h"
#include "../sys/sys_render.h"

#if defined( __APPLE__ )
#include <OpenGL/gl.h>
#else
#include <GL/gl.h>
#endif

int idRenderWorldLocal::NumPortals() const {
	return 0;
}

qhandle_t idRenderWorldLocal::FindPortal( const idBounds& ) const {
	return 0;
}

void idRenderWorldLocal::SetPortalState( qhandle_t, int ) {
}

int idRenderWorldLocal::GetPortalState( qhandle_t ) {
	return PS_BLOCK_NONE;
}

void idRenderWorldLocal::UpdatePortalOccTestView( int ) {
}

bool idRenderWorldLocal::AreasAreConnected( int areaNum1, int areaNum2, portalConnection_t ) {
	return areaNum1 == 0 && areaNum2 == 0;
}

bool idRenderWorldLocal::AreasAreConnected( int areaNum1, int areaNum2, portalFlags_t ) {
	return areaNum1 == 0 && areaNum2 == 0;
}

bool idRenderWorldLocal::AreasAreConnected( int areaNum1, int areaNum2 ) {
	return areaNum1 == 0 && areaNum2 == 0;
}

// Area visibility for the renderer (docs/render-portals.md, stage 1).  The
// game-facing portal API above stays stubbed: NumPortals, SetPortalState and
// AreasAreConnected keep their old answers, so the game, its PVS, bots and
// the network see no change.  Doors therefore never block the renderer's view
// in this stage (compat ledger 072).
idCVar r_usePortals( "r_usePortals", "1", CVAR_RENDERER | CVAR_BOOL,
	"cull static models, entities and lights in areas the view cannot see through the map's portals" );

namespace {

static const int MAX_PORTAL_PLANES = 32;
static const int MAX_FLOOD_DEPTH = 48;
// Portal traversals for one view before the flood gives up and treats every
// area as visible (never wrong, only uncull).  Stock maps need far fewer.
static const int MAX_FLOOD_STEPS = 20000;

struct portalFloodContext_t {
	const idRenderWorldLocal*	world;
	idVec3						origin;
	idList< bool >*				visibleAreas;
	idList< bool >				onPath;
	int							steps;
	bool						exhausted;
};

// planes face outward (positive side outside), as viewDef_s::frustum does.
void FloodArea_r( portalFloodContext_t& context, int areaNum, const idPlane* planes, int numPlanes, int depth ) {
	( *context.visibleAreas )[ areaNum ] = true;
	if ( depth >= MAX_FLOOD_DEPTH || context.exhausted ) {
		return;
	}
	context.onPath[ areaNum ] = true;
	const idList< int >& areaPortals = context.world->BackendAreaPortals( areaNum );
	for ( int index = 0; index < areaPortals.Num() && !context.exhausted; ++index ) {
		const renderPortal_t& portal = context.world->BackendPortal( areaPortals[ index ] );
		const int otherArea = portal.areas[ 0 ] == areaNum ? portal.areas[ 1 ] : portal.areas[ 0 ];
		if ( context.onPath[ otherArea ] ) {
			continue;
		}
		if ( ++context.steps > MAX_FLOOD_STEPS ) {
			context.exhausted = true;
			break;
		}

		// the part of the portal the current frustum can see
		idWinding visible( portal.winding );
		bool clippedAway = false;
		for ( int plane = 0; plane < numPlanes; ++plane ) {
			if ( !visible.ClipInPlace( -planes[ plane ], 0.0f, true ) ) {
				clippedAway = true;
				break;
			}
		}
		if ( clippedAway || visible.GetNumPoints() < 3 ) {
			continue;
		}

		// Standing in (or very near) the portal: look through it with the
		// current frustum, unnarrowed.
		const float originDistance = portal.plane.Distance( context.origin );
		if ( idMath::Fabs( originDistance ) < 1.0f ) {
			FloodArea_r( context, otherArea, planes, numPlanes, depth + 1 );
			continue;
		}

		// Narrow the frustum to the visible part of the portal: a plane
		// through the view origin and each edge, its outside away from the
		// winding.
		idPlane narrowed[ MAX_PORTAL_PLANES ];
		int numNarrowed = 0;
		const idVec3 center = visible.GetCenter();
		const int numPoints = visible.GetNumPoints();
		for ( int point = 0; point < numPoints && numNarrowed < MAX_PORTAL_PLANES; ++point ) {
			const idVec3 a = visible[ point ].ToVec3();
			const idVec3 b = visible[ ( point + 1 ) % numPoints ].ToVec3();
			idVec3 normal = ( a - context.origin ).Cross( b - context.origin );
			if ( normal.Normalize() < 1e-6f ) {
				continue;
			}
			idPlane edgePlane( normal, -( normal * context.origin ) );
			if ( edgePlane.Distance( center ) > 0.0f ) {
				edgePlane = -edgePlane;
			}
			narrowed[ numNarrowed++ ] = edgePlane;
		}
		if ( numNarrowed < 3 ) {
			// degenerate from here (edge-on): keep the parent frustum
			FloodArea_r( context, otherArea, planes, numPlanes, depth + 1 );
			continue;
		}
		FloodArea_r( context, otherArea, narrowed, numNarrowed, depth + 1 );
	}
	context.onPath[ areaNum ] = false;
}

}

bool idRenderWorldLocal::FindVisibleAreas( const idVec3& viewOrigin, const idPlane* frustum, int numFrustumPlanes,
		idList< bool >& visibleAreas ) const {
	const int numAreas = portalAreas.Num();
	visibleAreas.SetNum( numAreas );
	for ( int area = 0; area < numAreas; ++area ) {
		visibleAreas[ area ] = true;
	}
	if ( !r_usePortals.GetBool() || portals.Num() == 0 || numAreas < 2 || frustum == NULL || numFrustumPlanes <= 0 ) {
		return false;
	}
	const int viewArea = PointInArea( viewOrigin );
	if ( viewArea < 0 || viewArea >= numAreas ) {
		return false;	// outside the map (in solid or the void): draw everything
	}
	for ( int area = 0; area < numAreas; ++area ) {
		visibleAreas[ area ] = false;
	}
	portalFloodContext_t context;
	context.world = this;
	context.origin = viewOrigin;
	context.visibleAreas = &visibleAreas;
	context.onPath.SetNum( numAreas );
	for ( int area = 0; area < numAreas; ++area ) {
		context.onPath[ area ] = false;
	}
	context.steps = 0;
	context.exhausted = false;
	const int numPlanes = Min( numFrustumPlanes, MAX_PORTAL_PLANES );
	FloodArea_r( context, viewArea, frustum, numPlanes, 0 );
	if ( context.exhausted ) {
		for ( int area = 0; area < numAreas; ++area ) {
			visibleAreas[ area ] = true;
		}
		return false;
	}
	return true;
}

bool idRenderWorldLocal::AnyAreaVisible( const int* areas, int numAreas, const idList< bool >& visibleAreas ) {
	if ( areas == NULL || numAreas <= 0 ) {
		return true;
	}
	for ( int index = 0; index < numAreas; ++index ) {
		const int area = areas[ index ];
		if ( area < 0 || area >= visibleAreas.Num() || visibleAreas[ area ] ) {
			return true;
		}
	}
	return false;
}

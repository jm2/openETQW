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

namespace {

bool ReadProcInt( idFile* file, int& value ) {
	return file != NULL && file->ReadInt( value ) == sizeof( value );
}

bool ReadProcFloat( idFile* file, float& value ) {
	return file != NULL && file->ReadFloat( value ) == sizeof( value );
}

bool ReadProcString( idFile* file, idStr& value ) {
	if ( file == NULL || file->Tell() + static_cast< int >( sizeof( int ) ) > file->Length() ) {
		return false;
	}
	const int before = file->Tell();
	file->ReadString( value );
	return file->Tell() > before && file->Tell() <= file->Length();
}

bool ReadProcBounds( idFile* file, idBounds& bounds ) {
	return file->ReadVec3( bounds[ 0 ] ) == sizeof( idVec3 ) &&
		file->ReadVec3( bounds[ 1 ] ) == sizeof( idVec3 );
}

bool ReadProcFloatArray( idFile* file, float* values, int expectedCount ) {
	int count = 0;
	if ( !ReadProcInt( file, count ) || count != expectedCount || values == NULL ) return false;
	for ( int index = 0; index < count; ++index ) {
		if ( !ReadProcFloat( file, values[ index ] ) ) return false;
	}
	return true;
}

void DeriveProcFacePlanes( srfTriangles_t* triangles ) {
	if ( triangles == NULL || triangles->numIndexes < 3 ) {
		return;
	}

	const int numFaces = triangles->numIndexes / 3;
	triangles->facePlanes = static_cast< idPlane* >( Mem_ClearedAlloc( numFaces * sizeof( idPlane ) ) );
	triangles->numAllocatedFacePlanes = numFaces;
	for ( int i = 0; i < numFaces; ++i ) {
		const int i0 = triangles->indexes[ i * 3 + 0 ];
		const int i1 = triangles->indexes[ i * 3 + 1 ];
		const int i2 = triangles->indexes[ i * 3 + 2 ];
		if ( i0 < 0 || i0 >= triangles->numVerts ||
			i1 < 0 || i1 >= triangles->numVerts ||
			i2 < 0 || i2 >= triangles->numVerts ) {
			continue;
		}
		triangles->facePlanes[ i ].FromPoints(
			triangles->verts[ i0 ].xyz,
			triangles->verts[ i1 ].xyz,
			triangles->verts[ i2 ].xyz
		);
	}
	triangles->facePlanesCalculated = true;
}

idRenderModel* ParseProcModelBinary(
	idFile* file,
	const int chunkEnd,
	const idStrList& materials,
	const bool proc009
) {
	idStr modelName;
	int numSurfaces = 0;
	if ( !ReadProcString( file, modelName ) || modelName.IsEmpty() ||
		!ReadProcInt( file, numSurfaces ) || numSurfaces < 0 || numSurfaces > 65536 ) {
		return NULL;
	}

	idRenderModel* model = renderModelManager->AllocModel();
	if ( model == NULL ) {
		return NULL;
	}
	model->InitEmpty( modelName );

	idList< int > fixedAreas;
	if ( !proc009 ) {
		int numFixedAreas = 0;
		if ( !ReadProcInt( file, numFixedAreas ) || numFixedAreas < 0 || numFixedAreas > 65536 ) {
			renderModelManager->FreeModel( model );
			return NULL;
		}
		fixedAreas.SetNum( numFixedAreas );
		for ( int i = 0; i < numFixedAreas; ++i ) {
			if ( !ReadProcInt( file, fixedAreas[ i ] ) ) {
				renderModelManager->FreeModel( model );
				return NULL;
			}
		}
		model->SetFixedAreas( fixedAreas );
	}

	idBounds modelBounds;
	modelBounds.Clear();
	bool valid = true;
	for ( int surfaceIndex = 0; valid && surfaceIndex < numSurfaces; ++surfaceIndex ) {
		int materialIndex = -1;
		bool hasVertexColors = false;
		idBounds surfaceBounds;
		int numVerts = 0;
		int numIndexes = 0;
		int numIndexTree = 0;

		valid = ReadProcInt( file, materialIndex ) &&
			materialIndex >= 0 && materialIndex < materials.Num();
		valid = valid && file->ReadBool( hasVertexColors ) == sizeof( byte );
		valid = valid && ReadProcBounds( file, surfaceBounds );
		valid = valid && ReadProcInt( file, numVerts ) && numVerts >= 0 && numVerts <= ( 1 << 24 );
		valid = valid && ReadProcInt( file, numIndexes ) && numIndexes >= 0 && numIndexes <= ( 1 << 27 );
		if ( valid && !proc009 ) {
			valid = ReadProcInt( file, numIndexTree ) && numIndexTree >= 0 && numIndexTree <= ( 1 << 24 );
		}
		if ( !valid ) {
			break;
		}

		// ETQW materials can request the wider low-range UV packing used by the
		// interaction programs (notably the MegaTexture materials).  Packing all
		// proc vertices with the regular 4096 scale makes those programs divide
		// the coordinates by 32768 and collapses the terrain onto a tiny part of
		// the MegaTexture atlas.
		const idMaterial* surfaceMaterial = declHolder.FindMaterial( materials[ materialIndex ], true );
		const bool lowRangeTexCoords = surfaceMaterial != NULL &&
			surfaceMaterial->TestMaterialFlag( MF_LOWRANGEUVCOMPRESS );

		srfTriangles_t* triangles = model->AllocSurfaceTriangles( numVerts, numIndexes );
		if ( triangles == NULL ) {
			valid = false;
			break;
		}
		triangles->bounds = surfaceBounds;
		triangles->tangentsCalculated = true;

		for ( int vertexIndex = 0; valid && vertexIndex < numVerts; ++vertexIndex ) {
			int floatCount = 0;
			valid = ReadProcInt( file, floatCount ) && floatCount >= 8 && floatCount <= 32;
			float values[ 32 ] = { 0.0f };
			if ( valid ) {
				valid = file->Read( values, floatCount * sizeof( float ) ) == floatCount * sizeof( float );
				if ( valid ) {
					LittleRevBytes( values, sizeof( float ), floatCount );
				}
			}

			int colorCount = 0;
			valid = valid && ReadProcInt( file, colorCount ) && colorCount >= 0 && colorCount <= 16;
			byte colors[ 16 ] = { 255, 255, 255, 255 };
			if ( valid && colorCount > 0 ) {
				valid = file->Read( colors, colorCount ) == colorCount;
			}
			if ( !valid ) {
				break;
			}

			idDrawVert& vertex = triangles->verts[ vertexIndex ];
			vertex.xyz.Set( values[ 0 ], values[ 1 ], values[ 2 ] );
			vertex.SetST( lowRangeTexCoords, idVec2( values[ 3 ], values[ 4 ] ) );
			vertex.SetNormal( idVec3( values[ 5 ], values[ 6 ], values[ 7 ] ) );
			if ( floatCount >= 12 ) {
				vertex.SetTangent( idVec3( values[ 8 ], values[ 9 ], values[ 10 ] ) );
				vertex.SetBiTangentSign( values[ 11 ] );
			}
			for ( int i = 0; i < 4; ++i ) {
				vertex.color[ i ] = hasVertexColors && colorCount >= 4 ? colors[ i ] : 255;
			}
		}

		for ( int index = 0; valid && index < numIndexes; ++index ) {
			int value = 0;
			valid = ReadProcInt( file, value ) && value >= 0 && value < numVerts;
			if ( valid ) {
				triangles->indexes[ index ] = static_cast< glIndex_t >( value );
			}
		}

		const int indexTreeBytes = numIndexTree * static_cast< int >( sizeof( srfIndexTree_t ) );
		if ( valid && ( indexTreeBytes < 0 || file->Tell() + indexTreeBytes > chunkEnd ) ) {
			valid = false;
		}
		if ( valid && numIndexTree > 0 ) {
			triangles->numIndexTree = numIndexTree;
			triangles->indexTree = static_cast< srfIndexTree_t* >( Mem_Alloc( indexTreeBytes ) );
			memset( triangles->indexTree, 0, indexTreeBytes );
			for ( int treeIndex = 0; valid && treeIndex < numIndexTree; ++treeIndex ) {
				srfIndexTree_t& node = triangles->indexTree[ treeIndex ];
				valid = ReadProcBounds( file, node.bb );
				int rangeStart = 0;
				int rangeEnd = 0;
				valid = valid && ReadProcInt( file, rangeStart ) && ReadProcInt( file, rangeEnd );
				valid = valid && rangeStart >= 0 && rangeStart <= rangeEnd && rangeEnd <= numIndexes && rangeEnd <= 0xFFFF;
				valid = valid && ReadProcInt( file, node.kids[ 0 ] ) && ReadProcInt( file, node.kids[ 1 ] );
				if ( !valid ) {
					break;
				}
				node.range[ 0 ] = static_cast< unsigned short >( rangeStart );
				node.range[ 1 ] = static_cast< unsigned short >( rangeEnd );
				unsigned short minimumIndex = 0;
				unsigned short maximumIndex = 0;
				if ( rangeStart < rangeEnd ) {
					minimumIndex = maximumIndex = triangles->indexes[ rangeStart ];
					for ( int index = rangeStart + 1; index < rangeEnd; ++index ) {
						minimumIndex = Min( minimumIndex, triangles->indexes[ index ] );
						maximumIndex = Max( maximumIndex, triangles->indexes[ index ] );
					}
				}
				node.range[ 2 ] = minimumIndex;
				node.range[ 3 ] = maximumIndex + 1;
			}
		}

		if ( !valid ) {
			model->FreeSurfaceTriangles( triangles );
			break;
		}

		DeriveProcFacePlanes( triangles );
		modelSurface_t surface;
		surface.id = surfaceIndex;
		surface.material = surfaceMaterial;
		surface.geometry = triangles;
		model->AddSurface( surface );
		modelBounds += surfaceBounds;
	}

	valid = valid && file->Tell() <= chunkEnd;
	if ( !valid ) {
		renderModelManager->FreeModel( model );
		return NULL;
	}

	file->Seek( chunkEnd, FS_SEEK_SET );
	model->SetBounds( modelBounds );
	model->FinishSurfaces();
	return model;
}

bool LoadProcBinary( const char* fileName, idRenderWorldLocal* world, idList< idRenderModel* >& localModels ) {
	idFile* sourceFile = fileSystem->OpenFileRead( fileName, true, NULL, true );
	if ( sourceFile == NULL ) {
		return false;
	}
	idFile* file = fileSystem->OpenBufferedFile( sourceFile );

	idStr header;
	bool valid = ReadProcString( file, header );
	const bool proc009 = valid && !header.Icmp( "mapProcFile009" );
	valid = valid && ( proc009 || !header.Icmp( "mapProcFile010" ) );
	idStrList materials;
	int modelCount = 0;

	while ( valid && file->Tell() < file->Length() ) {
		idStr section;
		valid = ReadProcString( file, section );
		if ( !valid ) {
			break;
		}

		if ( !section.Icmp( "materials" ) ) {
			int numMaterials = 0;
			valid = ReadProcInt( file, numMaterials ) && numMaterials >= 0 && numMaterials <= 65536;
			materials.SetNum( valid ? numMaterials : 0 );
			for ( int i = 0; valid && i < numMaterials; ++i ) {
				valid = ReadProcString( file, materials[ i ] ) && !materials[ i ].IsEmpty();
			}
			continue;
		}

		if ( !section.Icmp( "model" ) ) {
			int chunkLength = 0;
			valid = ReadProcInt( file, chunkLength );
			const int chunkStart = file->Tell();
			const int chunkEnd = chunkStart + chunkLength;
			valid = valid && chunkLength >= 0 && chunkEnd >= chunkStart && chunkEnd <= file->Length();
			if ( !valid ) break;
			idRenderModel* model = ParseProcModelBinary( file, chunkEnd, materials, proc009 );
			if ( model == NULL ) {
				valid = false;
				break;
			}
			renderModelManager->AddModel( model );
			localModels.Append( model );
			++modelCount;
		} else if ( !section.Icmp( "nodes" ) ) {
			valid = world != NULL && world->ParseNodes_Binary( file );
		} else if ( !section.Icmp( "interAreaPortals" ) ) {
			valid = world != NULL && world->ParseInterAreaPortals_Binary( file );
		} else if ( !section.Icmp( "megaTextureInfo" ) ) {
			valid = world != NULL && world->ParseMegatextureInfo_Binary( file );
		} else {
			// Every remaining binary proc section starts with its byte length.
			// Preserve stream alignment even while shadow models, environment
			// bounds and atmosphere projections are reconstructed separately.
			int chunkLength = 0;
			valid = ReadProcInt( file, chunkLength );
			const int chunkStart = file->Tell();
			const int chunkEnd = chunkStart + chunkLength;
			valid = valid && chunkLength >= 0 && chunkEnd >= chunkStart && chunkEnd <= file->Length() &&
				file->Seek( chunkEnd, FS_SEEK_SET ) >= 0;
		}
	}

	fileSystem->CloseFile( file );
	if ( valid ) {
		common->Printf( "Loaded %d inline render models from %s\n", modelCount, fileName );
	}
	return valid;
}


}

bool idRenderWorldLocal::InitFromMap( const char *name ) {
	Clear();
	mapName = name != NULL ? name : "";
	if ( mapName.IsEmpty() ) {
		return true;
	}

	idStr procName = mapName;
	procName.SetFileExtension( "procb" );
	if ( !LoadProcBinary( procName, this, localModels ) ) {
		common->Warning( "idRenderWorldLocal::InitFromMap: failed to load '%s'", procName.c_str() );
		for ( int i = 0; i < localModels.Num(); ++i ) {
			renderModelManager->FreeModel( localModels[ i ] );
		}
		localModels.Clear();
		return false;
	}
	if ( portalAreas.Num() == 0 ) portalAreas.SetNum( 1 );
	BuildLocalModelAreas();
	common->Printf( "%s: %d areas, %d portals\n", procName.c_str(), portalAreas.Num(), portals.Num() );
	return true;
}

bool idRenderWorldLocal::ParseNodes_Binary( idFile* file ) {
	int chunkLength = 0;
	if ( !ReadProcInt( file, chunkLength ) || chunkLength < 0 ) return false;
	const int chunkStart = file->Tell();
	const int chunkEnd = chunkStart + chunkLength;
	if ( chunkEnd < chunkStart || chunkEnd > file->Length() ) return false;

	int count = 0;
	if ( !ReadProcInt( file, count ) || count < 0 || count > ( 1 << 24 ) ) return false;
	areaNodes.SetNum( count );
	for ( int index = 0; index < count; ++index ) {
		if ( !ReadProcFloatArray( file, areaNodes[ index ].plane.ToFloatPtr(), 4 ) ||
			!ReadProcInt( file, areaNodes[ index ].children[ 0 ] ) ||
			!ReadProcInt( file, areaNodes[ index ].children[ 1 ] ) ) {
			areaNodes.Clear();
			return false;
		}
	}
	return file->Tell() <= chunkEnd && file->Seek( chunkEnd, FS_SEEK_SET ) >= 0;
}

bool idRenderWorldLocal::ParseInterAreaPortals_Binary( idFile* file ) {
	int chunkLength = 0;
	if ( !ReadProcInt( file, chunkLength ) || chunkLength < 0 ) return false;
	const int chunkStart = file->Tell();
	const int chunkEnd = chunkStart + chunkLength;
	if ( chunkEnd < chunkStart || chunkEnd > file->Length() ) return false;

	int numAreas = 0;
	int numPortals = 0;
	if ( !ReadProcInt( file, numAreas ) || numAreas < 0 || numAreas > ( 1 << 20 ) ||
		!ReadProcInt( file, numPortals ) || numPortals < 0 || numPortals > ( 1 << 24 ) ) return false;
	portalAreas.SetNum( numAreas );
	for ( int area = 0; area < numAreas; ++area ) {
		portalAreas[ area ].portals.Clear();
	}

	// Per portal (docs/render-portals.md section 3): point count, the two
	// areas, the portalFlags_t bits, an int and a vec3 that only
	// PORTAL_BLOCKAMBIENT portals use, then the points.  A portal the reader
	// cannot use is dropped; the areas stay, so culling only gets weaker.
	portals.Clear();
	portals.Resize( numPortals );
	for ( int index = 0; index < numPortals; ++index ) {
		renderPortal_t portal;
		int numPoints = 0;
		if ( !ReadProcInt( file, numPoints ) || numPoints < 0 || numPoints > 1024 ||
			!ReadProcInt( file, portal.areas[ 0 ] ) || !ReadProcInt( file, portal.areas[ 1 ] ) ||
			!ReadProcInt( file, portal.flags ) || !ReadProcInt( file, portal.unknownInt ) ||
			!ReadProcFloatArray( file, portal.unknownVec.ToFloatPtr(), 3 ) ) {
			portals.Clear();
			break;
		}
		bool pointsRead = true;
		portal.winding.SetNumPoints( 0 );
		for ( int point = 0; point < numPoints; ++point ) {
			idVec3 xyz;
			if ( !ReadProcFloatArray( file, xyz.ToFloatPtr(), 3 ) ) {
				pointsRead = false;
				break;
			}
			portal.winding.AddPoint( xyz );
		}
		if ( !pointsRead || file->Tell() > chunkEnd ) {
			portals.Clear();
			break;
		}
		if ( numPoints < 3 || portal.areas[ 0 ] < 0 || portal.areas[ 0 ] >= numAreas ||
			portal.areas[ 1 ] < 0 || portal.areas[ 1 ] >= numAreas || portal.areas[ 0 ] == portal.areas[ 1 ] ) {
			continue;
		}
		portal.winding.GetPlane( portal.plane );
		portal.bounds.Clear();
		for ( int point = 0; point < portal.winding.GetNumPoints(); ++point ) {
			portal.bounds.AddPoint( portal.winding[ point ].ToVec3() );
		}
		const int portalIndex = portals.Append( portal );
		portalAreas[ portal.areas[ 0 ] ].portals.Append( portalIndex );
		portalAreas[ portal.areas[ 1 ] ].portals.Append( portalIndex );
	}
	if ( portals.Num() == 0 ) {
		for ( int area = 0; area < numAreas; ++area ) {
			portalAreas[ area ].portals.Clear();
		}
	}
	return file->Seek( chunkEnd, FS_SEEK_SET ) >= 0;
}

void idRenderWorldLocal::BuildLocalModelAreas() {
	localModelAreas.SetNum( localModels.Num() );
	for ( int index = 0; index < localModels.Num(); ++index ) {
		idList< int >& areas = localModelAreas[ index ];
		areas.Clear();
		idRenderModel* model = localModels[ index ];
		if ( model == NULL ) {
			continue;
		}
		int areaNum = -1;
		if ( sscanf( model->Name(), "_area%d", &areaNum ) == 1 && areaNum >= 0 && areaNum < portalAreas.Num() ) {
			areas.Append( areaNum );
			continue;
		}
		const idList< int >* fixedAreas = model->GetFixedAreas();
		if ( fixedAreas == NULL ) {
			continue;
		}
		for ( int area = 0; area < fixedAreas->Num(); ++area ) {
			const int fixedArea = ( *fixedAreas )[ area ];
			if ( fixedArea < 0 || fixedArea >= portalAreas.Num() ) {
				areas.Clear();		// an area the map doesn't have: don't cull this model
				break;
			}
			areas.Append( fixedArea );
		}
	}
}

bool idRenderWorldLocal::ParseMegatextureInfo_Binary( idFile* file ) {
	int chunkLength = 0;
	if ( !ReadProcInt( file, chunkLength ) || chunkLength < 0 ) return false;
	const int chunkStart = file->Tell();
	const int chunkEnd = chunkStart + chunkLength;
	if ( chunkEnd < chunkStart || chunkEnd > file->Length() ) return false;

	idBounds bounds;
	if ( !ReadProcFloatArray( file, bounds[ 0 ].ToFloatPtr(), 3 ) ||
		!ReadProcFloatArray( file, bounds[ 1 ].ToFloatPtr(), 3 ) ) return false;
	int width = 0;
	int height = 0;
	if ( !ReadProcInt( file, width ) || !ReadProcInt( file, height ) ||
		width < 0 || height < 0 || width > ( 1 << 16 ) || height > ( 1 << 16 ) ) return false;
	const int64_t gridCount = static_cast< int64_t >( width ) * height;
	if ( gridCount > ( 1 << 26 ) || gridCount > INT_MAX ) return false;
	idList< idVec2 > grid;
	grid.SetNum( static_cast< int >( gridCount ) );
	for ( int index = 0; index < grid.Num(); ++index ) {
		if ( !ReadProcFloatArray( file, grid[ index ].ToFloatPtr(), 2 ) ) return false;
	}
	SetMegaTextureSTGrid( bounds, grid.Begin(), width, height );
	return file->Tell() <= chunkEnd && file->Seek( chunkEnd, FS_SEEK_SET ) >= 0;
}

void idRenderWorldLocal::LinkCullSectorsToArea( int area ) {
}

void idRenderWorldLocal::SetMegaTextureSTGrid( const idBounds& bounds, const idVec2* grid, int width, int height ) {
	megaTextureBounds = bounds;
	megaTextureSTGridWidth = Max( 0, width );
	megaTextureSTGridHeight = Max( 0, height );
	const int count = megaTextureSTGridWidth * megaTextureSTGridHeight;
	megaTextureSTGrid.SetNum( count, false );
	if ( count > 0 && grid != NULL ) {
		memcpy( megaTextureSTGrid.Begin(), grid, count * sizeof( grid[ 0 ] ) );
	} else if ( count > 0 ) {
		memset( megaTextureSTGrid.Begin(), 0, count * sizeof( megaTextureSTGrid[ 0 ] ) );
	}
}

atmosLightProjection_t *idRenderWorldLocal::FindAtmosLightProjection( int ) {
	return NULL;
}

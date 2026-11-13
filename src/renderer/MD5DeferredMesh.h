// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RENDERER_MD5DEFERREDMESH_H__
#define __RENDERER_MD5DEFERREDMESH_H__

/*
====================
R_CompactDeferredMeshIndexes

A deferred mesh in a binary MD5 model (generated/md5binary/<name>.md5b) owns only an index
range into the vertices of the first mesh with its material and noAnimate flag. Each
mesh is drawn as its own surface (#461) so that hiding one hides only that mesh: a
vehicle part hides its mesh by name when it breaks off, and with the meshes merged into
the first one's surface a mirror took the whole hull with it.

This gives the mesh its own vertex list: the source vertices its indexes use, in the
order of first use. sourceOfCompact[k] is the source vertex of compact vertex k, and
compactIndexes[i] is index i renumbered into the compact list. sourceOfCompact and
compactIndexes hold numIndexes entries, remapScratch numSourceVerts. Returns the number
of compact vertices, or -1 when an index is outside the source vertices.
====================
*/
inline int R_CompactDeferredMeshIndexes( const unsigned short* indexes, int numIndexes, int numSourceVerts,
	int* sourceOfCompact, int* compactIndexes, int* remapScratch ) {
	if ( numIndexes < 0 || numSourceVerts < 0 ) {
		return -1;
	}
	for ( int i = 0; i < numSourceVerts; i++ ) {
		remapScratch[ i ] = -1;
	}
	int numCompact = 0;
	for ( int i = 0; i < numIndexes; i++ ) {
		const int source = indexes[ i ];
		if ( source >= numSourceVerts ) {
			return -1;
		}
		if ( remapScratch[ source ] < 0 ) {
			remapScratch[ source ] = numCompact;
			sourceOfCompact[ numCompact ] = source;
			numCompact++;
		}
		compactIndexes[ i ] = remapScratch[ source ];
	}
	return numCompact;
}

#endif /* !__RENDERER_MD5DEFERREDMESH_H__ */

// SPDX-License-Identifier: GPL-3.0-or-later
// Cutting a GUI draw's polygon to a rectangle (a window's clip rectangle, or the
// range of a masked draw's mask coordinates).

#ifndef __GUI_CLIP_H__
#define __GUI_CLIP_H__

// Clips the winding to bounds against the four edges in turn (Sutherland-Hodgman),
// interpolating its texture coordinates; true while a polygon is left. idlib's
// clippers are not used: idWinding2D::ClipByBounds cuts each edge at one plane
// only, so a polygon that overhangs two opposite sides comes back as a triangle or
// nothing, and ClipInPlace leaves the texture coordinates of a point it adds on an
// axis-aligned plane unset.
inline bool GuiClipWindingToBounds( idWinding2D& winding, const sdBounds2D& bounds ) {
	if ( winding.GetNumPoints() < 3 ) {
		return false;
	}
	sdBounds2D windingBounds;
	winding.GetBounds( windingBounds );
	if ( bounds.ContainsBounds( windingBounds ) ) {
		return true;
	}
	// each edge clip adds at most one point
	const int maxPoints = idWinding2D::MAX_POINTS + 4;
	idVec2 xy[ 2 ][ maxPoints ];
	idVec2 st[ 2 ][ maxPoints ];
	int count = winding.GetNumPoints();
	for ( int i = 0; i < count; i++ ) {
		xy[ 0 ][ i ] = winding[ i ];
		st[ 0 ][ i ] = winding.GetST( i );
	}
	int from = 0;
	for ( int edge = 0; edge < 4; edge++ ) {
		const int axis = edge >> 1;
		const bool keepAbove = ( edge & 1 ) == 0;
		const float limit = keepAbove ? bounds.GetMins()[ axis ] : bounds.GetMaxs()[ axis ];
		const int to = from ^ 1;
		int out = 0;
		for ( int i = 0; i < count; i++ ) {
			const int j = ( i + 1 ) % count;
			const float di = keepAbove ? xy[ from ][ i ][ axis ] - limit : limit - xy[ from ][ i ][ axis ];
			const float dj = keepAbove ? xy[ from ][ j ][ axis ] - limit : limit - xy[ from ][ j ][ axis ];
			if ( di >= 0.0f ) {
				xy[ to ][ out ] = xy[ from ][ i ];
				st[ to ][ out ] = st[ from ][ i ];
				out++;
			}
			if ( ( di >= 0.0f ) != ( dj >= 0.0f ) ) {
				const float f = di / ( di - dj );
				xy[ to ][ out ] = xy[ from ][ i ] + f * ( xy[ from ][ j ] - xy[ from ][ i ] );
				xy[ to ][ out ][ axis ] = limit;
				st[ to ][ out ] = st[ from ][ i ] + f * ( st[ from ][ j ] - st[ from ][ i ] );
				out++;
			}
		}
		count = out;
		from = to;
		if ( count < 3 ) {
			winding.Clear();
			return false;
		}
	}
	// a polygon that only touches the bounds from outside is left with no area
	float area = 0.0f;
	for ( int i = 0; i < count; i++ ) {
		const idVec2& a = xy[ from ][ i ];
		const idVec2& b = xy[ from ][ ( i + 1 ) % count ];
		area += a.x * b.y - b.x * a.y;
	}
	if ( idMath::Fabs( area ) < 1.0e-4f ) {
		winding.Clear();
		return false;
	}
	if ( count > idWinding2D::MAX_POINTS ) {
		count = idWinding2D::MAX_POINTS;
	}
	winding.Clear();
	for ( int i = 0; i < count; i++ ) {
		winding.AddPoint( xy[ from ][ i ], st[ from ][ i ] );
	}
	return true;
}

#endif /* !__GUI_CLIP_H__ */

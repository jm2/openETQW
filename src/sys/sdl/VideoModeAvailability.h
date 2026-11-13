// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_VIDEOMODEAVAILABILITY_H__
#define __SYS_VIDEOMODEAVAILABILITY_H__

/*
====================
Sys_MarkAvailableVideoModes

The game lists only the modes marked available when fullscreen is selected
(idGameLocal::CreateVideoModeList: "don't show modes that the monitor says it
doesn't support"), so a table mode is available when the display lists its exact
size. displaySizes holds width, height pairs in pixels; a display that reports
scaled modes (macOS) is passed both its point and its pixel sizes. Returns how
many modes are available.
====================
*/
inline int Sys_MarkAvailableVideoModes( vidmode_t* modes, int numModes, const int* displaySizes, int numDisplaySizes ) {
	int available = 0;
	for ( int i = 0; i < numModes; i++ ) {
		modes[i].available = false;
		for ( int j = 0; j < numDisplaySizes; j++ ) {
			if ( displaySizes[j * 2] == modes[i].width && displaySizes[j * 2 + 1] == modes[i].height ) {
				modes[i].available = true;
				available++;
				break;
			}
		}
	}
	return available;
}

/*
====================
Sys_NearestVideoModeAspect

The aspect index (the SDK's 0 4:3, 1 16:9, 2 16:10, 3 5:4; the menu prints one of
these four after each size) nearest to width / height, compared as ratios, so a
3456x2234 panel is 16:10 and a 3440x1440 one 16:9.
====================
*/
inline int Sys_NearestVideoModeAspect( int width, int height ) {
	static const double ratios[ 4 ] = { 4.0 / 3.0, 16.0 / 9.0, 16.0 / 10.0, 5.0 / 4.0 };
	if ( width <= 0 || height <= 0 ) {
		return 0;
	}
	const double ratio = static_cast< double >( width ) / static_cast< double >( height );
	int best = 0;
	double bestScore = 0.0;
	for ( int i = 0; i < 4; i++ ) {
		const double score = ratio > ratios[i] ? ratio / ratios[i] : ratios[i] / ratio;
		if ( i == 0 || score < bestScore ) {
			best = i;
			bestScore = score;
		}
	}
	return best;
}

/*
====================
Sys_AddNativeVideoMode

The r_mode table has no entry for many panels' native size (#455; owner,
2026-11-12, ledger 087). modes holds numModes fixed entries and room for one more:
unless one of them already has width x height, the extra entry becomes the
display's native size, described in the description buffer, and the table grows
by one. Returns the table's new size (numModes, or numModes + 1).
====================
*/
inline int Sys_AddNativeVideoMode( vidmode_t* modes, int numModes, char* description, int descriptionSize,
		int width, int height ) {
	if ( width <= 0 || height <= 0 ) {
		return numModes;
	}
	for ( int i = 0; i < numModes; i++ ) {
		if ( modes[i].width == width && modes[i].height == height ) {
			return numModes;
		}
	}
	idStr::snPrintf( description, descriptionSize, "Mode %2d: %dx%d (native)", numModes, width, height );
	vidmode_t& native = modes[ numModes ];
	native.description = description;
	native.width = width;
	native.height = height;
	native.aspectRatio = Sys_NearestVideoModeAspect( width, height );
	native.available = false;
	return numModes + 1;
}

#endif // !__SYS_VIDEOMODEAVAILABILITY_H__

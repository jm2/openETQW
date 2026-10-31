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

#endif // !__SYS_VIDEOMODEAVAILABILITY_H__

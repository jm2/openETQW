// SPDX-License-Identifier: GPL-3.0-or-later
// Copied unchanged from openQ4 src/sys/osx/macosx_common.h (jm2/openQ4 b37b1456; GPL-3.0-or-later, owner decision 2026-10-07).

const char *macosx_scanForLibraryDirectory(void);

#if defined(USE_SDL3)
struct SDL_Window;

// SDL does not currently expose Cocoa decoration extents through
// SDL_GetWindowBordersSize. Supply the same logical-coordinate values from
// AppKit so the shared SDL3 window manager can reason about the complete frame.
bool Sys_SDL_GetNativeWindowBorders( SDL_Window *window, int *top, int *left, int *bottom, int *right );
#endif


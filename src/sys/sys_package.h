// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __SYS_PACKAGE_H__
#define __SYS_PACKAGE_H__

/*
Where an installed or packaged engine keeps its game module. The staged SDK
sys_public.h cannot declare this, so the engine declares it here.

macOS: Contents/Frameworks of a self-contained openETQW.app (macosx_compat.mm).
Linux and Windows packages put the module next to the executable, which
FindDLL already searches, so there it returns false.
*/
bool Sys_GetGameModuleRootDirectory( char *moduleRoot, int moduleRootSize );

#endif /* !__SYS_PACKAGE_H__ */

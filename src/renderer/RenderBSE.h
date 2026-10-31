// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#ifndef __RENDER_BSE_H__
#define __RENDER_BSE_H__

#include "../idlib/precompiled.h"

// The renderer's view of the BSE particle system (src/bse, #368): the effect
// simulation (bseSimulation), the per-frame particles (rvBSEParticle), the
// render preparation (bse->PrepareRender) and the geometry (BSE_BuildRenderModel).
// The engine-side types are named bse* so that they never meet the SDK's
// bse/BSE.h, which the game module and src/decllib/declEffect.cpp use; the two
// headers must not be included in the same file (their enumerators collide).
#include "../bse/BSESim.h"

#endif

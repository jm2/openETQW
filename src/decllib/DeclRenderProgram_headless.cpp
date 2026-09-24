// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Headless render-program backend for dedicated server.

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "DeclRenderProgram_opengl.h"
#include "declRenderProgram.h"
#include "../renderer/RendererTypesImpl.h"

sdRenderShader* SD_AllocRenderShader( const char* type ) {
	return NULL;
}

bool SD_HasRenderShaderBackend() {
	return false;
}

void SD_UnbindRenderProgram() {
}

void SD_ApplyRenderProgramState( int stateBits, cullType_t cullType ) {
}

void sdRenderShader::ProcessRenderBindings() {
}


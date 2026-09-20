// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
//
// rvDeclEffect declaration implementation.

#include "../idlib/precompiled.h"
#pragma hdrstop

#include "BSE.h"
#include "../framework/DeclManager.h"
#include "../framework/DeclParseHelper.h"

void rvDeclEffect::Init( void ) {
	FreeData();
}

bool rvDeclEffect::SetDefaultText( void ) {
	SetText( va( "effect %s // IMPLICITLY GENERATED\n%s", GetName(), DefaultDefinition() ) );
	return true;
}

const char *rvDeclEffect::DefaultDefinition( void ) const {
	return "{ }";
}

void rvDeclEffect::FreeData( void ) {
	mFlags = 0;
	mMinDuration = 0.0f;
	mMaxDuration = 0.0f;
	mCutOffDistance = 0.0f;
	mSize = 8.0f;
	mSegmentTemplates.Clear();
	mPlayCount = 0;
	mLoopCount = 0;
}

size_t rvDeclEffect::Size( void ) const {
	return sizeof( *this );
}

bool rvDeclEffect::Parse( const char *text, const int textLength ) {
	FreeData();
	if ( ( text == NULL || textLength <= 0 ) && !HasBinaryBuffer() ) {
		return false;
	}
	idParser src;
	src.SetFlags( DECL_LEXER_FLAGS );
	sdDeclParseHelper declHelper( this, text, textLength, src );
	idToken token;
	if ( !src.SkipUntilString( "{", &token ) ) {
		return false;
	}
	return src.SkipBracedSection( false ) != 0;
}

void rvDeclEffect::CacheFromDict( const idDict& dict ) {
	const qhandle_t effectType = declManager->GetDeclTypeHandle( declEffectsIdentifier );
	const idKeyValue *kv = NULL;
	while ( ( kv = dict.MatchPrefix( "fx", kv ) ) != NULL ) {
		const char *effectName = kv->GetValue().c_str();
		if ( effectName[0] == '\0' ) {
			continue;
		}
		declManager->FindType( effectType, effectName, true );
	}
}

bool rvDeclEffect::Compare( const rvDeclEffect& comp ) const {
	return false;
}

rvDeclEffect& rvDeclEffect::operator=( const rvDeclEffect& copy ) {
	mFlags = copy.mFlags;
	mMinDuration = copy.mMinDuration;
	mMaxDuration = copy.mMaxDuration;
	mCutOffDistance = copy.mCutOffDistance;
	mSize = copy.mSize;
	mPlayCount = copy.mPlayCount;
	mLoopCount = copy.mLoopCount;
	return *this;
}

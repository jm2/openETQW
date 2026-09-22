// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Parser for the image reference that follows a texture keyword in a material
// stage ("map", "diffusemap", any texture renderBinding name, ...) or in a
// renderBinding "texture { ... }" block: an optional list of image options on
// the same line, then an image name or image program.
//
// The client's idImageManager::ParseImage and the dedicated server's headless
// ParseImage both use this, so the two consume exactly the same tokens.  When
// the headless version consumed only the first token, the options after it
// ("clamp", "forceHighQuality", ...) were left in the stream and parsed as
// unknown material keywords, which defaulted the material on the dedicated
// server and threw away its contents and surface flags.

#ifndef __RENDERER_IMAGEPARSE_H__
#define __RENDERER_IMAGEPARSE_H__

/*
====================
R_ParseImageReference

Applies the image options to parms and returns the image name or program in
imageProgram.  Returns false if the reference is malformed (a bad colormipmaps
matrix, a missing name, or an unterminated image program).
====================
*/
ID_INLINE bool R_ParseImageReference( idParser& src, imageParams_t& parms, bool ignoreHighQuality, idStr& imageProgram ) {
	idToken token;
	while ( src.ReadTokenOnLine( &token ) ) {
		if ( token.Icmp( "bumpMap" ) == 0 ) {
			parms.td = TD_BUMP;
		} else if ( token.Icmp( "diffuseMap" ) == 0 ) {
			parms.td = TD_DIFFUSE;
		} else if ( token.Icmp( "specularMap" ) == 0 ) {
			parms.td = TD_SPECULAR;
		} else if ( token.Icmp( "cubeMap" ) == 0 ) {
			parms.cubeMap = CF_NATIVE;
		} else if ( token.Icmp( "cameraCubeMap" ) == 0 ) {
			parms.cubeMap = CF_CAMERA;
		} else if ( token.Icmp( "halfSphereMap" ) == 0 ) {
			parms.cubeMap = CF_HALFSPHERE;
		} else if ( token.Icmp( "nearest" ) == 0 ) {
			parms.tf = TF_NEAREST;
		} else if ( token.Icmp( "linear" ) == 0 ) {
			parms.tf = TF_LINEAR;
		} else if ( token.Icmp( "linearNearest" ) == 0 ) {
			parms.tf = TF_LINEARNEAREST;
		} else if ( token.Icmp( "waternormal" ) == 0 ) {
			parms.mipState.colorType = mipmapState_t::MT_WATER;
		} else if ( token.Icmp( "colormipmaps" ) == 0 ) {
			parms.mipState.colorType = mipmapState_t::MT_COLORLEVELS;
			if ( !src.Parse2DMatrix( 2, 4, parms.mipState.color ) ) {
				return false;
			}
		} else if ( token.Icmp( "mirror" ) == 0 ) {
			parms.trp = TR_MIRROR;
		} else if ( token.Icmp( "mirror_x" ) == 0 ) {
			parms.trp = TR_MIRROR_X;
		} else if ( token.Icmp( "mirror_y" ) == 0 ) {
			parms.trp = TR_MIRROR_Y;
		} else if ( token.Icmp( "clamp" ) == 0 ) {
			parms.trp = TR_CLAMP;
		} else if ( token.Icmp( "clamp_x" ) == 0 ) {
			// Retail ETQW names the one-axis modes for the direction that is
			// allowed to repeat, not the GL coordinate that is clamped.  The
			// skybox side panorama depends on clamp_x repeating S while clamping T.
			parms.trp = TR_CLAMP_Y;
		} else if ( token.Icmp( "clamp_y" ) == 0 ) {
			parms.trp = TR_CLAMP_X;
		} else if ( token.Icmp( "noclamp" ) == 0 ) {
			parms.trp = TR_REPEAT;
		} else if ( token.Icmp( "zeroclamp" ) == 0 ) {
			parms.trp = TR_CLAMP_TO_ZERO;
		} else if ( token.Icmp( "alphazeroclamp" ) == 0 ) {
			parms.trp = TR_CLAMP_TO_ZERO_ALPHA;
		} else if ( token.Icmp( "forceHighQuality" ) == 0 ) {
			parms.td = TD_HIGH_QUALITY;
		} else if ( token.Icmp( "uncompressed" ) == 0 || token.Icmp( "highquality" ) == 0 ) {
			if ( !ignoreHighQuality ) {
				parms.td = TD_HIGH_QUALITY;
			}
		} else if ( token.Icmp( "nopicmip" ) == 0 ) {
			parms.allowPicmip = false;
		} else if ( token.Icmp( "picmip" ) == 0 ) {
			parms.allowPicmip = true;
			parms.picmipofs = src.ParseInt();
		} else if ( token.Icmp( "picmipmin" ) == 0 ) {
			parms.picMipMin = src.ParseInt();
		} else if ( token.Icmp( "anisotropy" ) == 0 ) {
			parms.anisotropy = src.ParseFloat();
		} else if ( token.Icmp( "minLod" ) == 0 ) {
			parms.minLod = src.ParseFloat();
		} else if ( token.Icmp( "maxLod" ) == 0 ) {
			parms.maxLod = src.ParseFloat();
		} else if ( token.Icmp( "partialLoad" ) == 0 ) {
			parms.partialLoad = true;
		} else {
			src.UnreadToken( token );
			break;
		}
	}

	// The image name or image program; a program's parenthesised arguments are
	// concatenated so it can be used as a hash key.
	if ( !src.ReadToken( &token ) ) {
		return false;
	}
	imageProgram = token;
	if ( !src.PeekTokenString( "(" ) ) {
		return true;
	}

	int depth = 0;
	while ( src.ReadToken( &token ) ) {
		imageProgram += token;
		if ( token == "(" ) {
			++depth;
		} else if ( token == ")" ) {
			if ( --depth == 0 ) {
				return true;
			}
		}
	}
	return false;
}

#endif /* !__RENDERER_IMAGEPARSE_H__ */

// SPDX-License-Identifier: GPL-3.0-or-later
// The colour a GUI stage is drawn with on Vulkan, as retail's GUI render programs
// compute it.

#ifndef __GUI_STAGE_COLOR_H__
#define __GUI_STAGE_COLOR_H__

// Retail's trivial programs (trivialWithTextureMatrix and the like) draw with
// diffuseColor x ( vertex colour x colorModulate + colorAdd ): the stage's colour
// vector times the draw's colour as the stage's vertexColor mode applies it.
// trivialMaskImage passes the draw's colour through as it is (outData.col =
// indata.color), ignoring both the colour vector and the mode (#398).
inline idVec4 GuiStageColor( const idVec4& stageColor, stageVertexColor_t mode,
	const idVec4& vertexColor, bool rawVertexColor ) {
	if ( rawVertexColor ) {
		return vertexColor;
	}
	idVec4 color = stageColor;
	switch ( mode ) {
		case SVC_MODULATE:
			color.x *= vertexColor.x;
			color.y *= vertexColor.y;
			color.z *= vertexColor.z;
			color.w *= vertexColor.w;
			break;
		case SVC_MODULATE_ALPHA:
			color.w *= vertexColor.w;
			break;
		case SVC_INVERSE_MODULATE:
			color.x *= 1.0f - vertexColor.x;
			color.y *= 1.0f - vertexColor.y;
			color.z *= 1.0f - vertexColor.z;
			color.w *= 1.0f - vertexColor.w;
			break;
		default:
			break;
	}
	return color;
}

// True for a stage whose program takes the draw's colour as it is.
inline bool GuiStageUsesRawVertexColor( const char* programName ) {
	return programName != NULL && idStr::Icmp( programName, "trivialMaskImage" ) == 0;
}

#endif // !__GUI_STAGE_COLOR_H__

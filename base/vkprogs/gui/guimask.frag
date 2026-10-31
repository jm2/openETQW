#version 450

// retail's trivialMaskImage (#379): the stage's map times its mask, sampled at
// the draw's mask coordinates, times the colour. The HUD command map's mask
// (textures/common/cmmask) is what keeps its territory outlines in its circle

layout( set = 0, binding = 0 ) uniform sampler2D guiTexture;
layout( set = 1, binding = 0 ) uniform sampler2D guiMask;

layout( push_constant ) uniform GuiPushConstants {
	vec4 transform;
	vec4 color;
	vec4 mode;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in vec2 fragmentMaskCoord;

layout( location = 0 ) out vec4 outColor;

void main() {
	outColor = texture( guiTexture, fragmentTexCoord ) *
		texture( guiMask, fragmentMaskCoord ) * fragmentColor;
}

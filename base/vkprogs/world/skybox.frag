#version 450

layout( set = 0, binding = 0 ) uniform sampler2D skySideTexture;
layout( set = 0, binding = 1 ) uniform sampler2D skyTopTexture;

layout( push_constant ) uniform SkyboxPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 textureMatrixS;
	vec4 textureMatrixT;
	vec4 parameters;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in float fragmentSideBlend;
layout( location = 0 ) out vec4 outColor;

void main() {
	// This is the retail skies/skybox composition.  ETQW's box-dome mesh uses
	// vertex red to feather between a square zenith texture and a four-face
	// horizontal strip; treating either image as a regular diffuse map exposes
	// the atlas and produces the broken sky seen in the Vulkan fallback.
	vec4 top = texture( skyTopTexture, fragmentTexCoord );
	// The side strip holds the dome's four faces side by side: the mesh's side
	// coordinates run past 1 after retail's (0.75, 1.0) offset, so both wrap
	// around the strip. Clamping U pinned three of the faces to the strip's
	// edge column (horizontal streaks once the dome's texture coordinates were
	// retail's own; issue #121).
	// The mip level comes from the unwrapped coordinate: fract()'s jump at a
	// face edge would otherwise pick the smallest mip for the 2x2 quads along it
	// (a dashed pale line on every box edge).
	vec2 unwrapped = fragmentTexCoord + vec2( 0.75, 1.0 );
	vec4 side = textureGrad( skySideTexture, fract( unwrapped ), dFdx( unwrapped ), dFdy( unwrapped ) );
	outColor = mix( top, side, fragmentSideBlend ) * pc.color;
}

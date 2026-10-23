#version 450

layout( set = 0, binding = 0 ) uniform sampler2D guiTexture;

layout( push_constant ) uniform GuiPushConstants {
	vec4 transform;
	vec4 color;
	vec4 mode;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;

layout( location = 0 ) out vec4 outColor;

void main() {
	vec4 texel = texture( guiTexture, fragmentTexCoord );
	if ( pc.mode.x > 0.5 ) {
		// retail's trivialMakeAlpha* programs: white, with alpha the texel's
		// RGB summed at weight 0.333 each
		texel = vec4( 1.0, 1.0, 1.0, dot( texel.rgb, vec3( 0.333 ) ) );
	}
	outColor = texel * fragmentColor;
}

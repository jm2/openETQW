#version 450

layout( set = 0, binding = 0 ) uniform sampler2D ambientTexture;

layout( push_constant ) uniform AmbientPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 textureMatrixS;
	vec4 textureMatrixT;
	vec4 parameters;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in float fragmentLighting;

layout( location = 0 ) out vec4 outColor;

// 1 in an opaque base pass: alpha is then the postprocess glow's weight, 0 as
// retail's lit replace writes it, or 1 (the clear's) for an alpha-tested surface
layout( constant_id = 0 ) const bool OPAQUE_BASE = false;

void main() {
	vec4 sampled = texture( ambientTexture, fragmentTexCoord );
	if ( pc.parameters.w > 0.5 && sampled.a < pc.parameters.z ) {
		discard;
	}
	outColor = sampled * fragmentColor;
	if ( OPAQUE_BASE ) {
		outColor.a = ( pc.parameters.w > 0.5 ? 1.0 : 0.0 );
	}
}

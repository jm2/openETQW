#version 450

// The atmosphere fog for the rasterized lighting path (#233), drawn over each lit
// opaque surface after its interactions with interaction.vert's geometry.  Retail
// fogs inside its lit programs (r_megaDrawMethod != 0): the ambient pass lerps
// toward $fogColor by f = distance * fogDepths.z + fogDepths.w (the distance from
// the view origin, clamped to 0..1) and each additive light pass scales by 1 - f,
// so a lit pixel ends as lit * (1 - f) + fogColor * f; this pass draws exactly that
// over the summed lighting.  Retail computes the distance per vertex; here it is
// per pixel from the interpolated position.
// Blend: src * 1 + dst * (1 - src.a); destination alpha is kept.

layout( set = 0, binding = 0 ) uniform sampler2D diffuseTexture;

layout( push_constant ) uniform FogPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec3 textureMatrixS;
	uint packedRotationXY;
	vec3 textureMatrixT;
	uint packedRotationZW;
	vec4 parameters;		// z: alpha test reference, w: 1 when the stage alpha tests
	vec4 viewOriginFogScale;	// the view origin in model space; w: fogDepths.z, 1 / (end - start)
	vec4 fogColorBias;		// the atmosphere's fog colour; w: fogDepths.w, -start / (end - start)
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 2 ) in vec3 fragmentPosition;
layout( location = 0 ) out vec4 outColor;

void main() {
	// the base pass's alpha test, so the holes keep the fog of what is behind them
	if ( pc.parameters.w > 0.5 && texture( diffuseTexture, fragmentTexCoord ).a < pc.parameters.z ) {
		discard;
	}
	float fog = clamp( length( fragmentPosition - pc.viewOriginFogScale.xyz ) * pc.viewOriginFogScale.w +
		pc.fogColorBias.w, 0.0, 1.0 );
	outColor = vec4( pc.fogColorBias.rgb * fog, fog );
}

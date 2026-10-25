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

// The 16x16 ordered-dither threshold of _diffusionMask (Image_init.cpp), 1/256..1.
float DiffusionMask( ivec2 pixel ) {
	int x = pixel.x & 15;
	int xy = x ^ ( pixel.y & 15 );
	int rank = ( ( xy & 1 ) << 7 ) | ( ( x & 1 ) << 6 ) | ( ( xy & 2 ) << 4 ) | ( ( x & 2 ) << 3 ) |
		( ( xy & 4 ) << 1 ) | ( x & 4 ) | ( ( xy & 8 ) >> 2 ) | ( ( x & 8 ) >> 3 );
	return float( min( 255, rank + 1 ) ) / 255.0;
}

void main() {
	vec4 sampled = texture( ambientTexture, fragmentTexCoord );
	if ( pc.parameters.w > 0.5 && sampled.a < pc.parameters.z ) {
		discard;
	}
	if ( pc.parameters.w > 1.5 ) {
		// stuff grass (#256): the vertex alpha is the instance's fade, discarded
		// against the dither as retail's stuff/grass_alphatestcg does with $noiseMap
		if ( fragmentColor.a < DiffusionMask( ivec2( gl_FragCoord.xy ) ) ) {
			discard;
		}
		outColor = vec4( sampled.rgb * fragmentColor.rgb, sampled.a );
		return;
	}
	outColor = sampled * fragmentColor;
}

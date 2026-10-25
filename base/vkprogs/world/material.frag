#version 450

layout( set = 0, binding = 0 ) uniform sampler2D diffuseTexture;
layout( set = 0, binding = 1 ) uniform sampler2D bumpTexture;
layout( set = 0, binding = 2 ) uniform sampler2D specularTexture;
layout( set = 0, binding = 3 ) uniform sampler2D selfIllumTexture;
layout( set = 0, binding = 4 ) uniform samplerCube ambientCubeTexture;

layout( push_constant ) uniform MaterialPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 textureMatrixS;
	vec4 textureMatrixT;
	vec4 parameters;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in vec3 fragmentSunDirection;
layout( location = 3 ) in vec3 fragmentTangent;
layout( location = 4 ) in vec3 fragmentBitangent;
layout( location = 5 ) in vec3 fragmentNormal;
layout( location = 0 ) out vec4 outColor;

// 1 in an opaque base pass: alpha is then the postprocess glow's weight, 0 as
// retail's lit replace writes it, or 1 (the clear's) for an alpha-tested surface
layout( constant_id = 0 ) const bool OPAQUE_BASE = false;

void main() {
	vec4 diffuse = texture( diffuseTexture, fragmentTexCoord );
	if ( pc.parameters.w > 0.5 && diffuse.a < pc.parameters.z ) {
		discard;
	}
	vec3 selfIllum = texture( selfIllumTexture, fragmentTexCoord ).rgb;
	// Direct and ambient illumination are applied once, after opaque visibility,
	// by vkprogs/raytracing/lighting.comp.  This pass only supplies material
	// albedo and authored emission to that ray-query lighting stage; it doesn't
	// read the bump map (binding 1).
	outColor = vec4( ( diffuse.rgb + selfIllum ) * fragmentColor.rgb,
		diffuse.a * fragmentColor.a );
	if ( OPAQUE_BASE ) {
		outColor.a = ( pc.parameters.w > 0.5 ? 1.0 : 0.0 );
	}
}

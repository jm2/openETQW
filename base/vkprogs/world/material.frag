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

void main() {
	vec4 diffuse = texture( diffuseTexture, fragmentTexCoord );
	if ( pc.parameters.w > 0.5 && diffuse.a < pc.parameters.z ) {
		discard;
	}
	// Normal maps reach Vulkan with X and Y in red and green (retail's ATI2/BC5
	// decodes to (X, Y, 0, 255), RXGB to (X, Y, Z, 255)); positive Z is rebuilt.
	vec4 packedNormal = texture( bumpTexture, fragmentTexCoord );
	vec2 tangentXY = packedNormal.rg * 2.0 - 1.0;
	float tangentZ = sqrt( max( 0.0, 1.0 - dot( tangentXY, tangentXY ) ) );
	vec3 tangentNormal = normalize( vec3( tangentXY, tangentZ ) );
	vec3 selfIllum = texture( selfIllumTexture, fragmentTexCoord ).rgb;
	// Direct and ambient illumination are applied once, after opaque visibility,
	// by vkprogs/raytracing/lighting.comp.  This pass only supplies material
	// albedo and authored emission to that ray-query lighting stage.
	outColor = vec4( ( diffuse.rgb + selfIllum ) * fragmentColor.rgb,
		diffuse.a * fragmentColor.a );
}

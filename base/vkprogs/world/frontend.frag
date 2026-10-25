#version 450

// Retail 1.5's front-end ambient programs, ported from their ARB fragment
// programs (renderprogs sfx/feAmbDecal and sfx/feAmbDecal_cloud): the main
// menu's globe and its clouds.  They are lit only by the entity's ambient cube
// map, with a rim term and, for the globe, the night lights where the cube map
// is dark.  sfx/atmos, the atmosphere shell around them, is added on top (its
// material blends GL_ONE, GL_ONE).  mode: 0 globe, 1 clouds, 2 atmosphere.

layout( set = 0, binding = 0 ) uniform sampler2D diffuseMap;
layout( set = 0, binding = 1 ) uniform sampler2D bumpMap;
layout( set = 0, binding = 2 ) uniform sampler2D specularMap;
layout( set = 0, binding = 3 ) uniform sampler2D selfIllumMap;
layout( set = 0, binding = 4 ) uniform samplerCube ambientCubeMap;
layout( set = 0, binding = 5 ) uniform samplerCube specularCubeMap;

layout( push_constant ) uniform FrontEndPushConstants {
	mat4 modelViewProjection;
	vec4 parameters;
	vec3 textureMatrixS;
	uint packedRotationXY;
	vec3 textureMatrixT;
	uint packedRotationZW;
	float vertexColorModulate;
	float texCoordScale;
	float ambientBrightness;
	float mode;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in vec3 fragmentToEye;
layout( location = 3 ) in vec3 fragmentTangent;
layout( location = 4 ) in vec3 fragmentBitangent;
layout( location = 5 ) in vec3 fragmentNormal;
layout( location = 0 ) out vec4 outColor;

// 1 in an opaque base pass: alpha is then the postprocess glow's weight, 0 as
// retail's lit replace writes it, or 1 (the clear's) for an alpha-tested surface
layout( constant_id = 0 ) const bool OPAQUE_BASE = false;

vec3 WorldNormal() {
	// Normal maps reach Vulkan with X and Y in red and green (see interaction.frag);
	// Z is rebuilt, as the ARB programs do under r_normalizeNormalMaps.
	vec2 tangentXY = texture( bumpMap, fragmentTexCoord ).rg * 2.0 - 1.0;
	vec3 localNormal = vec3( tangentXY,
		sqrt( max( 0.0, 1.0 - dot( tangentXY, tangentXY ) ) ) );
	return fragmentTangent * localNormal.x + fragmentBitangent * localNormal.y +
		fragmentNormal * localNormal.z;
}

void main() {
	int mode = int( pc.mode + 0.5 );
	vec3 worldNormal = WorldNormal();
	float facing = dot( fragmentToEye, worldNormal );

	if ( mode == 2 ) {
		// sfx/atmos: sat( 5 (N.V)^2 ) by the stage's parameters, alpha 1
		float glow = clamp( facing * facing * 5.0, 0.0, 1.0 );
		outColor = vec4( glow * pc.parameters.rgb, 1.0 );
		return;
	}

	vec4 diffuse = texture( diffuseMap, fragmentTexCoord );
	if ( mode == 0 && diffuse.a - 0.001 < 0.0 ) {
		discard;
	}
	vec4 ambient = texture( ambientCubeMap, worldNormal ) * pc.ambientBrightness * 2.0;
	vec4 color = diffuse * ambient;

	// specular from the cube map along the reflection, scaled by the specular map * 2
	vec3 reflection = worldNormal * ( 2.0 * facing ) - fragmentToEye;
	vec4 specular = texture( specularCubeMap, reflection );
	color += 2.0 * texture( specularMap, fragmentTexCoord ) * specular;
	color *= fragmentColor;

	// rim: (1 - N.V)^4 by the stage's parameters
	float edge = 1.0 - facing;
	edge *= edge;
	edge *= edge;
	vec4 rim = edge * pc.parameters;
	vec3 glow = rim.rgb * ambient.rgb + rim.rgb * 0.1;

	if ( mode == 1 ) {
		// sfx/feAmbDecal_cloud: no night lights; the rim thins the clouds
		outColor = vec4( color.rgb + glow, diffuse.a * ( 1.0 - rim.a ) );
		return;
	}
	// night lights only where the ambient cube map is dark
	vec3 night = texture( selfIllumMap, fragmentTexCoord ).rgb;
	float lit = clamp( ambient.r * 8.0, 0.0, 1.0 );
	glow += max( night - night * lit, 0.0 );
	outColor = vec4( color.rgb + glow, diffuse.a );
	if ( OPAQUE_BASE ) {
		outColor.a = 0.0;
	}
}

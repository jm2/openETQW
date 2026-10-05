#version 450

layout( set = 0, binding = 0 ) uniform sampler2D diffuseTexture;
layout( set = 0, binding = 1 ) uniform sampler2D bumpTexture;
layout( set = 0, binding = 2 ) uniform sampler2D specularTexture;
layout( set = 0, binding = 3 ) uniform sampler2D selfIllumTexture;
layout( set = 0, binding = 4 ) uniform samplerCube ambientCubeTexture;

layout( push_constant ) uniform InteractionPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec3 textureMatrixS;
	uint packedRotationXY;
	vec3 textureMatrixT;
	uint packedRotationZW;
	vec4 parameters;
	vec4 lightOriginRadius;
	vec4 lightColorDirectional;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in vec3 fragmentPosition;
layout( location = 3 ) in vec3 fragmentTangent;
layout( location = 4 ) in vec3 fragmentBitangent;
layout( location = 5 ) in vec3 fragmentNormal;
layout( location = 0 ) out vec4 outColor;

void main() {
	// color.a < 0: MegaTexture terrain, whose albedo lives in the megatexture
	// tiles, not in a stage texture.  The base already holds albedo * ambient,
	// so this writes direct / ambient (color.rgb = 1 / ambient) and the
	// pipeline blends dst * src + dst = albedo * (ambient + direct).
	const bool relative = pc.color.a < 0.0;
	vec4 diffuse = relative ? vec4( 1.0 ) : texture( diffuseTexture, fragmentTexCoord );
	if ( !relative && pc.parameters.w > 0.5 && diffuse.a < pc.parameters.z ) {
		discard;
	}

	vec3 lightDirection;
	float attenuation = 1.0;
	if ( pc.lightColorDirectional.w > 0.5 ) {
		lightDirection = normalize( pc.lightOriginRadius.xyz );
	} else {
		vec3 toLight = pc.lightOriginRadius.xyz - fragmentPosition;
		float distanceToLight = length( toLight );
		if ( distanceToLight <= 0.001 || distanceToLight >= pc.lightOriginRadius.w ) {
			discard;
		}
		lightDirection = toLight / distanceToLight;
		attenuation = 1.0 - distanceToLight / pc.lightOriginRadius.w;
		attenuation *= attenuation;
	}

	vec3 geometricNormal = normalize( fragmentNormal );
	if ( dot( geometricNormal, lightDirection ) <= 0.0 ) {
		discard;
	}
	// RXGB (normal compression 2): X in alpha, Y in green, Z in blue.  The
	// generated _flat image is (128, 128, 255, 255); no valid RXGB normal has
	// both X and Z near 1, so that texel is read as the unperturbed normal.
	vec4 packedNormal = relative ? vec4( 1.0 ) : texture( bumpTexture, fragmentTexCoord );
	vec3 normal = geometricNormal;
	if ( !( packedNormal.a > 0.99 && packedNormal.b > 0.99 ) ) {
		vec2 tangentXY = vec2( packedNormal.a, packedNormal.g ) * 2.0 - 1.0;
		float tangentZ = sqrt( max( 0.0, 1.0 - dot( tangentXY, tangentXY ) ) );
		normal = normalize( fragmentTangent * tangentXY.x +
			fragmentBitangent * tangentXY.y + geometricNormal * tangentZ );
	}
	float lambert = max( dot( normal, lightDirection ), 0.0 );
	vec3 lit = ( relative ? pc.color.rgb : diffuse.rgb * fragmentColor.rgb ) *
		pc.lightColorDirectional.rgb * ( lambert * attenuation );
	// additive into colour only; destination alpha is left as the base pass wrote it
	outColor = vec4( lit, 0.0 );
}

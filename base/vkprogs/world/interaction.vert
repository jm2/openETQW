#version 450

// One light's contribution to one surface, for the rasterized lighting path
// (no ray-query device).  The terms match vkprogs/raytracing/lighting.comp:
// N.L, (1 - d/r)^2 for point lights, and a directional sun.  Lighting is done
// in the surface's model space; the CPU passes the light in that space.

layout( location = 0 ) in vec3 inPosition;
layout( location = 1 ) in vec4 inColor;
layout( location = 2 ) in vec2 inPackedTexCoord;
layout( location = 3 ) in vec2 inPackedNormal;
layout( location = 4 ) in uint inNormalSign;
layout( location = 5 ) in vec2 inPackedTangent;
layout( location = 6 ) in uint inTangentSign;
layout( location = 7 ) in uint inBitangentSign;

layout( push_constant ) uniform InteractionPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec3 textureMatrixS;
	uint packedRotationXY;
	vec3 textureMatrixT;
	uint packedRotationZW;
	vec4 parameters;
	vec4 lightOriginRadius;		// model space; for the sun: the direction towards it, w = 0
	vec4 lightColorDirectional;	// rgb, w = 1 for the sun
} pc;

layout( location = 0 ) out vec2 fragmentTexCoord;
layout( location = 1 ) out vec4 fragmentColor;
layout( location = 2 ) out vec3 fragmentPosition;
layout( location = 3 ) out vec3 fragmentTangent;
layout( location = 4 ) out vec3 fragmentBitangent;
layout( location = 5 ) out vec3 fragmentNormal;

vec3 unpackDirection( vec2 packedDirection, uint zSign ) {
	float z = sqrt( max( 0.0, 1.0 - dot( packedDirection, packedDirection ) ) );
	if ( zSign == 0u ) {
		z = -z;
	}
	return normalize( vec3( packedDirection, z ) );
}

void main() {
	vec2 texCoord = inPackedTexCoord * pc.parameters.y;
	fragmentTexCoord = vec2(
		dot( pc.textureMatrixS.xyz, vec3( texCoord, 1.0 ) ),
		dot( pc.textureMatrixT.xyz, vec3( texCoord, 1.0 ) )
	);
	fragmentColor = pc.color * mix( vec4( 1.0 ), inColor, pc.parameters.x );
	vec3 normal = unpackDirection( inPackedNormal, inNormalSign );
	vec3 tangent = unpackDirection( inPackedTangent, inTangentSign );
	fragmentNormal = normal;
	fragmentTangent = tangent;
	fragmentBitangent = normalize( cross( normal, tangent ) ) *
		( float( inBitangentSign ) - 1.0 );
	fragmentPosition = inPosition;
	gl_Position = pc.modelViewProjection * vec4( inPosition, 1.0 );
}

#version 450

// Vertex half of retail 1.5's front-end ambient programs (sfx/feAmbDecal and
// sfx/feAmbDecal_cloud, which references its vertex program), used by the main
// menu's globe.  It passes the texture coordinate, the vertex
// colour, the tangent frame in world space and the unit vector to the eye in
// world space, as the ARB program's texcoord[4], color, texcoord[5..7] and
// texcoord[2] do.

layout( location = 0 ) in vec3 inPosition;
layout( location = 1 ) in vec4 inColor;
layout( location = 2 ) in vec2 inPackedTexCoord;
layout( location = 3 ) in vec2 inPackedNormal;
layout( location = 4 ) in uint inNormalSign;
layout( location = 5 ) in vec2 inPackedTangent;
layout( location = 6 ) in uint inTangentSign;
layout( location = 7 ) in uint inBitangentSign;

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

layout( location = 0 ) out vec2 fragmentTexCoord;
layout( location = 1 ) out vec4 fragmentColor;
layout( location = 2 ) out vec3 fragmentToEye;
layout( location = 3 ) out vec3 fragmentTangent;
layout( location = 4 ) out vec3 fragmentBitangent;
layout( location = 5 ) out vec3 fragmentNormal;

vec3 rotateToWorld( vec3 value, vec4 idQuaternion ) {
	// idQuat transforms a vector as inverse(q) * v * q, the conjugate of
	// GLSL's conventional quaternion-vector formula.
	vec3 quaternionVector = -idQuaternion.xyz;
	vec3 temporary = 2.0 * cross( quaternionVector, value );
	return value + idQuaternion.w * temporary +
		cross( quaternionVector, temporary );
}

vec3 unpackDirection( vec2 packedDirection, uint zSign ) {
	float z = sqrt( max( 0.0, 1.0 - dot( packedDirection, packedDirection ) ) );
	if ( zSign == 0u ) {
		z = -z;
	}
	return normalize( vec3( packedDirection, z ) );
}

void main() {
	vec2 texCoord = inPackedTexCoord * pc.texCoordScale;
	fragmentTexCoord = vec2(
		dot( pc.textureMatrixS, vec3( texCoord, 1.0 ) ),
		dot( pc.textureMatrixT, vec3( texCoord, 1.0 ) )
	);
	// the ARB program's MAD color, colorModulate, colorAdd
	fragmentColor = mix( vec4( 1.0 ), inColor, pc.vertexColorModulate );
	vec3 normal = unpackDirection( inPackedNormal, inNormalSign );
	vec3 tangent = unpackDirection( inPackedTangent, inTangentSign );
	vec3 bitangent = normalize( cross( normal, tangent ) ) *
		( float( inBitangentSign ) - 1.0 );
	vec4 modelRotation = normalize( vec4( unpackSnorm2x16( pc.packedRotationXY ),
		unpackSnorm2x16( pc.packedRotationZW ) ) );
	fragmentTangent = rotateToWorld( tangent, modelRotation );
	fragmentBitangent = rotateToWorld( bitangent, modelRotation );
	fragmentNormal = rotateToWorld( normal, modelRotation );
	// The eye in model space: a perspective projection maps the eye to a clip
	// point with w = 0 and x = y = 0, so the inverse maps (0, 0, 1, 0) back to it.
	vec4 eye = inverse( pc.modelViewProjection ) * vec4( 0.0, 0.0, 1.0, 0.0 );
	vec3 localToEye = eye.xyz / eye.w - inPosition;
	// normalized per vertex, as the ARB program does
	fragmentToEye = normalize( rotateToWorld( localToEye, modelRotation ) );
	gl_Position = pc.modelViewProjection * vec4( inPosition, 1.0 );
}

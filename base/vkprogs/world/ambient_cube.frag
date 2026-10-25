#version 450

// The ambient term for the rasterized lighting path (#122), drawn over each lit
// opaque surface with interaction.vert's geometry before the flat
// r_rayTracingAmbient scale (which then skips these pixels) and the light
// interactions.  As retail's interaction/basic_ambient_fragment.inc, a
// surface's ambient is the ambient cube map looked up by the bumped world-space
// normal, times $ambientBrightness and $ambientScale.x; the self-illumination
// map is added unscaled.  Retail's specular term (2 * the specular map * the
// specular cube map along the reflection) is not drawn here.
//
// The pass scales what the base pass and the decals left instead of
// replacing it, so blended decals and extra stages keep their colour:
//   mode 0, blend 2 * src * dst: the ambient, halved here so up to 2 fits;
//   mode 1, blend dst + src: the self-illumination the scale took away,
//           selfIllum * vertex colour * ( 1 - ambient ).

layout( set = 0, binding = 0 ) uniform sampler2D diffuseTexture;
layout( set = 0, binding = 1 ) uniform sampler2D bumpTexture;
layout( set = 0, binding = 2 ) uniform sampler2D specularTexture;
layout( set = 0, binding = 3 ) uniform sampler2D selfIllumTexture;
layout( set = 0, binding = 4 ) uniform samplerCube ambientCubeTexture;

layout( push_constant ) uniform AmbientCubePushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec3 textureMatrixS;
	uint packedRotationXY;
	vec3 textureMatrixT;
	uint packedRotationZW;
	vec4 parameters;		// z: alpha test reference, w: 1 when the stage alpha tests
	vec4 ambientParameters;		// x: $ambientBrightness, y: $ambientScale.x, z: mode
	vec4 unused;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec4 fragmentColor;
layout( location = 2 ) in vec3 fragmentPosition;
layout( location = 3 ) in vec3 fragmentTangent;
layout( location = 4 ) in vec3 fragmentBitangent;
layout( location = 5 ) in vec3 fragmentNormal;
layout( location = 0 ) out vec4 outColor;

vec3 rotateToWorld( vec3 value, vec4 idQuaternion ) {
	// idQuat transforms a vector as inverse(q) * v * q, the conjugate of
	// GLSL's conventional quaternion-vector formula.
	vec3 quaternionVector = -idQuaternion.xyz;
	vec3 temporary = 2.0 * cross( quaternionVector, value );
	return value + idQuaternion.w * temporary +
		cross( quaternionVector, temporary );
}

void main() {
	if ( pc.parameters.w > 0.5 && texture( diffuseTexture, fragmentTexCoord ).a < pc.parameters.z ) {
		discard;
	}
	// the bumped normal as interaction.frag decodes it, in model space
	vec2 tangentXY = texture( bumpTexture, fragmentTexCoord ).rg * 2.0 - 1.0;
	float tangentZ = sqrt( max( 0.0, 1.0 - dot( tangentXY, tangentXY ) ) );
	vec3 normal = normalize( fragmentTangent * tangentXY.x +
		fragmentBitangent * tangentXY.y + normalize( fragmentNormal ) * tangentZ );
	vec4 modelRotation = normalize( vec4( unpackSnorm2x16( pc.packedRotationXY ),
		unpackSnorm2x16( pc.packedRotationZW ) ) );
	vec3 ambient = texture( ambientCubeTexture, rotateToWorld( normal, modelRotation ) ).rgb *
		pc.ambientParameters.x * pc.ambientParameters.y;
	if ( pc.ambientParameters.z < 0.5 ) {
		outColor = vec4( ambient * 0.5, 1.0 );
	} else {
		vec3 selfIllum = texture( selfIllumTexture, fragmentTexCoord ).rgb;
		outColor = vec4( selfIllum * fragmentColor.rgb * max( 1.0 - ambient, vec3( 0.0 ) ), 0.0 );
	}
}

#version 450

layout( location = 0 ) in vec3 inPosition;
layout( location = 1 ) in vec4 inColor;
layout( location = 2 ) in vec2 inPackedTexCoord;
layout( location = 8 ) in uvec4 inJointRows;
layout( location = 9 ) in vec4 inJointWeights;

layout( std140, set = 1, binding = 0 ) uniform JointPalette {
	vec4 rows[ 210 ];
} jointPalette;

layout( push_constant ) uniform SkyboxPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 textureMatrixS;
	vec4 textureMatrixT;
	vec4 parameters;
} pc;

layout( location = 0 ) out vec2 fragmentTexCoord;
layout( location = 1 ) out float fragmentSideBlend;

vec3 skinPosition( vec3 position ) {
	vec4 weights = inJointWeights;
	float total = dot( weights, vec4( 1.0 ) );
	weights = total > 0.0 ? weights / total : vec4( 1.0, 0.0, 0.0, 0.0 );
	vec4 row0 = jointPalette.rows[ inJointRows.x ] * weights.x + jointPalette.rows[ inJointRows.y ] * weights.y + jointPalette.rows[ inJointRows.z ] * weights.z + jointPalette.rows[ inJointRows.w ] * weights.w;
	vec4 row1 = jointPalette.rows[ inJointRows.x + 1u ] * weights.x + jointPalette.rows[ inJointRows.y + 1u ] * weights.y + jointPalette.rows[ inJointRows.z + 1u ] * weights.z + jointPalette.rows[ inJointRows.w + 1u ] * weights.w;
	vec4 row2 = jointPalette.rows[ inJointRows.x + 2u ] * weights.x + jointPalette.rows[ inJointRows.y + 2u ] * weights.y + jointPalette.rows[ inJointRows.z + 2u ] * weights.z + jointPalette.rows[ inJointRows.w + 2u ] * weights.w;
	vec4 value = vec4( position, 1.0 );
	return vec3( dot( row0, value ), dot( row1, value ), dot( row2, value ) );
}

void main() {
	// retail skies/skybox: the stage's texture matrix turns the dome's positions
	// (DP3 position with $diffuseMatrix_s/_t; z kept), and the mesh's own texture
	// coordinates are used as they are. Applying the matrix to the coordinates
	// instead rotated the top image against the side strip, so the box's faces
	// showed hard seams (issue #121).
	fragmentTexCoord = inPackedTexCoord * pc.parameters.y;
	fragmentSideBlend = clamp( inColor.r, 0.0, 1.0 );
	vec3 skinned = skinPosition( inPosition );
	vec3 position = vec3( dot( pc.textureMatrixS.xyz, skinned ),
		dot( pc.textureMatrixT.xyz, skinned ), skinned.z );
	gl_Position = pc.modelViewProjection * vec4( position, 1.0 );
}

#version 450

layout( location = 0 ) in vec3 inPosition;
layout( location = 1 ) in vec4 inColor;
layout( location = 2 ) in vec2 inPackedTexCoord;

layout( push_constant ) uniform SkyboxPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 textureMatrixS;
	vec4 textureMatrixT;
	vec4 parameters;
} pc;

layout( location = 0 ) out vec2 fragmentTexCoord;
layout( location = 1 ) out float fragmentSideBlend;

void main() {
	// retail skies/skybox: the stage's texture matrix turns the dome's positions
	// (DP3 position with $diffuseMatrix_s/_t; z kept), and the mesh's own texture
	// coordinates are used as they are. Applying the matrix to the coordinates
	// instead rotated the top image against the side strip, so the box's faces
	// showed hard seams (issue #121).
	fragmentTexCoord = inPackedTexCoord * pc.parameters.y;
	fragmentSideBlend = clamp( inColor.r, 0.0, 1.0 );
	vec3 position = vec3( dot( pc.textureMatrixS.xyz, inPosition ),
		dot( pc.textureMatrixT.xyz, inPosition ), inPosition.z );
	gl_Position = pc.modelViewProjection * vec4( position, 1.0 );
}

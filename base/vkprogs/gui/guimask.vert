#version 450

// gui.vert with the mask coordinates retail's trivialMaskImage reads from the
// normal attribute (#379)

layout( location = 0 ) in vec2 inPosition;
layout( location = 1 ) in vec2 inTexCoord;
layout( location = 2 ) in vec2 inMaskCoord;

layout( push_constant ) uniform GuiPushConstants {
	vec4 transform;
	vec4 color;
	vec4 mode;
} pc;

layout( location = 0 ) out vec2 fragmentTexCoord;
layout( location = 1 ) out vec4 fragmentColor;
layout( location = 2 ) out vec2 fragmentMaskCoord;

void main() {
	gl_Position = vec4( inPosition * pc.transform.xy + pc.transform.zw, 0.0, 1.0 );
	fragmentTexCoord = inTexCoord;
	fragmentColor = pc.color;
	fragmentMaskCoord = inMaskCoord;
}

#version 450

// Rasterized lighting path: scales the opaque base colour by the ambient term
// before the per-light interactions add their direct light, so a surface ends
// as albedo * (ambient + direct), as vkprogs/raytracing/lighting.comp writes
// it.  Drawn with sky.vert's full-screen triangle at depth 0.999999 and a
// GREATER depth test, so the sky (at or behind that depth) is left alone, as
// lighting.comp skips depth >= 0.999999.  Blend: dst * src.

layout( push_constant ) uniform AmbientPushConstants {
	mat4 unusedMatrix;
	vec4 ambient;
	vec4 viewParameters;
	vec4 unused0;
	vec4 unused1;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 0 ) out vec4 outColor;

void main() {
	outColor = vec4( pc.ambient.rgb, 1.0 );
}

#version 450

// mega.frag with retail's detail texture (renderprogs megatexture/detail_fragment.inc):
// combined += combined * dot( mask( st ), 2 * detail( ( st - 0.5 ) * scale ) - 1 ),
// where st is the megatexture coordinate and scale $megaDetailTextureParams.x
// (megaLevelParams.w; 0 turns it off).  The term scales the colour, so applying it
// to each level before the level blend gives the same result as after it.

layout( set = 0, binding = 0 ) uniform sampler2D levelTexture;
layout( set = 0, binding = 1 ) uniform sampler2D detailTexture;
layout( set = 0, binding = 2 ) uniform sampler2D detailMaskTexture;

layout( push_constant ) uniform MegaPushConstants {
	mat4 modelViewProjection;
	vec4 color;
	vec4 megaMaskParams;
	vec4 megaLevelParams;
	vec4 parameters;
} pc;

layout( location = 0 ) in vec2 fragmentTexCoord;
layout( location = 1 ) in vec2 fragmentMaskCoord;
layout( location = 2 ) in vec4 fragmentColor;
layout( location = 3 ) in float fragmentLighting;
layout( location = 0 ) out vec4 outColor;

void main() {
	vec4 sampled = texture( levelTexture, fragmentTexCoord );
	float weight = pc.megaLevelParams.y;
	if ( pc.megaLevelParams.z > 0.5 ) {
		float edgeDistance = max( abs( fragmentMaskCoord.x ),
			abs( fragmentMaskCoord.y ) );
		weight *= clamp( -32.0 * edgeDistance + 16.0, 0.0, 1.0 );
	}
	vec3 rgb = sampled.rgb;
	if ( pc.megaLevelParams.w > 0.0 ) {
		vec2 st = fragmentTexCoord / pc.megaLevelParams.x;
		vec4 detail = texture( detailTexture, ( st - 0.5 ) * pc.megaLevelParams.w ) * 2.0 - 1.0;
		rgb += rgb * dot( texture( detailMaskTexture, st ), detail );
	}
	outColor = vec4( rgb * fragmentColor.rgb, weight );
}

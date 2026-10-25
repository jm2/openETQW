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
	// tiles, not in a stage texture.  Binding 0 is then the light block's
	// snapshot of the unlit opaque base, read at this pixel.
	const bool relative = pc.color.a < 0.0;
	vec4 diffuse = relative ? texelFetch( diffuseTexture, ivec2( gl_FragCoord.xy ), 0 ) :
		texture( diffuseTexture, fragmentTexCoord );
	if ( !relative && pc.parameters.w > 0.5 && diffuse.a < pc.parameters.z ) {
		discard;
	}

	// lightColorDirectional.w: 1 for the sun, + 2 for a two-sided material, + 4 for
	// MegaTexture terrain under the sun drawn as retail's megaTexture/notlit_lit:
	// albedo x ( 0.65 + 0.35 x light colour ), no N.L.  The light block has already
	// scaled the pixel to albedo x ambient (parameters.z here), so add the rest.
	// color.rgb scales the term: 1, or the shadow split's minSpecShadowColor and
	// ( 1 - minSpecShadowColor ) with no ambient (VulkanBackend.cpp).
	if ( relative && pc.lightColorDirectional.w > 3.5 ) {
		vec3 notLit = pc.color.rgb * ( 0.65 + 0.35 * pc.lightColorDirectional.rgb );
		outColor = vec4( diffuse.rgb * max( notLit - pc.parameters.z, 0.0 ), 0.0 );
		return;
	}
	const bool directional = mod( pc.lightColorDirectional.w, 2.0 ) > 0.5;
	const bool twoSided = mod( pc.lightColorDirectional.w, 4.0 ) > 1.5;
	vec3 lightDirection;
	float attenuation = 1.0;
	if ( directional ) {
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
	if ( twoSided && dot( geometricNormal, lightDirection ) < 0.0 ) {
		geometricNormal = -geometricNormal;	// a cloth or foliage card lit from behind
	}
	if ( dot( geometricNormal, lightDirection ) <= 0.0 ) {
		discard;
	}
	// Normal maps reach Vulkan with X in red and Y in green.  Retail's ATI2 keeps
	// Y in its first block and X in its second (its ARB programs read X from LATC2's
	// alpha), and the BC5 view swizzle and the decode fallback swap them; RXGB has
	// its alpha moved into red; uncompressed maps carry X in red; and the generated
	// _flat image reads as X = Y = 0.  Z is rebuilt from X and Y.
	vec4 packedNormal = relative ? vec4( 0.5, 0.5, 1.0, 1.0 ) : texture( bumpTexture, fragmentTexCoord );
	vec3 normal = geometricNormal;
	{
		vec2 tangentXY = packedNormal.rg * 2.0 - 1.0;
		float tangentZ = sqrt( max( 0.0, 1.0 - dot( tangentXY, tangentXY ) ) );
		normal = normalize( fragmentTangent * tangentXY.x +
			fragmentBitangent * tangentXY.y + geometricNormal * tangentZ );
	}
	float lambert = max( dot( normal, lightDirection ), 0.0 );
	vec3 color = diffuse.rgb * ( relative ? vec3( 1.0 ) : fragmentColor.rgb );
	if ( !relative ) {
		// Retail's specular (interaction/basic_fragment.inc): the falloff
		// sat(4 * N.H - 3)^2 (its specular table) times the light's colour times
		// 2 * the specular map, added to the diffuse before the light's N.L.
		// pc.color.rgb is the view origin in model space.
		vec3 viewDirection = normalize( pc.color.rgb - fragmentPosition );
		vec3 halfAngle = normalize( lightDirection + viewDirection );
		float specular = clamp( 4.0 * dot( halfAngle, normal ) - 3.0, 0.0, 1.0 );
		color += specular * specular * 2.0 * texture( specularTexture, fragmentTexCoord ).rgb;
	}
	vec3 lit = color * pc.lightColorDirectional.rgb * ( lambert * attenuation );
	// additive into colour only; destination alpha is left as the base pass wrote it
	outColor = vec4( lit, 0.0 );
}

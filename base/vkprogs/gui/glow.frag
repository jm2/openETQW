#version 450

// guis/postprocess's postprocess/glow material (#233): ports of retail's
// renderprogs/postprocess/glare.rprog programs, one per pass.
//   pass 0  downsample_glare: _currentRender -> _postProcessBuffer_1
//   pass 1  blur_bilinear:    one buffer -> the other, along screen.zw
//   pass 2  glow:             _currentRender and _postProcessBuffer_1 -> the screen
// Retail samples RECT textures in OpenGL's bottom-up texel coordinates; the maths
// below keeps those coordinates and converts at each fetch, so the taps land on
// the same texels.

layout( set = 0, binding = 0 ) uniform sampler2D sourceImage;	// $currentRender, or $map for a blur
layout( set = 0, binding = 1 ) uniform sampler2D blurImage;	// $map of the glow pass

layout( push_constant ) uniform GlowPushConstants {
	vec4 glare;					// $postGlareParameters
	vec4 saturationContrast;	// $postSaturationContrast: x saturation, y contrast
	vec4 tint;					// $postTint (w 0, as the OpenGL binding sets it)
	vec4 sizes;					// source width, height; target width, height
	vec4 screen;				// screen width, height; blur direction x, y
	vec4 mode;					// x pass; y 1 shows the scene's alpha instead (r_vkGlowDebug)
} pc;

layout( location = 0 ) out vec4 outColor;

vec4 TexRect( sampler2D image, vec2 imageSize, vec2 coord ) {
	return textureLod( image, vec2( coord.x, imageSize.y - coord.y ) / imageSize, 0.0 );
}

// Cg's smoothstep: saturate( ( x - a ) / ( b - a ) ), so equal edges give a step
// (valley's glareParms put the threshold at 1)
float CgSmoothstep( float a, float b, float x ) {
	float t = b > a ? clamp( ( x - a ) / ( b - a ), 0.0, 1.0 ) : ( x > b ? 1.0 : 0.0 );
	return t * t * ( 3.0 - 2.0 * t );
}

void main() {
	const vec2 sourceSize = pc.sizes.xy;
	const vec2 targetSize = pc.sizes.zw;
	// the OpenGL window position of this fragment, and the GUI quad's texcoord
	// with retail's coord.y = 1 - t flip folded in
	const vec2 window = vec2( gl_FragCoord.x, targetSize.y - gl_FragCoord.y );
	const vec2 quad = window / targetSize;
	const int pass = int( pc.mode.x + 0.5 );
	if ( pass == 0 ) {
		const vec2 coord = quad * pc.screen.xy;
		vec4 c = TexRect( sourceImage, sourceSize, coord ) * 0.25;
		c += TexRect( sourceImage, sourceSize, coord + vec2( 2.0, 0.0 ) ) * 0.25;
		c += TexRect( sourceImage, sourceSize, coord + vec2( 2.0, 2.0 ) ) * 0.25;
		c += TexRect( sourceImage, sourceSize, coord + vec2( 0.0, 2.0 ) ) * 0.25;
		const float luminance = dot( c.rgb, vec3( 0.33 ) );
		const float scale = mix( 1.0, CgSmoothstep( pc.glare.z, 1.0, luminance ), pc.glare.w ) * c.a;
		outColor = c * vec4( scale, scale, scale, 1.0 );
	} else if ( pass == 1 ) {
		// "Blurring happens at 1/4 of the screen size"
		const vec2 coord = quad * ( pc.screen.xy * 0.25 );
		const vec2 blurSize = pc.screen.zw;
		const float weightSum = 2.0 * ( 1.0 + 0.8 + 0.6 + 0.4 );
		vec4 result = TexRect( sourceImage, sourceSize, coord + 6.5 * blurSize ) * ( 0.4 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord + 4.5 * blurSize ) * ( 0.6 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord + 2.5 * blurSize ) * ( 0.8 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord + 0.5 * blurSize ) * ( 1.0 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord - 0.5 * blurSize ) * ( 1.0 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord - 2.5 * blurSize ) * ( 0.8 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord - 4.5 * blurSize ) * ( 0.6 / weightSum );
		result += TexRect( sourceImage, sourceSize, coord - 6.5 * blurSize ) * ( 0.4 / weightSum );
		outColor = result;
	} else {
		const vec4 clean = TexRect( sourceImage, sourceSize, quad * pc.screen.xy );
		if ( pc.mode.y > 0.5 ) {
			outColor = vec4( clean.aaa, 1.0 );
			return;
		}
		const vec2 blurImageSize = vec2( textureSize( blurImage, 0 ) );
		const vec4 blur = TexRect( blurImage, blurImageSize, quad * ( pc.screen.xy * 0.25 ) );
		const vec4 final = clean * pc.glare.x + blur * pc.glare.y;
		const vec4 grayscale = vec4( dot( final.rgb, vec3( 0.299, 0.587, 0.114 ) ) );
		outColor = mix( vec4( 0.5 ), mix( grayscale, final, pc.saturationContrast.x ),
			pc.saturationContrast.y ) * pc.tint;
	}
}

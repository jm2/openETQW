#version 450

// The last pass of a frame (#418): sets the swapchain's alpha to 1 and leaves its
// colour alone (the pipeline writes alpha only). Lit world surfaces leave alpha 0,
// the postprocess glow's source weight, and a compositor that reads the alpha
// (macOS fullscreen keeps the layer non-opaque, #385) would show them black.

layout( location = 0 ) out vec4 outColor;

void main() {
	outColor = vec4( 0.0, 0.0, 0.0, 1.0 );
}

// Copyright (C) 2007 Id Software, Inc.

#ifndef __RENDERER_VULKANBACKEND_H__
#define __RENDERER_VULKANBACKEND_H__

struct sdVulkanBackendState;
struct viewDef_s;

// DDS block formats the backend can upload without decoding.
enum vulkanBlockFormat_t {
	VBF_BC1_RGB,	// DXT1
	VBF_BC1_RGBA,	// DXT1 with 1-bit alpha
	VBF_BC2,		// DXT3
	VBF_BC3,		// DXT5
	VBF_BC3_RXGB,	// RXGB: DXT5 with red in the alpha block
	VBF_BC4,		// ATI1 / LATC1
	VBF_BC5			// ATI2
};

struct sdVulkanGuiVertex {
	float x;
	float y;
	float s;
	float t;
};

struct sdVulkanToolVertex {
	float x;
	float y;
	float z;
	float w;
	float s;
	float t;
	float r;
	float g;
	float b;
	float a;
};

class sdVulkanBackend {
public:
	sdVulkanBackend();
	~sdVulkanBackend();

	bool Init( void* nativeWindow, int width, int height );
	void Shutdown();

	bool BeginFrame( int width, int height );
	void EndFrame( bool present );
	void WaitIdle();

	// Renderer resource mirrors.  The owner is the stable idImage/vertex-cache
	// object address; Vulkan handles stay private to this backend.
	bool UploadImage2D( const void* owner, const unsigned char* rgba,
		int width, int height, int mipLevels, bool linearFilter, bool repeat );
	// Precompressed DDS blocks, uploaded as they are (no decode, no generated
	// mipmaps).  SupportsBlockFormat is false without textureCompressionBC or
	// the format's sampled-image (and, for a linear sampler, linear-filter)
	// support; the caller then decodes to RGBA.
	bool SupportsBlockFormat( vulkanBlockFormat_t blockFormat, bool linearFilter ) const;
	bool UploadBlockImage2D( const void* owner, vulkanBlockFormat_t blockFormat,
		const unsigned char* blocks, size_t blockDataBytes, int width, int height,
		int mipLevels, bool linearFilter, bool repeat );
	bool UploadImageCube( const void* owner, const unsigned char* const rgba[ 6 ],
		int size, bool linearFilter );
	bool UpdateImage2D( const void* owner, int mipLevel, int x, int y,
		int width, int height, const unsigned char* rgba );
	bool CanQueueImageUpload( int bytes ) const;
	void DestroyImage( const void* owner );
	bool UploadBuffer( const void* owner, const void* data, int bytes,
		bool indexBuffer, bool frameTemporary = false );
	bool UpdateBuffer( const void* owner, int offset, const void* data, int bytes );
	void DestroyBuffer( const void* owner );
	bool DrawGuiFan( const void* imageOwner, const sdVulkanGuiVertex* vertices,
		int vertexCount, const float* color, int drawStateBits, bool makeAlpha = false );
	bool DrawGuiTriangles( const void* imageOwner,
		const sdVulkanToolVertex* vertices, int vertexCount );
	bool DrawOverlayTriangles( const void* imageOwner,
		const sdVulkanToolVertex* vertices, int vertexCount,
		int scissorX, int scissorY, int scissorWidth, int scissorHeight );
	void DrawView( const viewDef_s* view );

	// Native editor windows use their own presentation surfaces.  Radiant's
	// fixed-function compatibility layer submits pre-transformed triangle lists
	// here, keeping tool rendering independent from the game swapchain.
	bool BeginToolWindow( void* nativeWindow, int width, int height,
		const float clearColor[ 4 ] );
	// Legacy editor views render into sampled Vulkan images.  The Dear ImGui
	// shell only composites those images; it never owns or expands map geometry.
	bool BeginToolRenderTarget( const void* owner, int width, int height,
		const float clearColor[ 4 ] );
	void EndToolRenderTarget();
	void DestroyToolRenderTarget( const void* owner );
	bool GetToolRenderTargetSize( const void* owner, int& width, int& height,
		int& textureWidth, int& textureHeight ) const;
	bool DrawToolTriangles( const sdVulkanToolVertex* vertices, int vertexCount,
		bool depthTest, bool blend );
	bool DrawToolIndexed( const sdVulkanToolVertex* vertices, int vertexCount,
		const unsigned int* indices, int indexCount, bool depthTest, bool blend );
	// Static render models already live in the renderer vertex cache.  Bind those
	// buffers directly for Radiant instead of copying every model instance into
	// the small per-frame immediate-mode stream.
	bool DrawToolCachedIndexed( const void* vertexCache, const void* indexCache,
		int vertexCount, int indexCount, const float* modelViewProjection,
		const float* color, float texCoordScale, bool depthTest, bool blend,
		bool wireframe );
	void SetToolScissor( int x, int y, int width, int height );
	void SetToolImage( const void* imageOwner );
	void ClearToolRegion( const float color[ 4 ], bool clearColor, bool clearDepth );
	bool GetActiveToolWindowExtent( int& width, int& height ) const;
	void EndToolWindow();
	bool IsToolWindowActive() const;

	bool IsInitialized() const;
	bool IsFrameActive() const;
	const char* GetDeviceName() const;
	// The selected device's vendor (from its PCI vendor ID) and
	// "Vulkan <api> / <driver name> <driver info>", for GL_VENDOR/GL_VERSION.
	const char* GetVendorString() const;
	const char* GetVersionString() const;
	unsigned int GetVendorID() const;

private:
	sdVulkanBackendState*	state;
};

extern sdVulkanBackend vulkanBackend;

bool R_UseVulkanBackend();
// Permanent fallback switch: once set, R_UseVulkanBackend() is false for the
// rest of the run so every caller takes the OpenGL path (r_renderAPI may still
// read "vulkan"; the engine fell back because Vulkan could not initialize).
void R_NotifyVulkanUnavailable();

struct drawSurf_s;
struct viewLight_s;

int ShadowIndexCount( const drawSurf_s* surface, const viewLight_s* light, bool& external );
int ShadowIndexCount( const drawSurf_s* surface, bool& external );

void DrawVulkanShadowSurface( sdVulkanBackendState& state, const drawSurf_s* surface,
	const viewLight_s* light, const viewDef_s* view );
void DrawVulkanLightInteraction( sdVulkanBackendState& state, const drawSurf_s* surface,
	const viewLight_s* light, const viewDef_s* view, bool unshadowed = false, const float* fog = NULL,
	const float* ambient = NULL, bool amblitShadowSplit = false );

#endif /* !__RENDERER_VULKANBACKEND_H__ */

// Copyright (C) 2007 Id Software, Inc.

#include "../framework/precompiled.h"
#pragma hdrstop

#include "VulkanBackend.h"
#include "RuntimeSpirvCompiler.h"
#include "RendererJobs.h"
#include "RendererMetrics.h"
#include "FrameSyncTiming.h"
#include "tr_render.h"
#include "draw_local.h"
#include "Material.h"
#include "Image.h"
#include "VertexCache.h"
#include "renderbindings.h"
#include "megatexture/megatexture.h"
#include "../decllib/declAtmosphere.h"
#include "../decllib/declAmbientCubeMap.h"
#include "../decllib/declRenderBinding.h"
#include "../decllib/declRenderProgram.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>
#define VK_ENABLE_BETA_EXTENSIONS
#define VK_NO_PROTOTYPES
#if defined( _WIN32 )
#define VK_USE_PLATFORM_WIN32_KHR
#endif
#include <vulkan/vulkan.h>

#if defined( _WIN32 )
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#include "draw_raytracing.h"

#include <atomic>
#include <chrono>
#include <deque>
#include <unordered_map>
#include <vector>

void R_AxisToModelMatrix( const idMat3& axis, const idVec3& origin, float* const modelMatrix );
void R_MultMatrix( const float* a, const float* b, float* output );

extern idCVar r_offsetFactor;
extern idCVar r_offsetUnits;
extern idCVar r_megaDrawMethod;
extern idCVar r_shadows;
extern idCVar r_shadowPass;
extern idCVar r_useExternalShadows;
extern idCVar r_shadowPolygonFactor;
extern idCVar r_shadowPolygonOffset;
extern idCVar r_skipAtmosInteractions;

namespace {

const int NUM_VULKAN_FRAMES = 2;

// FrameSyncTiming.h: a frame's waits, added to the sums only once it reaches the present,
// so a frame that fails earlier adds nothing. Frames run on the main thread, as does
// R_TakeFrameSyncTiming; the sums are atomic only so that nothing depends on that.
uint64_t				pendingFenceUsec = 0;
uint64_t				pendingAcquireUsec = 0;
uint64_t				pendingSubmitUsec = 0;
uint64_t				pendingPoolUsec = 0;		// vkResetCommandPool, in BeginFrame or ahead (r_vkPrepareAhead)
uint64_t				pendingAheadFenceUsec = 0;	// r_vkPrepareAhead: the fence wait EndFrame did for this frame
uint64_t				pendingAheadPoolUsec = 0;	// r_vkPrepareAhead: the pool reset EndFrame did for this frame
long long				pendingFenceBeginUsec = 0;	// r_vkLogFrameSync: BeginFrame's fence wait began (the FRAME_POSE clock)
std::atomic< int >		syncFrames( 0 );
std::atomic< uint64_t >	syncFenceUsec( 0 );
std::atomic< uint64_t >	syncAcquireUsec( 0 );
std::atomic< uint64_t >	syncSubmitUsec( 0 );
std::atomic< uint64_t >	syncPresentUsec( 0 );

uint64_t SyncNowUsec() {
	return static_cast< uint64_t >( std::chrono::duration_cast< std::chrono::microseconds >(
		std::chrono::steady_clock::now().time_since_epoch() ).count() );
}
// Radiant shares this mapped stream with the game GUI. Model-heavy .world
// files expand legacy fixed-function lines, quads and display lists into
// several hundred thousand vertices in a frame, which does not fit in 4 MiB.
const VkDeviceSize VULKAN_GUI_VERTEX_BYTES = 32 * 1024 * 1024;
const VkDeviceSize VULKAN_GUI_INDEX_BYTES = 16 * 1024 * 1024;
// A complete 16x16 RGBA MegaTexture level including its mip tail is roughly
// 22 MiB. One persistent arena per frame avoids allocation, mapping and a
// queue-idle submission for every streamed tile mip.
const VkDeviceSize VULKAN_IMAGE_UPLOAD_BYTES = 32 * 1024 * 1024;
// Static geometry can become visible in large bursts as particles, models and
// new world areas enter the view.  Keep those copies separate from the
// MegaTexture arena so neither streaming system can starve the other.
const VkDeviceSize VULKAN_BUFFER_UPLOAD_BYTES = 32 * 1024 * 1024;
// Buffer uploads made outside a frame wait for the next frame, up to this many
// bytes; beyond it they are copied at once, in one batch (see UploadBufferBytes).
const VkDeviceSize VULKAN_PENDING_BUFFER_UPLOAD_BYTES = VULKAN_BUFFER_UPLOAD_BYTES / 2;
const VkDeviceSize VULKAN_JOINT_BUFFER_BYTES = 4 * 1024 * 1024;
const VkDeviceSize VULKAN_JOINT_PALETTE_BYTES =
	MAX_JOINTS_PER_MESH * sizeof( idJointMat );
const int VULKAN_MATERIAL_TEXTURES = 5;
// retail's front-end ambient programs (sfx/feAmbDecal and the programs built on its
// vertex program) also read the ambient cube map's specular cube map
const int VULKAN_FRONTEND_TEXTURES = 6;

// Off by default: where the layer is installed it costs most of the frame rate
// (56 against ~370 fps uncapped on an RX 6400), and players do not need it.
// campaign-client-maps and client-network-* turn it on.
idCVar r_vkValidation(
	"r_vkValidation",
	"0",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_BOOL | CVAR_NOCHEAT,
	"enable the Khronos Vulkan validation layer when it is installed (slow; for development)"
);

idCVar r_vkScreenshot(
	"r_vkScreenshot",
	"",
	CVAR_RENDERER | CVAR_NOCHEAT,
	"write the next presented frame (Vulkan or OpenGL) to this path as a 32-bit TGA, then clear itself"
);

idCVar r_vkSkipGui(
	"r_vkSkipGui",
	"0",
	CVAR_RENDERER | CVAR_BOOL,
	"skip Vulkan GUI draws for renderer diagnostics"
);

idCVar r_vkGlowDebug(
	"r_vkGlowDebug",
	"0",
	CVAR_RENDERER | CVAR_BOOL,
	"draw the scene's alpha, the postprocess glow's source weight, in place of the glow"
);

idCVar r_vkPrepareAhead(
	"r_vkPrepareAhead",
	"1",
	CVAR_RENDERER | CVAR_BOOL | CVAR_NOCHEAT,
	"at the end of a frame, wait for the next frame's fence and reset its command pool, so the GPU wait and "
	"the driver's pool reset (up to 13 ms on Honeykrisp) fall before the next frame samples input and computes "
	"its fraction, not between ClientUpdateView and the draw (0 = do both in BeginFrame, as before)"
);

idCVar r_vkLogFrameSync(
	"r_vkLogFrameSync",
	"0",
	CVAR_RENDERER | CVAR_BOOL | CVAR_NOCHEAT,
	"print a FRAME_SYNC line for every presented Vulkan frame: when BeginFrame's fence wait began (us, the clock "
	"of FRAME_POSE) and the frame's fence, acquire, command-pool reset, submit and present times in microseconds; "
	"with r_vkPrepareAhead the fence and pool times include the part done at the end of the previous frame "
	"(test diagnostic)"
);

idCVar r_vkDebugMaterials(
	"r_vkDebugMaterials",
	"0",
	CVAR_RENDERER | CVAR_BOOL,
	"print the first Vulkan world materials and their resolved textures"
);

struct sdVulkanFrame {
	VkCommandPool		commandPool;
	VkCommandBuffer	commandBuffer;
	VkCommandBuffer	imageUploadCommandBuffer;
	VkSemaphore		imageAvailable;
	VkSemaphore		renderComplete;
	VkFence			fence;
	bool				commandPoolReset;	// r_vkPrepareAhead: EndFrame already waited for the fence and reset the pool
	VkBuffer			guiVertexBuffer;
	VkDeviceMemory	guiVertexMemory;
	void*				guiVertexMapped;
	VkDeviceSize		guiVertexOffset;
	VkBuffer			guiIndexBuffer;
	VkDeviceMemory	guiIndexMemory;
	void*				guiIndexMapped;
	VkDeviceSize		guiIndexOffset;
	bool				guiVertexOverflowWarned;
	bool				guiIndexOverflowWarned;
	VkBuffer			imageUploadBuffer;
	VkDeviceMemory	imageUploadMemory;
	void*				imageUploadMapped;
	VkDeviceSize		imageUploadOffset;
	bool				imageUploadRecorded;
	bool				imageUploadOverflowWarned;
	VkBuffer			bufferUploadBuffer;
	VkDeviceMemory	bufferUploadMemory;
	void*				bufferUploadMapped;
	VkDeviceSize		bufferUploadOffset;
	bool				bufferUploadOverflowWarned;
	VkBuffer			jointBuffer;
	VkDeviceMemory	jointMemory;
	void*				jointMapped;
	VkDeviceSize		jointOffset;
	bool				jointOverflowWarned;
	VkDescriptorSet	jointDescriptorSet;
};

struct sdVulkanImageResource {
	const void*		owner;
	VkImage			image;
	VkDeviceMemory	memory;
	VkImageView		view;
	VkSampler		sampler;
	VkDescriptorSet	descriptorSet;
	int				width;
	int				height;
	int				mipLevels;
	bool			cubeMap;
	bool			blockCompressed;	// BC blocks from a DDS file; UpdateImage2D cannot write it
};

struct sdVulkanToolDepthResource {
	VkImage			image;
	VkDeviceMemory	memory;
	VkImageView		view;
};

struct sdVulkanBufferResource {
	const void*		owner;
	VkBuffer			buffer;
	VkDeviceMemory	memory;
	void*				mapped;
	VkDeviceSize		bytes;
	bool				indexBuffer;
	// Memory sub-allocated from a shared block (CreatePooledBuffer): poolBlock is
	// the block's index + 1, or 0 when the buffer owns its memory.
	int					poolBlock;
	VkDeviceSize		poolOffset;
	VkDeviceSize		poolBytes;
};

struct sdVulkanMemoryRange {
	VkDeviceSize	offset;
	VkDeviceSize	bytes;
};

// A buffer upload made outside a frame, held until the next one (or a batch).
struct sdVulkanPendingBufferUpload {
	VkBuffer		destination;
	VkDeviceSize	offset;
	VkDeviceSize	bytes;
	bool			indexBuffer;
	byte*			data;		// Mem_Alloc'd copy, owned by the pending list
};

// One VkDeviceMemory that many buffers are bound into.  Host-visible blocks
// stay mapped for their lifetime.
struct sdVulkanMemoryBlock {
	VkDeviceMemory					memory;
	unsigned int					memoryType;
	bool							deviceAddress;
	void*							mapped;
	int								liveAllocations;
	idList< sdVulkanMemoryRange >	freeRanges;	// sorted by offset, never adjacent
};

struct sdVulkanMaterialDescriptor {
	const materialStage_t*	owner;
	const void*				imageOwners[ VULKAN_MATERIAL_TEXTURES ];
	VkImageView				imageViews[ VULKAN_MATERIAL_TEXTURES ];
	VkDescriptorSet			descriptorSet;
	unsigned int			lastUsedFrame;	// frameSerial when last returned
};

struct sdVulkanFrontEndDescriptor {
	const void*				owner;		// the stage, or the megatexture level

	const void*				imageOwners[ VULKAN_FRONTEND_TEXTURES ];
	VkImageView				imageViews[ VULKAN_FRONTEND_TEXTURES ];
	VkDescriptorSet			descriptorSet;
	unsigned int			lastUsedFrame;	// frameSerial when last returned
};

// The front-end sets (6 samplers each) have their own pool, so the cube-ambient
// pass's sets, one per stage and cube, cannot exhaust the material pool (#258).
const int VULKAN_FRONTEND_DESCRIPTOR_SETS = 4096;

const char* VkResultName( VkResult result ) {
	switch ( result ) {
		case VK_SUCCESS: return "VK_SUCCESS";
		case VK_NOT_READY: return "VK_NOT_READY";
		case VK_TIMEOUT: return "VK_TIMEOUT";
		case VK_EVENT_SET: return "VK_EVENT_SET";
		case VK_EVENT_RESET: return "VK_EVENT_RESET";
		case VK_INCOMPLETE: return "VK_INCOMPLETE";
		case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
		case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
		case VK_ERROR_OUT_OF_POOL_MEMORY: return "VK_ERROR_OUT_OF_POOL_MEMORY";
		case VK_ERROR_FRAGMENTED_POOL: return "VK_ERROR_FRAGMENTED_POOL";
		case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
		case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
		case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
		case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
		case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
		case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
		case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
		case VK_ERROR_TOO_MANY_OBJECTS: return "VK_ERROR_TOO_MANY_OBJECTS";
		case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
		case VK_ERROR_SURFACE_LOST_KHR: return "VK_ERROR_SURFACE_LOST_KHR";
		case VK_ERROR_NATIVE_WINDOW_IN_USE_KHR: return "VK_ERROR_NATIVE_WINDOW_IN_USE_KHR";
		case VK_SUBOPTIMAL_KHR: return "VK_SUBOPTIMAL_KHR";
		case VK_ERROR_OUT_OF_DATE_KHR: return "VK_ERROR_OUT_OF_DATE_KHR";
		default: return "unknown VkResult";
	}
}

PFN_vkAllocateMemory vkAllocateMemoryReal = NULL;
PFN_vkFreeMemory vkFreeMemoryReal = NULL;
unsigned int r_vkMemoryAllocations = 0;
unsigned int r_vkMemoryAllocationPeak = 0;

VkResult VKAPI_PTR TrackedAllocateMemory( VkDevice device,
	const VkMemoryAllocateInfo* allocateInfo,
	const VkAllocationCallbacks* allocator, VkDeviceMemory* memory ) {
	const VkResult result = vkAllocateMemoryReal( device, allocateInfo, allocator, memory );
	if ( result == VK_SUCCESS ) {
		++r_vkMemoryAllocations;
		if ( r_vkMemoryAllocations > r_vkMemoryAllocationPeak ) {
			r_vkMemoryAllocationPeak = r_vkMemoryAllocations;
		}
	}
	return result;
}

void VKAPI_PTR TrackedFreeMemory( VkDevice device, VkDeviceMemory memory,
	const VkAllocationCallbacks* allocator ) {
	if ( memory != VK_NULL_HANDLE && r_vkMemoryAllocations > 0 ) {
		--r_vkMemoryAllocations;
	}
	vkFreeMemoryReal( device, memory, allocator );
}

bool CheckVulkanResult( VkResult result, const char* operation ) {
	if ( result == VK_SUCCESS ) {
		return true;
	}
	common->Warning( "%s failed: %s (%d)", operation, VkResultName( result ),
		static_cast< int >( result ) );
	return false;
}

VKAPI_ATTR VkBool32 VKAPI_CALL VulkanDebugCallback(
	VkDebugUtilsMessageSeverityFlagBitsEXT severity,
	VkDebugUtilsMessageTypeFlagsEXT,
	const VkDebugUtilsMessengerCallbackDataEXT* callbackData,
	void* ) {
	const char* message = callbackData != NULL && callbackData->pMessage != NULL ?
		callbackData->pMessage : "<no validation message>";
	if ( severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT ) {
		common->Warning( "Vulkan validation error: %s", message );
	} else if ( severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT ) {
		common->Warning( "Vulkan validation warning: %s", message );
	} else {
		common->DPrintf( "Vulkan validation: %s\n", message );
	}
	return VK_FALSE;
}

void InitDebugMessengerCreateInfo( VkDebugUtilsMessengerCreateInfoEXT& createInfo ) {
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT;
	createInfo.messageSeverity =
		VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT |
		VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT;
	createInfo.messageType =
		VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT |
		VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT |
		VK_DEBUG_UTILS_MESSAGE_TYPE_PERFORMANCE_BIT_EXT;
	createInfo.pfnUserCallback = VulkanDebugCallback;
}

template< typename T >
bool LoadGlobalFunction( PFN_vkGetInstanceProcAddr getProcAddress,
	const char* name, T& function ) {
	function = reinterpret_cast< T >( getProcAddress( VK_NULL_HANDLE, name ) );
	if ( function == NULL ) {
		common->Warning( "Vulkan loader is missing %s", name );
		return false;
	}
	return true;
}

template< typename T >
bool LoadInstanceFunction( PFN_vkGetInstanceProcAddr getProcAddress,
	VkInstance instance, const char* name, T& function ) {
	function = reinterpret_cast< T >( getProcAddress( instance, name ) );
	if ( function == NULL ) {
		common->Warning( "Vulkan instance is missing %s", name );
		return false;
	}
	return true;
}

template< typename T >
bool LoadDeviceFunction( PFN_vkGetDeviceProcAddr getProcAddress,
	VkDevice device, const char* name, T& function ) {
	function = reinterpret_cast< T >( getProcAddress( device, name ) );
	if ( function == NULL ) {
		common->Warning( "Vulkan device is missing %s", name );
		return false;
	}
	return true;
}

bool HasExtension( const idList< VkExtensionProperties >& extensions,
	const char* name ) {
	for ( int i = 0; i < extensions.Num(); ++i ) {
		if ( idStr::Cmp( extensions[ i ].extensionName, name ) == 0 ) {
			return true;
		}
	}
	return false;
}

bool HasLayer( const idList< VkLayerProperties >& layers, const char* name ) {
	for ( int i = 0; i < layers.Num(); ++i ) {
		if ( idStr::Cmp( layers[ i ].layerName, name ) == 0 ) {
			return true;
		}
	}
	return false;
}

unsigned int ClampUnsigned( unsigned int value, unsigned int minimum,
	unsigned int maximum ) {
	if ( value < minimum ) {
		return minimum;
	}
	if ( maximum != 0 && value > maximum ) {
		return maximum;
	}
	return value;
}

struct sdVulkanPointerHash {
	template< class type >
	static int Hash( const idHashIndex& hasher, const type& value ) {
		const UINT_PTR pointer = reinterpret_cast< UINT_PTR >( value );
		const unsigned int folded = static_cast< unsigned int >(
			( pointer >> 4 ) ^ ( pointer >> 20 ) ^ ( pointer >> 36 ) );
		return hasher.GenerateKey( folded, 0 );
	}
};

typedef sdHashMapGeneric< const void*, int, sdHashCompareDefault,
	sdVulkanPointerHash > sdVulkanResourceLookup;

void AppendRayTracingInstances( idList< sdRayTracingGeometry >& destination,
	const sdRayTracingGeometry& source, const viewEntity_s* space ) {
	if ( space == NULL || space->numInsts <= 0 || space->insts == NULL ) {
		destination.Append( source );
		return;
	}
	for ( int instanceIndex = 0; instanceIndex < space->numInsts;
		++instanceIndex ) {
		const sdInstInfo& instance = space->insts[ instanceIndex ];
		sdRayTracingGeometry geometry = source;
		geometry.transform[ 0 ] = instance.inst.axis[ 0 ].x;
		geometry.transform[ 1 ] = instance.inst.axis[ 1 ].x;
		geometry.transform[ 2 ] = instance.inst.axis[ 2 ].x;
		geometry.transform[ 3 ] = instance.inst.origin.x;
		geometry.transform[ 4 ] = instance.inst.axis[ 0 ].y;
		geometry.transform[ 5 ] = instance.inst.axis[ 1 ].y;
		geometry.transform[ 6 ] = instance.inst.axis[ 2 ].y;
		geometry.transform[ 7 ] = instance.inst.origin.y;
		geometry.transform[ 8 ] = instance.inst.axis[ 0 ].z;
		geometry.transform[ 9 ] = instance.inst.axis[ 1 ].z;
		geometry.transform[ 10 ] = instance.inst.axis[ 2 ].z;
		geometry.transform[ 11 ] = instance.inst.origin.z;
		// Static stuff instances share immutable mesh data and therefore share a
		// BLAS.  If an instanced surface is deforming, the instance is the owner
		// so its frame-ring BLAS cannot be overwritten by a sibling instance.
		if ( geometry.deforming ) {
			geometry.geometryOwner = &space->insts[ instanceIndex ];
		}
		destination.Append( geometry );
	}
}

} // namespace

void R_VkTrackMemoryFunctions( PFN_vkAllocateMemory& allocateMemory,
	PFN_vkFreeMemory& freeMemory ) {
	if ( vkAllocateMemoryReal != NULL && vkFreeMemoryReal != NULL ) {
		allocateMemory = TrackedAllocateMemory;
		freeMemory = TrackedFreeMemory;
	}
}

struct sdVulkanBackendState {
	void*					vulkanLibrary;
	void*					nativeWindow;

	VkInstance				instance;
	VkDebugUtilsMessengerEXT	debugMessenger;
	VkSurfaceKHR				surface;
	VkPhysicalDevice			physicalDevice;
	VkPhysicalDeviceMemoryProperties memoryProperties;
	unsigned int				maxMemoryAllocationCount;
	VkDevice				device;
	VkQueue					graphicsQueue;
	unsigned int				queueFamilyIndex;
	char					deviceName[ VK_MAX_PHYSICAL_DEVICE_NAME_SIZE ];
	unsigned int			vendorID;
	char					vendorString[ 64 ];
	char					versionString[ 32 + VK_MAX_DRIVER_NAME_SIZE + VK_MAX_DRIVER_INFO_SIZE ];
	bool					fillModeNonSolid;
	bool					textureCompressionBC;
	bool					imageViewFormatSwizzle;	// false on a portability subset (MoltenVK) without it
	bool					rayTracingDeviceEnabled;
	VkDeviceSize			jointUniformAlignment;

	VkSwapchainKHR				swapchain;
	VkFormat				swapchainFormat;
	VkColorSpaceKHR			colorSpace;
	VkExtent2D				extent;
	idList< VkImage >			swapchainImages;
	idList< VkImageView >		swapchainViews;
	idList< VkSemaphore >	presentSemaphores;
	idList< byte >			imageInitialized;
	idList< VkImage >			depthImages;
	idList< VkDeviceMemory >	depthMemory;
	idList< VkImageView >		depthViews;
	idList< VkImageView >		depthSampleViews;
	idList< byte >			depthInitialized;
	bool					swapchainDirty;
	bool					swapchainTransferSource;
	// r_vkScreenshot readback: a host buffer filled by the frame's command buffer
	VkBuffer				screenshotBuffer;
	VkDeviceMemory			screenshotMemory;
	idStr					screenshotPath;
	uint32_t				screenshotWidth;
	uint32_t				screenshotHeight;
	VkFormat				screenshotFormat;
	int					swapInterval;
	sdVulkanImageResource	currentRenderResource;
	bool					currentRenderInitialized;

	sdVulkanFrame			frames[ NUM_VULKAN_FRAMES ];
	unsigned int				frameIndex;
	unsigned int				imageIndex;
	bool					frameActive;

	struct sdToolWindow {
		void*				window;
		VkSurfaceKHR		surface;
		VkSwapchainKHR		swapchain;
		VkExtent2D			extent;
		idList< VkImage >	images;
		idList< VkImageView > views;
		idList< VkSemaphore > presentSemaphores;
		idList< byte >		imageInitialized;
		idList< VkImage >	depthImages;
		idList< VkDeviceMemory > depthMemory;
		idList< VkImageView > depthViews;
		idList< byte >		depthInitialized;
		bool				dirty;

		sdToolWindow() {
			window = NULL;
			surface = VK_NULL_HANDLE;
			swapchain = VK_NULL_HANDLE;
			extent.width = extent.height = 0;
			dirty = true;
		}
	};
	struct sdToolRenderTarget {
		const void*		owner;
		VkExtent2D		contentExtent;
		VkExtent2D		allocationExtent;
		VkImage			depthImage;
		VkDeviceMemory	depthMemory;
		VkImageView		depthView;
		bool			initialized;

			sdToolRenderTarget() {
			owner = NULL;
			contentExtent.width = contentExtent.height = 0;
			allocationExtent.width = allocationExtent.height = 0;
			depthImage = VK_NULL_HANDLE;
			depthMemory = VK_NULL_HANDLE;
			depthView = VK_NULL_HANDLE;
			initialized = false;
		}
	};
	idList< sdToolWindow* > toolWindows;
	idList< sdToolRenderTarget* > toolRenderTargets;
	idList< sdVulkanToolDepthResource > retiredToolDepthResources;
	sdToolWindow*			activeToolWindow;
	sdToolRenderTarget*	activeToolRenderTarget;
	VkExtent2D			toolRenderExtent;
	unsigned int			toolImageIndex;
	bool					toolFrameActive;
	const void*			toolImageOwner;
	VkRect2D				toolScissor;
	VkPipeline			toolPipeline;
	VkPipeline			toolBlendPipeline;
	VkPipeline			toolDepthPipeline;
	VkPipeline			toolDepthBlendPipeline;
	VkPipeline			toolModelPipeline;
	VkPipeline			toolModelBlendPipeline;
	VkPipeline			toolModelDepthPipeline;
	VkPipeline			toolModelDepthBlendPipeline;
	VkPipeline			toolModelWirePipeline;
	VkPipeline			toolModelWireBlendPipeline;
	VkPipeline			toolModelWireDepthPipeline;
	VkPipeline			toolModelWireDepthBlendPipeline;
	unsigned int				framesPresented;
	unsigned int				guiDrawCalls;
	unsigned int				worldDrawCalls;
	unsigned int				worldDepthDrawCalls;
	unsigned int				worldViewAttempts;
	unsigned int				worldViews;
	unsigned int				worldSurfaceCandidates;
	unsigned int				worldCacheReady;
	unsigned int				worldMaterialReady;
	unsigned int				worldResourceReady;
	unsigned int				worldMissingGeometry;
	unsigned int				worldMissingCache;
	unsigned int				worldMissingMaterial;
	unsigned int				worldMissingResource;
	unsigned int				worldMegaDrawCalls;
	unsigned int				worldSkinnedDrawCalls;
	unsigned int				worldGpuSkinnedDrawCalls;
	unsigned int				worldSkyDrawCalls;
	unsigned int				worldStageDrawCalls;
	unsigned int				worldWaterDrawCalls;
	unsigned int				worldStuffDrawCalls;
	unsigned int				worldShadowDrawCalls;
	unsigned int				worldInteractionDrawCalls;
	unsigned int				worldWaterDescriptorMisses;
	idList< const idMaterial* > reportedMissingMaterials;
	idList< const idMaterial* > reportedDrawMaterials;
	idList< sdRayTracingGeometry > rayTracingGeometryScratch;
	idList< sdRayTracingGeometry > rayTracingWaterGeometryScratch;
	idList< sdVulkanImageResource > imageResources;
	idList< sdVulkanBufferResource > bufferResources;
	// Blocks the vertex-cache buffers are sub-allocated from; a freed block
	// leaves a NULL slot so the indexes in sdVulkanBufferResource stay valid.
	idList< sdVulkanMemoryBlock* > bufferMemoryBlocks;
	idList< sdVulkanPendingBufferUpload > pendingBufferUploads;
	VkDeviceSize				pendingBufferUploadBytes;
	sdVulkanResourceLookup		imageResourceLookup;
	sdVulkanResourceLookup		bufferResourceLookup;
	idList< sdVulkanMaterialDescriptor > materialDescriptors;
	idHashIndex				materialDescriptorHash;
	// sets of material descriptors whose image went away, freed to guiDescriptorPool
	// with that frame's retired images (#262)
	idList< VkDescriptorSet > frameRetiredMaterialSets[ NUM_VULKAN_FRAMES ];
	bool					materialPoolExhaustedWarned;
	idList< sdVulkanFrontEndDescriptor > frontEndDescriptors;
	idHashIndex				frontEndDescriptorHash;	// by owner
	VkDescriptorPool			frontEndDescriptorPool;
	// sets of front-end descriptors whose image went away, freed with that frame's
	// retired images
	idList< VkDescriptorSet > frameRetiredFrontEndSets[ NUM_VULKAN_FRAMES ];
	unsigned int				frameSerial;	// counts frames, for evicting unused sets
	int					frontEndDescriptorSetsLive;	// allocated from frontEndDescriptorPool, not yet freed
	bool					frontEndPoolExhaustedWarned;
	// Resources removed while a frame is being recorded must remain alive until
	// that command buffer has been submitted and completed.  ETQW retires vertex
	// cache blocks in idRenderSystemLocal::EndFrame before the Vulkan backend
	// submits its frame, so destroying them immediately invalidates recorded
	// bindings and can asynchronously lose the device.
	idList< sdVulkanImageResource > retiredImageResources;
	idList< sdVulkanBufferResource > retiredBufferResources;
	idList< sdVulkanImageResource > frameRetiredImageResources[ NUM_VULKAN_FRAMES ];
	idList< sdVulkanBufferResource > frameRetiredBufferResources[ NUM_VULKAN_FRAMES ];
	VkDescriptorSetLayout		guiDescriptorSetLayout;
	VkDescriptorSetLayout		jointDescriptorSetLayout;
	VkDescriptorPool			guiDescriptorPool;
	VkPipelineLayout			guiPipelineLayout;
	VkDescriptorSetLayout		frontEndDescriptorSetLayout;
	VkPipelineLayout			frontEndPipelineLayout;
	bool					megaDetailPipeline;	// worldMegaPipeline uses the front-end layout
	VkPipeline				worldFrontEndPipeline;
	VkPipeline				worldFrontEndAlphaPipeline;
	VkPipeline				worldFrontEndAddPipeline;
	VkPipeline				guiPipeline;
	VkPipeline				guiOpaquePipeline;
	VkPipeline				guiAddPipeline;
	VkPipeline				guiAlphaAddPipeline;
	VkPipeline				guiMultiplyPipeline;
	// guis/postprocess's postprocess/glow (glow.frag, #233): retail's
	// _postProcessBuffer_0 and _1 at a quarter of the screen, a descriptor set per
	// pass (downsample, two blurs, the glow), and the atmosphere of the last view
	sdVulkanImageResource	glowBuffers[ 2 ];
	bool					glowBufferInitialized[ 2 ];
	VkDescriptorSet			glowDescriptorSets[ 4 ];
	VkPipeline				glowBufferPipeline;
	VkPipeline				glowScreenPipeline;
	const sdDeclAtmosphere*	glowAtmosphere;
	VkPipeline				worldPipeline;
	VkPipeline				worldDepthPipeline;
	VkPipeline				worldAlphaPipeline;
	VkPipeline				worldAddPipeline;
	VkPipeline				worldAlphaAddPipeline;
	VkPipeline				worldMultiplyPipeline;
	VkPipeline				worldMegaPipeline;
	VkPipeline				worldAtmospherePipeline;
	VkPipeline				worldMaterialPipeline;
	VkPipeline				worldMaterialAlphaPipeline;
	VkPipeline				worldMaterialAddPipeline;
	VkPipeline				worldWaterPipeline;
	VkPipeline				worldHeatHazePipeline;
	VkPipeline				worldSkyboxPipeline;
	VkPipeline				worldSkyboxAlphaPipeline;
	VkPipeline				worldSkyboxAddPipeline;
	VkPipeline				worldSkinnedPipeline;
	VkPipeline				worldSkinnedDepthPipeline;
	VkPipeline				worldSkinnedAlphaPipeline;
	VkPipeline				worldSkinnedAddPipeline;
	VkPipeline				worldSkinnedAlphaAddPipeline;
	VkPipeline				worldSkinnedMultiplyPipeline;
	VkPipeline				worldSkinnedMegaPipeline;
	VkPipeline				worldSkinnedAtmospherePipeline;
	VkPipeline				worldSkinnedMaterialPipeline;
	VkPipeline				worldSkinnedMaterialAlphaPipeline;
	VkPipeline				worldSkinnedMaterialAddPipeline;
	VkPipeline				worldSkinnedWaterPipeline;
	VkPipeline				worldSkinnedHeatHazePipeline;
	VkPipeline				worldSkinnedSkyboxPipeline;
	VkPipeline				worldSkinnedSkyboxAlphaPipeline;
	VkPipeline				worldSkinnedSkyboxAddPipeline;
	VkPipeline				skyPipeline;
	VkPipeline				worldShadowPassPipeline;
	VkPipeline				worldShadowFailPipeline;
	VkPipeline				worldInteractionPipeline;
	// rasterized lighting (no ray-query device): unshadowed lights, and the
	// ambient scale applied to the opaque base before the interactions
	VkPipeline				worldInteractionUnshadowedPipeline;
	// GPU-skinned surfaces: skinned_interaction.vert with the weight stream
	VkPipeline				worldInteractionSkinnedPipeline;
	VkPipeline				worldInteractionSkinnedUnshadowedPipeline;
	// the atmosphere fog over the lit opaque surfaces (fog.frag, #233)
	VkPipeline				worldFogPipeline;
	VkPipeline				worldFogSkinnedPipeline;
	// the ambient cube term of the lit opaque surfaces (ambient_cube.frag, #122)
	// (2 * src * dst: the cube's ambient, marking the stencil; dst + src: self-illumination)
	VkPipeline				worldAmbientCubePipeline;
	VkPipeline				worldAmbientCubeSkinnedPipeline;
	VkPipeline				worldAmbientCubeAddPipeline;
	VkPipeline				worldAmbientCubeAddSkinnedPipeline;
	// #265: an amblit surface's ambient x minSpecShadowColor where the sun's stencil
	// says shadow (ambient_cube.frag's mode 2)
	VkPipeline				worldAmbientShadowPipeline;
	VkPipeline				worldAmbientShadowSkinnedPipeline;
	// set while the light block's snapshot of the opaque base is in _currentRender
	bool					sceneAlbedoCopied;
	VkPipeline				worldLightAmbientPipeline;
	// floats in the shared push-constant range: 40 when the device allows the
	// interaction's light block (160 bytes), else the original 32
	int						pushConstantFloats;
	uint32_t				maxPushConstantsSize;
	VkFormat				depthStencilFormat;
	VkFormat				guiPipelineFormat;

	PFN_vkGetInstanceProcAddr			GetInstanceProcAddr;
	PFN_vkEnumerateInstanceVersion		EnumerateInstanceVersion;
	PFN_vkEnumerateInstanceExtensionProperties EnumerateInstanceExtensionProperties;
	PFN_vkEnumerateInstanceLayerProperties	EnumerateInstanceLayerProperties;
	PFN_vkCreateInstance				CreateInstance;
	PFN_vkDestroyInstance				DestroyInstance;
	PFN_vkCreateDebugUtilsMessengerEXT	CreateDebugUtilsMessengerEXT;
	PFN_vkDestroyDebugUtilsMessengerEXT	DestroyDebugUtilsMessengerEXT;
#if defined( _WIN32 )
	PFN_vkCreateWin32SurfaceKHR			CreateWin32SurfaceKHR;
#endif
	PFN_vkDestroySurfaceKHR			DestroySurfaceKHR;
	PFN_vkEnumeratePhysicalDevices		EnumeratePhysicalDevices;
	PFN_vkGetPhysicalDeviceProperties		GetPhysicalDeviceProperties;
	PFN_vkGetPhysicalDeviceFormatProperties	GetPhysicalDeviceFormatProperties;
	PFN_vkGetPhysicalDeviceMemoryProperties GetPhysicalDeviceMemoryProperties;
	PFN_vkGetPhysicalDeviceFeatures2		GetPhysicalDeviceFeatures2;
	PFN_vkGetPhysicalDeviceProperties2		GetPhysicalDeviceProperties2;
	PFN_vkGetPhysicalDeviceQueueFamilyProperties GetPhysicalDeviceQueueFamilyProperties;
	PFN_vkEnumerateDeviceExtensionProperties	EnumerateDeviceExtensionProperties;
	PFN_vkGetPhysicalDeviceSurfaceSupportKHR	GetPhysicalDeviceSurfaceSupportKHR;
	PFN_vkGetPhysicalDeviceSurfaceCapabilitiesKHR GetPhysicalDeviceSurfaceCapabilitiesKHR;
	PFN_vkGetPhysicalDeviceSurfaceFormatsKHR	GetPhysicalDeviceSurfaceFormatsKHR;
	PFN_vkGetPhysicalDeviceSurfacePresentModesKHR GetPhysicalDeviceSurfacePresentModesKHR;
	PFN_vkCreateDevice				CreateDevice;
	PFN_vkGetDeviceProcAddr				GetDeviceProcAddr;

	PFN_vkDestroyDevice				DestroyDevice;
	PFN_vkGetDeviceQueue				GetDeviceQueue;
	PFN_vkDeviceWaitIdle				DeviceWaitIdle;
	PFN_vkQueueWaitIdle				QueueWaitIdle;
	PFN_vkCreateSwapchainKHR			CreateSwapchainKHR;
	PFN_vkDestroySwapchainKHR			DestroySwapchainKHR;
	PFN_vkGetSwapchainImagesKHR		GetSwapchainImagesKHR;
	PFN_vkAcquireNextImageKHR			AcquireNextImageKHR;
	PFN_vkQueuePresentKHR				QueuePresentKHR;
	PFN_vkCreateImageView				CreateImageView;
	PFN_vkDestroyImageView				DestroyImageView;
	PFN_vkCreateImage					CreateImage;
	PFN_vkDestroyImage				DestroyImage;
	PFN_vkGetImageMemoryRequirements	GetImageMemoryRequirements;
	PFN_vkAllocateMemory				AllocateMemory;
	PFN_vkFreeMemory					FreeMemory;
	PFN_vkBindImageMemory				BindImageMemory;
	PFN_vkCreateSampler				CreateSampler;
	PFN_vkDestroySampler				DestroySampler;
	PFN_vkCreateBuffer				CreateBuffer;
	PFN_vkDestroyBuffer				DestroyBuffer;
	PFN_vkGetBufferMemoryRequirements	GetBufferMemoryRequirements;
	PFN_vkBindBufferMemory				BindBufferMemory;
	PFN_vkMapMemory					MapMemory;
	PFN_vkUnmapMemory					UnmapMemory;
	PFN_vkCreateShaderModule			CreateShaderModule;
	PFN_vkDestroyShaderModule			DestroyShaderModule;
	PFN_vkCreateDescriptorSetLayout	CreateDescriptorSetLayout;
	PFN_vkDestroyDescriptorSetLayout	DestroyDescriptorSetLayout;
	PFN_vkCreateDescriptorPool			CreateDescriptorPool;
	PFN_vkDestroyDescriptorPool		DestroyDescriptorPool;
	PFN_vkAllocateDescriptorSets		AllocateDescriptorSets;
	PFN_vkFreeDescriptorSets			FreeDescriptorSets;
	PFN_vkUpdateDescriptorSets			UpdateDescriptorSets;
	PFN_vkCreatePipelineLayout			CreatePipelineLayout;
	PFN_vkDestroyPipelineLayout		DestroyPipelineLayout;
	PFN_vkCreateGraphicsPipelines		CreateGraphicsPipelines;
	PFN_vkDestroyPipeline				DestroyPipeline;
	PFN_vkCreateCommandPool			CreateCommandPool;
	PFN_vkDestroyCommandPool			DestroyCommandPool;
	PFN_vkResetCommandPool				ResetCommandPool;
	PFN_vkAllocateCommandBuffers		AllocateCommandBuffers;
	PFN_vkBeginCommandBuffer			BeginCommandBuffer;
	PFN_vkEndCommandBuffer				EndCommandBuffer;
	PFN_vkCreateSemaphore				CreateSemaphore;
	PFN_vkDestroySemaphore				DestroySemaphore;
	PFN_vkCreateFence				CreateFence;
	PFN_vkDestroyFence				DestroyFence;
	PFN_vkWaitForFences				WaitForFences;
	PFN_vkResetFences				ResetFences;
	PFN_vkQueueSubmit2				QueueSubmit2;
	PFN_vkCmdPipelineBarrier2			CmdPipelineBarrier2;
	PFN_vkCmdCopyBufferToImage			CmdCopyBufferToImage;
	PFN_vkCmdCopyBuffer				CmdCopyBuffer;
	PFN_vkCmdCopyImage				CmdCopyImage;
	PFN_vkCmdCopyImageToBuffer		CmdCopyImageToBuffer;
	PFN_vkCmdBlitImage				CmdBlitImage;
	PFN_vkCmdBeginRendering			CmdBeginRendering;
	PFN_vkCmdEndRendering				CmdEndRendering;
	PFN_vkCmdSetViewport				CmdSetViewport;
	PFN_vkCmdSetScissor				CmdSetScissor;
	PFN_vkCmdClearAttachments		CmdClearAttachments;
	PFN_vkCmdSetDepthBias			CmdSetDepthBias;
	PFN_vkCmdBindPipeline				CmdBindPipeline;
	PFN_vkCmdBindDescriptorSets		CmdBindDescriptorSets;
	PFN_vkCmdPushConstants				CmdPushConstants;
	PFN_vkCmdBindVertexBuffers		CmdBindVertexBuffers;
	PFN_vkCmdBindIndexBuffer			CmdBindIndexBuffer;
	PFN_vkCmdDraw					CmdDraw;
	PFN_vkCmdDrawIndexed				CmdDrawIndexed;

	sdVulkanBackendState() {
		vulkanLibrary = NULL;
		nativeWindow = NULL;
		instance = VK_NULL_HANDLE;
		debugMessenger = VK_NULL_HANDLE;
		surface = VK_NULL_HANDLE;
		physicalDevice = VK_NULL_HANDLE;
		memset( &memoryProperties, 0, sizeof( memoryProperties ) );
		maxMemoryAllocationCount = 0;
		pendingBufferUploadBytes = 0;
		device = VK_NULL_HANDLE;
		graphicsQueue = VK_NULL_HANDLE;
		queueFamilyIndex = UINT_MAX;
		deviceName[ 0 ] = '\0';
		vendorID = 0;
		vendorString[ 0 ] = '\0';
		versionString[ 0 ] = '\0';
		fillModeNonSolid = false;
		textureCompressionBC = false;
		imageViewFormatSwizzle = true;
		rayTracingDeviceEnabled = false;
		jointUniformAlignment = 1;
		swapchain = VK_NULL_HANDLE;
		swapchainFormat = VK_FORMAT_UNDEFINED;
		colorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR;
		extent.width = 0;
		extent.height = 0;
		swapchainDirty = false;
		swapchainTransferSource = false;
		screenshotBuffer = VK_NULL_HANDLE;
		screenshotMemory = VK_NULL_HANDLE;
		screenshotWidth = 0;
		screenshotHeight = 0;
		screenshotFormat = VK_FORMAT_UNDEFINED;
		swapInterval = 0;
		memset( &currentRenderResource, 0, sizeof( currentRenderResource ) );
		currentRenderInitialized = false;
		memset( frames, 0, sizeof( frames ) );
		frameIndex = 0;
		imageIndex = 0;
		frameActive = false;
		activeToolWindow = NULL;
		activeToolRenderTarget = NULL;
		toolRenderExtent.width = toolRenderExtent.height = 0;
		toolImageIndex = 0;
		toolFrameActive = false;
		toolImageOwner = NULL;
		memset( &toolScissor, 0, sizeof( toolScissor ) );
		rayTracingGeometryScratch.SetGranularity( 256 );
		rayTracingWaterGeometryScratch.SetGranularity( 32 );
		toolPipeline = VK_NULL_HANDLE;
		toolBlendPipeline = VK_NULL_HANDLE;
		toolDepthPipeline = VK_NULL_HANDLE;
		toolDepthBlendPipeline = VK_NULL_HANDLE;
		toolModelPipeline = VK_NULL_HANDLE;
		toolModelBlendPipeline = VK_NULL_HANDLE;
		toolModelDepthPipeline = VK_NULL_HANDLE;
		toolModelDepthBlendPipeline = VK_NULL_HANDLE;
		toolModelWirePipeline = VK_NULL_HANDLE;
		toolModelWireBlendPipeline = VK_NULL_HANDLE;
		toolModelWireDepthPipeline = VK_NULL_HANDLE;
		toolModelWireDepthBlendPipeline = VK_NULL_HANDLE;
		framesPresented = 0;
		guiDrawCalls = 0;
		worldDrawCalls = 0;
		worldDepthDrawCalls = 0;
		worldViewAttempts = 0;
		worldViews = 0;
		worldSurfaceCandidates = 0;
		worldCacheReady = 0;
		worldMaterialReady = 0;
		worldResourceReady = 0;
		worldMissingGeometry = 0;
		worldMissingCache = 0;
		worldMissingMaterial = 0;
		worldMissingResource = 0;
		worldMegaDrawCalls = 0;
		worldSkinnedDrawCalls = 0;
		worldGpuSkinnedDrawCalls = 0;
		worldSkyDrawCalls = 0;
		worldStageDrawCalls = 0;
		worldWaterDrawCalls = 0;
		worldStuffDrawCalls = 0;
		worldShadowDrawCalls = 0;
		worldInteractionDrawCalls = 0;
		worldWaterDescriptorMisses = 0;
		guiDescriptorSetLayout = VK_NULL_HANDLE;
		jointDescriptorSetLayout = VK_NULL_HANDLE;
		guiDescriptorPool = VK_NULL_HANDLE;
		guiPipelineLayout = VK_NULL_HANDLE;
		frontEndDescriptorSetLayout = VK_NULL_HANDLE;
		frontEndPipelineLayout = VK_NULL_HANDLE;
		frontEndDescriptorPool = VK_NULL_HANDLE;
		frameSerial = 0;
		frontEndDescriptorSetsLive = 0;
		frontEndPoolExhaustedWarned = false;
		materialPoolExhaustedWarned = false;
		megaDetailPipeline = false;
		worldFrontEndPipeline = VK_NULL_HANDLE;
		worldFrontEndAlphaPipeline = VK_NULL_HANDLE;
		worldFrontEndAddPipeline = VK_NULL_HANDLE;
		guiPipeline = VK_NULL_HANDLE;
		guiOpaquePipeline = VK_NULL_HANDLE;
		guiAddPipeline = VK_NULL_HANDLE;
		guiAlphaAddPipeline = VK_NULL_HANDLE;
		guiMultiplyPipeline = VK_NULL_HANDLE;
		memset( glowBuffers, 0, sizeof( glowBuffers ) );
		memset( glowBufferInitialized, 0, sizeof( glowBufferInitialized ) );
		memset( glowDescriptorSets, 0, sizeof( glowDescriptorSets ) );
		glowBufferPipeline = VK_NULL_HANDLE;
		glowScreenPipeline = VK_NULL_HANDLE;
		glowAtmosphere = NULL;
		worldPipeline = VK_NULL_HANDLE;
		worldDepthPipeline = VK_NULL_HANDLE;
		worldAlphaPipeline = VK_NULL_HANDLE;
		worldAddPipeline = VK_NULL_HANDLE;
		worldAlphaAddPipeline = VK_NULL_HANDLE;
		worldMultiplyPipeline = VK_NULL_HANDLE;
		worldMegaPipeline = VK_NULL_HANDLE;
		worldAtmospherePipeline = VK_NULL_HANDLE;
		worldMaterialPipeline = VK_NULL_HANDLE;
		worldMaterialAlphaPipeline = VK_NULL_HANDLE;
		worldMaterialAddPipeline = VK_NULL_HANDLE;
		worldWaterPipeline = VK_NULL_HANDLE;
		worldHeatHazePipeline = VK_NULL_HANDLE;
		worldSkyboxPipeline = VK_NULL_HANDLE;
		worldSkyboxAlphaPipeline = VK_NULL_HANDLE;
		worldSkyboxAddPipeline = VK_NULL_HANDLE;
		worldSkinnedPipeline = VK_NULL_HANDLE;
		worldSkinnedDepthPipeline = VK_NULL_HANDLE;
		worldSkinnedAlphaPipeline = VK_NULL_HANDLE;
		worldSkinnedAddPipeline = VK_NULL_HANDLE;
		worldSkinnedAlphaAddPipeline = VK_NULL_HANDLE;
		worldSkinnedMultiplyPipeline = VK_NULL_HANDLE;
		worldSkinnedMegaPipeline = VK_NULL_HANDLE;
		worldSkinnedAtmospherePipeline = VK_NULL_HANDLE;
		worldSkinnedMaterialPipeline = VK_NULL_HANDLE;
		worldSkinnedMaterialAlphaPipeline = VK_NULL_HANDLE;
		worldSkinnedMaterialAddPipeline = VK_NULL_HANDLE;
		worldSkinnedWaterPipeline = VK_NULL_HANDLE;
		worldSkinnedHeatHazePipeline = VK_NULL_HANDLE;
		worldSkinnedSkyboxPipeline = VK_NULL_HANDLE;
		worldSkinnedSkyboxAlphaPipeline = VK_NULL_HANDLE;
		worldSkinnedSkyboxAddPipeline = VK_NULL_HANDLE;
		skyPipeline = VK_NULL_HANDLE;
		worldShadowPassPipeline = VK_NULL_HANDLE;
		worldShadowFailPipeline = VK_NULL_HANDLE;
		worldInteractionPipeline = VK_NULL_HANDLE;
		worldInteractionUnshadowedPipeline = VK_NULL_HANDLE;
		worldInteractionSkinnedPipeline = VK_NULL_HANDLE;
		worldInteractionSkinnedUnshadowedPipeline = VK_NULL_HANDLE;
		worldFogPipeline = VK_NULL_HANDLE;
		worldFogSkinnedPipeline = VK_NULL_HANDLE;
		worldAmbientCubePipeline = VK_NULL_HANDLE;
		worldAmbientCubeSkinnedPipeline = VK_NULL_HANDLE;
		worldAmbientCubeAddPipeline = VK_NULL_HANDLE;
		worldAmbientCubeAddSkinnedPipeline = VK_NULL_HANDLE;
		worldAmbientShadowPipeline = VK_NULL_HANDLE;
		worldAmbientShadowSkinnedPipeline = VK_NULL_HANDLE;
		sceneAlbedoCopied = false;
		worldLightAmbientPipeline = VK_NULL_HANDLE;
		pushConstantFloats = 32;
		maxPushConstantsSize = 128;
		depthStencilFormat = VK_FORMAT_UNDEFINED;
		guiPipelineFormat = VK_FORMAT_UNDEFINED;

#define CLEAR_VK_FUNCTION( name ) name = NULL
		CLEAR_VK_FUNCTION( GetInstanceProcAddr );
		CLEAR_VK_FUNCTION( EnumerateInstanceVersion );
		CLEAR_VK_FUNCTION( EnumerateInstanceExtensionProperties );
		CLEAR_VK_FUNCTION( EnumerateInstanceLayerProperties );
		CLEAR_VK_FUNCTION( CreateInstance );
		CLEAR_VK_FUNCTION( DestroyInstance );
		CLEAR_VK_FUNCTION( CreateDebugUtilsMessengerEXT );
		CLEAR_VK_FUNCTION( DestroyDebugUtilsMessengerEXT );
#if defined( _WIN32 )
		CLEAR_VK_FUNCTION( CreateWin32SurfaceKHR );
#endif
		CLEAR_VK_FUNCTION( DestroySurfaceKHR );
		CLEAR_VK_FUNCTION( EnumeratePhysicalDevices );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceProperties );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceFormatProperties );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceMemoryProperties );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceFeatures2 );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceProperties2 );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceQueueFamilyProperties );
		CLEAR_VK_FUNCTION( EnumerateDeviceExtensionProperties );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceSurfaceSupportKHR );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceSurfaceCapabilitiesKHR );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceSurfaceFormatsKHR );
		CLEAR_VK_FUNCTION( GetPhysicalDeviceSurfacePresentModesKHR );
		CLEAR_VK_FUNCTION( CreateDevice );
		CLEAR_VK_FUNCTION( GetDeviceProcAddr );
		CLEAR_VK_FUNCTION( DestroyDevice );
		CLEAR_VK_FUNCTION( GetDeviceQueue );
		CLEAR_VK_FUNCTION( DeviceWaitIdle );
		CLEAR_VK_FUNCTION( QueueWaitIdle );
		CLEAR_VK_FUNCTION( CreateSwapchainKHR );
		CLEAR_VK_FUNCTION( DestroySwapchainKHR );
		CLEAR_VK_FUNCTION( GetSwapchainImagesKHR );
		CLEAR_VK_FUNCTION( AcquireNextImageKHR );
		CLEAR_VK_FUNCTION( QueuePresentKHR );
		CLEAR_VK_FUNCTION( CreateImageView );
		CLEAR_VK_FUNCTION( DestroyImageView );
		CLEAR_VK_FUNCTION( CreateImage );
		CLEAR_VK_FUNCTION( DestroyImage );
		CLEAR_VK_FUNCTION( GetImageMemoryRequirements );
		CLEAR_VK_FUNCTION( AllocateMemory );
		CLEAR_VK_FUNCTION( FreeMemory );
		CLEAR_VK_FUNCTION( BindImageMemory );
		CLEAR_VK_FUNCTION( CreateSampler );
		CLEAR_VK_FUNCTION( DestroySampler );
		CLEAR_VK_FUNCTION( CreateBuffer );
		CLEAR_VK_FUNCTION( DestroyBuffer );
		CLEAR_VK_FUNCTION( GetBufferMemoryRequirements );
		CLEAR_VK_FUNCTION( BindBufferMemory );
		CLEAR_VK_FUNCTION( MapMemory );
		CLEAR_VK_FUNCTION( UnmapMemory );
		CLEAR_VK_FUNCTION( CreateShaderModule );
		CLEAR_VK_FUNCTION( DestroyShaderModule );
		CLEAR_VK_FUNCTION( CreateDescriptorSetLayout );
		CLEAR_VK_FUNCTION( DestroyDescriptorSetLayout );
		CLEAR_VK_FUNCTION( CreateDescriptorPool );
		CLEAR_VK_FUNCTION( DestroyDescriptorPool );
		CLEAR_VK_FUNCTION( AllocateDescriptorSets );
		CLEAR_VK_FUNCTION( FreeDescriptorSets );
		CLEAR_VK_FUNCTION( UpdateDescriptorSets );
		CLEAR_VK_FUNCTION( CreatePipelineLayout );
		CLEAR_VK_FUNCTION( DestroyPipelineLayout );
		CLEAR_VK_FUNCTION( CreateGraphicsPipelines );
		CLEAR_VK_FUNCTION( DestroyPipeline );
		CLEAR_VK_FUNCTION( CreateCommandPool );
		CLEAR_VK_FUNCTION( DestroyCommandPool );
		CLEAR_VK_FUNCTION( ResetCommandPool );
		CLEAR_VK_FUNCTION( AllocateCommandBuffers );
		CLEAR_VK_FUNCTION( BeginCommandBuffer );
		CLEAR_VK_FUNCTION( EndCommandBuffer );
		CLEAR_VK_FUNCTION( CreateSemaphore );
		CLEAR_VK_FUNCTION( DestroySemaphore );
		CLEAR_VK_FUNCTION( CreateFence );
		CLEAR_VK_FUNCTION( DestroyFence );
		CLEAR_VK_FUNCTION( WaitForFences );
		CLEAR_VK_FUNCTION( ResetFences );
		CLEAR_VK_FUNCTION( QueueSubmit2 );
		CLEAR_VK_FUNCTION( CmdPipelineBarrier2 );
		CLEAR_VK_FUNCTION( CmdCopyBufferToImage );
		CLEAR_VK_FUNCTION( CmdCopyBuffer );
		CLEAR_VK_FUNCTION( CmdCopyImage );
		CLEAR_VK_FUNCTION( CmdCopyImageToBuffer );
		CLEAR_VK_FUNCTION( CmdBlitImage );
		CLEAR_VK_FUNCTION( CmdBeginRendering );
		CLEAR_VK_FUNCTION( CmdEndRendering );
		CLEAR_VK_FUNCTION( CmdSetViewport );
		CLEAR_VK_FUNCTION( CmdSetScissor );
		CLEAR_VK_FUNCTION( CmdClearAttachments );
		CLEAR_VK_FUNCTION( CmdBindPipeline );
		CLEAR_VK_FUNCTION( CmdBindDescriptorSets );
		CLEAR_VK_FUNCTION( CmdPushConstants );
		CLEAR_VK_FUNCTION( CmdBindVertexBuffers );
		CLEAR_VK_FUNCTION( CmdBindIndexBuffer );
		CLEAR_VK_FUNCTION( CmdDraw );
		CLEAR_VK_FUNCTION( CmdDrawIndexed );
#undef CLEAR_VK_FUNCTION
	}
};

namespace {

void EncodeOctahedralDirection( idVec3 direction, float& encodedX,
	float& encodedY ) {
	if ( direction.Normalize() == 0.0f ) {
		direction.Set( 0.0f, 0.0f, 1.0f );
	}
	const float denominator = idMath::Fabs( direction.x ) +
		idMath::Fabs( direction.y ) + idMath::Fabs( direction.z );
	encodedX = direction.x / denominator;
	encodedY = direction.y / denominator;
	if ( direction.z < 0.0f ) {
		const float oldX = encodedX;
		const float oldY = encodedY;
		encodedX = ( 1.0f - idMath::Fabs( oldY ) ) *
			( oldX < 0.0f ? -1.0f : 1.0f );
		encodedY = ( 1.0f - idMath::Fabs( oldX ) ) *
			( oldY < 0.0f ? -1.0f : 1.0f );
	}
}

float PackSnorm2x16AsFloat( float x, float y ) {
	const short packedX = static_cast< short >( idMath::Ftoi(
		idMath::ClampFloat( -1.0f, 1.0f, x ) * 32767.0f ) );
	const short packedY = static_cast< short >( idMath::Ftoi(
		idMath::ClampFloat( -1.0f, 1.0f, y ) * 32767.0f ) );
	const unsigned int packed =
		static_cast< unsigned short >( packedX ) |
		( static_cast< unsigned int >(
			static_cast< unsigned short >( packedY ) ) << 16 );
	float encoded;
	memcpy( &encoded, &packed, sizeof( encoded ) );
	return encoded;
}

void EncodeModelRotation( const float modelMatrix[ 16 ], float& encodedXY,
	float& encodedZW ) {
	idMat3 modelAxis;
	modelAxis[ 0 ].Set( modelMatrix[ 0 ], modelMatrix[ 1 ], modelMatrix[ 2 ] );
	modelAxis[ 1 ].Set( modelMatrix[ 4 ], modelMatrix[ 5 ], modelMatrix[ 6 ] );
	modelAxis[ 2 ].Set( modelMatrix[ 8 ], modelMatrix[ 9 ], modelMatrix[ 10 ] );
	idQuat modelRotation = modelAxis.ToQuat();
	modelRotation.Normalize();
	encodedXY = PackSnorm2x16AsFloat( modelRotation.x, modelRotation.y );
	encodedZW = PackSnorm2x16AsFloat( modelRotation.z, modelRotation.w );
}

// Retail's foliage programs colour each vertex by its colour attribute whatever the
// stage's vertexColor (foliage_vertex.inc: MOV result.color, $colorAttrib); the tree
// models carry their leaves' green there (#246).  stuff/grass reads it the same way:
// its rgb is the instance tint, and under Vulkan its alpha the fade (#256).
bool VulkanStageUsesVertexColor( const materialStage_t* stage ) {
	if ( stage == NULL ) {
		return false;
	}
	return stage->vertexColor == SVC_MODULATE || ( stage->renderProgram != NULL &&
		( idStr::Icmpn( stage->renderProgram->GetName(), "interaction/foliage", 19 ) == 0 ||
		  idStr::Icmpn( stage->renderProgram->GetName(), "stuff/grass", 11 ) == 0 ) );
}

void ReportMissingVulkanMaterial( sdVulkanBackendState& state,
	const drawSurf_s& surface ) {
	if ( state.reportedMissingMaterials.Num() >= 24 ) {
		return;
	}
	for ( int i = 0; i < state.reportedMissingMaterials.Num(); ++i ) {
		if ( state.reportedMissingMaterials[ i ] == surface.material ) {
			return;
		}
	}
	state.reportedMissingMaterials.Append( surface.material );
	common->Printf( "Vulkan material fallback missing: '%s' (%d stages)\n",
		surface.material->GetName(), surface.material->GetNumStages() );
	for ( int stageIndex = 0; stageIndex < surface.material->GetNumStages(); ++stageIndex ) {
		const materialStage_t* stage = surface.material->GetStage( stageIndex );
		const bool enabled = surface.materialRegisters != NULL &&
			surface.materialRegisters[ stage->conditionRegister ] != 0.0f;
		common->Printf( "  stage %d: %s, %d textures, mega=%s, sequence=%s, program=%s\n",
			stageIndex, enabled ? "enabled" : "disabled", stage->numTextures,
			stage->megaTexture != NULL ? "yes" : "no",
			stage->imgSequence != NULL ? "yes" : "no",
			stage->renderProgram != NULL ? stage->renderProgram->GetName() : "<none>" );
		for ( int textureIndex = 0; textureIndex < stage->numTextures; ++textureIndex ) {
			const stageTexture_t& texture = stage->textures[ textureIndex ];
			common->Printf( "    texture %d: %s%s\n", textureIndex,
				texture.image != NULL ? texture.image->imgName.c_str() : "<null>",
				texture.image != NULL && texture.image->defaulted ? " (defaulted)" : "" );
		}
	}
}

void ReportVulkanDrawMaterial( sdVulkanBackendState& state,
	const drawSurf_s& surface ) {
	const char* materialName = surface.material->GetName();
	const bool importantEffect =
		idStr::FindText( materialName, "muzzle", false ) >= 0 ||
		idStr::FindText( materialName, "flash", false ) >= 0 ||
		idStr::FindText( materialName, "smoke", false ) >= 0 ||
		idStr::FindText( materialName, "explode", false ) >= 0 ||
		idStr::FindText( materialName, "particle", false ) >= 0 ||
		idStr::FindText( materialName, "heatHaze", false ) >= 0 ||
		idStr::FindText( materialName, "water", false ) >= 0 ||
		surface.space->weaponDepthHack;
	if ( state.reportedDrawMaterials.Num() >= 16 && !importantEffect ) {
		return;
	}
	for ( int i = 0; i < state.reportedDrawMaterials.Num(); ++i ) {
		if ( state.reportedDrawMaterials[ i ] == surface.material ) {
			return;
		}
	}
	state.reportedDrawMaterials.Append( surface.material );
	common->Printf( "Vulkan material draw: '%s' (%d stages, coverage=%d, sort=%g, cull=%d, ambient cube map %s)\n",
		materialName, surface.material->GetNumStages(),
		static_cast< int >( surface.material->Coverage() ),
		surface.material->GetSort(),
		static_cast< int >( surface.material->GetCullType() ),
		surface.space != NULL && surface.space->ambientCubeMap != NULL ?
			surface.space->ambientCubeMap->GetName() : "<none>" );
	if ( importantEffect && surface.geo != NULL && surface.space != NULL ) {
		common->Printf( "  effect geometry: model=%s, %d verts, %d indexes, bounds "
			"(%g %g %g)-(%g %g %g), scissor (%d %d)-(%d %d), "
			"weaponHack=%d fov=(%g %g) modelHack=%g\n",
			surface.space->model != NULL ? surface.space->model->Name() : "<null>",
			surface.geo->numVerts, surface.geo->numIndexes,
			surface.geo->bounds[ 0 ].x, surface.geo->bounds[ 0 ].y,
			surface.geo->bounds[ 0 ].z, surface.geo->bounds[ 1 ].x,
			surface.geo->bounds[ 1 ].y, surface.geo->bounds[ 1 ].z,
			surface.scissorRect.x1, surface.scissorRect.y1,
			surface.scissorRect.x2, surface.scissorRect.y2,
			surface.space->weaponDepthHack ? 1 : 0,
			surface.space->weaponDepthHackFOV_x,
			surface.space->weaponDepthHackFOV_y,
			surface.space->modelDepthHack );
	}
	for ( int stageIndex = 0; stageIndex < surface.material->GetNumStages(); ++stageIndex ) {
		const materialStage_t* stage = surface.material->GetStage( stageIndex );
		const bool enabled = surface.materialRegisters != NULL &&
			surface.materialRegisters[ stage->conditionRegister ] != 0.0f;
		common->Printf( "  stage %d: %s, state=0x%08x, %d textures, program=%s\n",
			stageIndex, enabled ? "enabled" : "disabled", stage->drawStateBits,
			stage->numTextures, stage->renderProgram != NULL ?
			stage->renderProgram->GetName() : "<none>" );
		for ( int vectorIndex = 0; vectorIndex < stage->numVectors; ++vectorIndex ) {
			const stageVector_t& vector = stage->vectors[ vectorIndex ];
			common->Printf( "    vector %s = (%g %g %g %g)\n",
				vector.renderBinding != NULL ? vector.renderBinding->GetName() : "<binding>",
				surface.materialRegisters != NULL ? surface.materialRegisters[ vector.registers[ 0 ] ] : 0.0f,
				surface.materialRegisters != NULL ? surface.materialRegisters[ vector.registers[ 1 ] ] : 0.0f,
				surface.materialRegisters != NULL ? surface.materialRegisters[ vector.registers[ 2 ] ] : 0.0f,
				surface.materialRegisters != NULL ? surface.materialRegisters[ vector.registers[ 3 ] ] : 0.0f );
		}
		for ( int textureIndex = 0; textureIndex < stage->numTextures; ++textureIndex ) {
			const stageTexture_t& texture = stage->textures[ textureIndex ];
			common->Printf( "    %s = %s%s (%dx%d, type %d)\n",
				texture.renderBinding != NULL ? texture.renderBinding->GetName() : "<binding>",
				texture.image != NULL ? texture.image->imgName.c_str() : "<null>",
				texture.image != NULL && texture.image->defaulted ? " (defaulted)" : "",
				texture.image != NULL ? texture.image->uploadWidth : 0,
				texture.image != NULL ? texture.image->uploadHeight : 0,
				texture.image != NULL ? texture.image->type : -1 );
		}
	}
}

bool LoadVulkanLibrary( sdVulkanBackendState& state ) {
	if ( !SDL_Vulkan_LoadLibrary( NULL ) ) {
		common->Warning( "Could not load Vulkan library via SDL3: %s", SDL_GetError() );
		return false;
	}
	state.GetInstanceProcAddr = reinterpret_cast< PFN_vkGetInstanceProcAddr >(
		SDL_Vulkan_GetVkGetInstanceProcAddr() );
	if ( state.GetInstanceProcAddr == NULL ) {
		common->Warning( "SDL3 could not obtain vkGetInstanceProcAddr" );
		return false;
	}

	state.EnumerateInstanceVersion = reinterpret_cast< PFN_vkEnumerateInstanceVersion >(
		state.GetInstanceProcAddr( VK_NULL_HANDLE, "vkEnumerateInstanceVersion" ) );
	return
		LoadGlobalFunction( state.GetInstanceProcAddr,
			"vkEnumerateInstanceExtensionProperties", state.EnumerateInstanceExtensionProperties ) &&
		LoadGlobalFunction( state.GetInstanceProcAddr,
			"vkEnumerateInstanceLayerProperties", state.EnumerateInstanceLayerProperties ) &&
		LoadGlobalFunction( state.GetInstanceProcAddr,
			"vkCreateInstance", state.CreateInstance );
}

bool LoadInstanceFunctions( sdVulkanBackendState& state ) {
	bool loaded = true;
#define LOAD_INSTANCE_FUNCTION( name ) loaded = LoadInstanceFunction( state.GetInstanceProcAddr, state.instance, "vk" #name, state.name ) && loaded
	LOAD_INSTANCE_FUNCTION( DestroyInstance );
#if defined( _WIN32 )
	LOAD_INSTANCE_FUNCTION( CreateWin32SurfaceKHR );
#endif
	LOAD_INSTANCE_FUNCTION( DestroySurfaceKHR );
	LOAD_INSTANCE_FUNCTION( EnumeratePhysicalDevices );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceProperties );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceFormatProperties );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceMemoryProperties );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceFeatures2 );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceProperties2 );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceQueueFamilyProperties );
	LOAD_INSTANCE_FUNCTION( EnumerateDeviceExtensionProperties );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceSurfaceSupportKHR );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceSurfaceCapabilitiesKHR );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceSurfaceFormatsKHR );
	LOAD_INSTANCE_FUNCTION( GetPhysicalDeviceSurfacePresentModesKHR );
	LOAD_INSTANCE_FUNCTION( CreateDevice );
	LOAD_INSTANCE_FUNCTION( GetDeviceProcAddr );
#undef LOAD_INSTANCE_FUNCTION
	state.CreateDebugUtilsMessengerEXT = reinterpret_cast< PFN_vkCreateDebugUtilsMessengerEXT >(
		state.GetInstanceProcAddr( state.instance, "vkCreateDebugUtilsMessengerEXT" ) );
	state.DestroyDebugUtilsMessengerEXT = reinterpret_cast< PFN_vkDestroyDebugUtilsMessengerEXT >(
		state.GetInstanceProcAddr( state.instance, "vkDestroyDebugUtilsMessengerEXT" ) );
	return loaded;
}

bool LoadDeviceFunctions( sdVulkanBackendState& state ) {
	bool loaded = true;
#define LOAD_DEVICE_FUNCTION( name ) loaded = LoadDeviceFunction( state.GetDeviceProcAddr, state.device, "vk" #name, state.name ) && loaded
	LOAD_DEVICE_FUNCTION( DestroyDevice );
	LOAD_DEVICE_FUNCTION( GetDeviceQueue );
	LOAD_DEVICE_FUNCTION( DeviceWaitIdle );
	LOAD_DEVICE_FUNCTION( QueueWaitIdle );
	LOAD_DEVICE_FUNCTION( CreateSwapchainKHR );
	LOAD_DEVICE_FUNCTION( DestroySwapchainKHR );
	LOAD_DEVICE_FUNCTION( GetSwapchainImagesKHR );
	LOAD_DEVICE_FUNCTION( AcquireNextImageKHR );
	LOAD_DEVICE_FUNCTION( QueuePresentKHR );
	LOAD_DEVICE_FUNCTION( CreateImageView );
	LOAD_DEVICE_FUNCTION( DestroyImageView );
	LOAD_DEVICE_FUNCTION( CreateImage );
	LOAD_DEVICE_FUNCTION( DestroyImage );
	LOAD_DEVICE_FUNCTION( GetImageMemoryRequirements );
	LOAD_DEVICE_FUNCTION( AllocateMemory );
	LOAD_DEVICE_FUNCTION( FreeMemory );
	vkAllocateMemoryReal = state.AllocateMemory;
	state.AllocateMemory = TrackedAllocateMemory;
	vkFreeMemoryReal = state.FreeMemory;
	state.FreeMemory = TrackedFreeMemory;
	LOAD_DEVICE_FUNCTION( BindImageMemory );
	LOAD_DEVICE_FUNCTION( CreateSampler );
	LOAD_DEVICE_FUNCTION( DestroySampler );
	LOAD_DEVICE_FUNCTION( CreateBuffer );
	LOAD_DEVICE_FUNCTION( DestroyBuffer );
	LOAD_DEVICE_FUNCTION( GetBufferMemoryRequirements );
	LOAD_DEVICE_FUNCTION( BindBufferMemory );
	LOAD_DEVICE_FUNCTION( MapMemory );
	LOAD_DEVICE_FUNCTION( UnmapMemory );
	LOAD_DEVICE_FUNCTION( CreateShaderModule );
	LOAD_DEVICE_FUNCTION( DestroyShaderModule );
	LOAD_DEVICE_FUNCTION( CreateDescriptorSetLayout );
	LOAD_DEVICE_FUNCTION( DestroyDescriptorSetLayout );
	LOAD_DEVICE_FUNCTION( CreateDescriptorPool );
	LOAD_DEVICE_FUNCTION( DestroyDescriptorPool );
	LOAD_DEVICE_FUNCTION( AllocateDescriptorSets );
	LOAD_DEVICE_FUNCTION( FreeDescriptorSets );
	LOAD_DEVICE_FUNCTION( UpdateDescriptorSets );
	LOAD_DEVICE_FUNCTION( CreatePipelineLayout );
	LOAD_DEVICE_FUNCTION( DestroyPipelineLayout );
	LOAD_DEVICE_FUNCTION( CreateGraphicsPipelines );
	LOAD_DEVICE_FUNCTION( DestroyPipeline );
	LOAD_DEVICE_FUNCTION( CreateCommandPool );
	LOAD_DEVICE_FUNCTION( DestroyCommandPool );
	LOAD_DEVICE_FUNCTION( ResetCommandPool );
	LOAD_DEVICE_FUNCTION( AllocateCommandBuffers );
	LOAD_DEVICE_FUNCTION( BeginCommandBuffer );
	LOAD_DEVICE_FUNCTION( EndCommandBuffer );
	LOAD_DEVICE_FUNCTION( CreateSemaphore );
	LOAD_DEVICE_FUNCTION( DestroySemaphore );
	LOAD_DEVICE_FUNCTION( CreateFence );
	LOAD_DEVICE_FUNCTION( DestroyFence );
	LOAD_DEVICE_FUNCTION( WaitForFences );
	LOAD_DEVICE_FUNCTION( ResetFences );
	LOAD_DEVICE_FUNCTION( QueueSubmit2 );
	LOAD_DEVICE_FUNCTION( CmdPipelineBarrier2 );
	LOAD_DEVICE_FUNCTION( CmdCopyBufferToImage );
	LOAD_DEVICE_FUNCTION( CmdCopyBuffer );
	LOAD_DEVICE_FUNCTION( CmdCopyImage );
	LOAD_DEVICE_FUNCTION( CmdCopyImageToBuffer );
	LOAD_DEVICE_FUNCTION( CmdBlitImage );
	LOAD_DEVICE_FUNCTION( CmdBeginRendering );
	LOAD_DEVICE_FUNCTION( CmdEndRendering );
	LOAD_DEVICE_FUNCTION( CmdSetViewport );
	LOAD_DEVICE_FUNCTION( CmdSetScissor );
	LOAD_DEVICE_FUNCTION( CmdClearAttachments );
	LOAD_DEVICE_FUNCTION( CmdSetDepthBias );
	LOAD_DEVICE_FUNCTION( CmdBindPipeline );
	LOAD_DEVICE_FUNCTION( CmdBindDescriptorSets );
	LOAD_DEVICE_FUNCTION( CmdPushConstants );
	LOAD_DEVICE_FUNCTION( CmdBindVertexBuffers );
	LOAD_DEVICE_FUNCTION( CmdBindIndexBuffer );
	LOAD_DEVICE_FUNCTION( CmdDraw );
	LOAD_DEVICE_FUNCTION( CmdDrawIndexed );
#undef LOAD_DEVICE_FUNCTION
	return loaded;
}

bool CreateVulkanInstance( sdVulkanBackendState& state ) {
	unsigned int loaderVersion = VK_API_VERSION_1_0;
	if ( state.EnumerateInstanceVersion != NULL ) {
		state.EnumerateInstanceVersion( &loaderVersion );
	}
	if ( loaderVersion < VK_API_VERSION_1_3 ) {
		common->Warning( "Vulkan 1.3 is required; the loader only exposes %u.%u.%u",
			VK_API_VERSION_MAJOR( loaderVersion ), VK_API_VERSION_MINOR( loaderVersion ),
			VK_API_VERSION_PATCH( loaderVersion ) );
		return false;
	}

	unsigned int extensionCount = 0;
	if ( !CheckVulkanResult( state.EnumerateInstanceExtensionProperties(
		NULL, &extensionCount, NULL ), "vkEnumerateInstanceExtensionProperties" ) ) {
		return false;
	}
	idList< VkExtensionProperties > extensions;
	extensions.SetNum( extensionCount );
	if ( extensionCount != 0 && !CheckVulkanResult(
		state.EnumerateInstanceExtensionProperties( NULL, &extensionCount, extensions.Begin() ),
		"vkEnumerateInstanceExtensionProperties" ) ) {
		return false;
	}
	Uint32 sdlExtCount = 0;
	char const * const * sdlExtNames = SDL_Vulkan_GetInstanceExtensions( &sdlExtCount );
	if ( sdlExtNames == NULL || sdlExtCount == 0 ) {
		common->Warning( "SDL_Vulkan_GetInstanceExtensions failed: %s", SDL_GetError() );
		return false;
	}

	idList< const char* > enabledExtensions;
	for ( Uint32 i = 0; i < sdlExtCount; ++i ) {
		if ( HasExtension( extensions, sdlExtNames[ i ] ) ) {
			enabledExtensions.Append( sdlExtNames[ i ] );
		}
	}

	bool portabilityEnumeration = false;
	if ( HasExtension( extensions, "VK_KHR_portability_enumeration" ) ) {
		enabledExtensions.Append( "VK_KHR_portability_enumeration" );
		portabilityEnumeration = true;
	}

	unsigned int layerCount = 0;
	state.EnumerateInstanceLayerProperties( &layerCount, NULL );
	idList< VkLayerProperties > layers;
	layers.SetNum( layerCount );
	if ( layerCount != 0 ) {
		state.EnumerateInstanceLayerProperties( &layerCount, layers.Begin() );
	}
	const bool enableValidation = r_vkValidation.GetBool() &&
		HasLayer( layers, "VK_LAYER_KHRONOS_validation" );
	if ( r_vkValidation.GetBool() && !enableValidation ) {
		common->Warning( "VK_LAYER_KHRONOS_validation is not installed" );
	}
	const bool enableDebugUtils = enableValidation &&
		HasExtension( extensions, VK_EXT_DEBUG_UTILS_EXTENSION_NAME );

	if ( enableDebugUtils ) {
		enabledExtensions.Append( VK_EXT_DEBUG_UTILS_EXTENSION_NAME );
	}
	const char* enabledLayers[ 1 ];
	unsigned int enabledLayerCount = 0;
	if ( enableValidation ) {
		enabledLayers[ enabledLayerCount++ ] = "VK_LAYER_KHRONOS_validation";
	}

	VkApplicationInfo applicationInfo;
	memset( &applicationInfo, 0, sizeof( applicationInfo ) );
	applicationInfo.sType = VK_STRUCTURE_TYPE_APPLICATION_INFO;
	applicationInfo.pApplicationName = GAME_NAME;
	applicationInfo.applicationVersion = VK_MAKE_VERSION( 1, 5, 0 );
	applicationInfo.pEngineName = "ETQW Vulkan";
	applicationInfo.engineVersion = VK_MAKE_VERSION( 1, 0, 0 );
	applicationInfo.apiVersion = VK_API_VERSION_1_3;

	VkDebugUtilsMessengerCreateInfoEXT debugCreateInfo;
	InitDebugMessengerCreateInfo( debugCreateInfo );
	VkInstanceCreateInfo createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO;
	if ( portabilityEnumeration ) {
		createInfo.flags |= VK_INSTANCE_CREATE_ENUMERATE_PORTABILITY_BIT_KHR;
	}
	createInfo.pNext = enableDebugUtils ? &debugCreateInfo : NULL;
	createInfo.pApplicationInfo = &applicationInfo;
	createInfo.enabledExtensionCount = enabledExtensions.Num();
	createInfo.ppEnabledExtensionNames = enabledExtensions.Begin();
	createInfo.enabledLayerCount = enabledLayerCount;
	createInfo.ppEnabledLayerNames = enabledLayers;

	if ( !CheckVulkanResult( state.CreateInstance( &createInfo, NULL, &state.instance ),
		"vkCreateInstance" ) ) {
		return false;
	}
	if ( !LoadInstanceFunctions( state ) ) {
		return false;
	}
	if ( enableDebugUtils && state.CreateDebugUtilsMessengerEXT != NULL ) {
		if ( !CheckVulkanResult( state.CreateDebugUtilsMessengerEXT( state.instance,
			&debugCreateInfo, NULL, &state.debugMessenger ),
			"vkCreateDebugUtilsMessengerEXT" ) ) {
			return false;
		}
	}
	return true;
}

bool CreateVulkanSurface( sdVulkanBackendState& state ) {
	VkSurfaceKHR surface = VK_NULL_HANDLE;
#if defined( _WIN32 )
	HWND window = static_cast< HWND >( state.nativeWindow );
	VkWin32SurfaceCreateInfoKHR surfaceInfo;
	memset( &surfaceInfo, 0, sizeof( surfaceInfo ) );
	surfaceInfo.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
	surfaceInfo.hinstance = GetModuleHandleA( NULL );
	surfaceInfo.hwnd = window;
	if ( !CheckVulkanResult( state.CreateWin32SurfaceKHR( state.instance,
		&surfaceInfo, NULL, &surface ),
		"vkCreateWin32SurfaceKHR" ) ) {
		return false;
	}
#else
	if ( !SDL_Vulkan_CreateSurface( static_cast< SDL_Window* >( state.nativeWindow ),
		state.instance, NULL, &surface ) ) {
		common->Warning( "SDL_Vulkan_CreateSurface failed: %s", SDL_GetError() );
		return false;
	}
#endif
	state.surface = surface;
	return true;
}

bool DeviceHasExtension( sdVulkanBackendState& state, VkPhysicalDevice device,
	const char* extensionName ) {
	unsigned int count = 0;
	if ( state.EnumerateDeviceExtensionProperties( device, NULL, &count, NULL ) != VK_SUCCESS ) {
		return false;
	}
	idList< VkExtensionProperties > extensions;
	extensions.SetNum( count );
	if ( count != 0 && state.EnumerateDeviceExtensionProperties(
		device, NULL, &count, extensions.Begin() ) != VK_SUCCESS ) {
		return false;
	}
	return HasExtension( extensions, extensionName );
}

bool FindGraphicsPresentQueue( sdVulkanBackendState& state,
	VkPhysicalDevice device, unsigned int& queueFamilyIndex ) {
	unsigned int count = 0;
	state.GetPhysicalDeviceQueueFamilyProperties( device, &count, NULL );
	idList< VkQueueFamilyProperties > families;
	families.SetNum( count );
	if ( count != 0 ) {
		state.GetPhysicalDeviceQueueFamilyProperties( device, &count, families.Begin() );
	}
	for ( unsigned int i = 0; i < count; ++i ) {
		VkBool32 presentSupported = VK_FALSE;
		state.GetPhysicalDeviceSurfaceSupportKHR( device, i, state.surface,
			&presentSupported );
		if ( families[ i ].queueCount != 0 &&
			( families[ i ].queueFlags & VK_QUEUE_GRAPHICS_BIT ) != 0 &&
			presentSupported ) {
			queueFamilyIndex = i;
			return true;
		}
	}
	return false;
}

bool SurfaceIsUsable( sdVulkanBackendState& state, VkPhysicalDevice device ) {
	unsigned int formatCount = 0;
	unsigned int presentModeCount = 0;
	return state.GetPhysicalDeviceSurfaceFormatsKHR( device, state.surface,
		&formatCount, NULL ) == VK_SUCCESS && formatCount != 0 &&
		state.GetPhysicalDeviceSurfacePresentModesKHR( device, state.surface,
		&presentModeCount, NULL ) == VK_SUCCESS && presentModeCount != 0;
}

bool SurfaceSupportsRayTracingImages( sdVulkanBackendState& state,
	VkPhysicalDevice device ) {
	VkSurfaceCapabilitiesKHR capabilities;
	memset( &capabilities, 0, sizeof( capabilities ) );
	return state.GetPhysicalDeviceSurfaceCapabilitiesKHR( device, state.surface,
		&capabilities ) == VK_SUCCESS &&
		( capabilities.supportedUsageFlags & VK_IMAGE_USAGE_STORAGE_BIT ) != 0 &&
		( capabilities.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_SRC_BIT ) != 0;
}

VkFormat SelectDepthStencilFormat( sdVulkanBackendState& state ) {
	const VkFormat candidates[] = {
		VK_FORMAT_D32_SFLOAT_S8_UINT,
		VK_FORMAT_D24_UNORM_S8_UINT
	};
	for ( int i = 0; i < 2; ++i ) {
		if ( state.GetPhysicalDeviceFormatProperties != NULL ) {
			VkFormatProperties properties;
			memset( &properties, 0, sizeof( properties ) );
			state.GetPhysicalDeviceFormatProperties( state.physicalDevice, candidates[ i ], &properties );
			if ( ( properties.optimalTilingFeatures & VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT ) ==
				VK_FORMAT_FEATURE_DEPTH_STENCIL_ATTACHMENT_BIT ) {
				return candidates[ i ];
			}
		}
	}
	return VK_FORMAT_D32_SFLOAT_S8_UINT;
}

// GL_VENDOR and GL_VERSION for the selected device: the vendor from its PCI
// vendor ID (the ID in hex when it is not a known GPU vendor), and
// "Vulkan <api> / <driver name> <driver info>" from VkPhysicalDeviceDriverProperties
// (core in Vulkan 1.2; every accepted device is 1.3).
static void DescribeVulkanDevice( sdVulkanBackendState& state ) {
	VkPhysicalDeviceDriverProperties driverProperties;
	memset( &driverProperties, 0, sizeof( driverProperties ) );
	driverProperties.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES;
	VkPhysicalDeviceProperties2 properties2;
	memset( &properties2, 0, sizeof( properties2 ) );
	properties2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2;
	properties2.pNext = &driverProperties;
	state.GetPhysicalDeviceProperties2( state.physicalDevice, &properties2 );
	driverProperties.driverName[ VK_MAX_DRIVER_NAME_SIZE - 1 ] = '\0';
	driverProperties.driverInfo[ VK_MAX_DRIVER_INFO_SIZE - 1 ] = '\0';

	const char* vendor = NULL;
	switch ( properties2.properties.vendorID ) {
		case 0x1002: vendor = "AMD"; break;
		case 0x10DE: vendor = "NVIDIA"; break;
		case 0x8086: vendor = "Intel"; break;
		case 0x106B: vendor = "Apple"; break;
		case 0x5143: vendor = "Qualcomm"; break;
		case 0x13B5: vendor = "ARM"; break;
		case 0x1010: vendor = "Imagination Technologies"; break;
		case 0x14E4: vendor = "Broadcom"; break;
		case 0x1AE0: vendor = "Google"; break;
		case 0x10005: vendor = "Mesa"; break;
		default: break;
	}
	if ( vendor != NULL ) {
		idStr::Copynz( state.vendorString, vendor, sizeof( state.vendorString ) );
	} else {
		idStr::snPrintf( state.vendorString, sizeof( state.vendorString ), "0x%04X",
			properties2.properties.vendorID );
	}

	const uint32_t api = properties2.properties.apiVersion;
	idStr::snPrintf( state.versionString, sizeof( state.versionString ), "Vulkan %u.%u.%u / %s%s%s",
		VK_API_VERSION_MAJOR( api ), VK_API_VERSION_MINOR( api ), VK_API_VERSION_PATCH( api ),
		driverProperties.driverName[ 0 ] != '\0' ? driverProperties.driverName : "unknown driver",
		driverProperties.driverInfo[ 0 ] != '\0' ? " " : "", driverProperties.driverInfo );
}

bool SelectPhysicalDevice( sdVulkanBackendState& state ) {
	unsigned int count = 0;
	if ( !CheckVulkanResult( state.EnumeratePhysicalDevices( state.instance,
		&count, NULL ), "vkEnumeratePhysicalDevices" ) || count == 0 ) {
		common->Warning( "No Vulkan physical devices were found" );
		return false;
	}
	idList< VkPhysicalDevice > devices;
	devices.SetNum( count );
	if ( !CheckVulkanResult( state.EnumeratePhysicalDevices( state.instance,
		&count, devices.Begin() ), "vkEnumeratePhysicalDevices" ) ) {
		return false;
	}

	int bestScore = -1;
	for ( unsigned int i = 0; i < count; ++i ) {
		VkPhysicalDeviceProperties properties;
		state.GetPhysicalDeviceProperties( devices[ i ], &properties );
		if ( properties.apiVersion < VK_API_VERSION_1_3 ||
			!DeviceHasExtension( state, devices[ i ], VK_KHR_SWAPCHAIN_EXTENSION_NAME ) ||
			!SurfaceIsUsable( state, devices[ i ] ) ) {
			continue;
		}

		unsigned int queueFamilyIndex = UINT_MAX;
		if ( !FindGraphicsPresentQueue( state, devices[ i ], queueFamilyIndex ) ) {
			continue;
		}

		VkPhysicalDeviceVulkan13Features features13;
		memset( &features13, 0, sizeof( features13 ) );
		features13.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES;
		VkPhysicalDeviceFeatures2 features2;
		memset( &features2, 0, sizeof( features2 ) );
		features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
		features2.pNext = &features13;
		state.GetPhysicalDeviceFeatures2( devices[ i ], &features2 );
		if ( !features13.dynamicRendering || !features13.synchronization2 ||
			!features13.shaderDemoteToHelperInvocation ) {
			continue;
		}

		int score = 0;
		if ( properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ) {
			score += 1000;
		} else if ( properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ) {
			score += 500;
		}
		score += static_cast< int >( properties.limits.maxImageDimension2D / 1024 );
		if ( score > bestScore ) {
			bestScore = score;
			state.physicalDevice = devices[ i ];
			state.queueFamilyIndex = queueFamilyIndex;
			idStr::Copynz( state.deviceName, properties.deviceName,
				sizeof( state.deviceName ) );
			state.vendorID = properties.vendorID;
			state.fillModeNonSolid = features2.features.fillModeNonSolid == VK_TRUE;
			state.textureCompressionBC = features2.features.textureCompressionBC == VK_TRUE;
			state.maxPushConstantsSize = properties.limits.maxPushConstantsSize;
			state.jointUniformAlignment =
				properties.limits.minUniformBufferOffsetAlignment > 1 ?
				properties.limits.minUniformBufferOffsetAlignment : 1;
			state.maxMemoryAllocationCount = properties.limits.maxMemoryAllocationCount;
		}
	}

	if ( state.physicalDevice == VK_NULL_HANDLE ) {
		common->Warning( "No Vulkan 1.3 device supports the required swapchain and shader features" );
		return false;
	}
	state.GetPhysicalDeviceMemoryProperties( state.physicalDevice,
		&state.memoryProperties );
	state.depthStencilFormat = SelectDepthStencilFormat( state );
	DescribeVulkanDevice( state );
	return true;
}

bool CreateLogicalDevice( sdVulkanBackendState& state,
	sdRayTracingDeviceConfiguration& rayTracingConfiguration,
	bool rayTracingAvailable ) {
	const float queuePriority = 1.0f;
	VkDeviceQueueCreateInfo queueCreateInfo;
	memset( &queueCreateInfo, 0, sizeof( queueCreateInfo ) );
	queueCreateInfo.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO;
	queueCreateInfo.queueFamilyIndex = state.queueFamilyIndex;
	queueCreateInfo.queueCount = 1;
	queueCreateInfo.pQueuePriorities = &queuePriority;

	VkPhysicalDeviceVulkan13Features features13;
	memset( &features13, 0, sizeof( features13 ) );
	features13.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES;
	features13.dynamicRendering = VK_TRUE;
	features13.synchronization2 = VK_TRUE;
	features13.shaderDemoteToHelperInvocation = VK_TRUE;

	VkPhysicalDeviceFeatures coreFeatures;
	memset( &coreFeatures, 0, sizeof( coreFeatures ) );
	coreFeatures.robustBufferAccess = VK_TRUE;
	if ( state.fillModeNonSolid ) {
		coreFeatures.fillModeNonSolid = VK_TRUE;
	}
	if ( state.textureCompressionBC ) {
		coreFeatures.textureCompressionBC = VK_TRUE;
	}

	if ( rayTracingAvailable ) {
		features13.pNext = &rayTracingConfiguration.bufferDeviceAddress;
		if ( state.fillModeNonSolid ) {
			rayTracingConfiguration.coreFeatures.fillModeNonSolid = VK_TRUE;
		}
		rayTracingConfiguration.coreFeatures.robustBufferAccess = VK_TRUE;
		if ( state.textureCompressionBC ) {
			rayTracingConfiguration.coreFeatures.textureCompressionBC = VK_TRUE;
		}
		coreFeatures = rayTracingConfiguration.coreFeatures;
	}

	bool hasPortabilitySubset = DeviceHasExtension( state, state.physicalDevice, "VK_KHR_portability_subset" );
	VkPhysicalDevicePortabilitySubsetFeaturesKHR portabilityFeatures;
	memset( &portabilityFeatures, 0, sizeof( portabilityFeatures ) );
	portabilityFeatures.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PORTABILITY_SUBSET_FEATURES_KHR;
	if ( hasPortabilitySubset ) {
		VkPhysicalDeviceFeatures2 features2;
		memset( &features2, 0, sizeof( features2 ) );
		features2.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
		features2.pNext = &portabilityFeatures;
		state.GetPhysicalDeviceFeatures2( state.physicalDevice, &features2 );
		// Without it, image views must use the identity swizzle, so the block
		// formats that need one (RXGB, ATI1, ATI2) take the decode path instead.
		state.imageViewFormatSwizzle = portabilityFeatures.imageViewFormatSwizzle == VK_TRUE;

		portabilityFeatures.pNext = features13.pNext;
		features13.pNext = &portabilityFeatures;
	}

	idList< const char* > extensions;
	extensions.Append( VK_KHR_SWAPCHAIN_EXTENSION_NAME );
	if ( hasPortabilitySubset ) {
		extensions.Append( "VK_KHR_portability_subset" );
	}
	if ( rayTracingAvailable ) {
		for ( unsigned int extensionIndex = 0;
			extensionIndex < rayTracingConfiguration.deviceExtensionCount;
			++extensionIndex ) {
			extensions.Append( rayTracingConfiguration.deviceExtensions[ extensionIndex ] );
		}
	}

	VkDeviceCreateInfo createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO;
	createInfo.pNext = &features13;
	createInfo.pEnabledFeatures = &coreFeatures;
	createInfo.queueCreateInfoCount = 1;
	createInfo.pQueueCreateInfos = &queueCreateInfo;
	createInfo.enabledExtensionCount = extensions.Num();
	createInfo.ppEnabledExtensionNames = extensions.Begin();
	if ( !CheckVulkanResult( state.CreateDevice( state.physicalDevice,
		&createInfo, NULL, &state.device ), "vkCreateDevice" ) ) {
		return false;
	}
	if ( !LoadDeviceFunctions( state ) ) {
		return false;
	}
	state.GetDeviceQueue( state.device, state.queueFamilyIndex, 0,
		&state.graphicsQueue );
	return state.graphicsQueue != VK_NULL_HANDLE;
}

VkSurfaceFormatKHR ChooseSurfaceFormat(
	const idList< VkSurfaceFormatKHR >& formats ) {
	// ETQW's shaders, textures, and legacy blend equations operate in the
	// gamma-encoded framebuffer domain.  An sRGB swapchain silently linearizes
	// blend operations, which makes low-valued additive GUI art (notably the
	// main-menu scanlines) much brighter than the OpenGL result.
	for ( int i = 0; i < formats.Num(); ++i ) {
		if ( formats[ i ].format == VK_FORMAT_B8G8R8A8_UNORM &&
			formats[ i ].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR ) {
			return formats[ i ];
		}
	}
	for ( int i = 0; i < formats.Num(); ++i ) {
		if ( formats[ i ].format == VK_FORMAT_B8G8R8A8_SRGB ) {
			return formats[ i ];
		}
	}
	return formats[ 0 ];
}

VkPresentModeKHR ChoosePresentMode(
	const idList< VkPresentModeKHR >& presentModes, int swapInterval ) {
	if ( swapInterval <= 0 ) {
		for ( int i = 0; i < presentModes.Num(); ++i ) {
			if ( presentModes[ i ] == VK_PRESENT_MODE_MAILBOX_KHR ) {
				return presentModes[ i ];
			}
		}
		for ( int i = 0; i < presentModes.Num(); ++i ) {
			if ( presentModes[ i ] == VK_PRESENT_MODE_IMMEDIATE_KHR ) {
				return presentModes[ i ];
			}
		}
	}
	return VK_PRESENT_MODE_FIFO_KHR;
}

VkCompositeAlphaFlagBitsKHR ChooseCompositeAlpha(
	VkCompositeAlphaFlagsKHR supported ) {
	const VkCompositeAlphaFlagBitsKHR choices[] = {
		VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR,
		VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR,
		VK_COMPOSITE_ALPHA_POST_MULTIPLIED_BIT_KHR,
		VK_COMPOSITE_ALPHA_INHERIT_BIT_KHR
	};
	for ( int i = 0; i < 4; ++i ) {
		if ( ( supported & choices[ i ] ) != 0 ) {
			return choices[ i ];
		}
	}
	return VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
}

bool FindMemoryType( const sdVulkanBackendState& state, unsigned int typeBits,
	VkMemoryPropertyFlags required, unsigned int& typeIndex );

void DestroyCurrentRenderResource( sdVulkanBackendState& state,
	sdVulkanImageResource& resource ) {
	if ( state.device != VK_NULL_HANDLE ) {
		if ( resource.sampler != VK_NULL_HANDLE ) {
			state.DestroySampler( state.device, resource.sampler, NULL );
		}
		if ( resource.view != VK_NULL_HANDLE ) {
			state.DestroyImageView( state.device, resource.view, NULL );
		}
		if ( resource.image != VK_NULL_HANDLE ) {
			state.DestroyImage( state.device, resource.image, NULL );
		}
		if ( resource.memory != VK_NULL_HANDLE ) {
			state.FreeMemory( state.device, resource.memory, NULL );
		}
	}
	memset( &resource, 0, sizeof( resource ) );
}

// The glow buffers and their descriptor sets follow the swapchain's size; the
// sets also name _currentRender's view, so they go whenever it does.
void DestroyGlowBuffers( sdVulkanBackendState& state ) {
	for ( int i = 0; i < 4; ++i ) {
		if ( state.glowDescriptorSets[ i ] != VK_NULL_HANDLE && state.device != VK_NULL_HANDLE &&
			state.guiDescriptorPool != VK_NULL_HANDLE ) {
			state.FreeDescriptorSets( state.device, state.guiDescriptorPool, 1,
				&state.glowDescriptorSets[ i ] );
		}
		state.glowDescriptorSets[ i ] = VK_NULL_HANDLE;
	}
	for ( int i = 0; i < 2; ++i ) {
		DestroyCurrentRenderResource( state, state.glowBuffers[ i ] );
		state.glowBufferInitialized[ i ] = false;
	}
}

bool CreateCurrentRenderResource( sdVulkanBackendState& state,
	VkFormat format, VkExtent2D extent, sdVulkanImageResource& resource,
	VkImageUsageFlags usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT ) {
	memset( &resource, 0, sizeof( resource ) );
	resource.width = static_cast< int >( extent.width );
	resource.height = static_cast< int >( extent.height );
	resource.mipLevels = 1;

	VkImageCreateInfo imageInfo;
	memset( &imageInfo, 0, sizeof( imageInfo ) );
	imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
	imageInfo.imageType = VK_IMAGE_TYPE_2D;
	imageInfo.format = format;
	imageInfo.extent.width = extent.width;
	imageInfo.extent.height = extent.height;
	imageInfo.extent.depth = 1;
	imageInfo.mipLevels = 1;
	imageInfo.arrayLayers = 1;
	imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
	imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
	imageInfo.usage = usage | VK_IMAGE_USAGE_SAMPLED_BIT;
	imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	if ( !CheckVulkanResult( state.CreateImage( state.device, &imageInfo,
		NULL, &resource.image ), "vkCreateImage(current render)" ) ) {
		return false;
	}

	VkMemoryRequirements requirements;
	state.GetImageMemoryRequirements( state.device, resource.image, &requirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( state, requirements.memoryTypeBits,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, memoryType ) ) {
		DestroyCurrentRenderResource( state, resource );
		return false;
	}
	VkMemoryAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	allocateInfo.allocationSize = requirements.size;
	allocateInfo.memoryTypeIndex = memoryType;
	if ( !CheckVulkanResult( state.AllocateMemory( state.device, &allocateInfo,
		NULL, &resource.memory ), "vkAllocateMemory(current render)" ) ||
		!CheckVulkanResult( state.BindImageMemory( state.device, resource.image,
			resource.memory, 0 ), "vkBindImageMemory(current render)" ) ) {
		DestroyCurrentRenderResource( state, resource );
		return false;
	}

	VkImageViewCreateInfo viewInfo;
	memset( &viewInfo, 0, sizeof( viewInfo ) );
	viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
	viewInfo.image = resource.image;
	viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
	viewInfo.format = format;
	viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	viewInfo.subresourceRange.levelCount = 1;
	viewInfo.subresourceRange.layerCount = 1;
	if ( !CheckVulkanResult( state.CreateImageView( state.device, &viewInfo,
		NULL, &resource.view ), "vkCreateImageView(current render)" ) ) {
		DestroyCurrentRenderResource( state, resource );
		return false;
	}

	VkSamplerCreateInfo samplerInfo;
	memset( &samplerInfo, 0, sizeof( samplerInfo ) );
	samplerInfo.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
	samplerInfo.magFilter = VK_FILTER_LINEAR;
	samplerInfo.minFilter = VK_FILTER_LINEAR;
	samplerInfo.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
	samplerInfo.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.maxLod = 0.0f;
	if ( !CheckVulkanResult( state.CreateSampler( state.device, &samplerInfo,
		NULL, &resource.sampler ), "vkCreateSampler(current render)" ) ) {
		DestroyCurrentRenderResource( state, resource );
		return false;
	}
	return true;
}

void DestroyDepthResources( sdVulkanBackendState& state ) {
	if ( state.device != VK_NULL_HANDLE ) {
		for ( int i = 0; i < state.depthViews.Num(); ++i ) {
			if ( state.depthViews[ i ] != VK_NULL_HANDLE ) {
				state.DestroyImageView( state.device, state.depthViews[ i ], NULL );
			}
		}
		for ( int i = 0; i < state.depthSampleViews.Num(); ++i ) {
			if ( state.depthSampleViews[ i ] != VK_NULL_HANDLE ) {
				state.DestroyImageView( state.device, state.depthSampleViews[ i ], NULL );
			}
		}
		for ( int i = 0; i < state.depthImages.Num(); ++i ) {
			if ( state.depthImages[ i ] != VK_NULL_HANDLE ) {
				state.DestroyImage( state.device, state.depthImages[ i ], NULL );
			}
			if ( state.depthMemory[ i ] != VK_NULL_HANDLE ) {
				state.FreeMemory( state.device, state.depthMemory[ i ], NULL );
			}
		}
	}
	state.depthImages.Clear();
	state.depthMemory.Clear();
	state.depthViews.Clear();
	state.depthSampleViews.Clear();
	state.depthInitialized.Clear();
}

void DestroySemaphoreList( sdVulkanBackendState& state,
	idList< VkSemaphore >& semaphores );

void PurgeCurrentRenderDescriptors( sdVulkanBackendState& state );

void DestroySwapchain( sdVulkanBackendState& state ) {
	DestroyGlowBuffers( state );
	PurgeCurrentRenderDescriptors( state );
	DestroyCurrentRenderResource( state, state.currentRenderResource );
	state.currentRenderInitialized = false;
	state.swapchainTransferSource = false;
	DestroyDepthResources( state );
	DestroySemaphoreList( state, state.presentSemaphores );
	if ( state.device != VK_NULL_HANDLE ) {
		for ( int i = 0; state.DestroyImageView != NULL &&
			i < state.swapchainViews.Num(); ++i ) {
			if ( state.swapchainViews[ i ] != VK_NULL_HANDLE ) {
				state.DestroyImageView( state.device, state.swapchainViews[ i ], NULL );
			}
		}
		if ( state.swapchain != VK_NULL_HANDLE && state.DestroySwapchainKHR != NULL ) {
			state.DestroySwapchainKHR( state.device, state.swapchain, NULL );
		}
	}
	state.swapchain = VK_NULL_HANDLE;
	state.swapchainImages.Clear();
	state.swapchainViews.Clear();
	state.imageInitialized.Clear();
	state.extent.width = 0;
	state.extent.height = 0;
}

bool CreateDepthResources( sdVulkanBackendState& state, unsigned int count,
	VkExtent2D extent, idList< VkImage >& images,
	idList< VkDeviceMemory >& memory, idList< VkImageView >& views,
	idList< VkImageView >* sampleViews = NULL ) {
	images.SetNum( count );
	memory.SetNum( count );
	views.SetNum( count );
	memset( images.Begin(), 0, count * sizeof( VkImage ) );
	memset( memory.Begin(), 0, count * sizeof( VkDeviceMemory ) );
	memset( views.Begin(), 0, count * sizeof( VkImageView ) );
	if ( sampleViews != NULL ) {
		sampleViews->SetNum( count );
		memset( sampleViews->Begin(), 0, count * sizeof( VkImageView ) );
	}
	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
	for ( unsigned int i = 0; i < count; ++i ) {
		VkImageCreateInfo imageInfo;
		memset( &imageInfo, 0, sizeof( imageInfo ) );
		imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
		imageInfo.imageType = VK_IMAGE_TYPE_2D;
		imageInfo.format = depthFormat;
		imageInfo.extent.width = extent.width;
		imageInfo.extent.height = extent.height;
		imageInfo.extent.depth = 1;
		imageInfo.mipLevels = 1;
		imageInfo.arrayLayers = 1;
		imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
		imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
		imageInfo.usage = VK_IMAGE_USAGE_DEPTH_STENCIL_ATTACHMENT_BIT |
			VK_IMAGE_USAGE_SAMPLED_BIT;
		imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
		if ( !CheckVulkanResult( state.CreateImage( state.device, &imageInfo,
			NULL, &images[ i ] ), "vkCreateImage(depth)" ) ) {
			break;
		}
		VkMemoryRequirements requirements;
		state.GetImageMemoryRequirements( state.device, images[ i ], &requirements );
		unsigned int memoryType = UINT_MAX;
		if ( !FindMemoryType( state, requirements.memoryTypeBits,
			VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, memoryType ) ) {
			break;
		}
		VkMemoryAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
		allocateInfo.allocationSize = requirements.size;
		allocateInfo.memoryTypeIndex = memoryType;
		if ( !CheckVulkanResult( state.AllocateMemory( state.device,
			&allocateInfo, NULL, &memory[ i ] ), "vkAllocateMemory(depth)" ) ||
			!CheckVulkanResult( state.BindImageMemory( state.device, images[ i ],
				memory[ i ], 0 ), "vkBindImageMemory(depth)" ) ) {
			break;
		}
		VkImageViewCreateInfo viewInfo;
		memset( &viewInfo, 0, sizeof( viewInfo ) );
		viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
		viewInfo.image = images[ i ];
		viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
		viewInfo.format = depthFormat;
		viewInfo.subresourceRange.aspectMask =
			VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
		viewInfo.subresourceRange.levelCount = 1;
		viewInfo.subresourceRange.layerCount = 1;
		if ( !CheckVulkanResult( state.CreateImageView( state.device,
			&viewInfo, NULL, &views[ i ] ), "vkCreateImageView(depth)" ) ) {
			break;
		}
		if ( sampleViews != NULL ) {
			viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT;
			if ( !CheckVulkanResult( state.CreateImageView( state.device,
				&viewInfo, NULL, &( *sampleViews )[ i ] ), "vkCreateImageView(depth sample)" ) ) {
				break;
			}
		}
	}
	for ( unsigned int i = 0; i < count; ++i ) {
		if ( views[ i ] == VK_NULL_HANDLE ) {
			for ( unsigned int j = 0; j < count; ++j ) {
				if ( views[ j ] != VK_NULL_HANDLE ) {
					state.DestroyImageView( state.device, views[ j ], NULL );
				}
				if ( sampleViews != NULL && ( *sampleViews )[ j ] != VK_NULL_HANDLE ) {
					state.DestroyImageView( state.device, ( *sampleViews )[ j ], NULL );
				}
				if ( images[ j ] != VK_NULL_HANDLE ) {
					state.DestroyImage( state.device, images[ j ], NULL );
				}
				if ( memory[ j ] != VK_NULL_HANDLE ) {
					state.FreeMemory( state.device, memory[ j ], NULL );
				}
			}
			images.Clear();
			memory.Clear();
			views.Clear();
			if ( sampleViews != NULL ) {
				sampleViews->Clear();
			}
			return false;
		}
	}
	return true;
}

void DestroySemaphoreList( sdVulkanBackendState& state,
	idList< VkSemaphore >& semaphores ) {
	for ( int i = 0; i < semaphores.Num(); ++i ) {
		if ( semaphores[ i ] != VK_NULL_HANDLE ) {
			state.DestroySemaphore( state.device, semaphores[ i ], NULL );
		}
	}
	semaphores.Clear();
}

bool CreateSemaphoreList( sdVulkanBackendState& state, unsigned int count,
	idList< VkSemaphore >& semaphores, const char* operation ) {
	semaphores.SetNum( count );
	memset( semaphores.Begin(), 0, count * sizeof( VkSemaphore ) );
	VkSemaphoreCreateInfo createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
	for ( unsigned int i = 0; i < count; ++i ) {
		if ( !CheckVulkanResult( state.CreateSemaphore( state.device, &createInfo,
			NULL, &semaphores[ i ] ), operation ) ) {
			DestroySemaphoreList( state, semaphores );
			return false;
		}
	}
	return true;
}

bool CreateSwapchain( sdVulkanBackendState& state, int requestedWidth,
	int requestedHeight ) {
	if ( requestedWidth <= 0 || requestedHeight <= 0 ) {
		return false;
	}
	VkSurfaceCapabilitiesKHR capabilities;
	if ( !CheckVulkanResult( state.GetPhysicalDeviceSurfaceCapabilitiesKHR(
		state.physicalDevice, state.surface, &capabilities ),
		"vkGetPhysicalDeviceSurfaceCapabilitiesKHR" ) ) {
		return false;
	}

	unsigned int formatCount = 0;
	state.GetPhysicalDeviceSurfaceFormatsKHR( state.physicalDevice, state.surface,
		&formatCount, NULL );
	idList< VkSurfaceFormatKHR > formats;
	formats.SetNum( formatCount );
	state.GetPhysicalDeviceSurfaceFormatsKHR( state.physicalDevice, state.surface,
		&formatCount, formats.Begin() );
	unsigned int presentModeCount = 0;
	state.GetPhysicalDeviceSurfacePresentModesKHR( state.physicalDevice, state.surface,
		&presentModeCount, NULL );
	idList< VkPresentModeKHR > presentModes;
	presentModes.SetNum( presentModeCount );
	state.GetPhysicalDeviceSurfacePresentModesKHR( state.physicalDevice, state.surface,
		&presentModeCount, presentModes.Begin() );
	if ( formats.Num() == 0 || presentModes.Num() == 0 ) {
		return false;
	}

	const VkSurfaceFormatKHR surfaceFormat = ChooseSurfaceFormat( formats );
	state.swapInterval = cvarSystem->GetCVarInteger( "r_swapInterval" );
	const VkPresentModeKHR presentMode = ChoosePresentMode( presentModes,
		state.swapInterval );
	VkExtent2D extent;
	if ( capabilities.currentExtent.width != UINT_MAX ) {
		extent = capabilities.currentExtent;
	} else {
		extent.width = ClampUnsigned( static_cast< unsigned int >( requestedWidth ),
			capabilities.minImageExtent.width, capabilities.maxImageExtent.width );
		extent.height = ClampUnsigned( static_cast< unsigned int >( requestedHeight ),
			capabilities.minImageExtent.height, capabilities.maxImageExtent.height );
	}
	if ( extent.width == 0 || extent.height == 0 ) {
		return false;
	}

	unsigned int imageCount = capabilities.minImageCount + 1;
	if ( capabilities.maxImageCount != 0 && imageCount > capabilities.maxImageCount ) {
		imageCount = capabilities.maxImageCount;
	}
	VkImageUsageFlags imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
	// Only a device created for ray tracing needs storage swapchain images; when Init
	// fell back to rasterization (no extensions, or a surface without storage usage,
	// as on Adreno), r_rayTracing alone must not fail the swapchain.
	if ( state.rayTracingDeviceEnabled &&
		( R_RayTracingIsInitialized() || R_RayTracingRequested() ) ) {
		if ( ( capabilities.supportedUsageFlags & VK_IMAGE_USAGE_STORAGE_BIT ) == 0 ) {
			common->Warning( "The Vulkan surface cannot expose swapchain storage images required by ray tracing" );
			return false;
		}
		imageUsage |= VK_IMAGE_USAGE_STORAGE_BIT;
	}
	if ( capabilities.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_SRC_BIT ) {
		imageUsage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
	}
	if ( capabilities.supportedUsageFlags & VK_IMAGE_USAGE_TRANSFER_DST_BIT ) {
		imageUsage |= VK_IMAGE_USAGE_TRANSFER_DST_BIT;
	}

	VkSwapchainCreateInfoKHR createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR;
	createInfo.surface = state.surface;
	createInfo.minImageCount = imageCount;
	createInfo.imageFormat = surfaceFormat.format;
	createInfo.imageColorSpace = surfaceFormat.colorSpace;
	createInfo.imageExtent = extent;
	createInfo.imageArrayLayers = 1;
	createInfo.imageUsage = imageUsage;
	createInfo.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
	createInfo.preTransform = capabilities.currentTransform;
	createInfo.compositeAlpha = ChooseCompositeAlpha( capabilities.supportedCompositeAlpha );
	createInfo.presentMode = presentMode;
	createInfo.clipped = VK_TRUE;
	createInfo.oldSwapchain = state.swapchain;

	VkSwapchainKHR newSwapchain = VK_NULL_HANDLE;
	if ( !CheckVulkanResult( state.CreateSwapchainKHR( state.device, &createInfo,
		NULL, &newSwapchain ), "vkCreateSwapchainKHR" ) ) {
		return false;
	}
	unsigned int newImageCount = 0;
	state.GetSwapchainImagesKHR( state.device, newSwapchain, &newImageCount, NULL );
	idList< VkImage > newImages;
	newImages.SetNum( newImageCount );
	if ( !CheckVulkanResult( state.GetSwapchainImagesKHR( state.device, newSwapchain,
		&newImageCount, newImages.Begin() ), "vkGetSwapchainImagesKHR" ) ) {
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	idList< VkImageView > newViews;
	newViews.SetNum( newImageCount );
	memset( newViews.Begin(), 0, newImageCount * sizeof( VkImageView ) );
	for ( unsigned int i = 0; i < newImageCount; ++i ) {
		VkImageViewCreateInfo viewCreateInfo;
		memset( &viewCreateInfo, 0, sizeof( viewCreateInfo ) );
		viewCreateInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
		viewCreateInfo.image = newImages[ i ];
		viewCreateInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
		viewCreateInfo.format = surfaceFormat.format;
		viewCreateInfo.components.r = VK_COMPONENT_SWIZZLE_IDENTITY;
		viewCreateInfo.components.g = VK_COMPONENT_SWIZZLE_IDENTITY;
		viewCreateInfo.components.b = VK_COMPONENT_SWIZZLE_IDENTITY;
		viewCreateInfo.components.a = VK_COMPONENT_SWIZZLE_IDENTITY;
		viewCreateInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		viewCreateInfo.subresourceRange.levelCount = 1;
		viewCreateInfo.subresourceRange.layerCount = 1;
		if ( !CheckVulkanResult( state.CreateImageView( state.device,
			&viewCreateInfo, NULL, &newViews[ i ] ), "vkCreateImageView" ) ) {
			for ( unsigned int j = 0; j < i; ++j ) {
				state.DestroyImageView( state.device, newViews[ j ], NULL );
			}
			state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
			return false;
		}
	}
	idList< VkImage > newDepthImages;
	idList< VkDeviceMemory > newDepthMemory;
	idList< VkImageView > newDepthViews;
	idList< VkImageView > newDepthSampleViews;
	if ( !CreateDepthResources( state, newImageCount, extent, newDepthImages,
		newDepthMemory, newDepthViews, &newDepthSampleViews ) ) {
		for ( unsigned int i = 0; i < newImageCount; ++i ) {
			state.DestroyImageView( state.device, newViews[ i ], NULL );
		}
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	idList< VkSemaphore > newPresentSemaphores;
	if ( !CreateSemaphoreList( state, newImageCount, newPresentSemaphores,
		"vkCreateSemaphore(present)" ) ) {
		for ( unsigned int i = 0; i < newImageCount; ++i ) {
			state.DestroyImageView( state.device, newDepthSampleViews[ i ], NULL );
			state.DestroyImageView( state.device, newDepthViews[ i ], NULL );
			state.DestroyImage( state.device, newDepthImages[ i ], NULL );
			state.FreeMemory( state.device, newDepthMemory[ i ], NULL );
			state.DestroyImageView( state.device, newViews[ i ], NULL );
		}
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	sdVulkanImageResource newCurrentRenderResource;
	memset( &newCurrentRenderResource, 0, sizeof( newCurrentRenderResource ) );
	const bool canCopyCurrentRender =
		( imageUsage & VK_IMAGE_USAGE_TRANSFER_SRC_BIT ) != 0;
	if ( canCopyCurrentRender && !CreateCurrentRenderResource( state,
		surfaceFormat.format, extent, newCurrentRenderResource ) ) {
		DestroySemaphoreList( state, newPresentSemaphores );
		for ( unsigned int i = 0; i < newImageCount; ++i ) {
			state.DestroyImageView( state.device, newDepthSampleViews[ i ], NULL );
			state.DestroyImageView( state.device, newDepthViews[ i ], NULL );
			state.DestroyImage( state.device, newDepthImages[ i ], NULL );
			state.FreeMemory( state.device, newDepthMemory[ i ], NULL );
			state.DestroyImageView( state.device, newViews[ i ], NULL );
		}
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}

	DestroyGlowBuffers( state );
	PurgeCurrentRenderDescriptors( state );
	DestroyCurrentRenderResource( state, state.currentRenderResource );
	DestroyDepthResources( state );
	DestroySemaphoreList( state, state.presentSemaphores );
	for ( int i = 0; i < state.swapchainViews.Num(); ++i ) {
		state.DestroyImageView( state.device, state.swapchainViews[ i ], NULL );
	}
	if ( state.swapchain != VK_NULL_HANDLE ) {
		state.DestroySwapchainKHR( state.device, state.swapchain, NULL );
	}
	state.swapchain = newSwapchain;
	state.swapchainImages = newImages;
	state.swapchainViews = newViews;
	state.presentSemaphores = newPresentSemaphores;
	state.imageInitialized.SetNum( newImageCount );
	memset( state.imageInitialized.Begin(), 0, newImageCount );
	state.depthImages = newDepthImages;
	state.depthMemory = newDepthMemory;
	state.depthViews = newDepthViews;
	state.depthSampleViews = newDepthSampleViews;
	state.depthInitialized.SetNum( newImageCount );
	memset( state.depthInitialized.Begin(), 0, newImageCount );
	state.currentRenderResource = newCurrentRenderResource;
	state.currentRenderInitialized = false;
	state.swapchainTransferSource = canCopyCurrentRender;
	state.swapchainFormat = surfaceFormat.format;
	state.colorSpace = surfaceFormat.colorSpace;
	state.extent = extent;
	state.swapchainDirty = false;
	common->Printf( "Vulkan swapchain: %ux%u, %u images, format %d, present mode %d\n",
		extent.width, extent.height, newImageCount,
		static_cast< int >( surfaceFormat.format ), static_cast< int >( presentMode ) );
	return true;
}

bool CreateBufferAllocation( sdVulkanBackendState& state, VkDeviceSize bytes,
	VkBufferUsageFlags usage, VkMemoryPropertyFlags memoryFlags,
	VkBuffer& buffer, VkDeviceMemory& memory );

void DestroyFrames( sdVulkanBackendState& state ) {
	if ( state.device == VK_NULL_HANDLE ) {
		return;
	}
	for ( int i = 0; i < NUM_VULKAN_FRAMES; ++i ) {
		sdVulkanFrame& frame = state.frames[ i ];
		if ( frame.fence != VK_NULL_HANDLE && state.DestroyFence != NULL ) {
			state.DestroyFence( state.device, frame.fence, NULL );
		}
		if ( frame.imageAvailable != VK_NULL_HANDLE && state.DestroySemaphore != NULL ) {
			state.DestroySemaphore( state.device, frame.imageAvailable, NULL );
		}
		if ( frame.renderComplete != VK_NULL_HANDLE && state.DestroySemaphore != NULL ) {
			state.DestroySemaphore( state.device, frame.renderComplete, NULL );
		}
		if ( frame.guiVertexMapped != NULL && state.UnmapMemory != NULL ) {
			state.UnmapMemory( state.device, frame.guiVertexMemory );
			frame.guiVertexMapped = NULL;
		}
		if ( frame.guiVertexBuffer != VK_NULL_HANDLE && state.DestroyBuffer != NULL ) {
			state.DestroyBuffer( state.device, frame.guiVertexBuffer, NULL );
		}
		if ( frame.guiVertexMemory != VK_NULL_HANDLE && state.FreeMemory != NULL ) {
			state.FreeMemory( state.device, frame.guiVertexMemory, NULL );
		}
		if ( frame.guiIndexMapped != NULL && state.UnmapMemory != NULL ) {
			state.UnmapMemory( state.device, frame.guiIndexMemory );
			frame.guiIndexMapped = NULL;
		}
		if ( frame.guiIndexBuffer != VK_NULL_HANDLE && state.DestroyBuffer != NULL ) {
			state.DestroyBuffer( state.device, frame.guiIndexBuffer, NULL );
		}
		if ( frame.guiIndexMemory != VK_NULL_HANDLE && state.FreeMemory != NULL ) {
			state.FreeMemory( state.device, frame.guiIndexMemory, NULL );
		}
		if ( frame.imageUploadMapped != NULL && state.UnmapMemory != NULL ) {
			state.UnmapMemory( state.device, frame.imageUploadMemory );
			frame.imageUploadMapped = NULL;
		}
		if ( frame.imageUploadBuffer != VK_NULL_HANDLE && state.DestroyBuffer != NULL ) {
			state.DestroyBuffer( state.device, frame.imageUploadBuffer, NULL );
		}
		if ( frame.imageUploadMemory != VK_NULL_HANDLE && state.FreeMemory != NULL ) {
			state.FreeMemory( state.device, frame.imageUploadMemory, NULL );
		}
		if ( frame.bufferUploadMapped != NULL && state.UnmapMemory != NULL ) {
			state.UnmapMemory( state.device, frame.bufferUploadMemory );
			frame.bufferUploadMapped = NULL;
		}
		if ( frame.bufferUploadBuffer != VK_NULL_HANDLE && state.DestroyBuffer != NULL ) {
			state.DestroyBuffer( state.device, frame.bufferUploadBuffer, NULL );
		}
		if ( frame.bufferUploadMemory != VK_NULL_HANDLE && state.FreeMemory != NULL ) {
			state.FreeMemory( state.device, frame.bufferUploadMemory, NULL );
		}
		if ( frame.jointMapped != NULL && state.UnmapMemory != NULL ) {
			state.UnmapMemory( state.device, frame.jointMemory );
			frame.jointMapped = NULL;
		}
		if ( frame.jointBuffer != VK_NULL_HANDLE && state.DestroyBuffer != NULL ) {
			state.DestroyBuffer( state.device, frame.jointBuffer, NULL );
		}
		if ( frame.jointMemory != VK_NULL_HANDLE && state.FreeMemory != NULL ) {
			state.FreeMemory( state.device, frame.jointMemory, NULL );
		}
		if ( frame.commandPool != VK_NULL_HANDLE && state.DestroyCommandPool != NULL ) {
			state.DestroyCommandPool( state.device, frame.commandPool, NULL );
		}
		memset( &frame, 0, sizeof( frame ) );
	}
}

bool CreateFrames( sdVulkanBackendState& state ) {
	VkSemaphoreCreateInfo semaphoreCreateInfo;
	memset( &semaphoreCreateInfo, 0, sizeof( semaphoreCreateInfo ) );
	semaphoreCreateInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO;
	VkFenceCreateInfo fenceCreateInfo;
	memset( &fenceCreateInfo, 0, sizeof( fenceCreateInfo ) );
	fenceCreateInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
	fenceCreateInfo.flags = VK_FENCE_CREATE_SIGNALED_BIT;

	for ( int i = 0; i < NUM_VULKAN_FRAMES; ++i ) {
		sdVulkanFrame& frame = state.frames[ i ];
		VkCommandPoolCreateInfo poolCreateInfo;
		memset( &poolCreateInfo, 0, sizeof( poolCreateInfo ) );
		poolCreateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
		poolCreateInfo.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
		poolCreateInfo.queueFamilyIndex = state.queueFamilyIndex;
		if ( !CheckVulkanResult( state.CreateCommandPool( state.device,
			&poolCreateInfo, NULL, &frame.commandPool ), "vkCreateCommandPool" ) ) {
			return false;
		}
		VkCommandBufferAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		allocateInfo.commandPool = frame.commandPool;
		allocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		allocateInfo.commandBufferCount = 1;
		if ( !CheckVulkanResult( state.AllocateCommandBuffers( state.device,
			&allocateInfo, &frame.commandBuffer ), "vkAllocateCommandBuffers" ) ||
			!CheckVulkanResult( state.CreateSemaphore( state.device,
				&semaphoreCreateInfo, NULL, &frame.imageAvailable ), "vkCreateSemaphore" ) ||
			!CheckVulkanResult( state.CreateSemaphore( state.device,
				&semaphoreCreateInfo, NULL, &frame.renderComplete ), "vkCreateSemaphore" ) ||
			!CheckVulkanResult( state.CreateFence( state.device,
				&fenceCreateInfo, NULL, &frame.fence ), "vkCreateFence" ) ) {
			return false;
		}
		if ( !CreateBufferAllocation( state, VULKAN_GUI_VERTEX_BYTES,
			VK_BUFFER_USAGE_VERTEX_BUFFER_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			frame.guiVertexBuffer, frame.guiVertexMemory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, frame.guiVertexMemory,
				0, VULKAN_GUI_VERTEX_BYTES, 0, &frame.guiVertexMapped ),
				"vkMapMemory(gui vertices)" ) ) {
			return false;
		}
		if ( !CheckVulkanResult( state.AllocateCommandBuffers( state.device,
			&allocateInfo, &frame.imageUploadCommandBuffer ),
			"vkAllocateCommandBuffers(image uploads)" ) ) {
			return false;
		}
		if ( !CreateBufferAllocation( state, VULKAN_GUI_INDEX_BYTES,
			VK_BUFFER_USAGE_INDEX_BUFFER_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			frame.guiIndexBuffer, frame.guiIndexMemory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, frame.guiIndexMemory,
				0, VULKAN_GUI_INDEX_BYTES, 0, &frame.guiIndexMapped ),
				"vkMapMemory(gui indices)" ) ) {
			return false;
		}
		if ( !CreateBufferAllocation( state, VULKAN_IMAGE_UPLOAD_BYTES,
			VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			frame.imageUploadBuffer, frame.imageUploadMemory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, frame.imageUploadMemory,
				0, VULKAN_IMAGE_UPLOAD_BYTES, 0, &frame.imageUploadMapped ),
				"vkMapMemory(image upload arena)" ) ) {
			return false;
		}
		if ( !CreateBufferAllocation( state, VULKAN_BUFFER_UPLOAD_BYTES,
			VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			frame.bufferUploadBuffer, frame.bufferUploadMemory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, frame.bufferUploadMemory,
				0, VULKAN_BUFFER_UPLOAD_BYTES, 0, &frame.bufferUploadMapped ),
				"vkMapMemory(buffer upload arena)" ) ) {
			return false;
		}
		if ( !CreateBufferAllocation( state, VULKAN_JOINT_BUFFER_BYTES,
			VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			frame.jointBuffer, frame.jointMemory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, frame.jointMemory,
				0, VULKAN_JOINT_BUFFER_BYTES, 0, &frame.jointMapped ),
				"vkMapMemory(joint palette arena)" ) ) {
			return false;
		}
		VkDescriptorSetAllocateInfo jointAllocateInfo;
		memset( &jointAllocateInfo, 0, sizeof( jointAllocateInfo ) );
		jointAllocateInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
		jointAllocateInfo.descriptorPool = state.guiDescriptorPool;
		jointAllocateInfo.descriptorSetCount = 1;
		jointAllocateInfo.pSetLayouts = &state.jointDescriptorSetLayout;
		if ( !CheckVulkanResult( state.AllocateDescriptorSets( state.device,
			&jointAllocateInfo, &frame.jointDescriptorSet ),
			"vkAllocateDescriptorSets(joint palette)" ) ) {
			return false;
		}
		VkDescriptorBufferInfo jointBufferInfo;
		jointBufferInfo.buffer = frame.jointBuffer;
		jointBufferInfo.offset = 0;
		jointBufferInfo.range = VULKAN_JOINT_PALETTE_BYTES;
		VkWriteDescriptorSet jointWrite;
		memset( &jointWrite, 0, sizeof( jointWrite ) );
		jointWrite.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		jointWrite.dstSet = frame.jointDescriptorSet;
		jointWrite.dstBinding = 0;
		jointWrite.descriptorCount = 1;
		jointWrite.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
		jointWrite.pBufferInfo = &jointBufferInfo;
		state.UpdateDescriptorSets( state.device, 1, &jointWrite, 0, NULL );
		frame.guiVertexOffset = 0;
		frame.guiIndexOffset = 0;
		frame.guiVertexOverflowWarned = false;
		frame.guiIndexOverflowWarned = false;
		frame.imageUploadOffset = 0;
		frame.imageUploadRecorded = false;
		frame.imageUploadOverflowWarned = false;
		frame.bufferUploadOffset = 0;
		frame.bufferUploadOverflowWarned = false;
		frame.jointOffset = 0;
		frame.jointOverflowWarned = false;
	}
	return true;
}

void RestoreSignaledFence( sdVulkanBackendState& state, sdVulkanFrame& frame ) {
	if ( frame.fence != VK_NULL_HANDLE ) {
		state.DestroyFence( state.device, frame.fence, NULL );
		frame.fence = VK_NULL_HANDLE;
	}
	VkFenceCreateInfo createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO;
	createInfo.flags = VK_FENCE_CREATE_SIGNALED_BIT;
	state.CreateFence( state.device, &createInfo, NULL, &frame.fence );
}

bool FindMemoryType( const sdVulkanBackendState& state, unsigned int typeBits,
	VkMemoryPropertyFlags required, unsigned int& typeIndex ) {
	for ( unsigned int i = 0; i < state.memoryProperties.memoryTypeCount; ++i ) {
		if ( ( typeBits & ( 1u << i ) ) != 0 &&
			( state.memoryProperties.memoryTypes[ i ].propertyFlags & required ) == required ) {
			typeIndex = i;
			return true;
		}
	}
	return false;
}

void DestroyImageResource( sdVulkanBackendState& state,
	sdVulkanImageResource& resource ) {
	if ( resource.descriptorSet != VK_NULL_HANDLE &&
		state.guiDescriptorPool != VK_NULL_HANDLE ) {
		state.FreeDescriptorSets( state.device, state.guiDescriptorPool, 1,
			&resource.descriptorSet );
	}
	if ( resource.sampler != VK_NULL_HANDLE ) {
		state.DestroySampler( state.device, resource.sampler, NULL );
	}
	if ( resource.view != VK_NULL_HANDLE ) {
		state.DestroyImageView( state.device, resource.view, NULL );
	}
	if ( resource.image != VK_NULL_HANDLE ) {
		state.DestroyImage( state.device, resource.image, NULL );
	}
	if ( resource.memory != VK_NULL_HANDLE ) {
		state.FreeMemory( state.device, resource.memory, NULL );
	}
	memset( &resource, 0, sizeof( resource ) );
}

void DestroyAllImageResources( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.imageResources.Num(); ++i ) {
		DestroyImageResource( state, state.imageResources[ i ] );
	}
	state.imageResources.Clear();
	state.imageResourceLookup.Clear();
	for ( int i = 0; i < state.retiredImageResources.Num(); ++i ) {
		DestroyImageResource( state, state.retiredImageResources[ i ] );
	}
	state.retiredImageResources.Clear();
	for ( int frameIndex = 0; frameIndex < NUM_VULKAN_FRAMES; ++frameIndex ) {
		for ( int i = 0; i < state.frameRetiredImageResources[ frameIndex ].Num(); ++i ) {
			DestroyImageResource( state,
				state.frameRetiredImageResources[ frameIndex ][ i ] );
		}
		state.frameRetiredImageResources[ frameIndex ].Clear();
	}
}

void DestroyToolDepthResource( sdVulkanBackendState& state,
	sdVulkanToolDepthResource& resource ) {
	if ( resource.view != VK_NULL_HANDLE ) {
		state.DestroyImageView( state.device, resource.view, NULL );
	}
	if ( resource.image != VK_NULL_HANDLE ) {
		state.DestroyImage( state.device, resource.image, NULL );
	}
	if ( resource.memory != VK_NULL_HANDLE ) {
		state.FreeMemory( state.device, resource.memory, NULL );
	}
	memset( &resource, 0, sizeof( resource ) );
}

void DestroyAllToolRenderTargets( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.toolRenderTargets.Num(); ++i ) {
		sdVulkanBackendState::sdToolRenderTarget* target =
			state.toolRenderTargets[ i ];
		if ( target != NULL ) {
			sdVulkanToolDepthResource depth = { target->depthImage,
				target->depthMemory, target->depthView };
			DestroyToolDepthResource( state, depth );
			delete target;
		}
	}
	state.toolRenderTargets.Clear();
	for ( int i = 0; i < state.retiredToolDepthResources.Num(); ++i ) {
		DestroyToolDepthResource( state, state.retiredToolDepthResources[ i ] );
	}
	state.retiredToolDepthResources.Clear();
	state.activeToolRenderTarget = NULL;
}

/*
Vertex-cache buffers (UploadBuffer) are bound into shared blocks of device
memory.  Each cache block used to own a VkDeviceMemory, about 3,700 of them on
valley, while many Windows drivers allow 4096 allocations in total
(maxMemoryAllocationCount), images and the rest of the backend included.  A
buffer keeps its own VkBuffer, bound at an offset into a block; larger requests
still get their own allocation.
*/
const VkDeviceSize VULKAN_BUFFER_BLOCK_BYTES = 32 * 1024 * 1024;
const VkDeviceSize VULKAN_BUFFER_POOLED_MAX_BYTES = VULKAN_BUFFER_BLOCK_BYTES / 4;

bool AllocateFromMemoryBlock( sdVulkanMemoryBlock& block, VkDeviceSize bytes,
	VkDeviceSize alignment, VkDeviceSize& offset ) {
	if ( alignment == 0 ) {
		alignment = 1;
	}
	for ( int i = 0; i < block.freeRanges.Num(); ++i ) {
		sdVulkanMemoryRange& range = block.freeRanges[ i ];
		const VkDeviceSize start = ( range.offset + alignment - 1 ) / alignment * alignment;
		const VkDeviceSize end = range.offset + range.bytes;
		if ( start + bytes > end ) {
			continue;
		}
		sdVulkanMemoryRange tail;
		tail.offset = start + bytes;
		tail.bytes = end - tail.offset;
		if ( start > range.offset ) {
			// the alignment padding in front stays free
			range.bytes = start - range.offset;
			if ( tail.bytes > 0 ) {
				block.freeRanges.Insert( tail, i + 1 );
			}
		} else if ( tail.bytes > 0 ) {
			range = tail;
		} else {
			block.freeRanges.RemoveIndex( i );
		}
		offset = start;
		++block.liveAllocations;
		return true;
	}
	return false;
}

void FreeToMemoryBlock( sdVulkanMemoryBlock& block, VkDeviceSize offset,
	VkDeviceSize bytes ) {
	int index = 0;
	while ( index < block.freeRanges.Num() && block.freeRanges[ index ].offset < offset ) {
		++index;
	}
	sdVulkanMemoryRange range;
	range.offset = offset;
	range.bytes = bytes;
	block.freeRanges.Insert( range, index );
	if ( index + 1 < block.freeRanges.Num() && block.freeRanges[ index ].offset +
		block.freeRanges[ index ].bytes == block.freeRanges[ index + 1 ].offset ) {
		block.freeRanges[ index ].bytes += block.freeRanges[ index + 1 ].bytes;
		block.freeRanges.RemoveIndex( index + 1 );
	}
	if ( index > 0 && block.freeRanges[ index - 1 ].offset +
		block.freeRanges[ index - 1 ].bytes == block.freeRanges[ index ].offset ) {
		block.freeRanges[ index - 1 ].bytes += block.freeRanges[ index ].bytes;
		block.freeRanges.RemoveIndex( index );
	}
	--block.liveAllocations;
}

void DestroyMemoryBlock( sdVulkanBackendState& state, int blockIndex ) {
	sdVulkanMemoryBlock* block = state.bufferMemoryBlocks[ blockIndex ];
	if ( block->mapped != NULL ) {
		state.UnmapMemory( state.device, block->memory );
	}
	state.FreeMemory( state.device, block->memory, NULL );
	delete block;
	state.bufferMemoryBlocks[ blockIndex ] = NULL;
}

// Returns a buffer's range to its block.  An empty block is freed unless it is
// the only empty one of its kind, which is kept for the next frame's buffers.
void ReleasePooledBufferMemory( sdVulkanBackendState& state, int blockIndex,
	VkDeviceSize offset, VkDeviceSize bytes ) {
	sdVulkanMemoryBlock* block = state.bufferMemoryBlocks[ blockIndex ];
	FreeToMemoryBlock( *block, offset, bytes );
	if ( block->liveAllocations > 0 ) {
		return;
	}
	for ( int i = 0; i < state.bufferMemoryBlocks.Num(); ++i ) {
		const sdVulkanMemoryBlock* other = state.bufferMemoryBlocks[ i ];
		if ( i != blockIndex && other != NULL && other->liveAllocations == 0 &&
			other->memoryType == block->memoryType &&
			other->deviceAddress == block->deviceAddress ) {
			DestroyMemoryBlock( state, blockIndex );
			return;
		}
	}
}

void DestroyBufferMemoryBlocks( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.bufferMemoryBlocks.Num(); ++i ) {
		if ( state.bufferMemoryBlocks[ i ] == NULL ) {
			continue;
		}
		if ( state.bufferMemoryBlocks[ i ]->liveAllocations != 0 ) {
			common->Warning( "Vulkan buffer memory block %d freed with %d live buffers", i,
				state.bufferMemoryBlocks[ i ]->liveAllocations );
		}
		DestroyMemoryBlock( state, i );
	}
	state.bufferMemoryBlocks.Clear();
}

int LiveBufferMemoryBlocks( const sdVulkanBackendState& state ) {
	int count = 0;
	for ( int i = 0; i < state.bufferMemoryBlocks.Num(); ++i ) {
		if ( state.bufferMemoryBlocks[ i ] != NULL ) {
			++count;
		}
	}
	return count;
}

void DestroyBufferResource( sdVulkanBackendState& state,
	sdVulkanBufferResource& resource ) {
	if ( resource.poolBlock > 0 ) {
		// the buffer goes first: its range may be reused as soon as it is free
		if ( resource.buffer != VK_NULL_HANDLE ) {
			state.DestroyBuffer( state.device, resource.buffer, NULL );
		}
		ReleasePooledBufferMemory( state, resource.poolBlock - 1, resource.poolOffset,
			resource.poolBytes );
		memset( &resource, 0, sizeof( resource ) );
		return;
	}
	if ( resource.mapped != NULL ) {
		state.UnmapMemory( state.device, resource.memory );
		resource.mapped = NULL;
	}
	if ( resource.buffer != VK_NULL_HANDLE ) {
		state.DestroyBuffer( state.device, resource.buffer, NULL );
	}
	if ( resource.memory != VK_NULL_HANDLE ) {
		state.FreeMemory( state.device, resource.memory, NULL );
	}
	memset( &resource, 0, sizeof( resource ) );
}

void DestroyAllBufferResources( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.bufferResources.Num(); ++i ) {
		DestroyBufferResource( state, state.bufferResources[ i ] );
	}
	state.bufferResources.Clear();
	state.bufferResourceLookup.Clear();
	for ( int i = 0; i < state.retiredBufferResources.Num(); ++i ) {
		DestroyBufferResource( state, state.retiredBufferResources[ i ] );
	}
	state.retiredBufferResources.Clear();
	for ( int frameIndex = 0; frameIndex < NUM_VULKAN_FRAMES; ++frameIndex ) {
		for ( int i = 0; i < state.frameRetiredBufferResources[ frameIndex ].Num(); ++i ) {
			DestroyBufferResource( state,
				state.frameRetiredBufferResources[ frameIndex ][ i ] );
		}
		state.frameRetiredBufferResources[ frameIndex ].Clear();
	}
}

void DestroyRetiredResources( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.retiredImageResources.Num(); ++i ) {
		DestroyImageResource( state, state.retiredImageResources[ i ] );
	}
	state.retiredImageResources.Clear();
	for ( int i = 0; i < state.retiredBufferResources.Num(); ++i ) {
		DestroyBufferResource( state, state.retiredBufferResources[ i ] );
	}
	state.retiredBufferResources.Clear();
	for ( int i = 0; i < state.retiredToolDepthResources.Num(); ++i ) {
		DestroyToolDepthResource( state, state.retiredToolDepthResources[ i ] );
	}
	state.retiredToolDepthResources.Clear();
}

int FrontEndDescriptorKey( const sdVulkanBackendState& state, const void* owner ) {
	return sdVulkanPointerHash::Hash( state.frontEndDescriptorHash, owner );
}

// Removes a front-end descriptor from the cache (the last one takes its index);
// the caller frees or retires its set.
void RemoveFrontEndDescriptor( sdVulkanBackendState& state, int index ) {
	const int lastIndex = state.frontEndDescriptors.Num() - 1;
	state.frontEndDescriptorHash.Remove(
		FrontEndDescriptorKey( state, state.frontEndDescriptors[ index ].owner ), index );
	if ( index != lastIndex ) {
		state.frontEndDescriptorHash.Remove(
			FrontEndDescriptorKey( state, state.frontEndDescriptors[ lastIndex ].owner ), lastIndex );
	}
	state.frontEndDescriptors.RemoveIndexFast( index );
	if ( index != lastIndex ) {
		state.frontEndDescriptorHash.Add(
			FrontEndDescriptorKey( state, state.frontEndDescriptors[ index ].owner ), index );
	}
}

void FreeFrontEndDescriptorSets( sdVulkanBackendState& state, idList< VkDescriptorSet >& sets ) {
	if ( sets.Num() > 0 && state.frontEndDescriptorPool != VK_NULL_HANDLE ) {
		state.FreeDescriptorSets( state.device, state.frontEndDescriptorPool,
			static_cast< uint32_t >( sets.Num() ), &sets[ 0 ] );
		state.frontEndDescriptorSetsLive -= sets.Num();
	}
	sets.Clear();
}

// Frees the sets no frame has used for longer than the frames in flight, so no
// command buffer can still reference them.  Returns how many it freed.
int EvictUnusedFrontEndDescriptors( sdVulkanBackendState& state ) {
	idList< VkDescriptorSet > unused;
	for ( int index = state.frontEndDescriptors.Num() - 1; index >= 0; --index ) {
		const sdVulkanFrontEndDescriptor& descriptor = state.frontEndDescriptors[ index ];
		if ( state.frameSerial - descriptor.lastUsedFrame > static_cast< unsigned int >( NUM_VULKAN_FRAMES ) ) {
			unused.Append( descriptor.descriptorSet );
			RemoveFrontEndDescriptor( state, index );
		}
	}
	const int evicted = unused.Num();
	FreeFrontEndDescriptorSets( state, unused );
	return evicted;
}

int MaterialDescriptorKey( const sdVulkanBackendState& state, const void* owner ) {
	return sdVulkanPointerHash::Hash( state.materialDescriptorHash, owner );
}

// Removes a material descriptor from the cache (the last one takes its index);
// the caller frees or retires its set.
void RemoveMaterialDescriptor( sdVulkanBackendState& state, int index ) {
	const int lastIndex = state.materialDescriptors.Num() - 1;
	state.materialDescriptorHash.Remove(
		MaterialDescriptorKey( state, state.materialDescriptors[ index ].owner ), index );
	if ( index != lastIndex ) {
		state.materialDescriptorHash.Remove(
			MaterialDescriptorKey( state, state.materialDescriptors[ lastIndex ].owner ), lastIndex );
	}
	state.materialDescriptors.RemoveIndexFast( index );
	if ( index != lastIndex ) {
		state.materialDescriptorHash.Add(
			MaterialDescriptorKey( state, state.materialDescriptors[ index ].owner ), index );
	}
}

void FreeMaterialDescriptorSets( sdVulkanBackendState& state, idList< VkDescriptorSet >& sets ) {
	if ( sets.Num() > 0 && state.guiDescriptorPool != VK_NULL_HANDLE ) {
		state.FreeDescriptorSets( state.device, state.guiDescriptorPool,
			static_cast< uint32_t >( sets.Num() ), &sets[ 0 ] );
	}
	sets.Clear();
}

// As EvictUnusedFrontEndDescriptors, for the material cache.
int EvictUnusedMaterialDescriptors( sdVulkanBackendState& state ) {
	idList< VkDescriptorSet > unused;
	for ( int index = state.materialDescriptors.Num() - 1; index >= 0; --index ) {
		const sdVulkanMaterialDescriptor& descriptor = state.materialDescriptors[ index ];
		if ( state.frameSerial - descriptor.lastUsedFrame > static_cast< unsigned int >( NUM_VULKAN_FRAMES ) ) {
			unused.Append( descriptor.descriptorSet );
			RemoveMaterialDescriptor( state, index );
		}
	}
	const int evicted = unused.Num();
	FreeMaterialDescriptorSets( state, unused );
	return evicted;
}

// Allocates a material-layout set from guiDescriptorPool.  When the pool is full,
// the material sets no recent frame used are freed and the allocation is tried
// once more; a failure after that warns once.
bool AllocateMaterialDescriptorSet( sdVulkanBackendState& state, VkDescriptorSet& descriptorSet,
	const char* what ) {
	VkDescriptorSetAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
	allocateInfo.descriptorPool = state.guiDescriptorPool;
	allocateInfo.descriptorSetCount = 1;
	allocateInfo.pSetLayouts = &state.guiDescriptorSetLayout;
	VkResult result = state.AllocateDescriptorSets( state.device, &allocateInfo, &descriptorSet );
	if ( ( result == VK_ERROR_OUT_OF_POOL_MEMORY || result == VK_ERROR_FRAGMENTED_POOL ) &&
		EvictUnusedMaterialDescriptors( state ) > 0 ) {
		result = state.AllocateDescriptorSets( state.device, &allocateInfo, &descriptorSet );
	}
	if ( result != VK_SUCCESS ) {
		if ( !state.materialPoolExhaustedWarned ) {
			common->Warning( "vkAllocateDescriptorSets(%s) failed: %s (%d material sets cached); "
				"draws needing a new set are skipped until sets are freed (warned once)",
				what, VkResultName( result ), state.materialDescriptors.Num() );
			state.materialPoolExhaustedWarned = true;
		}
		descriptorSet = VK_NULL_HANDLE;
		return false;
	}
	state.materialPoolExhaustedWarned = false;	// warn once per exhaustion
	return true;
}

// The water, heat-haze and scene-albedo sets sample the current-render copy,
// cached under its fixed address (&state.currentRenderResource).  When the copy is
// recreated with the swapchain, its new view can reuse the old handle's value, so
// the cache would return a set holding the destroyed view: drop those entries and
// free their sets once the frame that may use them has completed.
void PurgeCurrentRenderDescriptors( sdVulkanBackendState& state ) {
	const void* owner = &state.currentRenderResource;
	const unsigned int retireFrame = state.frameActive || state.toolFrameActive ? state.frameIndex :
		( state.frameIndex + NUM_VULKAN_FRAMES - 1 ) % NUM_VULKAN_FRAMES;
	for ( int index = state.materialDescriptors.Num() - 1; index >= 0; --index ) {
		const sdVulkanMaterialDescriptor& descriptor = state.materialDescriptors[ index ];
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
			if ( descriptor.imageOwners[ imageIndex ] == owner ||
				( state.currentRenderResource.view != VK_NULL_HANDLE &&
				  descriptor.imageViews[ imageIndex ] == state.currentRenderResource.view ) ) {
				state.frameRetiredMaterialSets[ retireFrame ].Append( descriptor.descriptorSet );
				RemoveMaterialDescriptor( state, index );
				break;
			}
		}
	}
}

void DestroyFrameRetiredResources( sdVulkanBackendState& state,
	unsigned int frameIndex ) {
	FreeFrontEndDescriptorSets( state, state.frameRetiredFrontEndSets[ frameIndex ] );
	FreeMaterialDescriptorSets( state, state.frameRetiredMaterialSets[ frameIndex ] );
	for ( int i = 0; i < state.frameRetiredImageResources[ frameIndex ].Num(); ++i ) {
		DestroyImageResource( state,
			state.frameRetiredImageResources[ frameIndex ][ i ] );
	}
	state.frameRetiredImageResources[ frameIndex ].Clear();
	for ( int i = 0; i < state.frameRetiredBufferResources[ frameIndex ].Num(); ++i ) {
		DestroyBufferResource( state,
			state.frameRetiredBufferResources[ frameIndex ][ i ] );
	}
	state.frameRetiredBufferResources[ frameIndex ].Clear();
}

bool CreateBufferAllocation( sdVulkanBackendState& state, VkDeviceSize bytes,
	VkBufferUsageFlags usage, VkMemoryPropertyFlags memoryFlags,
	VkBuffer& buffer, VkDeviceMemory& memory ) {
	buffer = VK_NULL_HANDLE;
	memory = VK_NULL_HANDLE;
	VkBufferCreateInfo bufferInfo;
	memset( &bufferInfo, 0, sizeof( bufferInfo ) );
	bufferInfo.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
	bufferInfo.size = bytes;
	bufferInfo.usage = usage;
	bufferInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	if ( !CheckVulkanResult( state.CreateBuffer( state.device, &bufferInfo, NULL,
		&buffer ), "vkCreateBuffer" ) ) {
		return false;
	}
	VkMemoryRequirements requirements;
	state.GetBufferMemoryRequirements( state.device, buffer, &requirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( state, requirements.memoryTypeBits, memoryFlags,
		memoryType ) ) {
		common->Warning( "No Vulkan buffer memory type satisfies flags 0x%x",
			static_cast< unsigned int >( memoryFlags ) );
		state.DestroyBuffer( state.device, buffer, NULL );
		buffer = VK_NULL_HANDLE;
		return false;
	}
	VkMemoryAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	VkMemoryAllocateFlagsInfo addressFlags;
	memset( &addressFlags, 0, sizeof( addressFlags ) );
	if ( ( usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT ) != 0 ) {
		addressFlags.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
		addressFlags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
		allocateInfo.pNext = &addressFlags;
	}
	allocateInfo.allocationSize = requirements.size;
	allocateInfo.memoryTypeIndex = memoryType;
	if ( !CheckVulkanResult( state.AllocateMemory( state.device, &allocateInfo,
		NULL, &memory ), "vkAllocateMemory(buffer)" ) ||
		!CheckVulkanResult( state.BindBufferMemory( state.device, buffer, memory, 0 ),
			"vkBindBufferMemory" ) ) {
		if ( memory != VK_NULL_HANDLE ) {
			state.FreeMemory( state.device, memory, NULL );
		}
		state.DestroyBuffer( state.device, buffer, NULL );
		buffer = VK_NULL_HANDLE;
		memory = VK_NULL_HANDLE;
		return false;
	}
	return true;
}

// Creates resource.buffer (resource.bytes long) with memory from a shared block,
// or with its own allocation when the request is large.  When memoryFlags ask for
// host-visible memory, resource.mapped points at the buffer's bytes; otherwise it
// stays NULL even if the chosen type is host-visible, so updates keep going
// through the staged upload.
bool CreatePooledBuffer( sdVulkanBackendState& state, VkBufferUsageFlags usage,
	VkMemoryPropertyFlags memoryFlags, sdVulkanBufferResource& resource ) {
	resource.buffer = VK_NULL_HANDLE;
	resource.memory = VK_NULL_HANDLE;
	resource.mapped = NULL;
	resource.poolBlock = 0;
	VkBufferCreateInfo bufferInfo;
	memset( &bufferInfo, 0, sizeof( bufferInfo ) );
	bufferInfo.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
	bufferInfo.size = resource.bytes;
	bufferInfo.usage = usage;
	bufferInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	if ( !CheckVulkanResult( state.CreateBuffer( state.device, &bufferInfo, NULL,
		&resource.buffer ), "vkCreateBuffer" ) ) {
		resource.buffer = VK_NULL_HANDLE;
		return false;
	}
	VkMemoryRequirements requirements;
	state.GetBufferMemoryRequirements( state.device, resource.buffer, &requirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( state, requirements.memoryTypeBits, memoryFlags, memoryType ) ) {
		common->Warning( "No Vulkan buffer memory type satisfies flags 0x%x",
			static_cast< unsigned int >( memoryFlags ) );
		state.DestroyBuffer( state.device, resource.buffer, NULL );
		resource.buffer = VK_NULL_HANDLE;
		return false;
	}
	const bool wantMapped = ( memoryFlags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT ) != 0;
	const bool deviceAddress = ( usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT ) != 0;
	VkMemoryAllocateFlagsInfo addressFlags;
	memset( &addressFlags, 0, sizeof( addressFlags ) );
	addressFlags.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
	addressFlags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
	VkMemoryAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	allocateInfo.pNext = deviceAddress ? &addressFlags : NULL;
	allocateInfo.memoryTypeIndex = memoryType;

	if ( requirements.size <= VULKAN_BUFFER_POOLED_MAX_BYTES ) {
		int blockIndex = -1;
		VkDeviceSize offset = 0;
		for ( int i = 0; i < state.bufferMemoryBlocks.Num() && blockIndex < 0; ++i ) {
			sdVulkanMemoryBlock* block = state.bufferMemoryBlocks[ i ];
			if ( block != NULL && block->memoryType == memoryType &&
				block->deviceAddress == deviceAddress &&
				AllocateFromMemoryBlock( *block, requirements.size, requirements.alignment, offset ) ) {
				blockIndex = i;
			}
		}
		if ( blockIndex < 0 ) {
			allocateInfo.allocationSize = VULKAN_BUFFER_BLOCK_BYTES;
			VkDeviceMemory memory = VK_NULL_HANDLE;
			void* mapped = NULL;
			const bool hostVisible = ( state.memoryProperties.memoryTypes[ memoryType ].propertyFlags &
				VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT ) != 0;
			if ( CheckVulkanResult( state.AllocateMemory( state.device, &allocateInfo, NULL,
				&memory ), "vkAllocateMemory(buffer block)" ) ) {
				if ( hostVisible && !CheckVulkanResult( state.MapMemory( state.device, memory, 0,
					VK_WHOLE_SIZE, 0, &mapped ), "vkMapMemory(buffer block)" ) ) {
					state.FreeMemory( state.device, memory, NULL );
					memory = VK_NULL_HANDLE;
				}
			}
			if ( memory != VK_NULL_HANDLE ) {
				sdVulkanMemoryBlock* block = new sdVulkanMemoryBlock;
				block->memory = memory;
				block->memoryType = memoryType;
				block->deviceAddress = deviceAddress;
				block->mapped = mapped;
				block->liveAllocations = 0;
				sdVulkanMemoryRange whole;
				whole.offset = 0;
				whole.bytes = VULKAN_BUFFER_BLOCK_BYTES;
				block->freeRanges.Append( whole );
				blockIndex = state.bufferMemoryBlocks.FindIndex( NULL );
				if ( blockIndex < 0 ) {
					blockIndex = state.bufferMemoryBlocks.Append( block );
				} else {
					state.bufferMemoryBlocks[ blockIndex ] = block;
				}
				AllocateFromMemoryBlock( *block, requirements.size, requirements.alignment, offset );
			}
		}
		if ( blockIndex >= 0 ) {
			sdVulkanMemoryBlock* block = state.bufferMemoryBlocks[ blockIndex ];
			if ( !CheckVulkanResult( state.BindBufferMemory( state.device, resource.buffer,
				block->memory, offset ), "vkBindBufferMemory(buffer block)" ) ) {
				state.DestroyBuffer( state.device, resource.buffer, NULL );
				resource.buffer = VK_NULL_HANDLE;
				ReleasePooledBufferMemory( state, blockIndex, offset, requirements.size );
				return false;
			}
			resource.poolBlock = blockIndex + 1;
			resource.poolOffset = offset;
			resource.poolBytes = requirements.size;
			if ( wantMapped ) {
				resource.mapped = static_cast< byte* >( block->mapped ) + offset;
			}
			return true;
		}
		// no block could be allocated: try a buffer-sized allocation below
	}

	allocateInfo.allocationSize = requirements.size;
	if ( !CheckVulkanResult( state.AllocateMemory( state.device, &allocateInfo, NULL,
		&resource.memory ), "vkAllocateMemory(buffer)" ) ||
		!CheckVulkanResult( state.BindBufferMemory( state.device, resource.buffer,
			resource.memory, 0 ), "vkBindBufferMemory" ) ||
		( wantMapped && !CheckVulkanResult( state.MapMemory( state.device, resource.memory, 0,
			resource.bytes, 0, &resource.mapped ), "vkMapMemory(buffer)" ) ) ) {
		resource.mapped = NULL;
		if ( resource.memory != VK_NULL_HANDLE ) {
			state.FreeMemory( state.device, resource.memory, NULL );
			resource.memory = VK_NULL_HANDLE;
		}
		state.DestroyBuffer( state.device, resource.buffer, NULL );
		resource.buffer = VK_NULL_HANDLE;
		return false;
	}
	return true;
}

bool SubmitImmediate( sdVulkanBackendState& state, VkCommandPool commandPool,
	VkCommandBuffer commandBuffer ) {
	if ( !CheckVulkanResult( state.EndCommandBuffer( commandBuffer ),
		"vkEndCommandBuffer(upload)" ) ) {
		state.DestroyCommandPool( state.device, commandPool, NULL );
		return false;
	}
	VkCommandBufferSubmitInfo commandInfo;
	memset( &commandInfo, 0, sizeof( commandInfo ) );
	commandInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
	commandInfo.commandBuffer = commandBuffer;
	VkSubmitInfo2 submitInfo;
	memset( &submitInfo, 0, sizeof( submitInfo ) );
	submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
	submitInfo.commandBufferInfoCount = 1;
	submitInfo.pCommandBufferInfos = &commandInfo;
	const bool submitted = CheckVulkanResult( state.QueueSubmit2(
		state.graphicsQueue, 1, &submitInfo, VK_NULL_HANDLE ),
		"vkQueueSubmit2(upload)" );
	const bool completed = submitted && CheckVulkanResult(
		state.QueueWaitIdle( state.graphicsQueue ), "vkQueueWaitIdle(upload)" );
	state.DestroyCommandPool( state.device, commandPool, NULL );
	return completed;
}

bool QueueFrameBufferUpload( sdVulkanBackendState& state, VkBuffer destination,
	VkDeviceSize destinationOffset, const void* data, VkDeviceSize bytes,
	bool indexBuffer ) {
	if ( !state.frameActive || destination == VK_NULL_HANDLE || data == NULL ||
		bytes == 0 ) {
		return false;
	}

	sdVulkanFrame& frame = state.frames[ state.frameIndex ];
	VkBuffer sourceBuffer = frame.bufferUploadBuffer;
	VkDeviceSize sourceOffset = ( frame.bufferUploadOffset + 15 ) & ~15ULL;
	if ( frame.bufferUploadMapped != NULL &&
		sourceOffset + bytes <= VULKAN_BUFFER_UPLOAD_BYTES ) {
		memcpy( static_cast< byte* >( frame.bufferUploadMapped ) + sourceOffset,
			data, static_cast< size_t >( bytes ) );
		frame.bufferUploadOffset = sourceOffset + bytes;
	} else {
		// Very large first-use bursts must remain asynchronous too.  Spill to a
		// one-shot staging allocation and retire it only after this frame's fence.
		if ( !frame.bufferUploadOverflowWarned ) {
			common->Warning( "Vulkan buffer upload arena exhausted (%u MiB); using fence-retired spill buffers",
				static_cast< unsigned int >(
					VULKAN_BUFFER_UPLOAD_BYTES / ( 1024 * 1024 ) ) );
			frame.bufferUploadOverflowWarned = true;
		}
		sdVulkanBufferResource spill;
		memset( &spill, 0, sizeof( spill ) );
		spill.bytes = bytes;
		spill.indexBuffer = indexBuffer;
		if ( !CreateBufferAllocation( state, bytes,
			VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
				VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			spill.buffer, spill.memory ) ||
			!CheckVulkanResult( state.MapMemory( state.device, spill.memory, 0,
				bytes, 0, &spill.mapped ), "vkMapMemory(buffer upload spill)" ) ) {
			DestroyBufferResource( state, spill );
			return false;
		}
		memcpy( spill.mapped, data, static_cast< size_t >( bytes ) );
		state.UnmapMemory( state.device, spill.memory );
		spill.mapped = NULL;
		sourceBuffer = spill.buffer;
		sourceOffset = 0;
		state.frameRetiredBufferResources[ state.frameIndex ].Append( spill );
	}

	VkBufferCopy copy;
	copy.srcOffset = sourceOffset;
	copy.dstOffset = destinationOffset;
	copy.size = bytes;
	state.CmdCopyBuffer( frame.imageUploadCommandBuffer, sourceBuffer,
		destination, 1, &copy );
	VkBufferMemoryBarrier2 barrier;
	memset( &barrier, 0, sizeof( barrier ) );
	barrier.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
	barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	// These cache buffers may also be consumed as acceleration-structure build
	// input.  Cover every later read in the main frame command buffer, not just
	// fixed-function vertex/index fetch.
	barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
	// and a later upload's copy into the same range lands after this one
	barrier.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT | VK_ACCESS_2_TRANSFER_WRITE_BIT;
	barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.buffer = destination;
	barrier.offset = destinationOffset;
	barrier.size = bytes;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.bufferMemoryBarrierCount = 1;
	dependencyInfo.pBufferMemoryBarriers = &barrier;
	state.CmdPipelineBarrier2( frame.imageUploadCommandBuffer, &dependencyInfo );
	frame.imageUploadRecorded = true;
	return true;
}

// Copies the uploads into their buffers now: one staging buffer, one submission
// and one wait for the whole batch.
bool SubmitBufferUploadsNow( sdVulkanBackendState& state,
	const sdVulkanPendingBufferUpload* uploads, int count ) {
	if ( count <= 0 ) {
		return true;
	}
	VkDeviceSize totalBytes = 0;
	for ( int i = 0; i < count; ++i ) {
		totalBytes = ( ( totalBytes + 15 ) & ~15ULL ) + uploads[ i ].bytes;
	}
	VkBuffer stagingBuffer = VK_NULL_HANDLE;
	VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
	if ( !CreateBufferAllocation( state, totalBytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		stagingBuffer, stagingMemory ) ) {
		return false;
	}
	void* mapped = NULL;
	if ( !CheckVulkanResult( state.MapMemory( state.device, stagingMemory, 0,
		totalBytes, 0, &mapped ), "vkMapMemory(buffer upload)" ) ) {
		state.DestroyBuffer( state.device, stagingBuffer, NULL );
		state.FreeMemory( state.device, stagingMemory, NULL );
		return false;
	}
	idList< VkDeviceSize > sourceOffsets;
	sourceOffsets.SetNum( count );
	VkDeviceSize offset = 0;
	for ( int i = 0; i < count; ++i ) {
		offset = ( offset + 15 ) & ~15ULL;
		sourceOffsets[ i ] = offset;
		memcpy( static_cast< byte* >( mapped ) + offset, uploads[ i ].data,
			static_cast< size_t >( uploads[ i ].bytes ) );
		offset += uploads[ i ].bytes;
	}
	state.UnmapMemory( state.device, stagingMemory );

	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
	poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
	poolInfo.queueFamilyIndex = state.queueFamilyIndex;
	bool succeeded = CheckVulkanResult( state.CreateCommandPool( state.device,
		&poolInfo, NULL, &commandPool ), "vkCreateCommandPool(buffer upload)" );
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	if ( succeeded ) {
		VkCommandBufferAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		allocateInfo.commandPool = commandPool;
		allocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		allocateInfo.commandBufferCount = 1;
		succeeded = CheckVulkanResult( state.AllocateCommandBuffers( state.device,
			&allocateInfo, &commandBuffer ), "vkAllocateCommandBuffers(buffer upload)" );
	}
	if ( succeeded ) {
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		succeeded = CheckVulkanResult( state.BeginCommandBuffer( commandBuffer,
			&beginInfo ), "vkBeginCommandBuffer(buffer upload)" );
	}
	if ( succeeded ) {
		// a buffer written twice in one batch (partial updates) gets its writes in order
		idList< VkBuffer > written;
		for ( int i = 0; i < count; ++i ) {
			if ( written.FindIndex( uploads[ i ].destination ) >= 0 ) {
				VkMemoryBarrier2 writeOrder;
				memset( &writeOrder, 0, sizeof( writeOrder ) );
				writeOrder.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
				writeOrder.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
				writeOrder.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
				writeOrder.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
				writeOrder.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
				VkDependencyInfo writeDependency;
				memset( &writeDependency, 0, sizeof( writeDependency ) );
				writeDependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
				writeDependency.memoryBarrierCount = 1;
				writeDependency.pMemoryBarriers = &writeOrder;
				state.CmdPipelineBarrier2( commandBuffer, &writeDependency );
				written.Clear();
			}
			written.Append( uploads[ i ].destination );
			VkBufferCopy copy;
			copy.srcOffset = sourceOffsets[ i ];
			copy.dstOffset = uploads[ i ].offset;
			copy.size = uploads[ i ].bytes;
			state.CmdCopyBuffer( commandBuffer, stagingBuffer, uploads[ i ].destination, 1, &copy );
		}
		// the copies must land before any later read: vertex/index fetch, or
		// acceleration-structure build input
		VkMemoryBarrier2 barrier;
		memset( &barrier, 0, sizeof( barrier ) );
		barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
		barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
		barrier.dstAccessMask = VK_ACCESS_2_MEMORY_READ_BIT;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.memoryBarrierCount = 1;
		dependencyInfo.pMemoryBarriers = &barrier;
		state.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		succeeded = SubmitImmediate( state, commandPool, commandBuffer );
		commandPool = VK_NULL_HANDLE;
	}
	if ( commandPool != VK_NULL_HANDLE ) {
		state.DestroyCommandPool( state.device, commandPool, NULL );
	}
	state.DestroyBuffer( state.device, stagingBuffer, NULL );
	state.FreeMemory( state.device, stagingMemory, NULL );
	return succeeded;
}

void ClearPendingBufferUploads( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.pendingBufferUploads.Num(); ++i ) {
		Mem_Free( state.pendingBufferUploads[ i ].data );
	}
	state.pendingBufferUploads.Clear();
	state.pendingBufferUploadBytes = 0;
}

void WarnFailedBufferUploads( int count, VkDeviceSize bytes ) {
	common->Warning( "Vulkan: %d deferred vertex-cache upload(s) (%u KiB) failed; "
		"those buffers keep undefined contents", count,
		static_cast< unsigned int >( bytes / 1024 ) );
}

// Copies every pending upload now, in one batch.
bool FlushPendingBufferUploadsNow( sdVulkanBackendState& state ) {
	const bool succeeded = SubmitBufferUploadsNow( state,
		state.pendingBufferUploads.Begin(), state.pendingBufferUploads.Num() );
	if ( !succeeded ) {
		WarnFailedBufferUploads( state.pendingBufferUploads.Num(), state.pendingBufferUploadBytes );
	}
	ClearPendingBufferUploads( state );
	return succeeded;
}

// A buffer that is being destroyed takes its pending uploads with it.
void DropPendingBufferUploads( sdVulkanBackendState& state, VkBuffer buffer ) {
	for ( int i = state.pendingBufferUploads.Num() - 1; i >= 0; --i ) {
		if ( state.pendingBufferUploads[ i ].destination == buffer ) {
			state.pendingBufferUploadBytes -= state.pendingBufferUploads[ i ].bytes;
			Mem_Free( state.pendingBufferUploads[ i ].data );
			state.pendingBufferUploads.RemoveIndex( i );
		}
	}
}

// At the start of a frame, before anything is drawn: the pending uploads go into
// this frame's upload command buffer, which is submitted ahead of the frame's own,
// so nothing waits for them.  What does not fit the frame's arena is copied at
// once instead.
void FlushPendingBufferUploadsIntoFrame( sdVulkanBackendState& state ) {
	if ( state.pendingBufferUploads.Num() == 0 ) {
		return;
	}
	const sdVulkanFrame& frame = state.frames[ state.frameIndex ];
	VkDeviceSize needed = frame.bufferUploadOffset;
	for ( int i = 0; i < state.pendingBufferUploads.Num(); ++i ) {
		needed = ( ( needed + 15 ) & ~15ULL ) + state.pendingBufferUploads[ i ].bytes;
	}
	if ( frame.bufferUploadMapped == NULL || needed > VULKAN_BUFFER_UPLOAD_BYTES ) {
		FlushPendingBufferUploadsNow( state );
		return;
	}
	int failed = 0;
	VkDeviceSize failedBytes = 0;
	for ( int i = 0; i < state.pendingBufferUploads.Num(); ++i ) {
		const sdVulkanPendingBufferUpload& upload = state.pendingBufferUploads[ i ];
		if ( !QueueFrameBufferUpload( state, upload.destination, upload.offset, upload.data,
			upload.bytes, upload.indexBuffer ) ) {
			++failed;
			failedBytes += upload.bytes;
		}
	}
	if ( failed > 0 ) {
		WarnFailedBufferUploads( failed, failedBytes );
	}
	ClearPendingBufferUploads( state );
}

bool UploadBufferBytes( sdVulkanBackendState& state, VkBuffer destination,
	VkDeviceSize destinationOffset, const void* data, VkDeviceSize bytes,
	bool indexBuffer ) {
	if ( state.frameActive ) {
		return QueueFrameBufferUpload( state, destination, destinationOffset,
			data, bytes, indexBuffer );
	}
	// Outside a frame (map loading, or a listen server's game frame spawning an
	// entity) every upload used to submit on its own and wait for the queue to go
	// idle: 120-200 ms for one vehicle's models on the Windows host.  Hold them for
	// the next frame instead; a burst larger than VULKAN_PENDING_BUFFER_UPLOAD_BYTES
	// is copied at once, as one batch.  The copies keep their order.
	sdVulkanPendingBufferUpload upload;
	upload.destination = destination;
	upload.offset = destinationOffset;
	upload.bytes = bytes;
	upload.indexBuffer = indexBuffer;
	upload.data = static_cast< byte* >( const_cast< void* >( data ) );
	// (a failed flush of the earlier uploads warns there; it is not this upload's result)
	if ( bytes > VULKAN_PENDING_BUFFER_UPLOAD_BYTES ) {
		FlushPendingBufferUploadsNow( state );
		return SubmitBufferUploadsNow( state, &upload, 1 );
	}
	if ( state.pendingBufferUploadBytes + bytes > VULKAN_PENDING_BUFFER_UPLOAD_BYTES ) {
		FlushPendingBufferUploadsNow( state );
	}
	upload.data = static_cast< byte* >( Mem_Alloc( static_cast< int >( bytes ) ) );
	memcpy( upload.data, data, static_cast< size_t >( bytes ) );
	state.pendingBufferUploads.Append( upload );
	state.pendingBufferUploadBytes += bytes;
	return true;
}

bool CompileVulkanShaderSource( sdVulkanBackendState& state,
	const char* sourceName, const char* sourceText,
	sdSpirvShaderStage stage, VkShaderModule& module ) {
	module = VK_NULL_HANDLE;
	void* sourceBuffer = NULL;
	int sourceLength = fileSystem != NULL && sourceName != NULL ?
		fileSystem->ReadFile( sourceName, &sourceBuffer ) : -1;
	const char* textToCompile = static_cast< const char* >( sourceBuffer );
	if ( sourceLength <= 0 || sourceBuffer == NULL ) {
		if ( sourceText != NULL && *sourceText != '\0' ) {
			textToCompile = sourceText;
			sourceLength = idStr::Length( sourceText );
		} else {
			common->Warning( "Could not load Vulkan shader '%s' through the engine filesystem",
				sourceName != NULL ? sourceName : "(embedded)" );
			return false;
		}
	}
	sdSpirvCompileResult result;
	const bool compiled = R_CompileVulkanGLSL( sourceName != NULL ? sourceName : "embedded",
		textToCompile, sourceLength, stage,
		"renderer-vulkan-v1", false, result );
	if ( sourceBuffer != NULL ) {
		fileSystem->FreeFile( sourceBuffer );
	}
	if ( !compiled || result.words.Num() == 0 ) {
		common->Warning( "Vulkan shader compilation failed for '%s':\n%s",
			sourceName != NULL ? sourceName : "(embedded)", result.diagnostics.c_str() );
		return false;
	}
	VkShaderModuleCreateInfo createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
	createInfo.codeSize = result.words.Num() * sizeof( unsigned int );
	createInfo.pCode = result.words.Begin();
	return CheckVulkanResult( state.CreateShaderModule( state.device,
		&createInfo, NULL, &module ), "vkCreateShaderModule" );
}

bool CompileVulkanShaderModule( sdVulkanBackendState& state,
	const char* sourceName, sdSpirvShaderStage stage, VkShaderModule& module ) {
	return CompileVulkanShaderSource( state, sourceName, NULL, stage, module );
}

void DestroyGuiResources( sdVulkanBackendState& state ) {
	state.materialDescriptors.Clear();
	state.materialDescriptorHash.Clear();
	for ( int frameIndex = 0; frameIndex < NUM_VULKAN_FRAMES; ++frameIndex ) {
		state.frameRetiredMaterialSets[ frameIndex ].Clear();	// freed with the pool
	}
	state.materialPoolExhaustedWarned = false;
	state.frontEndDescriptors.Clear();
	state.frontEndDescriptorHash.Clear();
	for ( int frameIndex = 0; frameIndex < NUM_VULKAN_FRAMES; ++frameIndex ) {
		state.frameRetiredFrontEndSets[ frameIndex ].Clear();	// freed with the pool
	}
	if ( state.frontEndDescriptorPool != VK_NULL_HANDLE ) {
		state.DestroyDescriptorPool( state.device, state.frontEndDescriptorPool, NULL );
		state.frontEndDescriptorPool = VK_NULL_HANDLE;
	}
	state.frontEndDescriptorSetsLive = 0;
	state.frontEndPoolExhaustedWarned = false;
	VkPipeline* frontEndPipelines[] = {
		&state.worldFrontEndPipeline,
		&state.worldFrontEndAlphaPipeline,
		&state.worldFrontEndAddPipeline
	};
	for ( unsigned int pipelineIndex = 0;
		pipelineIndex < sizeof( frontEndPipelines ) / sizeof( frontEndPipelines[ 0 ] );
		++pipelineIndex ) {
		if ( *frontEndPipelines[ pipelineIndex ] != VK_NULL_HANDLE ) {
			state.DestroyPipeline( state.device,
				*frontEndPipelines[ pipelineIndex ], NULL );
			*frontEndPipelines[ pipelineIndex ] = VK_NULL_HANDLE;
		}
	}
	if ( state.frontEndPipelineLayout != VK_NULL_HANDLE ) {
		state.DestroyPipelineLayout( state.device, state.frontEndPipelineLayout, NULL );
		state.frontEndPipelineLayout = VK_NULL_HANDLE;
	}
	if ( state.frontEndDescriptorSetLayout != VK_NULL_HANDLE ) {
		state.DestroyDescriptorSetLayout( state.device,
			state.frontEndDescriptorSetLayout, NULL );
		state.frontEndDescriptorSetLayout = VK_NULL_HANDLE;
	}
	VkPipeline* skinnedPipelines[] = {
		&state.worldSkinnedPipeline,
		&state.worldSkinnedDepthPipeline,
		&state.worldSkinnedAlphaPipeline,
		&state.worldSkinnedAddPipeline,
		&state.worldSkinnedAlphaAddPipeline,
		&state.worldSkinnedMultiplyPipeline,
		&state.worldSkinnedMegaPipeline,
		&state.worldSkinnedAtmospherePipeline,
		&state.worldSkinnedMaterialPipeline,
		&state.worldSkinnedMaterialAlphaPipeline,
		&state.worldSkinnedMaterialAddPipeline,
		&state.worldSkinnedWaterPipeline,
		&state.worldSkinnedHeatHazePipeline,
		&state.worldSkinnedSkyboxPipeline,
		&state.worldSkinnedSkyboxAlphaPipeline,
		&state.worldSkinnedSkyboxAddPipeline
	};
	for ( unsigned int pipelineIndex = 0;
		pipelineIndex < sizeof( skinnedPipelines ) / sizeof( skinnedPipelines[ 0 ] );
		++pipelineIndex ) {
		if ( *skinnedPipelines[ pipelineIndex ] != VK_NULL_HANDLE ) {
			state.DestroyPipeline( state.device,
				*skinnedPipelines[ pipelineIndex ], NULL );
			*skinnedPipelines[ pipelineIndex ] = VK_NULL_HANDLE;
		}
	}
	if ( state.toolModelWireDepthBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device,
			state.toolModelWireDepthBlendPipeline, NULL );
		state.toolModelWireDepthBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelWireDepthPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelWireDepthPipeline, NULL );
		state.toolModelWireDepthPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelWireBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelWireBlendPipeline, NULL );
		state.toolModelWireBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelWirePipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelWirePipeline, NULL );
		state.toolModelWirePipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelDepthBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelDepthBlendPipeline, NULL );
		state.toolModelDepthBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelDepthPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelDepthPipeline, NULL );
		state.toolModelDepthPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelBlendPipeline, NULL );
		state.toolModelBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolModelPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolModelPipeline, NULL );
		state.toolModelPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolDepthBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolDepthBlendPipeline, NULL );
		state.toolDepthBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolDepthPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolDepthPipeline, NULL );
		state.toolDepthPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolBlendPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolBlendPipeline, NULL );
		state.toolBlendPipeline = VK_NULL_HANDLE;
	}
	if ( state.toolPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.toolPipeline, NULL );
		state.toolPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldSkyboxAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldSkyboxAddPipeline, NULL );
		state.worldSkyboxAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldSkyboxAlphaPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldSkyboxAlphaPipeline, NULL );
		state.worldSkyboxAlphaPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldSkyboxPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldSkyboxPipeline, NULL );
		state.worldSkyboxPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldHeatHazePipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldHeatHazePipeline, NULL );
		state.worldHeatHazePipeline = VK_NULL_HANDLE;
	}
	if ( state.guiMultiplyPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.guiMultiplyPipeline, NULL );
		state.guiMultiplyPipeline = VK_NULL_HANDLE;
	}
	if ( state.guiAlphaAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.guiAlphaAddPipeline, NULL );
		state.guiAlphaAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.guiAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.guiAddPipeline, NULL );
		state.guiAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.guiOpaquePipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.guiOpaquePipeline, NULL );
		state.guiOpaquePipeline = VK_NULL_HANDLE;
	}
	DestroyGlowBuffers( state );
	if ( state.glowBufferPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.glowBufferPipeline, NULL );
		state.glowBufferPipeline = VK_NULL_HANDLE;
	}
	if ( state.glowScreenPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.glowScreenPipeline, NULL );
		state.glowScreenPipeline = VK_NULL_HANDLE;
	}
	if ( state.skyPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.skyPipeline, NULL );
		state.skyPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAtmospherePipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAtmospherePipeline, NULL );
		state.worldAtmospherePipeline = VK_NULL_HANDLE;
	}
	if ( state.worldMaterialAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldMaterialAddPipeline, NULL );
		state.worldMaterialAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldMaterialAlphaPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldMaterialAlphaPipeline, NULL );
		state.worldMaterialAlphaPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldMaterialPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldMaterialPipeline, NULL );
		state.worldMaterialPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldWaterPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldWaterPipeline, NULL );
		state.worldWaterPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldMultiplyPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldMultiplyPipeline, NULL );
		state.worldMultiplyPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAlphaAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAlphaAddPipeline, NULL );
		state.worldAlphaAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAddPipeline, NULL );
		state.worldAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAlphaPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAlphaPipeline, NULL );
		state.worldAlphaPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldMegaPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldMegaPipeline, NULL );
		state.worldMegaPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldDepthPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldDepthPipeline, NULL );
		state.worldDepthPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldPipeline, NULL );
		state.worldPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldShadowPassPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldShadowPassPipeline, NULL );
		state.worldShadowPassPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldShadowFailPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldShadowFailPipeline, NULL );
		state.worldShadowFailPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldInteractionPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldInteractionPipeline, NULL );
		state.worldInteractionPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldInteractionUnshadowedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldInteractionUnshadowedPipeline, NULL );
		state.worldInteractionUnshadowedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldInteractionSkinnedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldInteractionSkinnedPipeline, NULL );
		state.worldInteractionSkinnedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldInteractionSkinnedUnshadowedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldInteractionSkinnedUnshadowedPipeline, NULL );
		state.worldInteractionSkinnedUnshadowedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldFogPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldFogPipeline, NULL );
		state.worldFogPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldFogSkinnedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldFogSkinnedPipeline, NULL );
		state.worldFogSkinnedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientCubePipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientCubePipeline, NULL );
		state.worldAmbientCubePipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientCubeSkinnedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientCubeSkinnedPipeline, NULL );
		state.worldAmbientCubeSkinnedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientCubeAddPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientCubeAddPipeline, NULL );
		state.worldAmbientCubeAddPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientCubeAddSkinnedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientCubeAddSkinnedPipeline, NULL );
		state.worldAmbientCubeAddSkinnedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientShadowPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientShadowPipeline, NULL );
		state.worldAmbientShadowPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldAmbientShadowSkinnedPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldAmbientShadowSkinnedPipeline, NULL );
		state.worldAmbientShadowSkinnedPipeline = VK_NULL_HANDLE;
	}
	if ( state.worldLightAmbientPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.worldLightAmbientPipeline, NULL );
		state.worldLightAmbientPipeline = VK_NULL_HANDLE;
	}
	if ( state.guiPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.guiPipeline, NULL );
		state.guiPipeline = VK_NULL_HANDLE;
	}
	if ( state.guiPipelineLayout != VK_NULL_HANDLE ) {
		state.DestroyPipelineLayout( state.device, state.guiPipelineLayout, NULL );
		state.guiPipelineLayout = VK_NULL_HANDLE;
	}
	if ( state.guiDescriptorPool != VK_NULL_HANDLE ) {
		state.DestroyDescriptorPool( state.device, state.guiDescriptorPool, NULL );
		state.guiDescriptorPool = VK_NULL_HANDLE;
	}
	if ( state.jointDescriptorSetLayout != VK_NULL_HANDLE ) {
		state.DestroyDescriptorSetLayout( state.device,
			state.jointDescriptorSetLayout, NULL );
		state.jointDescriptorSetLayout = VK_NULL_HANDLE;
	}
	if ( state.guiDescriptorSetLayout != VK_NULL_HANDLE ) {
		state.DestroyDescriptorSetLayout( state.device,
			state.guiDescriptorSetLayout, NULL );
		state.guiDescriptorSetLayout = VK_NULL_HANDLE;
	}
	state.guiPipelineFormat = VK_FORMAT_UNDEFINED;
}

bool CreateWorldPipelineVariant( sdVulkanBackendState& state,
	const char* vertexShader, const char* fragmentShader,
	VkBlendFactor sourceBlend, VkBlendFactor destinationBlend, bool depthTest,
	bool depthWrite, bool vertexless,
	VkPipeline& pipeline, VkCompareOp depthCompare = VK_COMPARE_OP_LESS_OR_EQUAL,
	bool colorWrite = true,
	VkPolygonMode polygonMode = VK_POLYGON_MODE_FILL,
	bool skinned = false, VkPipelineLayout layout = VK_NULL_HANDLE,
	VkCullModeFlags cullMode = VK_CULL_MODE_NONE, bool stencilZeroOnly = false ) {
	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderModule( state, vertexShader,
		SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderModule( state, fragmentShader,
			SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		return false;
	}
	VkPipelineShaderStageCreateInfo shaderStages[ 2 ];
	memset( shaderStages, 0, sizeof( shaderStages ) );
	shaderStages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	shaderStages[ 0 ].module = vertexModule;
	shaderStages[ 0 ].pName = "main";
	shaderStages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	shaderStages[ 1 ].module = fragmentModule;
	shaderStages[ 1 ].pName = "main";
	// The framebuffer's alpha is the postprocess glow's source weight (#233), so it
	// follows retail's OpenGL.  An opaque surface's lit replace pass writes 0 there
	// (megatexture/notlit_lit and the interaction ambients: MOV result.color.a, 0),
	// while an alpha-tested one leaves the clear's 1: constant 0 (OPAQUE_BASE) tells
	// the base pass's shader it is that replace.
	const VkBool32 opaqueBase = depthWrite && colorWrite &&
		sourceBlend == VK_BLEND_FACTOR_ONE && destinationBlend == VK_BLEND_FACTOR_ZERO;
	VkSpecializationMapEntry specializationEntry;
	specializationEntry.constantID = 0;
	specializationEntry.offset = 0;
	specializationEntry.size = sizeof( opaqueBase );
	VkSpecializationInfo specializationInfo;
	specializationInfo.mapEntryCount = 1;
	specializationInfo.pMapEntries = &specializationEntry;
	specializationInfo.dataSize = sizeof( opaqueBase );
	specializationInfo.pData = &opaqueBase;
	shaderStages[ 1 ].pSpecializationInfo = &specializationInfo;

	VkVertexInputBindingDescription vertexBindings[ 2 ];
	vertexBindings[ 0 ].binding = 0;
	vertexBindings[ 0 ].stride = sizeof( idDrawVert );
	vertexBindings[ 0 ].inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
	vertexBindings[ 1 ].binding = 1;
	vertexBindings[ 1 ].stride = sizeof( vertWeight_t );
	vertexBindings[ 1 ].inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
	VkVertexInputAttributeDescription attributes[ 10 ];
	memset( attributes, 0, sizeof( attributes ) );
	attributes[ 0 ].location = 0;
	attributes[ 0 ].binding = 0;
	attributes[ 0 ].format = VK_FORMAT_R32G32B32_SFLOAT;
	attributes[ 0 ].offset = 0;
	attributes[ 1 ].location = 1;
	attributes[ 1 ].binding = 0;
	attributes[ 1 ].format = VK_FORMAT_R8G8B8A8_UNORM;
	attributes[ 1 ].offset = 12;
	attributes[ 2 ].location = 2;
	attributes[ 2 ].binding = 0;
	attributes[ 2 ].format = VK_FORMAT_R16G16_SSCALED;
	attributes[ 2 ].offset = 28;
	attributes[ 3 ].location = 3;
	attributes[ 3 ].binding = 0;
	attributes[ 3 ].format = VK_FORMAT_R16G16_SNORM;
	attributes[ 3 ].offset = 16;
	attributes[ 4 ].location = 4;
	attributes[ 4 ].binding = 0;
	attributes[ 4 ].format = VK_FORMAT_R8_UINT;
	attributes[ 4 ].offset = 24;
	attributes[ 5 ].location = 5;
	attributes[ 5 ].binding = 0;
	attributes[ 5 ].format = VK_FORMAT_R16G16_SNORM;
	attributes[ 5 ].offset = 20;
	attributes[ 6 ].location = 6;
	attributes[ 6 ].binding = 0;
	attributes[ 6 ].format = VK_FORMAT_R8_UINT;
	attributes[ 6 ].offset = 25;
	attributes[ 7 ].location = 7;
	attributes[ 7 ].binding = 0;
	attributes[ 7 ].format = VK_FORMAT_R8_UINT;
	attributes[ 7 ].offset = 26;
	attributes[ 8 ].location = 8;
	attributes[ 8 ].binding = 1;
	attributes[ 8 ].format = VK_FORMAT_R8G8B8A8_UINT;
	attributes[ 8 ].offset = 0;
	attributes[ 9 ].location = 9;
	attributes[ 9 ].binding = 1;
	attributes[ 9 ].format = VK_FORMAT_R8G8B8A8_UNORM;
	attributes[ 9 ].offset = MAX_WEIGHTS_PER_VERT;
	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	vertexInput.vertexBindingDescriptionCount = vertexless ? 0 : ( skinned ? 2 : 1 );
	vertexInput.pVertexBindingDescriptions = vertexless ? NULL : vertexBindings;
	vertexInput.vertexAttributeDescriptionCount = vertexless ? 0 : ( skinned ? 10 : 8 );
	vertexInput.pVertexAttributeDescriptions = vertexless ? NULL : attributes;
	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;
	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = polygonMode;
	rasterization.cullMode = cullMode;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.depthBiasEnable = VK_TRUE;
	rasterization.lineWidth = 1.0f;
	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
	VkPipelineDepthStencilStateCreateInfo depthStencil;
	memset( &depthStencil, 0, sizeof( depthStencil ) );
	depthStencil.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencil.depthTestEnable = depthTest ? VK_TRUE : VK_FALSE;
	depthStencil.depthWriteEnable = depthWrite ? VK_TRUE : VK_FALSE;
	depthStencil.depthCompareOp = depthCompare;
	if ( stencilZeroOnly ) {
		// draw only where the stencil is 0, leaving it as it is
		depthStencil.stencilTestEnable = VK_TRUE;
		depthStencil.front.compareOp = VK_COMPARE_OP_EQUAL;
		depthStencil.front.compareMask = 0xFF;
		depthStencil.front.failOp = VK_STENCIL_OP_KEEP;
		depthStencil.front.passOp = VK_STENCIL_OP_KEEP;
		depthStencil.front.depthFailOp = VK_STENCIL_OP_KEEP;
		depthStencil.back = depthStencil.front;
	}
	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.colorWriteMask = colorWrite ?
		( VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT |
		  VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT ) : 0;
	if ( sourceBlend != VK_BLEND_FACTOR_ONE ||
		destinationBlend != VK_BLEND_FACTOR_ZERO ) {
		blendAttachment.blendEnable = VK_TRUE;
		blendAttachment.srcColorBlendFactor = sourceBlend;
		blendAttachment.dstColorBlendFactor = destinationBlend;
		blendAttachment.colorBlendOp = VK_BLEND_OP_ADD;
		// glBlendFunc's factors apply to alpha too.  A blended pass that writes
		// depth is the MegaTexture's levels, whose alpha is the level weight; retail
		// draws the terrain in one notlit_lit replace that writes alpha 0.
		const bool levelBlend = depthWrite;
		blendAttachment.srcAlphaBlendFactor = levelBlend ? VK_BLEND_FACTOR_ZERO : sourceBlend;
		blendAttachment.dstAlphaBlendFactor = levelBlend ? VK_BLEND_FACTOR_ZERO : destinationBlend;
		blendAttachment.alphaBlendOp = VK_BLEND_OP_ADD;
	}
	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;
	const VkDynamicState dynamicStates[] = { VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR, VK_DYNAMIC_STATE_DEPTH_BIAS };
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 3;
	dynamicState.pDynamicStates = dynamicStates;
	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;
	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = shaderStages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pDepthStencilState = &depthStencil;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = layout != VK_NULL_HANDLE ? layout : state.guiPipelineLayout;
	const bool created = CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&pipeline ), "vkCreateGraphicsPipelines(world)" );
	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
	return created;
}

void CreateFrontEndPipelines( sdVulkanBackendState& state ) {
	if ( state.frontEndPipelineLayout == VK_NULL_HANDLE ) {
		return;
	}
	// sfx/feAmbDecal (opaque), sfx/feAmbDecal_cloud (blend) and sfx/atmos (add;
	// its material is blend GL_ONE, GL_ONE without depth writes).  Optional:
	// without them these surfaces fall back to the generic pipelines.  Culled as
	// GL_Cull culls a front-sided material, so the far side of the cloud shell
	// does not draw over the globe.  The atmosphere is culled as GL_Cull culls a
	// back-sided stage: retail's atmosphere stage says "cullFace front", which
	// ParseStage reads as CT_BACK_SIDED, so only the shell's far side draws, a soft
	// glow outside the globe's limb (#186).  The draw uses a pipeline only when the
	// stage's cull matches the one it was built with.
	const VkCullModeFlags frontSidedCull = VK_CULL_MODE_FRONT_BIT;
	const VkCullModeFlags backSidedCull = VK_CULL_MODE_BACK_BIT;
	if ( !CreateWorldPipelineVariant( state, "vkprogs/world/frontend.vert",
			"vkprogs/world/frontend.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, true, false, state.worldFrontEndPipeline,
			VK_COMPARE_OP_LESS_OR_EQUAL, true, VK_POLYGON_MODE_FILL, false,
			state.frontEndPipelineLayout, frontSidedCull ) ||
		!CreateWorldPipelineVariant( state, "vkprogs/world/frontend.vert",
			"vkprogs/world/frontend.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, false, false,
			state.worldFrontEndAlphaPipeline, VK_COMPARE_OP_LESS_OR_EQUAL, true,
			VK_POLYGON_MODE_FILL, false, state.frontEndPipelineLayout, frontSidedCull ) ||
		!CreateWorldPipelineVariant( state, "vkprogs/world/frontend.vert",
			"vkprogs/world/frontend.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ONE, true, false, false,
			state.worldFrontEndAddPipeline, VK_COMPARE_OP_LESS_OR_EQUAL, true,
			VK_POLYGON_MODE_FILL, false, state.frontEndPipelineLayout, backSidedCull ) ) {
		common->Warning( "Vulkan: the front-end pipelines (globe, clouds, atmosphere) failed; the menu globe uses the generic material path" );
	}
}

// The MegaTexture base pass.  With the front-end layout it binds the level, the
// detail texture and its mask (mega_detail.frag, retail's detail_fragment.inc);
// without it, the level alone (mega.frag), as before.
bool CreateMegaPipeline( sdVulkanBackendState& state ) {
	state.megaDetailPipeline = false;
	if ( state.frontEndPipelineLayout != VK_NULL_HANDLE &&
		CreateWorldPipelineVariant( state, "vkprogs/world/mega.vert",
			"vkprogs/world/mega_detail.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, true, false,
			state.worldMegaPipeline, VK_COMPARE_OP_LESS_OR_EQUAL, true,
			VK_POLYGON_MODE_FILL, false, state.frontEndPipelineLayout ) ) {
		state.megaDetailPipeline = true;
		return true;
	}
	return CreateWorldPipelineVariant( state, "vkprogs/world/mega.vert",
		"vkprogs/world/mega.frag", VK_BLEND_FACTOR_SRC_ALPHA,
		VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, true, false,
		state.worldMegaPipeline );
}

bool CreateWorldPipeline( sdVulkanBackendState& state ) {
	CreateFrontEndPipelines( state );
	return CreateWorldPipelineVariant( state, "vkprogs/world/ambient.vert",
		"vkprogs/world/ambient.frag", VK_BLEND_FACTOR_ONE,
		VK_BLEND_FACTOR_ZERO, true, true, false, state.worldDepthPipeline,
		VK_COMPARE_OP_LESS_OR_EQUAL, false ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/ambient.vert",
			"vkprogs/world/ambient.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, true, false, state.worldPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, false, false,
			state.worldAlphaPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ONE, true, false, false, state.worldAddPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE, true, false, false, state.worldAlphaAddPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/decal_multiply.frag", VK_BLEND_FACTOR_DST_COLOR,
			VK_BLEND_FACTOR_ZERO, true, false, false, state.worldMultiplyPipeline ) &&
		CreateMegaPipeline( state ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/atmosphere.vert",
			"vkprogs/world/atmosphere.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ONE, true, false, false,
			state.worldAtmospherePipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/material.vert",
			"vkprogs/world/material.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, true, false,
			state.worldMaterialPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/material.vert",
			"vkprogs/world/material.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, false, false,
			state.worldMaterialAlphaPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/material.vert",
			"vkprogs/world/material.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ONE, true, false, false,
			state.worldMaterialAddPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/water.vert",
			"vkprogs/world/water.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, true, false,
			state.worldWaterPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/heat_haze.vert",
			"vkprogs/world/heat_haze.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, false, false,
			state.worldHeatHazePipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/skybox.vert",
			"vkprogs/world/skybox.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, false, false,
			state.worldSkyboxPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/skybox.vert",
			"vkprogs/world/skybox.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, false, false,
			state.worldSkyboxAlphaPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/skybox.vert",
			"vkprogs/world/skybox.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ONE, true, false, false,
			state.worldSkyboxAddPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/sky.vert",
			"vkprogs/world/sky.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, false, false, true, state.skyPipeline ) &&
		// rasterized lighting: dst *= ambient wherever geometry is nearer than the sky
		// and the ambient cube pass has not scaled the pixel (stencil 0)
		CreateWorldPipelineVariant( state, "vkprogs/world/sky.vert",
			"vkprogs/world/light_ambient.frag", VK_BLEND_FACTOR_ZERO,
			VK_BLEND_FACTOR_SRC_COLOR, true, false, true,
			state.worldLightAmbientPipeline, VK_COMPARE_OP_GREATER, true,
			VK_POLYGON_MODE_FILL, false, VK_NULL_HANDLE, VK_CULL_MODE_NONE, true );
}

bool CreateSkinnedWorldPipelineVariant( sdVulkanBackendState& state,
	const char* vertexShader, const char* fragmentShader,
	VkBlendFactor sourceBlend, VkBlendFactor destinationBlend, bool depthTest,
	bool depthWrite, VkPipeline& pipeline,
	VkCompareOp depthCompare = VK_COMPARE_OP_LESS_OR_EQUAL,
	bool colorWrite = true ) {
	return CreateWorldPipelineVariant( state, vertexShader, fragmentShader,
		sourceBlend, destinationBlend, depthTest, depthWrite, false, pipeline,
		depthCompare, colorWrite, VK_POLYGON_MODE_FILL, true );
}

bool CreateSkinnedWorldPipelines( sdVulkanBackendState& state ) {
	return CreateSkinnedWorldPipelineVariant( state,
		"vkprogs/world/skinned_ambient.vert", "vkprogs/world/ambient.frag",
		VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, true,
		state.worldSkinnedDepthPipeline, VK_COMPARE_OP_LESS_OR_EQUAL, false ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_ambient.vert", "vkprogs/world/ambient.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, true,
			state.worldSkinnedPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_trivial.vert", "vkprogs/world/trivial.frag",
			VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
			true, false, state.worldSkinnedAlphaPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_trivial.vert", "vkprogs/world/trivial.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ONE, true, false,
			state.worldSkinnedAddPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_trivial.vert", "vkprogs/world/trivial.frag",
			VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE, true, false,
			state.worldSkinnedAlphaAddPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_trivial.vert", "vkprogs/world/decal_multiply.frag",
			VK_BLEND_FACTOR_DST_COLOR, VK_BLEND_FACTOR_ZERO, true, false,
			state.worldSkinnedMultiplyPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_mega.vert", "vkprogs/world/mega.frag",
			VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
			true, true, state.worldSkinnedMegaPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_atmosphere.vert", "vkprogs/world/atmosphere.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ONE, true, false,
			state.worldSkinnedAtmospherePipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_material.vert", "vkprogs/world/material.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, true,
			state.worldSkinnedMaterialPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_material.vert", "vkprogs/world/material.frag",
			VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
			true, false, state.worldSkinnedMaterialAlphaPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_material.vert", "vkprogs/world/material.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ONE, true, false,
			state.worldSkinnedMaterialAddPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_water.vert", "vkprogs/world/water.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, true,
			state.worldSkinnedWaterPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_heat_haze.vert", "vkprogs/world/heat_haze.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, false,
			state.worldSkinnedHeatHazePipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_skybox.vert", "vkprogs/world/skybox.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ZERO, true, false,
			state.worldSkinnedSkyboxPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_skybox.vert", "vkprogs/world/skybox.frag",
			VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA,
			true, false, state.worldSkinnedSkyboxAlphaPipeline ) &&
		CreateSkinnedWorldPipelineVariant( state,
			"vkprogs/world/skinned_skybox.vert", "vkprogs/world/skybox.frag",
			VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ONE, true, false,
			state.worldSkinnedSkyboxAddPipeline );
}

bool CreateShadowPipelines( sdVulkanBackendState& state ) {
	const char* shadowVertGlsl =
		"#version 450\n"
		"layout( location = 0 ) in vec4 inPosition;\n"
		"layout( push_constant ) uniform ShadowPushConstants {\n"
		"    mat4 modelViewProjection;\n"
		"    vec4 localLightOrigin;\n"
		"} pc;\n"
		// shadowCache_t holds each vertex twice: w = 1 is the vertex, w = 0 the
		// copy projected to infinity away from the light (id Tech 4's shadow.vp)
		"void main() {\n"
		"    vec4 position = inPosition.w > 0.5 ? vec4( inPosition.xyz, 1.0 ) :\n"
		"        vec4( inPosition.xyz - pc.localLightOrigin.xyz, 0.0 );\n"
		"    gl_Position = pc.modelViewProjection * position;\n"
		"}\n";

	const char* shadowFragGlsl =
		"#version 450\n"
		"void main() {\n"
		"}\n";

	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderSource( state, "vkprogs/world/shadow.vert",
		shadowVertGlsl, SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderSource( state, "vkprogs/world/shadow.frag",
			shadowFragGlsl, SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		return false;
	}

	VkPipelineShaderStageCreateInfo shaderStages[ 2 ];
	memset( shaderStages, 0, sizeof( shaderStages ) );
	shaderStages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	shaderStages[ 0 ].module = vertexModule;
	shaderStages[ 0 ].pName = "main";
	shaderStages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	shaderStages[ 1 ].module = fragmentModule;
	shaderStages[ 1 ].pName = "main";

	VkVertexInputBindingDescription vertexBinding;
	memset( &vertexBinding, 0, sizeof( vertexBinding ) );
	vertexBinding.binding = 0;
	vertexBinding.stride = sizeof( shadowCache_t );
	vertexBinding.inputRate = VK_VERTEX_INPUT_RATE_VERTEX;

	VkVertexInputAttributeDescription attribute;
	memset( &attribute, 0, sizeof( attribute ) );
	attribute.location = 0;
	attribute.binding = 0;
	attribute.format = VK_FORMAT_R32G32B32A32_SFLOAT;
	attribute.offset = 0;

	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	vertexInput.vertexBindingDescriptionCount = 1;
	vertexInput.pVertexBindingDescriptions = &vertexBinding;
	vertexInput.vertexAttributeDescriptionCount = 1;
	vertexInput.pVertexAttributeDescriptions = &attribute;

	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;

	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;

	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = VK_POLYGON_MODE_FILL;
	rasterization.cullMode = VK_CULL_MODE_NONE;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.depthBiasEnable = VK_TRUE;
	rasterization.lineWidth = 1.0f;

	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;

	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.colorWriteMask = 0;
	blendAttachment.blendEnable = VK_FALSE;

	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;

	const VkDynamicState dynamicStates[] = {
		VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR,
		VK_DYNAMIC_STATE_DEPTH_BIAS
	};
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 3;
	dynamicState.pDynamicStates = dynamicStates;

	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;

	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;

	// Depth-Pass Pipeline (front INCR_AND_WRAP, back DECR_AND_WRAP)
	VkPipelineDepthStencilStateCreateInfo depthStencilPass;
	memset( &depthStencilPass, 0, sizeof( depthStencilPass ) );
	depthStencilPass.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencilPass.depthTestEnable = VK_TRUE;
	depthStencilPass.depthWriteEnable = VK_FALSE;
	// LESS, as id Tech 4's stencil shadow pass: the volume's caps and sides lie
	// on the casting surface, and LEQUAL counts them there, shadowing lit surfaces
	depthStencilPass.depthCompareOp = VK_COMPARE_OP_LESS;
	depthStencilPass.stencilTestEnable = VK_TRUE;

	depthStencilPass.front.failOp = VK_STENCIL_OP_KEEP;
	depthStencilPass.front.passOp = VK_STENCIL_OP_INCREMENT_AND_WRAP;
	depthStencilPass.front.depthFailOp = VK_STENCIL_OP_KEEP;
	depthStencilPass.front.compareOp = VK_COMPARE_OP_ALWAYS;
	depthStencilPass.front.compareMask = 0xFF;
	depthStencilPass.front.writeMask = 0xFF;
	depthStencilPass.front.reference = 0;

	depthStencilPass.back.failOp = VK_STENCIL_OP_KEEP;
	depthStencilPass.back.passOp = VK_STENCIL_OP_DECREMENT_AND_WRAP;
	depthStencilPass.back.depthFailOp = VK_STENCIL_OP_KEEP;
	depthStencilPass.back.compareOp = VK_COMPARE_OP_ALWAYS;
	depthStencilPass.back.compareMask = 0xFF;
	depthStencilPass.back.writeMask = 0xFF;
	depthStencilPass.back.reference = 0;

	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = shaderStages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pDepthStencilState = &depthStencilPass;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = state.guiPipelineLayout;

	bool created = CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&state.worldShadowPassPipeline ), "vkCreateGraphicsPipelines(worldShadowPass)" );

	// Depth-Fail Pipeline (Carmack's Reverse: front DECR_AND_WRAP, back INCR_AND_WRAP)
	VkPipelineDepthStencilStateCreateInfo depthStencilFail;
	memset( &depthStencilFail, 0, sizeof( depthStencilFail ) );
	depthStencilFail.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencilFail.depthTestEnable = VK_TRUE;
	depthStencilFail.depthWriteEnable = VK_FALSE;
	// LESS, as id Tech 4's stencil shadow pass: the volume's caps and sides lie
	// on the casting surface, and LEQUAL counts them there, shadowing lit surfaces
	depthStencilFail.depthCompareOp = VK_COMPARE_OP_LESS;
	depthStencilFail.stencilTestEnable = VK_TRUE;

	depthStencilFail.front.failOp = VK_STENCIL_OP_KEEP;
	depthStencilFail.front.passOp = VK_STENCIL_OP_KEEP;
	depthStencilFail.front.depthFailOp = VK_STENCIL_OP_DECREMENT_AND_WRAP;
	depthStencilFail.front.compareOp = VK_COMPARE_OP_ALWAYS;
	depthStencilFail.front.compareMask = 0xFF;
	depthStencilFail.front.writeMask = 0xFF;
	depthStencilFail.front.reference = 0;

	depthStencilFail.back.failOp = VK_STENCIL_OP_KEEP;
	depthStencilFail.back.passOp = VK_STENCIL_OP_KEEP;
	depthStencilFail.back.depthFailOp = VK_STENCIL_OP_INCREMENT_AND_WRAP;
	depthStencilFail.back.compareOp = VK_COMPARE_OP_ALWAYS;
	depthStencilFail.back.compareMask = 0xFF;
	depthStencilFail.back.writeMask = 0xFF;
	depthStencilFail.back.reference = 0;

	pipelineInfo.pDepthStencilState = &depthStencilFail;

	created = created && CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&state.worldShadowFailPipeline ), "vkCreateGraphicsPipelines(worldShadowFail)" );

	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
	if ( created ) {
		common->Printf( "Vulkan shadow pipelines created (worldShadowPassPipeline, worldShadowFailPipeline)\n" );
	}
	return created;
}

bool CreateInteractionPipeline( sdVulkanBackendState& state ) {
	// The lit interaction needs the 160-byte light block; on a device whose
	// push-constant limit is the minimum 128 bytes the old albedo pass remains.
	const bool litInteraction = state.pushConstantFloats >= 40;
	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderModule( state, litInteraction ?
		"vkprogs/world/interaction.vert" : "vkprogs/world/material.vert",
		SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderModule( state, litInteraction ?
			"vkprogs/world/interaction.frag" : "vkprogs/world/material.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		return false;
	}

	VkPipelineShaderStageCreateInfo shaderStages[ 2 ];
	memset( shaderStages, 0, sizeof( shaderStages ) );
	shaderStages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	shaderStages[ 0 ].module = vertexModule;
	shaderStages[ 0 ].pName = "main";
	shaderStages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	shaderStages[ 1 ].module = fragmentModule;
	shaderStages[ 1 ].pName = "main";

	VkVertexInputBindingDescription vertexBinding;
	memset( &vertexBinding, 0, sizeof( vertexBinding ) );
	vertexBinding.binding = 0;
	vertexBinding.stride = sizeof( idDrawVert );
	vertexBinding.inputRate = VK_VERTEX_INPUT_RATE_VERTEX;

	VkVertexInputAttributeDescription attributes[ 8 ];
	memset( attributes, 0, sizeof( attributes ) );
	attributes[ 0 ].location = 0;
	attributes[ 0 ].binding = 0;
	attributes[ 0 ].format = VK_FORMAT_R32G32B32_SFLOAT;
	attributes[ 0 ].offset = 0;
	attributes[ 1 ].location = 1;
	attributes[ 1 ].binding = 0;
	attributes[ 1 ].format = VK_FORMAT_R8G8B8A8_UNORM;
	attributes[ 1 ].offset = 12;
	attributes[ 2 ].location = 2;
	attributes[ 2 ].binding = 0;
	attributes[ 2 ].format = VK_FORMAT_R16G16_SSCALED;
	attributes[ 2 ].offset = 28;
	attributes[ 3 ].location = 3;
	attributes[ 3 ].binding = 0;
	attributes[ 3 ].format = VK_FORMAT_R16G16_SNORM;
	attributes[ 3 ].offset = 16;
	attributes[ 4 ].location = 4;
	attributes[ 4 ].binding = 0;
	attributes[ 4 ].format = VK_FORMAT_R8_UINT;
	attributes[ 4 ].offset = 24;
	attributes[ 5 ].location = 5;
	attributes[ 5 ].binding = 0;
	attributes[ 5 ].format = VK_FORMAT_R16G16_SNORM;
	attributes[ 5 ].offset = 20;
	attributes[ 6 ].location = 6;
	attributes[ 6 ].binding = 0;
	attributes[ 6 ].format = VK_FORMAT_R8_UINT;
	attributes[ 6 ].offset = 25;
	attributes[ 7 ].location = 7;
	attributes[ 7 ].binding = 0;
	attributes[ 7 ].format = VK_FORMAT_R8_UINT;
	attributes[ 7 ].offset = 26;

	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	vertexInput.vertexBindingDescriptionCount = 1;
	vertexInput.pVertexBindingDescriptions = &vertexBinding;
	vertexInput.vertexAttributeDescriptionCount = 8;
	vertexInput.pVertexAttributeDescriptions = attributes;

	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;

	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;

	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = VK_POLYGON_MODE_FILL;
	rasterization.cullMode = VK_CULL_MODE_NONE;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.depthBiasEnable = VK_TRUE;
	rasterization.lineWidth = 1.0f;

	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;

	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.blendEnable = VK_TRUE;
	blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_ONE;
	blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE;
	blendAttachment.colorBlendOp = VK_BLEND_OP_ADD;
	// light adds to colour only; destination alpha stays as the base pass wrote it
	blendAttachment.srcAlphaBlendFactor = VK_BLEND_FACTOR_ZERO;
	blendAttachment.dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE;
	blendAttachment.alphaBlendOp = VK_BLEND_OP_ADD;
	blendAttachment.colorWriteMask = VK_COLOR_COMPONENT_R_BIT | VK_COLOR_COMPONENT_G_BIT |
		VK_COLOR_COMPONENT_B_BIT | VK_COLOR_COMPONENT_A_BIT;

	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;

	const VkDynamicState dynamicStates[] = {
		VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR,
		VK_DYNAMIC_STATE_DEPTH_BIAS
	};
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 3;
	dynamicState.pDynamicStates = dynamicStates;

	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;

	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;

	VkPipelineDepthStencilStateCreateInfo depthStencil;
	memset( &depthStencil, 0, sizeof( depthStencil ) );
	depthStencil.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencil.depthTestEnable = VK_TRUE;
	depthStencil.depthWriteEnable = VK_FALSE;
	depthStencil.depthCompareOp = VK_COMPARE_OP_LESS_OR_EQUAL;
	depthStencil.stencilTestEnable = VK_TRUE;

	// lit where the light's shadow count is at most 0: the stencil is cleared to
	// 128 and the volumes count around it, so 128 >= stencil, as OpenGL's
	// glStencilFunc( GL_GEQUAL, 128, 255 ).  Testing for exactly 0 shadowed every
	// pixel whose count went below 0 (a front face without its cap), which wraps
	// to 255: valley's distant buildings lost the sun (#267).
	depthStencil.front.failOp = VK_STENCIL_OP_KEEP;
	depthStencil.front.passOp = VK_STENCIL_OP_KEEP;
	depthStencil.front.depthFailOp = VK_STENCIL_OP_KEEP;
	depthStencil.front.compareOp = VK_COMPARE_OP_GREATER_OR_EQUAL;
	depthStencil.front.compareMask = 0xFF;
	depthStencil.front.writeMask = 0x00;
	depthStencil.front.reference = 128;

	depthStencil.back = depthStencil.front;

	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = shaderStages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pDepthStencilState = &depthStencil;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = state.guiPipelineLayout;

	VkShaderModule fogModule = VK_NULL_HANDLE;
	bool fogShader = false;
	auto CreateFogPipeline = [&]( VkPipeline& fogPipeline, const char* what ) {
		if ( !fogShader ) {
			return;
		}
		const VkShaderModule interactionModule = shaderStages[ 1 ].module;
		const VkPipelineDepthStencilStateCreateInfo interactionDepthStencil = depthStencil;
		shaderStages[ 1 ].module = fogModule;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
		// atmospherelinear blends alpha as colour (glBlendFunc), which the glow reads
		blendAttachment.srcAlphaBlendFactor = VK_BLEND_FACTOR_SRC_ALPHA;
		blendAttachment.dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
		// fog a pixel once: pass on stencil 0 (cleared before the fog) and mark it
		depthStencil.stencilTestEnable = VK_TRUE;
		depthStencil.front.compareOp = VK_COMPARE_OP_EQUAL;
		depthStencil.front.reference = 0;
		depthStencil.front.compareMask = 0xFF;
		depthStencil.front.writeMask = 0xFF;
		depthStencil.front.failOp = VK_STENCIL_OP_KEEP;
		depthStencil.front.depthFailOp = VK_STENCIL_OP_KEEP;
		depthStencil.front.passOp = VK_STENCIL_OP_INCREMENT_AND_CLAMP;
		depthStencil.back = depthStencil.front;
		if ( !CheckVulkanResult( state.CreateGraphicsPipelines( state.device, VK_NULL_HANDLE, 1,
			&pipelineInfo, NULL, &fogPipeline ), what ) ) {
			fogPipeline = VK_NULL_HANDLE;
		}
		shaderStages[ 1 ].module = interactionModule;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE;
		blendAttachment.srcAlphaBlendFactor = VK_BLEND_FACTOR_ZERO;
		blendAttachment.dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE;
		depthStencil = interactionDepthStencil;
	};
	VkShaderModule ambientCubeModule = VK_NULL_HANDLE;
	bool ambientCubeShader = false;
	// called while the unshadowed (no stencil test) state is set up; multiply:
	// dst * src (the ambient rescale), else dst + src (the specular and the
	// self-illumination).  The front-end layout: its set carries the specular cube.
	auto CreateAmbientCubePipeline = [&]( VkPipeline& ambientPipeline, bool multiply, const char* what,
		bool shadowScale = false ) {
		if ( !ambientCubeShader || state.frontEndPipelineLayout == VK_NULL_HANDLE ) {
			return;
		}
		const VkShaderModule interactionModule = shaderStages[ 1 ].module;
		shaderStages[ 1 ].module = ambientCubeModule;
		const VkPipelineLayout interactionLayout = pipelineInfo.layout;
		pipelineInfo.layout = state.frontEndPipelineLayout;
		const VkPipelineDepthStencilStateCreateInfo interactionDepthStencil = depthStencil;
		if ( multiply ) {
			// 2 * src * dst, so the scale reaches twice the base; scale a pixel once
			// (stencil 0) and mark it for the flat scale to skip
			blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_DST_COLOR;
			blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_SRC_COLOR;
			depthStencil.stencilTestEnable = VK_TRUE;
			depthStencil.front.compareOp = VK_COMPARE_OP_EQUAL;
			depthStencil.front.reference = 0;
			depthStencil.front.compareMask = 0xFF;
			depthStencil.front.writeMask = 0xFF;
			depthStencil.front.failOp = VK_STENCIL_OP_KEEP;
			depthStencil.front.depthFailOp = VK_STENCIL_OP_KEEP;
			depthStencil.front.passOp = VK_STENCIL_OP_INCREMENT_AND_CLAMP;
			depthStencil.back = depthStencil.front;
		}
		if ( shadowScale ) {
			// the same blend, only where the sun's shadow volumes left a count above
			// zero: the per-light stencil counts around 128 (#267), so 128 < stencil,
			// as OpenGL's GL_LESS 128 multiply (#265)
			depthStencil.front.compareOp = VK_COMPARE_OP_LESS;
			depthStencil.front.reference = 128;
			depthStencil.front.compareMask = 0xFF;
			depthStencil.front.writeMask = 0x00;
			depthStencil.front.passOp = VK_STENCIL_OP_KEEP;
			depthStencil.back = depthStencil.front;
		}
		if ( !CheckVulkanResult( state.CreateGraphicsPipelines( state.device, VK_NULL_HANDLE, 1,
			&pipelineInfo, NULL, &ambientPipeline ), what ) ) {
			ambientPipeline = VK_NULL_HANDLE;
		}
		shaderStages[ 1 ].module = interactionModule;
		pipelineInfo.layout = interactionLayout;
		blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_ONE;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE;
		depthStencil = interactionDepthStencil;
	};
	bool created = CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&state.worldInteractionPipeline ), "vkCreateGraphicsPipelines(worldInteraction)" );
	if ( created && litInteraction ) {
		// the same interaction for a light without shadows: no stencil test
		depthStencil.stencilTestEnable = VK_FALSE;
		created = CheckVulkanResult( state.CreateGraphicsPipelines(
			state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
			&state.worldInteractionUnshadowedPipeline ),
			"vkCreateGraphicsPipelines(worldInteractionUnshadowed)" );
		// The atmosphere fog (#233): an unshadowed interaction's geometry, depth test
		// and alpha test, with fog.frag blending the lit colour toward the fog colour
		// (dst * (1 - f) + fogColor * f).  Without it the view has no fog, nothing else.
		fogShader = created && CompileVulkanShaderModule( state, "vkprogs/world/fog.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, fogModule );
		CreateFogPipeline( state.worldFogPipeline, "vkCreateGraphicsPipelines(worldFog)" );
		// The ambient cube term (#122): the same geometry and alpha test, with
		// ambient_cube.frag replacing the colour.  Without it the flat ambient stays.
		ambientCubeShader = created && CompileVulkanShaderModule( state, "vkprogs/world/ambient_cube.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, ambientCubeModule );
		CreateAmbientCubePipeline( state.worldAmbientCubePipeline, true,
			"vkCreateGraphicsPipelines(worldAmbientCube)" );
		CreateAmbientCubePipeline( state.worldAmbientCubeAddPipeline, false,
			"vkCreateGraphicsPipelines(worldAmbientCubeAdd)" );
		CreateAmbientCubePipeline( state.worldAmbientShadowPipeline, true,
			"vkCreateGraphicsPipelines(worldAmbientShadow)", true );
		// GPU-skinned variants: skinned vertex shader, plus the vertWeight_t stream
		VkShaderModule skinnedModule = VK_NULL_HANDLE;
		if ( created && CompileVulkanShaderModule( state, "vkprogs/world/skinned_interaction.vert",
			SPIRV_SHADER_STAGE_VERTEX, skinnedModule ) ) {
			shaderStages[ 0 ].module = skinnedModule;
			VkVertexInputBindingDescription skinnedBindings[ 2 ];
			memset( skinnedBindings, 0, sizeof( skinnedBindings ) );
			skinnedBindings[ 0 ] = vertexBinding;
			skinnedBindings[ 1 ].binding = 1;
			skinnedBindings[ 1 ].stride = sizeof( vertWeight_t );
			skinnedBindings[ 1 ].inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
			VkVertexInputAttributeDescription skinnedAttributes[ 10 ];
			memcpy( skinnedAttributes, attributes, sizeof( attributes ) );
			skinnedAttributes[ 8 ].location = 8;
			skinnedAttributes[ 8 ].binding = 1;
			skinnedAttributes[ 8 ].format = VK_FORMAT_R8G8B8A8_UINT;
			skinnedAttributes[ 8 ].offset = 0;
			skinnedAttributes[ 9 ].location = 9;
			skinnedAttributes[ 9 ].binding = 1;
			skinnedAttributes[ 9 ].format = VK_FORMAT_R8G8B8A8_UNORM;
			skinnedAttributes[ 9 ].offset = MAX_WEIGHTS_PER_VERT;
			vertexInput.vertexBindingDescriptionCount = 2;
			vertexInput.pVertexBindingDescriptions = skinnedBindings;
			vertexInput.vertexAttributeDescriptionCount = 10;
			vertexInput.pVertexAttributeDescriptions = skinnedAttributes;
			created = CheckVulkanResult( state.CreateGraphicsPipelines(
				state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
				&state.worldInteractionSkinnedUnshadowedPipeline ),
				"vkCreateGraphicsPipelines(worldInteractionSkinnedUnshadowed)" );
			CreateFogPipeline( state.worldFogSkinnedPipeline, "vkCreateGraphicsPipelines(worldFogSkinned)" );
			CreateAmbientCubePipeline( state.worldAmbientCubeSkinnedPipeline, true,
				"vkCreateGraphicsPipelines(worldAmbientCubeSkinned)" );
			CreateAmbientCubePipeline( state.worldAmbientCubeAddSkinnedPipeline, false,
				"vkCreateGraphicsPipelines(worldAmbientCubeAddSkinned)" );
			CreateAmbientCubePipeline( state.worldAmbientShadowSkinnedPipeline, true,
				"vkCreateGraphicsPipelines(worldAmbientShadowSkinned)", true );
			depthStencil.stencilTestEnable = VK_TRUE;
			created = created && CheckVulkanResult( state.CreateGraphicsPipelines(
				state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
				&state.worldInteractionSkinnedPipeline ),
				"vkCreateGraphicsPipelines(worldInteractionSkinned)" );
			state.DestroyShaderModule( state.device, skinnedModule, NULL );
		}
	}

	if ( fogModule != VK_NULL_HANDLE ) {
		state.DestroyShaderModule( state.device, fogModule, NULL );
	}
	if ( ambientCubeModule != VK_NULL_HANDLE ) {
		state.DestroyShaderModule( state.device, ambientCubeModule, NULL );
	}
	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
	if ( created ) {
		common->Printf( "Vulkan interaction pipeline created (worldInteractionPipeline)\n" );
	}
	return created;
}

bool CreateToolPipelineVariant( sdVulkanBackendState& state, bool depthTest,
	bool blend, VkPipeline& pipeline ) {
	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderModule( state, "vkprogs/tool/radiant.vert",
		SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderModule( state, "vkprogs/tool/radiant.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		return false;
	}
	VkPipelineShaderStageCreateInfo stages[ 2 ];
	memset( stages, 0, sizeof( stages ) );
	stages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	stages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	stages[ 0 ].module = vertexModule;
	stages[ 0 ].pName = "main";
	stages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	stages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	stages[ 1 ].module = fragmentModule;
	stages[ 1 ].pName = "main";
	VkVertexInputBindingDescription binding;
	binding.binding = 0;
	binding.stride = sizeof( sdVulkanToolVertex );
	binding.inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
	VkVertexInputAttributeDescription attributes[ 3 ];
	memset( attributes, 0, sizeof( attributes ) );
	attributes[ 0 ].location = 0;
	attributes[ 0 ].binding = 0;
	attributes[ 0 ].format = VK_FORMAT_R32G32B32A32_SFLOAT;
	attributes[ 0 ].offset = 0;
	attributes[ 1 ].location = 1;
	attributes[ 1 ].binding = 0;
	attributes[ 1 ].format = VK_FORMAT_R32G32_SFLOAT;
	attributes[ 1 ].offset = sizeof( float ) * 4;
	attributes[ 2 ].location = 2;
	attributes[ 2 ].binding = 0;
	attributes[ 2 ].format = VK_FORMAT_R32G32B32A32_SFLOAT;
	attributes[ 2 ].offset = sizeof( float ) * 6;
	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	vertexInput.vertexBindingDescriptionCount = 1;
	vertexInput.pVertexBindingDescriptions = &binding;
	vertexInput.vertexAttributeDescriptionCount = 3;
	vertexInput.pVertexAttributeDescriptions = attributes;
	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;
	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = VK_POLYGON_MODE_FILL;
	rasterization.cullMode = VK_CULL_MODE_NONE;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.lineWidth = 1.0f;
	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
	VkPipelineDepthStencilStateCreateInfo depthStencil;
	memset( &depthStencil, 0, sizeof( depthStencil ) );
	depthStencil.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencil.depthTestEnable = depthTest ? VK_TRUE : VK_FALSE;
	depthStencil.depthWriteEnable = depthTest ? VK_TRUE : VK_FALSE;
	depthStencil.depthCompareOp = VK_COMPARE_OP_LESS_OR_EQUAL;
	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.blendEnable = blend ? VK_TRUE : VK_FALSE;
	blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_SRC_ALPHA;
	blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
	blendAttachment.colorBlendOp = VK_BLEND_OP_ADD;
	blendAttachment.srcAlphaBlendFactor = VK_BLEND_FACTOR_ONE;
	blendAttachment.dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
	blendAttachment.alphaBlendOp = VK_BLEND_OP_ADD;
	blendAttachment.colorWriteMask = VK_COLOR_COMPONENT_R_BIT |
		VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT |
		VK_COLOR_COMPONENT_A_BIT;
	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;
	const VkDynamicState dynamics[ 3 ] = { VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR, VK_DYNAMIC_STATE_DEPTH_BIAS };
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 3;
	dynamicState.pDynamicStates = dynamics;
	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;
	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = stages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pDepthStencilState = &depthStencil;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = state.guiPipelineLayout;
	const bool created = CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL, &pipeline ),
		"vkCreateGraphicsPipelines(Radiant)" );
	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
	return created;
}

bool CreateToolPipelines( sdVulkanBackendState& state ) {
	return CreateToolPipelineVariant( state, false, false, state.toolPipeline ) &&
		CreateToolPipelineVariant( state, false, true, state.toolBlendPipeline ) &&
		CreateToolPipelineVariant( state, true, false, state.toolDepthPipeline ) &&
		CreateToolPipelineVariant( state, true, true, state.toolDepthBlendPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, false, false, false,
			state.toolModelPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, false, false, false,
			state.toolModelBlendPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_ONE,
			VK_BLEND_FACTOR_ZERO, true, true, false,
			state.toolModelDepthPipeline ) &&
		CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
			"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
			VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, true, false,
			state.toolModelDepthBlendPipeline ) &&
		( !state.fillModeNonSolid ||
			( CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
				"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_ONE,
				VK_BLEND_FACTOR_ZERO, false, false, false,
				state.toolModelWirePipeline, VK_COMPARE_OP_LESS_OR_EQUAL,
				true, VK_POLYGON_MODE_LINE ) &&
			CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
				"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
				VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, false, false, false,
				state.toolModelWireBlendPipeline, VK_COMPARE_OP_LESS_OR_EQUAL,
				true, VK_POLYGON_MODE_LINE ) &&
			CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
				"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_ONE,
				VK_BLEND_FACTOR_ZERO, true, true, false,
				state.toolModelWireDepthPipeline, VK_COMPARE_OP_LESS_OR_EQUAL,
				true, VK_POLYGON_MODE_LINE ) &&
			CreateWorldPipelineVariant( state, "vkprogs/world/trivial.vert",
				"vkprogs/world/trivial.frag", VK_BLEND_FACTOR_SRC_ALPHA,
				VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, true, true, false,
				state.toolModelWireDepthBlendPipeline,
				VK_COMPARE_OP_LESS_OR_EQUAL, true, VK_POLYGON_MODE_LINE ) ) );
}

// postprocess/glow's passes (glow.frag, #233): one pipeline for the quarter-size
// buffers, one for the screen.  No vertex input: glow.vert covers the target with
// one triangle.  Missing pipelines leave the glow undrawn, as before.
void CreateGlowPipelines( sdVulkanBackendState& state ) {
	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderModule( state, "vkprogs/gui/glow.vert",
		SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderModule( state, "vkprogs/gui/glow.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		common->Warning( "Vulkan: the postprocess glow shaders failed; the glow is not drawn" );
		return;
	}
	VkPipelineShaderStageCreateInfo shaderStages[ 2 ];
	memset( shaderStages, 0, sizeof( shaderStages ) );
	shaderStages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	shaderStages[ 0 ].module = vertexModule;
	shaderStages[ 0 ].pName = "main";
	shaderStages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	shaderStages[ 1 ].module = fragmentModule;
	shaderStages[ 1 ].pName = "main";
	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;
	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = VK_POLYGON_MODE_FILL;
	rasterization.cullMode = VK_CULL_MODE_NONE;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.lineWidth = 1.0f;
	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.colorWriteMask = VK_COLOR_COMPONENT_R_BIT |
		VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT |
		VK_COLOR_COMPONENT_A_BIT;
	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;
	VkPipelineDepthStencilStateCreateInfo depthStencil;
	memset( &depthStencil, 0, sizeof( depthStencil ) );
	depthStencil.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	const VkDynamicState dynamicStates[] = { VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR };
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 2;
	dynamicState.pDynamicStates = dynamicStates;
	const VkFormat bufferFormat = VK_FORMAT_R8G8B8A8_UNORM;
	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &bufferFormat;
	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = shaderStages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDepthStencilState = &depthStencil;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = state.guiPipelineLayout;
	CheckVulkanResult( state.CreateGraphicsPipelines( state.device, VK_NULL_HANDLE, 1,
		&pipelineInfo, NULL, &state.glowBufferPipeline ), "vkCreateGraphicsPipelines(glow buffer)" );
	// the screen pass runs inside the swapchain rendering, so it names the depth
	// and stencil formats too; it writes colour only, as the HUD draws over it
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;
	blendAttachment.colorWriteMask = VK_COLOR_COMPONENT_R_BIT |
		VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT;
	CheckVulkanResult( state.CreateGraphicsPipelines( state.device, VK_NULL_HANDLE, 1,
		&pipelineInfo, NULL, &state.glowScreenPipeline ), "vkCreateGraphicsPipelines(glow screen)" );
	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
}

bool CreateGuiResources( sdVulkanBackendState& state ) {
	VkDescriptorSetLayoutBinding samplerBindings[ VULKAN_MATERIAL_TEXTURES ];
	memset( samplerBindings, 0, sizeof( samplerBindings ) );
	for ( int bindingIndex = 0; bindingIndex < VULKAN_MATERIAL_TEXTURES;
		++bindingIndex ) {
		samplerBindings[ bindingIndex ].binding = bindingIndex;
		samplerBindings[ bindingIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		samplerBindings[ bindingIndex ].descriptorCount = 1;
		samplerBindings[ bindingIndex ].stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT;
	}
	VkDescriptorSetLayoutCreateInfo setLayoutInfo;
	memset( &setLayoutInfo, 0, sizeof( setLayoutInfo ) );
	setLayoutInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
	setLayoutInfo.bindingCount = VULKAN_MATERIAL_TEXTURES;
	setLayoutInfo.pBindings = samplerBindings;
	if ( !CheckVulkanResult( state.CreateDescriptorSetLayout( state.device,
		&setLayoutInfo, NULL, &state.guiDescriptorSetLayout ),
		"vkCreateDescriptorSetLayout(gui)" ) ) {
		return false;
	}
	VkDescriptorSetLayoutBinding jointBinding;
	memset( &jointBinding, 0, sizeof( jointBinding ) );
	jointBinding.binding = 0;
	jointBinding.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
	jointBinding.descriptorCount = 1;
	jointBinding.stageFlags = VK_SHADER_STAGE_VERTEX_BIT;
	VkDescriptorSetLayoutCreateInfo jointSetLayoutInfo;
	memset( &jointSetLayoutInfo, 0, sizeof( jointSetLayoutInfo ) );
	jointSetLayoutInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
	jointSetLayoutInfo.bindingCount = 1;
	jointSetLayoutInfo.pBindings = &jointBinding;
	if ( !CheckVulkanResult( state.CreateDescriptorSetLayout( state.device,
		&jointSetLayoutInfo, NULL, &state.jointDescriptorSetLayout ),
		"vkCreateDescriptorSetLayout(joint palette)" ) ) {
		DestroyGuiResources( state );
		return false;
	}

	VkDescriptorPoolSize poolSizes[ 2 ];
	poolSizes[ 0 ].type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	poolSizes[ 0 ].descriptorCount = 16384 * VULKAN_MATERIAL_TEXTURES;
	poolSizes[ 1 ].type = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
	poolSizes[ 1 ].descriptorCount = NUM_VULKAN_FRAMES;
	VkDescriptorPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
	poolInfo.flags = VK_DESCRIPTOR_POOL_CREATE_FREE_DESCRIPTOR_SET_BIT;
	poolInfo.maxSets = 16384 + NUM_VULKAN_FRAMES;
	poolInfo.poolSizeCount = 2;
	poolInfo.pPoolSizes = poolSizes;
	if ( !CheckVulkanResult( state.CreateDescriptorPool( state.device,
		&poolInfo, NULL, &state.guiDescriptorPool ),
		"vkCreateDescriptorPool(gui)" ) ) {
		DestroyGuiResources( state );
		return false;
	}

	VkPushConstantRange pushRange;
	memset( &pushRange, 0, sizeof( pushRange ) );
	pushRange.stageFlags = VK_SHADER_STAGE_VERTEX_BIT |
		VK_SHADER_STAGE_FRAGMENT_BIT;
	state.pushConstantFloats = state.maxPushConstantsSize >= sizeof( float ) * 40 ? 40 : 32;
	pushRange.size = sizeof( float ) * state.pushConstantFloats;
	VkPipelineLayoutCreateInfo pipelineLayoutInfo;
	memset( &pipelineLayoutInfo, 0, sizeof( pipelineLayoutInfo ) );
	pipelineLayoutInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
	VkDescriptorSetLayout descriptorSetLayouts[ 2 ] = {
		state.guiDescriptorSetLayout,
		state.jointDescriptorSetLayout
	};
	pipelineLayoutInfo.setLayoutCount = 2;
	pipelineLayoutInfo.pSetLayouts = descriptorSetLayouts;
	pipelineLayoutInfo.pushConstantRangeCount = 1;
	pipelineLayoutInfo.pPushConstantRanges = &pushRange;
	if ( !CheckVulkanResult( state.CreatePipelineLayout( state.device,
		&pipelineLayoutInfo, NULL, &state.guiPipelineLayout ),
		"vkCreatePipelineLayout(gui)" ) ) {
		DestroyGuiResources( state );
		return false;
	}
	// The front-end ambient programs' set: the material set's five bindings plus
	// the specular cube map.  Same push constant range, so pushes stay compatible.
	VkDescriptorSetLayoutBinding frontEndBindings[ VULKAN_FRONTEND_TEXTURES ];
	memset( frontEndBindings, 0, sizeof( frontEndBindings ) );
	for ( int bindingIndex = 0; bindingIndex < VULKAN_FRONTEND_TEXTURES;
		++bindingIndex ) {
		frontEndBindings[ bindingIndex ].binding = bindingIndex;
		frontEndBindings[ bindingIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		frontEndBindings[ bindingIndex ].descriptorCount = 1;
		frontEndBindings[ bindingIndex ].stageFlags = VK_SHADER_STAGE_FRAGMENT_BIT;
	}
	VkDescriptorSetLayoutCreateInfo frontEndSetLayoutInfo = setLayoutInfo;
	frontEndSetLayoutInfo.bindingCount = VULKAN_FRONTEND_TEXTURES;
	frontEndSetLayoutInfo.pBindings = frontEndBindings;
	VkDescriptorSetLayout frontEndSetLayouts[ 2 ] = {
		VK_NULL_HANDLE,
		state.jointDescriptorSetLayout
	};
	if ( CheckVulkanResult( state.CreateDescriptorSetLayout( state.device,
		&frontEndSetLayoutInfo, NULL, &state.frontEndDescriptorSetLayout ),
		"vkCreateDescriptorSetLayout(front end)" ) ) {
		frontEndSetLayouts[ 0 ] = state.frontEndDescriptorSetLayout;
		VkPipelineLayoutCreateInfo frontEndLayoutInfo = pipelineLayoutInfo;
		frontEndLayoutInfo.pSetLayouts = frontEndSetLayouts;
		CheckVulkanResult( state.CreatePipelineLayout( state.device,
			&frontEndLayoutInfo, NULL, &state.frontEndPipelineLayout ),
			"vkCreatePipelineLayout(front end)" );
		VkDescriptorPoolSize frontEndPoolSize;
		frontEndPoolSize.type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		frontEndPoolSize.descriptorCount = VULKAN_FRONTEND_DESCRIPTOR_SETS * VULKAN_FRONTEND_TEXTURES;
		VkDescriptorPoolCreateInfo frontEndPoolInfo = poolInfo;
		frontEndPoolInfo.maxSets = VULKAN_FRONTEND_DESCRIPTOR_SETS;
		frontEndPoolInfo.poolSizeCount = 1;
		frontEndPoolInfo.pPoolSizes = &frontEndPoolSize;
		CheckVulkanResult( state.CreateDescriptorPool( state.device,
			&frontEndPoolInfo, NULL, &state.frontEndDescriptorPool ),
			"vkCreateDescriptorPool(front end)" );
	}

	VkShaderModule vertexModule = VK_NULL_HANDLE;
	VkShaderModule fragmentModule = VK_NULL_HANDLE;
	if ( !CompileVulkanShaderModule( state, "vkprogs/gui/gui.vert",
		SPIRV_SHADER_STAGE_VERTEX, vertexModule ) ||
		!CompileVulkanShaderModule( state, "vkprogs/gui/gui.frag",
			SPIRV_SHADER_STAGE_FRAGMENT, fragmentModule ) ) {
		if ( vertexModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, vertexModule, NULL );
		}
		if ( fragmentModule != VK_NULL_HANDLE ) {
			state.DestroyShaderModule( state.device, fragmentModule, NULL );
		}
		DestroyGuiResources( state );
		return false;
	}

	VkPipelineShaderStageCreateInfo shaderStages[ 2 ];
	memset( shaderStages, 0, sizeof( shaderStages ) );
	shaderStages[ 0 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 0 ].stage = VK_SHADER_STAGE_VERTEX_BIT;
	shaderStages[ 0 ].module = vertexModule;
	shaderStages[ 0 ].pName = "main";
	shaderStages[ 1 ].sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStages[ 1 ].stage = VK_SHADER_STAGE_FRAGMENT_BIT;
	shaderStages[ 1 ].module = fragmentModule;
	shaderStages[ 1 ].pName = "main";
	VkVertexInputBindingDescription vertexBinding;
	vertexBinding.binding = 0;
	vertexBinding.stride = sizeof( sdVulkanGuiVertex );
	vertexBinding.inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
	VkVertexInputAttributeDescription vertexAttributes[ 2 ];
	memset( vertexAttributes, 0, sizeof( vertexAttributes ) );
	vertexAttributes[ 0 ].location = 0;
	vertexAttributes[ 0 ].binding = 0;
	vertexAttributes[ 0 ].format = VK_FORMAT_R32G32_SFLOAT;
	vertexAttributes[ 0 ].offset = 0;
	vertexAttributes[ 1 ].location = 1;
	vertexAttributes[ 1 ].binding = 0;
	vertexAttributes[ 1 ].format = VK_FORMAT_R32G32_SFLOAT;
	vertexAttributes[ 1 ].offset = sizeof( float ) * 2;
	VkPipelineVertexInputStateCreateInfo vertexInput;
	memset( &vertexInput, 0, sizeof( vertexInput ) );
	vertexInput.sType = VK_STRUCTURE_TYPE_PIPELINE_VERTEX_INPUT_STATE_CREATE_INFO;
	vertexInput.vertexBindingDescriptionCount = 1;
	vertexInput.pVertexBindingDescriptions = &vertexBinding;
	vertexInput.vertexAttributeDescriptionCount = 2;
	vertexInput.pVertexAttributeDescriptions = vertexAttributes;
	VkPipelineInputAssemblyStateCreateInfo inputAssembly;
	memset( &inputAssembly, 0, sizeof( inputAssembly ) );
	inputAssembly.sType = VK_STRUCTURE_TYPE_PIPELINE_INPUT_ASSEMBLY_STATE_CREATE_INFO;
	inputAssembly.topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN;
	VkPipelineViewportStateCreateInfo viewportState;
	memset( &viewportState, 0, sizeof( viewportState ) );
	viewportState.sType = VK_STRUCTURE_TYPE_PIPELINE_VIEWPORT_STATE_CREATE_INFO;
	viewportState.viewportCount = 1;
	viewportState.scissorCount = 1;
	VkPipelineRasterizationStateCreateInfo rasterization;
	memset( &rasterization, 0, sizeof( rasterization ) );
	rasterization.sType = VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_STATE_CREATE_INFO;
	rasterization.polygonMode = VK_POLYGON_MODE_FILL;
	rasterization.cullMode = VK_CULL_MODE_NONE;
	rasterization.frontFace = VK_FRONT_FACE_COUNTER_CLOCKWISE;
	rasterization.lineWidth = 1.0f;
	VkPipelineMultisampleStateCreateInfo multisample;
	memset( &multisample, 0, sizeof( multisample ) );
	multisample.sType = VK_STRUCTURE_TYPE_PIPELINE_MULTISAMPLE_STATE_CREATE_INFO;
	multisample.rasterizationSamples = VK_SAMPLE_COUNT_1_BIT;
	VkPipelineColorBlendAttachmentState blendAttachment;
	memset( &blendAttachment, 0, sizeof( blendAttachment ) );
	blendAttachment.blendEnable = VK_TRUE;
	blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_SRC_ALPHA;
	blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
	blendAttachment.colorBlendOp = VK_BLEND_OP_ADD;
	blendAttachment.srcAlphaBlendFactor = VK_BLEND_FACTOR_ONE;
	blendAttachment.dstAlphaBlendFactor = VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA;
	blendAttachment.alphaBlendOp = VK_BLEND_OP_ADD;
	blendAttachment.colorWriteMask = VK_COLOR_COMPONENT_R_BIT |
		VK_COLOR_COMPONENT_G_BIT | VK_COLOR_COMPONENT_B_BIT |
		VK_COLOR_COMPONENT_A_BIT;
	VkPipelineColorBlendStateCreateInfo blendState;
	memset( &blendState, 0, sizeof( blendState ) );
	blendState.sType = VK_STRUCTURE_TYPE_PIPELINE_COLOR_BLEND_STATE_CREATE_INFO;
	blendState.attachmentCount = 1;
	blendState.pAttachments = &blendAttachment;
	VkPipelineDepthStencilStateCreateInfo depthStencil;
	memset( &depthStencil, 0, sizeof( depthStencil ) );
	depthStencil.sType = VK_STRUCTURE_TYPE_PIPELINE_DEPTH_STENCIL_STATE_CREATE_INFO;
	depthStencil.depthTestEnable = VK_FALSE;
	depthStencil.depthWriteEnable = VK_FALSE;
	const VkDynamicState dynamicStates[] = { VK_DYNAMIC_STATE_VIEWPORT,
		VK_DYNAMIC_STATE_SCISSOR };
	VkPipelineDynamicStateCreateInfo dynamicState;
	memset( &dynamicState, 0, sizeof( dynamicState ) );
	dynamicState.sType = VK_STRUCTURE_TYPE_PIPELINE_DYNAMIC_STATE_CREATE_INFO;
	dynamicState.dynamicStateCount = 2;
	dynamicState.pDynamicStates = dynamicStates;
	VkPipelineRenderingCreateInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachmentFormats = &state.swapchainFormat;
	const VkFormat depthFormat = state.depthStencilFormat != VK_FORMAT_UNDEFINED ?
		state.depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
	renderingInfo.depthAttachmentFormat = depthFormat;
	renderingInfo.stencilAttachmentFormat = depthFormat;
	VkGraphicsPipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO;
	pipelineInfo.pNext = &renderingInfo;
	pipelineInfo.stageCount = 2;
	pipelineInfo.pStages = shaderStages;
	pipelineInfo.pVertexInputState = &vertexInput;
	pipelineInfo.pInputAssemblyState = &inputAssembly;
	pipelineInfo.pViewportState = &viewportState;
	pipelineInfo.pRasterizationState = &rasterization;
	pipelineInfo.pMultisampleState = &multisample;
	pipelineInfo.pColorBlendState = &blendState;
	pipelineInfo.pDepthStencilState = &depthStencil;
	pipelineInfo.pDynamicState = &dynamicState;
	pipelineInfo.layout = state.guiPipelineLayout;
	bool created = CheckVulkanResult( state.CreateGraphicsPipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&state.guiPipeline ), "vkCreateGraphicsPipelines(gui)" );
	if ( created ) {
		blendAttachment.blendEnable = VK_FALSE;
		blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_ONE;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ZERO;
		created = CheckVulkanResult( state.CreateGraphicsPipelines(
			state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
			&state.guiOpaquePipeline ), "vkCreateGraphicsPipelines(gui opaque)" );
	}
	if ( created ) {
		blendAttachment.blendEnable = VK_TRUE;
		blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_ONE;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE;
		created = CheckVulkanResult( state.CreateGraphicsPipelines(
			state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
			&state.guiAddPipeline ), "vkCreateGraphicsPipelines(gui add)" );
	}
	if ( created ) {
		blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_SRC_ALPHA;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ONE;
		created = CheckVulkanResult( state.CreateGraphicsPipelines(
			state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
			&state.guiAlphaAddPipeline ), "vkCreateGraphicsPipelines(gui alpha add)" );
	}
	if ( created ) {
		blendAttachment.srcColorBlendFactor = VK_BLEND_FACTOR_DST_COLOR;
		blendAttachment.dstColorBlendFactor = VK_BLEND_FACTOR_ZERO;
		created = CheckVulkanResult( state.CreateGraphicsPipelines(
			state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
			&state.guiMultiplyPipeline ), "vkCreateGraphicsPipelines(gui multiply)" );
	}
	state.DestroyShaderModule( state.device, vertexModule, NULL );
	state.DestroyShaderModule( state.device, fragmentModule, NULL );
	if ( !created ) {
		DestroyGuiResources( state );
		return false;
	}
	if ( !CreateWorldPipeline( state ) || !CreateSkinnedWorldPipelines( state ) ||
		!CreateShadowPipelines( state ) || !CreateInteractionPipeline( state ) ) {
		DestroyGuiResources( state );
		return false;
	}
	if ( !CreateToolPipelines( state ) ) {
		DestroyGuiResources( state );
		return false;
	}
	CreateGlowPipelines( state );
	state.guiPipelineFormat = state.swapchainFormat;
	return true;
}

const sdVulkanImageResource* FindVulkanImageResource(
	const sdVulkanBackendState& state, const void* owner );

bool AllocateImageDescriptor( sdVulkanBackendState& state,
	sdVulkanImageResource& resource ) {
	// through the material allocator, so a full pool evicts unused material sets
	if ( !AllocateMaterialDescriptorSet( state, resource.descriptorSet, "image" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfo;
	memset( &imageInfo, 0, sizeof( imageInfo ) );
	imageInfo.sampler = resource.sampler;
	imageInfo.imageView = resource.view;
	imageInfo.imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	// Fallback for the "other" type: binding 4 is samplerCube, 0–3 are sampler2D.
	// Writing a cube view into a 2D slot (or vice versa) is a spec violation
	// (VUID-vkCmdDrawIndexed-viewType-07752).  Use the image's own view for the
	// matching slots and a matching-type fallback for the rest.
	const sdVulkanImageResource* fallback = NULL;
	if ( resource.cubeMap ) {
		// Cube view goes to binding 4 only; 2D slots get a 2D fallback.
		fallback = FindVulkanImageResource( state,
			globalImages != NULL ? globalImages->blackImage : NULL );
	} else {
		// 2D view goes to bindings 0–3; cube slot gets a cube fallback.
		fallback = FindVulkanImageResource( state,
			globalImages != NULL ? globalImages->blackCubeMapImage : NULL );
	}
	VkDescriptorImageInfo fallbackInfo;
	memset( &fallbackInfo, 0, sizeof( fallbackInfo ) );
	if ( fallback != NULL ) {
		fallbackInfo.sampler = fallback->sampler;
		fallbackInfo.imageView = fallback->view;
		fallbackInfo.imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	} else {
		fallbackInfo = imageInfo;
	}
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( writes, 0, sizeof( writes ) );
	for ( int bindingIndex = 0; bindingIndex < VULKAN_MATERIAL_TEXTURES;
		++bindingIndex ) {
		const bool isCubeSlot = ( bindingIndex == 4 );
		const bool useMain = ( resource.cubeMap == isCubeSlot );
		writes[ bindingIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ bindingIndex ].dstSet = resource.descriptorSet;
		writes[ bindingIndex ].dstBinding = bindingIndex;
		writes[ bindingIndex ].descriptorCount = 1;
		writes[ bindingIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ bindingIndex ].pImageInfo = useMain ? &imageInfo : &fallbackInfo;
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES,
		writes, 0, NULL );
	return true;
}

const sdVulkanImageResource* FindVulkanImageResource(
	const sdVulkanBackendState& state, const void* owner ) {
	sdVulkanResourceLookup::ConstIterator found =
		state.imageResourceLookup.Find( owner );
	if ( found != state.imageResourceLookup.End() && found->second >= 0 &&
		found->second < state.imageResources.Num() &&
		state.imageResources[ found->second ].owner == owner ) {
		return &state.imageResources[ found->second ];
	}
	return NULL;
}

sdVulkanImageResource* FindVulkanImageResource(
	sdVulkanBackendState& state, const void* owner ) {
	sdVulkanResourceLookup::Iterator found =
		state.imageResourceLookup.Find( owner );
	if ( found != state.imageResourceLookup.End() && found->second >= 0 &&
		found->second < state.imageResources.Num() &&
		state.imageResources[ found->second ].owner == owner ) {
		return &state.imageResources[ found->second ];
	}
	return NULL;
}

const sdVulkanBufferResource* FindVulkanBufferResource(
	const sdVulkanBackendState& state, const void* owner ) {
	sdVulkanResourceLookup::ConstIterator found =
		state.bufferResourceLookup.Find( owner );
	if ( found != state.bufferResourceLookup.End() && found->second >= 0 &&
		found->second < state.bufferResources.Num() &&
		state.bufferResources[ found->second ].owner == owner ) {
		return &state.bufferResources[ found->second ];
	}
	return NULL;
}

bool StageUsesVulkanMaterialTextures( const materialStage_t& stage ) {
	for ( int textureIndex = 0; textureIndex < stage.numTextures; ++textureIndex ) {
		const stageTexture_t& texture = stage.textures[ textureIndex ];
		if ( rbinds != NULL && ( texture.renderBinding == rbinds->bumpMap ||
			 texture.renderBinding == rbinds->specularMap ) ) {
			return true;
		}
		if ( texture.renderBinding != NULL &&
			idStr::Icmp( texture.renderBinding->GetName(), "selfillummap" ) == 0 ) {
			return true;
		}
	}
	return false;
}

// Retail 1.5's front-end ambient programs, lit by the entity's ambient cube map
// (the main menu's globe), and sfx/atmos, the globe's atmosphere shell, which
// shares their vertex program.  The values match frontend.frag's mode.
enum vulkanFrontEndProgram_t {
	FRONTEND_AMBIENT_DECAL,		// sfx/feAmbDecal
	FRONTEND_AMBIENT_CLOUD,		// sfx/feAmbDecal_cloud
	FRONTEND_ATMOSPHERE			// sfx/atmos
};

int VulkanFrontEndMode( const char* programName ) {
	if ( programName == NULL ) {
		return 0;
	}
	if ( idStr::Icmp( programName, "sfx/feAmbDecal" ) == 0 ) {
		return FRONTEND_AMBIENT_DECAL + 1;
	}
	if ( idStr::Icmp( programName, "sfx/feAmbDecal_cloud" ) == 0 ) {
		return FRONTEND_AMBIENT_CLOUD + 1;
	}
	if ( idStr::Icmp( programName, "sfx/atmos" ) == 0 ) {
		return FRONTEND_ATMOSPHERE + 1;
	}
	return 0;
}

enum vulkanSurfacePreparationStatus_t {
	VULKAN_SURFACE_MISSING_GEOMETRY,
	VULKAN_SURFACE_MISSING_CACHE,
	VULKAN_SURFACE_READY
};

struct vulkanSurfaceSelection_t {
	vulkanSurfacePreparationStatus_t status;
	const materialStage_t*	stage;
	idImage*			image;
	idMegaTexture*		megaTexture;
	bool			atmosphere;
	bool			water;
	bool			heatHaze;
	bool			stuffGrass;
	bool			skybox;
	bool			foggyWaterUnderside;
	bool			materialTextures;
	int				frontEndMode;	// FRONTEND_* + 1 for retail's front-end ambient programs, else 0
};

struct vulkanSurfaceSelectionCacheEntry_t {
	const idMaterial*		material;
	const float*			registers;
	vulkanSurfaceSelection_t	selection;
};

const int VULKAN_SURFACE_SELECTION_CACHE_SIZE = 64;
const int VULKAN_SURFACE_PREPARATION_LANES = 13;
vulkanSurfaceSelectionCacheEntry_t vulkanSurfaceSelectionCaches[
	VULKAN_SURFACE_PREPARATION_LANES ][ VULKAN_SURFACE_SELECTION_CACHE_SIZE ];
idList< vulkanSurfaceSelection_t > vulkanSurfaceSelections;

struct vulkanSurfaceAnalysisContext_t {
	const viewDef_s*	view;
	vulkanSurfaceSelection_t*	selections;
};

void AnalyzeVulkanSurfaces( void* opaqueContext, int firstItem, int itemCount,
	int workerIndex ) {
	vulkanSurfaceAnalysisContext_t* context =
		static_cast< vulkanSurfaceAnalysisContext_t* >( opaqueContext );
	const int lane = idMath::ClampInt( 0,
		VULKAN_SURFACE_PREPARATION_LANES - 1, workerIndex );
	for ( int surfaceIndex = firstItem;
		surfaceIndex < firstItem + itemCount; ++surfaceIndex ) {
		const drawSurf_s* surface = context->view->drawSurfs[ surfaceIndex ];
		vulkanSurfaceSelection_t selection;
		memset( &selection, 0, sizeof( selection ) );
		if ( surface == NULL || surface->geo == NULL || surface->space == NULL ||
			surface->space->culled || surface->material == NULL ||
			surface->materialRegisters == NULL || surface->geo->numIndexes <= 0 ||
			surface->geo->mode == PM_POINTSPRITE ) {
			selection.status = VULKAN_SURFACE_MISSING_GEOMETRY;
			context->selections[ surfaceIndex ] = selection;
			continue;
		}
		if ( surface->geo->ambientCache == NULL || surface->geo->indexCache == NULL ) {
			selection.status = VULKAN_SURFACE_MISSING_CACHE;
			context->selections[ surfaceIndex ] = selection;
			continue;
		}
		selection.status = VULKAN_SURFACE_READY;

		const size_t registerKey = reinterpret_cast< size_t >(
			surface->materialRegisters );
		const int cacheIndex = static_cast< int >(
			( ( registerKey >> 4 ) ^ ( registerKey >> 13 ) ) &
			( VULKAN_SURFACE_SELECTION_CACHE_SIZE - 1 ) );
		vulkanSurfaceSelectionCacheEntry_t& cached =
			vulkanSurfaceSelectionCaches[ lane ][ cacheIndex ];
		if ( cached.material == surface->material &&
			cached.registers == surface->materialRegisters ) {
			selection = cached.selection;
			context->selections[ surfaceIndex ] = selection;
			continue;
		}

		int selectedImageScore = -0x7fffffff;
		for ( int stageIndex = 0;
			stageIndex < surface->material->GetNumStages(); ++stageIndex ) {
			const materialStage_t* stage =
				surface->material->GetStage( stageIndex );
			if ( surface->materialRegisters[ stage->conditionRegister ] == 0.0f ||
				( stage->drawStateBits & 0xFF ) == 0x21 ) {
				continue;
			}
			if ( stage->megaTexture != NULL ) {
				idMegaTextureLevel* level = stage->megaTexture->GetLevel(
					stage->megaTexture->GetNumLevels() - 1 );
				if ( level != NULL && level->GetImage() != NULL ) {
					selection.stage = stage;
					selection.image = level->GetImage();
					selection.megaTexture = stage->megaTexture;
					selectedImageScore = 10000;
				}
				continue;
			}
			for ( int textureIndex = 0; textureIndex < stage->numTextures;
				++textureIndex ) {
				const stageTexture_t& texture = stage->textures[ textureIndex ];
				if ( texture.image == NULL || texture.image->defaulted ) continue;
				int score = 10;
				if ( rbinds != NULL && texture.renderBinding == rbinds->diffuseMap ) {
					score = 100;
				} else if ( rbinds != NULL && texture.renderBinding == rbinds->map ) {
					score = 90;
				} else if ( rbinds != NULL &&
					texture.renderBinding == rbinds->cinematicY ) {
					score = 80;
				}
				if ( globalImages != NULL &&
					( texture.image == globalImages->whiteImage ||
					  texture.image == globalImages->blackImage ||
					  texture.image == globalImages->grayImage ) ) {
					score -= 50;
				}
				if ( score > selectedImageScore ) {
					selection.stage = stage;
					selection.image = texture.image;
					selection.megaTexture = NULL;
					selectedImageScore = score;
				}
			}
		}
		if ( selection.stage != NULL ) {
			const char* programName = selection.stage->renderProgram != NULL ?
				selection.stage->renderProgram->GetName() : NULL;
			selection.atmosphere = programName != NULL &&
				idStr::Icmp( programName, "sfx/atmos" ) == 0;
			selection.water = programName != NULL &&
				idStr::Icmpn( programName, "water/", 6 ) == 0;
			selection.heatHaze = programName != NULL &&
				idStr::Icmpn( programName, "heatHaze", 8 ) == 0;
			selection.stuffGrass = programName != NULL &&
				idStr::Icmpn( programName, "stuff/grass", 11 ) == 0;
			selection.skybox = programName != NULL &&
				idStr::Icmp( programName, "skies/skybox" ) == 0;
			selection.foggyWaterUnderside = programName != NULL &&
				idStr::Icmp( programName, "sfx/foggyWaterSurface" ) == 0;
			selection.materialTextures =
				StageUsesVulkanMaterialTextures( *selection.stage );
			selection.frontEndMode = VulkanFrontEndMode( programName );
		}
		cached.material = surface->material;
		cached.registers = surface->materialRegisters;
		cached.selection = selection;
		context->selections[ surfaceIndex ] = selection;
	}
}

bool StageUsesCoverageFade( const materialStage_t& stage ) {
	return stage.renderProgram != NULL &&
		idStr::FindText( stage.renderProgram->GetName(), "coverage", false ) >= 0;
}

void SetVulkanPolygonOffset( sdVulkanBackendState& state,
	VkCommandBuffer commandBuffer, const idMaterial* material,
	const materialStage_t* stage ) {
	float polygonOffset = 0.0f;
	if ( material != NULL && material->TestMaterialFlag( MF_POLYGONOFFSET ) ) {
		polygonOffset = material->GetPolygonOffset();
	}
	// A stage-local offset overrides the material-wide value, matching the
	// legacy glPolygonOffset state applied immediately before that stage.
	if ( stage != NULL && stage->privatePolygonOffset != 0.0f ) {
		polygonOffset = stage->privatePolygonOffset;
	}
	const bool enabled = polygonOffset != 0.0f;
	state.CmdSetDepthBias( commandBuffer,
		enabled ? r_offsetUnits.GetFloat() * polygonOffset : 0.0f,
		0.0f, enabled ? r_offsetFactor.GetFloat() : 0.0f );
}

bool GetVulkanFrontEndLayoutDescriptor( sdVulkanBackendState& state, const void* owner,
	idImage* images[ VULKAN_FRONTEND_TEXTURES ], idImage* fallbacks[ VULKAN_FRONTEND_TEXTURES ],
	VkDescriptorSet& descriptorSet );

// The descriptor for a MegaTexture level in the terrain base pass: the level's
// image, then retail's detail texture and its mask ($megaDetailTexture,
// $megaDetailTextureMask, as idMegaTexture::UpdateForViewOrigin sets them),
// mega_detail.frag's bindings 0-2.  The rest of the front-end layout is unused.
bool GetVulkanMegaDescriptor( sdVulkanBackendState& state, const void* level,
	idImage* levelImage, VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL || levelImage == NULL || rbinds == NULL ) {
		return false;
	}
	idImage* detail = rbinds->megaDetailTexture->GetImage();
	idImage* detailMask = rbinds->megaDetailTextureMask->GetImage();
	idImage* images[ VULKAN_FRONTEND_TEXTURES ] = {
		levelImage,
		detail != NULL ? detail : globalImages->grayImage,
		detailMask != NULL ? detailMask : globalImages->defaultDetailMaskImage,
		globalImages->blackImage,
		globalImages->blackCubeMapImage,
		globalImages->blackCubeMapImage
	};
	// A gray detail adds nothing (2 x 0.5 - 1 = 0), whatever the mask.
	idImage* fallbacks[ VULKAN_FRONTEND_TEXTURES ] = {
		globalImages->blackImage,
		globalImages->grayImage,
		globalImages->defaultDetailMaskImage,
		globalImages->blackImage,
		globalImages->blackCubeMapImage,
		globalImages->blackCubeMapImage
	};
	return GetVulkanFrontEndLayoutDescriptor( state, level, images, fallbacks, descriptorSet );
}

// The descriptor for a front-end ambient program: diffuse, bump, specular and
// self-illumination maps, then the entity's ambient and specular cube maps, bound
// as the GL path's SetAmbientCubeMapBindings does (black when there are none).
bool GetVulkanFrontEndDescriptor( sdVulkanBackendState& state,
	const materialStage_t& stage, idImage* selectedImage,
	const viewEntity_s* space, VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL || selectedImage == NULL ||
		state.frontEndDescriptorSetLayout == VK_NULL_HANDLE ) {
		return false;
	}
	idImage* images[ VULKAN_FRONTEND_TEXTURES ] = {
		selectedImage,
		globalImages->flatNormalMap,
		globalImages->blackImage,
		globalImages->blackImage,
		globalImages->blackCubeMapImage,
		globalImages->blackCubeMapImage
	};
	const sdDeclAmbientCubeMap* cubeMap = space != NULL ? space->ambientCubeMap : NULL;
	if ( cubeMap != NULL && cubeMap->GetAmbientCubeMap() != NULL ) {
		images[ 4 ] = cubeMap->GetAmbientCubeMap();
	}
	if ( cubeMap != NULL && cubeMap->GetSpecularCubeMap() != NULL ) {
		images[ 5 ] = cubeMap->GetSpecularCubeMap();
	}
	for ( int textureIndex = 0; textureIndex < stage.numTextures; ++textureIndex ) {
		const stageTexture_t& texture = stage.textures[ textureIndex ];
		if ( texture.image == NULL || texture.image->defaulted ) {
			continue;
		}
		if ( rbinds != NULL && texture.renderBinding == rbinds->diffuseMap ) {
			images[ 0 ] = texture.image;
		} else if ( rbinds != NULL && texture.renderBinding == rbinds->bumpMap ) {
			images[ 1 ] = texture.image;
		} else if ( rbinds != NULL && texture.renderBinding == rbinds->specularMap ) {
			images[ 2 ] = texture.image;
		} else if ( texture.renderBinding != NULL &&
			idStr::Icmp( texture.renderBinding->GetName(), "selfillummap" ) == 0 ) {
			images[ 3 ] = texture.image;
		}
	}
	idImage* fallbacks[ VULKAN_FRONTEND_TEXTURES ];
	for ( int slot = 0; slot < VULKAN_FRONTEND_TEXTURES; ++slot ) {
		fallbacks[ slot ] = slot >= 4 ? globalImages->blackCubeMapImage :
			globalImages->blackImage;
	}
	return GetVulkanFrontEndLayoutDescriptor( state, &stage, images, fallbacks, descriptorSet );
}

// A descriptor set of the front-end layout (bindings 0-3 sampler2D, 4-5
// samplerCube) for these images, cached per owner; an image that isn't uploaded,
// or whose type doesn't fit its slot, is replaced by its fallback.
bool GetVulkanFrontEndLayoutDescriptor( sdVulkanBackendState& state, const void* owner,
	idImage* images[ VULKAN_FRONTEND_TEXTURES ], idImage* fallbacks[ VULKAN_FRONTEND_TEXTURES ],
	VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( state.frontEndDescriptorSetLayout == VK_NULL_HANDLE ) {
		return false;
	}
	for ( int slot = 0; slot < VULKAN_FRONTEND_TEXTURES; ++slot ) {
		if ( images[ slot ] == NULL ) {
			images[ slot ] = fallbacks[ slot ];
		}
		if ( !images[ slot ]->IsLoaded() ) {
			images[ slot ]->BindFragment();
		}
		if ( !fallbacks[ slot ]->IsLoaded() ) {
			fallbacks[ slot ]->BindFragment();
		}
	}
	// BindFragment can reallocate state.imageResources: resolve afterwards.  A
	// generated cube map keeps cubeFiles CF_2D, so the slot type is checked on
	// the uploaded resource: binding 4 and 5 are samplerCube, the rest sampler2D.
	const sdVulkanImageResource* resources[ VULKAN_FRONTEND_TEXTURES ];
	for ( int slot = 0; slot < VULKAN_FRONTEND_TEXTURES; ++slot ) {
		const bool cubeSlot = slot >= 4;
		resources[ slot ] = FindVulkanImageResource( state, images[ slot ] );
		if ( resources[ slot ] == NULL || resources[ slot ]->cubeMap != cubeSlot ) {
			images[ slot ] = fallbacks[ slot ];
			resources[ slot ] = FindVulkanImageResource( state, images[ slot ] );
		}
		if ( resources[ slot ] == NULL || resources[ slot ]->cubeMap != cubeSlot ) {
			return false;
		}
	}
	const int descriptorHashKey = FrontEndDescriptorKey( state, owner );
	for ( int descriptorIndex = state.frontEndDescriptorHash.GetFirst( descriptorHashKey );
		descriptorIndex != -1;
		descriptorIndex = state.frontEndDescriptorHash.GetNext( descriptorIndex ) ) {
		sdVulkanFrontEndDescriptor& cached = state.frontEndDescriptors[ descriptorIndex ];
		if ( cached.owner != owner ) {
			continue;
		}
		bool matches = true;
		for ( int slot = 0; slot < VULKAN_FRONTEND_TEXTURES; ++slot ) {
			if ( cached.imageOwners[ slot ] != images[ slot ] ||
				cached.imageViews[ slot ] != resources[ slot ]->view ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}
	if ( state.frontEndDescriptorPool == VK_NULL_HANDLE ) {
		return false;
	}
	sdVulkanFrontEndDescriptor frontEndDescriptor;
	memset( &frontEndDescriptor, 0, sizeof( frontEndDescriptor ) );
	frontEndDescriptor.owner = owner;
	frontEndDescriptor.lastUsedFrame = state.frameSerial;
	VkDescriptorSetAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
	allocateInfo.descriptorPool = state.frontEndDescriptorPool;
	allocateInfo.descriptorSetCount = 1;
	allocateInfo.pSetLayouts = &state.frontEndDescriptorSetLayout;
	// A full pool (a long session over many maps): drop the sets no recent frame
	// used and try once more.  The pool's size is kept in software as well, since
	// a driver may let allocations run past maxSets.
	const bool full = state.frontEndDescriptorSetsLive >= VULKAN_FRONTEND_DESCRIPTOR_SETS;
	VkResult result = full ? VK_ERROR_OUT_OF_POOL_MEMORY :
		state.AllocateDescriptorSets( state.device, &allocateInfo, &frontEndDescriptor.descriptorSet );
	if ( ( result == VK_ERROR_OUT_OF_POOL_MEMORY || result == VK_ERROR_FRAGMENTED_POOL ) &&
		EvictUnusedFrontEndDescriptors( state ) > 0 &&
		state.frontEndDescriptorSetsLive < VULKAN_FRONTEND_DESCRIPTOR_SETS ) {
		result = state.AllocateDescriptorSets( state.device,
			&allocateInfo, &frontEndDescriptor.descriptorSet );
	}
	if ( result == VK_SUCCESS ) {
		state.frontEndDescriptorSetsLive++;
		state.frontEndPoolExhaustedWarned = false;	// warn once per exhaustion
	} else {
		if ( !state.frontEndPoolExhaustedWarned ) {
			common->Warning( "vkAllocateDescriptorSets(front end) failed: %s (%d sets in use); "
				"draws needing a new set are skipped until sets are freed (warned once)",
				VkResultName( result ), state.frontEndDescriptorSetsLive );
			state.frontEndPoolExhaustedWarned = true;
		}
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_FRONTEND_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_FRONTEND_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int slot = 0; slot < VULKAN_FRONTEND_TEXTURES; ++slot ) {
		frontEndDescriptor.imageOwners[ slot ] = images[ slot ];
		frontEndDescriptor.imageViews[ slot ] = resources[ slot ]->view;
		imageInfos[ slot ].sampler = resources[ slot ]->sampler;
		imageInfos[ slot ].imageView = resources[ slot ]->view;
		imageInfos[ slot ].imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ slot ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ slot ].dstSet = frontEndDescriptor.descriptorSet;
		writes[ slot ].dstBinding = slot;
		writes[ slot ].descriptorCount = 1;
		writes[ slot ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ slot ].pImageInfo = &imageInfos[ slot ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_FRONTEND_TEXTURES,
		writes, 0, NULL );
	state.frontEndDescriptorHash.Add( descriptorHashKey,
		state.frontEndDescriptors.Append( frontEndDescriptor ) );
	descriptorSet = frontEndDescriptor.descriptorSet;
	return true;
}

bool GetVulkanMaterialDescriptor( sdVulkanBackendState& state,
	const materialStage_t& stage, idImage* selectedImage,
	const viewEntity_s* space,
	VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL || selectedImage == NULL ) {
		return false;
	}
	idImage* images[ VULKAN_MATERIAL_TEXTURES ] = {
		selectedImage,
		globalImages->flatNormalMap,
		globalImages->blackImage,
		globalImages->blackImage,
		globalImages->blackCubeMapImage
	};
	if ( space != NULL && space->ambientCubeMap != NULL &&
		space->ambientCubeMap->GetAmbientCubeMap() != NULL ) {
		images[ 4 ] = space->ambientCubeMap->GetAmbientCubeMap();
	}
	for ( int textureIndex = 0; textureIndex < stage.numTextures; ++textureIndex ) {
		const stageTexture_t& texture = stage.textures[ textureIndex ];
		if ( texture.image == NULL || texture.image->defaulted ) {
			continue;
		}
		if ( rbinds != NULL && texture.renderBinding == rbinds->diffuseMap ) {
			images[ 0 ] = texture.image;
		} else if ( rbinds != NULL && texture.renderBinding == rbinds->bumpMap ) {
			images[ 1 ] = texture.image;
		} else if ( rbinds != NULL && texture.renderBinding == rbinds->specularMap ) {
			images[ 2 ] = texture.image;
		} else if ( texture.renderBinding != NULL &&
			idStr::Icmp( texture.renderBinding->GetName(), "selfillummap" ) == 0 ) {
			images[ 3 ] = texture.image;
		}
	}
	const sdVulkanImageResource* resources[ VULKAN_MATERIAL_TEXTURES ];
	idImage* fallbacks[ VULKAN_MATERIAL_TEXTURES ];
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
		if ( images[ imageIndex ] == NULL ) {
			return false;
		}
		fallbacks[ imageIndex ] = imageIndex == 4 ? globalImages->blackCubeMapImage :
			globalImages->blackImage;
		if ( !images[ imageIndex ]->IsLoaded() ) {
			images[ imageIndex ]->BindFragment();
		}
		if ( !fallbacks[ imageIndex ]->IsLoaded() ) {
			fallbacks[ imageIndex ]->BindFragment();
		}
	}
	// BindFragment can append to state.imageResources and reallocate its idList.
	// Resolve every resource pointer only after all requested images have been
	// loaded so no pointer retained here can be invalidated by a later binding.
	// Bindings 0–3 are sampler2D; binding 4 is samplerCube.  A cube map in a
	// 2D slot (or vice versa) is a spec violation (VUID-vkCmdDrawIndexed-
	// viewType-07752), so a mismatch takes the slot's fallback.  As the front
	// end's descriptor, the type is checked on the uploaded resource: a generated
	// cube map (the ambient cubes) keeps cubeFiles CF_2D.
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
		const bool cubeSlot = imageIndex == 4;
		resources[ imageIndex ] = FindVulkanImageResource( state,
			images[ imageIndex ] );
		if ( resources[ imageIndex ] != NULL && resources[ imageIndex ]->cubeMap != cubeSlot ) {
			images[ imageIndex ] = fallbacks[ imageIndex ];
			resources[ imageIndex ] = FindVulkanImageResource( state, images[ imageIndex ] );
		}
		if ( resources[ imageIndex ] == NULL || resources[ imageIndex ]->cubeMap != cubeSlot ) {
			return false;
		}
	}
	const int descriptorHashKey = sdVulkanPointerHash::Hash(
		state.materialDescriptorHash, &stage );
	for ( int descriptorIndex = state.materialDescriptorHash.GetFirst(
		descriptorHashKey ); descriptorIndex != idHashIndex::NULL_INDEX;
		descriptorIndex = state.materialDescriptorHash.GetNext( descriptorIndex ) ) {
		if ( descriptorIndex < 0 || descriptorIndex >= state.materialDescriptors.Num() ) {
			continue;
		}
		sdVulkanMaterialDescriptor& cached =
			state.materialDescriptors[ descriptorIndex ];
		if ( cached.owner != &stage ) {
			continue;
		}
		bool matches = true;
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
			++imageIndex ) {
			if ( cached.imageOwners[ imageIndex ] != images[ imageIndex ] ||
				cached.imageViews[ imageIndex ] != resources[ imageIndex ]->view ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}
	sdVulkanMaterialDescriptor materialDescriptor;
	memset( &materialDescriptor, 0, sizeof( materialDescriptor ) );
	materialDescriptor.owner = &stage;
	materialDescriptor.lastUsedFrame = state.frameSerial;
	if ( !AllocateMaterialDescriptorSet( state, materialDescriptor.descriptorSet, "material" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_MATERIAL_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
		materialDescriptor.imageOwners[ imageIndex ] = images[ imageIndex ];
		materialDescriptor.imageViews[ imageIndex ] = resources[ imageIndex ]->view;
		imageInfos[ imageIndex ].sampler = resources[ imageIndex ]->sampler;
		imageInfos[ imageIndex ].imageView = resources[ imageIndex ]->view;
		imageInfos[ imageIndex ].imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ imageIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ imageIndex ].dstSet = materialDescriptor.descriptorSet;
		writes[ imageIndex ].dstBinding = imageIndex;
		writes[ imageIndex ].descriptorCount = 1;
		writes[ imageIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ imageIndex ].pImageInfo = &imageInfos[ imageIndex ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES,
		writes, 0, NULL );
	const int descriptorIndex = state.materialDescriptors.Append( materialDescriptor );
	state.materialDescriptorHash.Add( descriptorHashKey, descriptorIndex );
	descriptorSet = materialDescriptor.descriptorSet;
	return true;
}

void DestroyToolSwapchain( sdVulkanBackendState& state,
	sdVulkanBackendState::sdToolWindow& tool ) {
	DestroySemaphoreList( state, tool.presentSemaphores );
	for ( int i = 0; i < tool.views.Num(); ++i ) {
		if ( tool.views[ i ] != VK_NULL_HANDLE ) {
			state.DestroyImageView( state.device, tool.views[ i ], NULL );
		}
	}
	for ( int i = 0; i < tool.depthViews.Num(); ++i ) {
		if ( tool.depthViews[ i ] != VK_NULL_HANDLE ) {
			state.DestroyImageView( state.device, tool.depthViews[ i ], NULL );
		}
		if ( tool.depthImages[ i ] != VK_NULL_HANDLE ) {
			state.DestroyImage( state.device, tool.depthImages[ i ], NULL );
		}
		if ( tool.depthMemory[ i ] != VK_NULL_HANDLE ) {
			state.FreeMemory( state.device, tool.depthMemory[ i ], NULL );
		}
	}
	if ( tool.swapchain != VK_NULL_HANDLE ) {
		state.DestroySwapchainKHR( state.device, tool.swapchain, NULL );
	}
	tool.swapchain = VK_NULL_HANDLE;
	tool.images.Clear();
	tool.views.Clear();
	tool.imageInitialized.Clear();
	tool.depthImages.Clear();
	tool.depthMemory.Clear();
	tool.depthViews.Clear();
	tool.depthInitialized.Clear();
	tool.extent.width = tool.extent.height = 0;
}

void DestroyToolWindows( sdVulkanBackendState& state ) {
	for ( int i = 0; i < state.toolWindows.Num(); ++i ) {
		sdVulkanBackendState::sdToolWindow* tool = state.toolWindows[ i ];
		if ( tool == NULL ) {
			continue;
		}
		DestroyToolSwapchain( state, *tool );
		if ( tool->surface != VK_NULL_HANDLE ) {
			state.DestroySurfaceKHR( state.instance, tool->surface, NULL );
		}
		delete tool;
	}
	state.toolWindows.Clear();
	state.activeToolWindow = NULL;
	state.toolFrameActive = false;
}

bool CreateToolSwapchain( sdVulkanBackendState& state,
	sdVulkanBackendState::sdToolWindow& tool, int requestedWidth,
	int requestedHeight ) {
	if ( requestedWidth <= 0 || requestedHeight <= 0 ) {
		return false;
	}
	VkBool32 canPresent = VK_FALSE;
	if ( !CheckVulkanResult( state.GetPhysicalDeviceSurfaceSupportKHR(
		state.physicalDevice, state.queueFamilyIndex, tool.surface, &canPresent ),
		"vkGetPhysicalDeviceSurfaceSupportKHR(tool)" ) || !canPresent ) {
		common->Warning( "The Vulkan graphics queue cannot present a Radiant window" );
		return false;
	}
	VkSurfaceCapabilitiesKHR capabilities;
	if ( !CheckVulkanResult( state.GetPhysicalDeviceSurfaceCapabilitiesKHR(
		state.physicalDevice, tool.surface, &capabilities ),
		"vkGetPhysicalDeviceSurfaceCapabilitiesKHR(tool)" ) ) {
		return false;
	}
	unsigned int formatCount = 0;
	state.GetPhysicalDeviceSurfaceFormatsKHR( state.physicalDevice, tool.surface,
		&formatCount, NULL );
	idList< VkSurfaceFormatKHR > formats;
	formats.SetNum( formatCount );
	state.GetPhysicalDeviceSurfaceFormatsKHR( state.physicalDevice, tool.surface,
		&formatCount, formats.Begin() );
	VkSurfaceFormatKHR surfaceFormat;
	memset( &surfaceFormat, 0, sizeof( surfaceFormat ) );
	bool foundFormat = false;
	for ( int i = 0; i < formats.Num(); ++i ) {
		if ( formats[ i ].format == state.swapchainFormat ) {
			surfaceFormat = formats[ i ];
			foundFormat = true;
			break;
		}
	}
	if ( !foundFormat && formats.Num() == 1 &&
		formats[ 0 ].format == VK_FORMAT_UNDEFINED ) {
		surfaceFormat.format = state.swapchainFormat;
		surfaceFormat.colorSpace = state.colorSpace;
		foundFormat = true;
	}
	if ( !foundFormat ) {
		common->Warning( "Radiant Vulkan window does not support main swapchain format %d",
			static_cast< int >( state.swapchainFormat ) );
		return false;
	}
	VkExtent2D extent;
	if ( capabilities.currentExtent.width != UINT_MAX ) {
		extent = capabilities.currentExtent;
	} else {
		extent.width = ClampUnsigned( static_cast< unsigned int >( requestedWidth ),
			capabilities.minImageExtent.width, capabilities.maxImageExtent.width );
		extent.height = ClampUnsigned( static_cast< unsigned int >( requestedHeight ),
			capabilities.minImageExtent.height, capabilities.maxImageExtent.height );
	}
	if ( extent.width == 0 || extent.height == 0 ) {
		return false;
	}
	unsigned int imageCount = capabilities.minImageCount + 1;
	if ( capabilities.maxImageCount != 0 && imageCount > capabilities.maxImageCount ) {
		imageCount = capabilities.maxImageCount;
	}
	VkSwapchainCreateInfoKHR createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR;
	createInfo.surface = tool.surface;
	createInfo.minImageCount = imageCount;
	createInfo.imageFormat = surfaceFormat.format;
	createInfo.imageColorSpace = surfaceFormat.colorSpace;
	createInfo.imageExtent = extent;
	createInfo.imageArrayLayers = 1;
	createInfo.imageUsage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
	createInfo.imageSharingMode = VK_SHARING_MODE_EXCLUSIVE;
	createInfo.preTransform = capabilities.currentTransform;
	createInfo.compositeAlpha = ChooseCompositeAlpha( capabilities.supportedCompositeAlpha );
	createInfo.presentMode = VK_PRESENT_MODE_FIFO_KHR;
	createInfo.clipped = VK_TRUE;
	createInfo.oldSwapchain = tool.swapchain;
	VkSwapchainKHR newSwapchain = VK_NULL_HANDLE;
	if ( !CheckVulkanResult( state.CreateSwapchainKHR( state.device, &createInfo,
		NULL, &newSwapchain ), "vkCreateSwapchainKHR(tool)" ) ) {
		return false;
	}
	unsigned int newImageCount = 0;
	state.GetSwapchainImagesKHR( state.device, newSwapchain, &newImageCount, NULL );
	idList< VkImage > newImages;
	newImages.SetNum( newImageCount );
	if ( !CheckVulkanResult( state.GetSwapchainImagesKHR( state.device,
		newSwapchain, &newImageCount, newImages.Begin() ),
		"vkGetSwapchainImagesKHR(tool)" ) ) {
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	idList< VkImageView > newViews;
	newViews.SetNum( newImageCount );
	memset( newViews.Begin(), 0, newImageCount * sizeof( VkImageView ) );
	for ( unsigned int i = 0; i < newImageCount; ++i ) {
		VkImageViewCreateInfo viewInfo;
		memset( &viewInfo, 0, sizeof( viewInfo ) );
		viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
		viewInfo.image = newImages[ i ];
		viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
		viewInfo.format = surfaceFormat.format;
		viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		viewInfo.subresourceRange.levelCount = 1;
		viewInfo.subresourceRange.layerCount = 1;
		if ( !CheckVulkanResult( state.CreateImageView( state.device, &viewInfo,
			NULL, &newViews[ i ] ), "vkCreateImageView(tool)" ) ) {
			for ( unsigned int j = 0; j < i; ++j ) {
				state.DestroyImageView( state.device, newViews[ j ], NULL );
			}
			state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
			return false;
		}
	}
	idList< VkImage > newDepthImages;
	idList< VkDeviceMemory > newDepthMemory;
	idList< VkImageView > newDepthViews;
	if ( !CreateDepthResources( state, newImageCount, extent, newDepthImages,
		newDepthMemory, newDepthViews ) ) {
		for ( unsigned int i = 0; i < newImageCount; ++i ) {
			state.DestroyImageView( state.device, newViews[ i ], NULL );
		}
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	idList< VkSemaphore > newPresentSemaphores;
	if ( !CreateSemaphoreList( state, newImageCount, newPresentSemaphores,
		"vkCreateSemaphore(Radiant present)" ) ) {
		for ( unsigned int i = 0; i < newImageCount; ++i ) {
			state.DestroyImageView( state.device, newDepthViews[ i ], NULL );
			state.DestroyImage( state.device, newDepthImages[ i ], NULL );
			state.FreeMemory( state.device, newDepthMemory[ i ], NULL );
			state.DestroyImageView( state.device, newViews[ i ], NULL );
		}
		state.DestroySwapchainKHR( state.device, newSwapchain, NULL );
		return false;
	}
	DestroyToolSwapchain( state, tool );
	tool.swapchain = newSwapchain;
	tool.images = newImages;
	tool.views = newViews;
	tool.presentSemaphores = newPresentSemaphores;
	tool.imageInitialized.SetNum( newImageCount );
	memset( tool.imageInitialized.Begin(), 0, newImageCount );
	tool.depthImages = newDepthImages;
	tool.depthMemory = newDepthMemory;
	tool.depthViews = newDepthViews;
	tool.depthInitialized.SetNum( newImageCount );
	memset( tool.depthInitialized.Begin(), 0, newImageCount );
	tool.extent = extent;
	tool.dirty = false;
	return true;
}

bool GetVulkanSkyboxDescriptor( sdVulkanBackendState& state,
	const materialStage_t& stage, VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL ) {
		return false;
	}
	idImage* images[ VULKAN_MATERIAL_TEXTURES ] = {
		NULL, NULL, globalImages->blackImage, globalImages->blackImage,
		globalImages->blackCubeMapImage
	};
	for ( int textureIndex = 0; textureIndex < stage.numTextures; ++textureIndex ) {
		const stageTexture_t& texture = stage.textures[ textureIndex ];
		if ( texture.image == NULL || texture.image->defaulted ||
			texture.renderBinding == NULL ) {
			continue;
		}
		const char* bindingName = texture.renderBinding->GetName();
		if ( idStr::Icmp( bindingName, "map" ) == 0 ) {
			images[ 0 ] = texture.image;
		} else if ( idStr::Icmp( bindingName, "mask" ) == 0 ) {
			images[ 1 ] = texture.image;
		}
	}
	if ( images[ 0 ] == NULL || images[ 1 ] == NULL ) {
		return false;
	}
	// Bindings 0–3 are sampler2D; binding 4 is samplerCube.
	for ( int slot = 0; slot < 4; ++slot ) {
		if ( images[ slot ] != NULL && images[ slot ]->cubeFiles != CF_2D ) {
			images[ slot ] = globalImages->blackImage;
		}
	}
	if ( images[ 4 ] != NULL && images[ 4 ]->cubeFiles == CF_2D ) {
		images[ 4 ] = globalImages->blackCubeMapImage;
	}
	const sdVulkanImageResource* resources[ VULKAN_MATERIAL_TEXTURES ];
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
		++imageIndex ) {
		if ( !images[ imageIndex ]->IsLoaded() ) {
			images[ imageIndex ]->BindFragment();
		}
	}
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
		++imageIndex ) {
		resources[ imageIndex ] = FindVulkanImageResource( state,
			images[ imageIndex ] );
		if ( resources[ imageIndex ] == NULL ) {
			return false;
		}
	}
	const int descriptorHashKey = sdVulkanPointerHash::Hash(
		state.materialDescriptorHash, &stage );
	for ( int descriptorIndex = state.materialDescriptorHash.GetFirst(
		descriptorHashKey ); descriptorIndex != idHashIndex::NULL_INDEX;
		descriptorIndex = state.materialDescriptorHash.GetNext( descriptorIndex ) ) {
		if ( descriptorIndex < 0 || descriptorIndex >= state.materialDescriptors.Num() ) {
			continue;
		}
		sdVulkanMaterialDescriptor& cached =
			state.materialDescriptors[ descriptorIndex ];
		if ( cached.owner != &stage ) {
			continue;
		}
		bool matches = true;
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
			++imageIndex ) {
			if ( cached.imageOwners[ imageIndex ] != images[ imageIndex ] ||
				cached.imageViews[ imageIndex ] != resources[ imageIndex ]->view ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}
	sdVulkanMaterialDescriptor skyboxDescriptor;
	memset( &skyboxDescriptor, 0, sizeof( skyboxDescriptor ) );
	skyboxDescriptor.owner = &stage;
	skyboxDescriptor.lastUsedFrame = state.frameSerial;
	if ( !AllocateMaterialDescriptorSet( state, skyboxDescriptor.descriptorSet, "skybox" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_MATERIAL_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
		++imageIndex ) {
		skyboxDescriptor.imageOwners[ imageIndex ] = images[ imageIndex ];
		skyboxDescriptor.imageViews[ imageIndex ] = resources[ imageIndex ]->view;
		imageInfos[ imageIndex ].sampler = resources[ imageIndex ]->sampler;
		imageInfos[ imageIndex ].imageView = resources[ imageIndex ]->view;
		imageInfos[ imageIndex ].imageLayout =
			VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ imageIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ imageIndex ].dstSet = skyboxDescriptor.descriptorSet;
		writes[ imageIndex ].dstBinding = imageIndex;
		writes[ imageIndex ].descriptorCount = 1;
		writes[ imageIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ imageIndex ].pImageInfo = &imageInfos[ imageIndex ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES,
		writes, 0, NULL );
	const int skyboxDescriptorIndex = state.materialDescriptors.Append( skyboxDescriptor );
	state.materialDescriptorHash.Add( descriptorHashKey, skyboxDescriptorIndex );
	descriptorSet = skyboxDescriptor.descriptorSet;
	return true;
}

bool GetVulkanWaterDescriptor( sdVulkanBackendState& state,
	const materialStage_t& stage, VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL ||
		state.currentRenderResource.view == VK_NULL_HANDLE ||
		state.currentRenderResource.sampler == VK_NULL_HANDLE ) {
		return false;
	}
	idImage* images[ VULKAN_MATERIAL_TEXTURES - 1 ] = {
		globalImages->grayImage, globalImages->blackCubeMapImage,
		globalImages->whiteImage, globalImages->grayImage
	};
	for ( int textureIndex = 0; textureIndex < stage.numTextures; ++textureIndex ) {
		const stageTexture_t& texture = stage.textures[ textureIndex ];
		if ( texture.image == NULL || texture.image->defaulted ||
			texture.renderBinding == NULL ) {
			continue;
		}
		const char* bindingName = texture.renderBinding->GetName();
		if ( idStr::Icmp( bindingName, "bumpmap" ) == 0 ) {
			images[ 0 ] = texture.image;
		} else if ( idStr::Icmp( bindingName, "bumpmap2" ) == 0 ) {
			images[ 3 ] = texture.image;
		} else if ( idStr::Icmp( bindingName, "environmentcubemap" ) == 0 ) {
			images[ 1 ] = texture.image;
		} else if ( idStr::Icmp( bindingName, "map" ) == 0 ) {
			images[ 2 ] = texture.image;
		}
	}
	// The retail data primarily ships hashed, precompressed water frames.  A
	// missing frame must remain a neutral normal, not kick this special stage
	// into the generic renderer where its white specular lookup becomes the
	// visible surface.  The shader adds a subtle procedural ripple over these
	// neutral fallbacks, so water still looks alive while assets stream in.
	const sdVulkanImageResource* resources[ VULKAN_MATERIAL_TEXTURES - 1 ];
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES - 1;
		++imageIndex ) {
		if ( !images[ imageIndex ]->IsLoaded() ) {
			images[ imageIndex ]->BindFragment();
		}
	}
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES - 1;
		++imageIndex ) {
		resources[ imageIndex ] = FindVulkanImageResource( state,
			images[ imageIndex ] );
		if ( resources[ imageIndex ] == NULL ) {
			return false;
		}
	}
	const void* owners[ VULKAN_MATERIAL_TEXTURES ] = {
		images[ 0 ], images[ 1 ], images[ 2 ], images[ 3 ],
		&state.currentRenderResource
	};
	VkImageView views[ VULKAN_MATERIAL_TEXTURES ] = {
		resources[ 0 ]->view, resources[ 1 ]->view, resources[ 2 ]->view,
		resources[ 3 ]->view, state.currentRenderResource.view
	};
	VkSampler samplers[ VULKAN_MATERIAL_TEXTURES ] = {
		resources[ 0 ]->sampler, resources[ 1 ]->sampler,
		resources[ 2 ]->sampler, resources[ 3 ]->sampler,
		state.currentRenderResource.sampler
	};
	const int descriptorHashKey = sdVulkanPointerHash::Hash(
		state.materialDescriptorHash, &stage );
	for ( int descriptorIndex = state.materialDescriptorHash.GetFirst(
		descriptorHashKey ); descriptorIndex != idHashIndex::NULL_INDEX;
		descriptorIndex = state.materialDescriptorHash.GetNext( descriptorIndex ) ) {
		if ( descriptorIndex < 0 || descriptorIndex >= state.materialDescriptors.Num() ) {
			continue;
		}
		sdVulkanMaterialDescriptor& cached =
			state.materialDescriptors[ descriptorIndex ];
		if ( cached.owner != &stage ) {
			continue;
		}
		bool matches = true;
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
			++imageIndex ) {
			if ( cached.imageOwners[ imageIndex ] != owners[ imageIndex ] ||
				cached.imageViews[ imageIndex ] != views[ imageIndex ] ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}
	sdVulkanMaterialDescriptor waterDescriptor;
	memset( &waterDescriptor, 0, sizeof( waterDescriptor ) );
	waterDescriptor.owner = &stage;
	waterDescriptor.lastUsedFrame = state.frameSerial;
	if ( !AllocateMaterialDescriptorSet( state, waterDescriptor.descriptorSet, "water" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_MATERIAL_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
		waterDescriptor.imageOwners[ imageIndex ] = owners[ imageIndex ];
		waterDescriptor.imageViews[ imageIndex ] = views[ imageIndex ];
		imageInfos[ imageIndex ].sampler = samplers[ imageIndex ];
		imageInfos[ imageIndex ].imageView = views[ imageIndex ];
		imageInfos[ imageIndex ].imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ imageIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ imageIndex ].dstSet = waterDescriptor.descriptorSet;
		writes[ imageIndex ].dstBinding = imageIndex;
		writes[ imageIndex ].descriptorCount = 1;
		writes[ imageIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ imageIndex ].pImageInfo = &imageInfos[ imageIndex ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES,
		writes, 0, NULL );
	const int waterDescriptorIndex = state.materialDescriptors.Append( waterDescriptor );
	state.materialDescriptorHash.Add( descriptorHashKey, waterDescriptorIndex );
	descriptorSet = waterDescriptor.descriptorSet;
	return true;
}

bool GetVulkanHeatHazeDescriptor( sdVulkanBackendState& state,
	const materialStage_t& stage, idImage* bumpImage,
	VkDescriptorSet& descriptorSet ) {
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL || bumpImage == NULL ||
		state.currentRenderResource.view == VK_NULL_HANDLE ||
		state.currentRenderResource.sampler == VK_NULL_HANDLE ) {
		return false;
	}
	if ( !bumpImage->IsLoaded() ) {
		bumpImage->BindFragment();
	}
	idImage* fallbackImages[ 3 ] = { globalImages->blackImage,
		globalImages->blackImage, globalImages->blackCubeMapImage };
	const sdVulkanImageResource* bumpResource =
		FindVulkanImageResource( state, bumpImage );
	const sdVulkanImageResource* fallbackResources[ 3 ];
	if ( bumpResource == NULL ) {
		return false;
	}
	for ( int fallbackIndex = 0; fallbackIndex < 3; ++fallbackIndex ) {
		if ( !fallbackImages[ fallbackIndex ]->IsLoaded() ) {
			fallbackImages[ fallbackIndex ]->BindFragment();
		}
		fallbackResources[ fallbackIndex ] = FindVulkanImageResource( state,
			fallbackImages[ fallbackIndex ] );
		if ( fallbackResources[ fallbackIndex ] == NULL ) {
			return false;
		}
	}

	const void* owners[ VULKAN_MATERIAL_TEXTURES ] = {
		bumpImage, &state.currentRenderResource, fallbackImages[ 0 ],
		fallbackImages[ 1 ], fallbackImages[ 2 ]
	};
	VkImageView views[ VULKAN_MATERIAL_TEXTURES ] = {
		bumpResource->view, state.currentRenderResource.view,
		fallbackResources[ 0 ]->view, fallbackResources[ 1 ]->view,
		fallbackResources[ 2 ]->view
	};
	VkSampler samplers[ VULKAN_MATERIAL_TEXTURES ] = {
		bumpResource->sampler, state.currentRenderResource.sampler,
		fallbackResources[ 0 ]->sampler, fallbackResources[ 1 ]->sampler,
		fallbackResources[ 2 ]->sampler
	};
	const int descriptorHashKey = sdVulkanPointerHash::Hash(
		state.materialDescriptorHash, &stage );
	for ( int descriptorIndex = state.materialDescriptorHash.GetFirst(
		descriptorHashKey ); descriptorIndex != idHashIndex::NULL_INDEX;
		descriptorIndex = state.materialDescriptorHash.GetNext( descriptorIndex ) ) {
		if ( descriptorIndex < 0 || descriptorIndex >= state.materialDescriptors.Num() ) {
			continue;
		}
		sdVulkanMaterialDescriptor& cached =
			state.materialDescriptors[ descriptorIndex ];
		if ( cached.owner != &stage ) {
			continue;
		}
		bool matches = true;
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
			++imageIndex ) {
			if ( cached.imageOwners[ imageIndex ] != owners[ imageIndex ] ||
				cached.imageViews[ imageIndex ] != views[ imageIndex ] ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}

	sdVulkanMaterialDescriptor heatDescriptor;
	memset( &heatDescriptor, 0, sizeof( heatDescriptor ) );
	heatDescriptor.owner = &stage;
	heatDescriptor.lastUsedFrame = state.frameSerial;
	if ( !AllocateMaterialDescriptorSet( state, heatDescriptor.descriptorSet, "heat haze" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_MATERIAL_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
		++imageIndex ) {
		heatDescriptor.imageOwners[ imageIndex ] = owners[ imageIndex ];
		heatDescriptor.imageViews[ imageIndex ] = views[ imageIndex ];
		imageInfos[ imageIndex ].sampler = samplers[ imageIndex ];
		imageInfos[ imageIndex ].imageView = views[ imageIndex ];
		imageInfos[ imageIndex ].imageLayout =
			VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ imageIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ imageIndex ].dstSet = heatDescriptor.descriptorSet;
		writes[ imageIndex ].dstBinding = imageIndex;
		writes[ imageIndex ].descriptorCount = 1;
		writes[ imageIndex ].descriptorType =
			VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ imageIndex ].pImageInfo = &imageInfos[ imageIndex ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES,
		writes, 0, NULL );
	const int heatDescriptorIndex = state.materialDescriptors.Append( heatDescriptor );
	state.materialDescriptorHash.Add( descriptorHashKey, heatDescriptorIndex );
	descriptorSet = heatDescriptor.descriptorSet;
	return true;
}

// The rasterized lighting path snapshots the opaque base (albedo) into
// _currentRender before the ambient pass; MegaTexture terrain, whose albedo
// lives in the megatexture tiles rather than a stage texture, reads it back
// per pixel in interaction.frag.  Binding 0 is the snapshot, the rest neutral.
bool GetVulkanSceneAlbedoDescriptor( sdVulkanBackendState& state,
	VkDescriptorSet& descriptorSet ) {
	static const int sceneAlbedoKey = 0;	// identity only, never dereferenced
	const materialStage_t* const sceneAlbedoOwner =
		reinterpret_cast< const materialStage_t* >( &sceneAlbedoKey );
	descriptorSet = VK_NULL_HANDLE;
	if ( globalImages == NULL || state.currentRenderResource.view == VK_NULL_HANDLE ||
		state.currentRenderResource.sampler == VK_NULL_HANDLE ) {
		return false;
	}
	idImage* fallbackImages[ 4 ] = { globalImages->flatNormalMap, globalImages->blackImage,
		globalImages->blackImage, globalImages->blackCubeMapImage };
	const sdVulkanImageResource* fallbackResources[ 4 ];
	for ( int fallbackIndex = 0; fallbackIndex < 4; ++fallbackIndex ) {
		if ( fallbackImages[ fallbackIndex ] == NULL ) {
			return false;
		}
		if ( !fallbackImages[ fallbackIndex ]->IsLoaded() ) {
			fallbackImages[ fallbackIndex ]->BindFragment();
		}
	}
	// resolve after every BindFragment: it can reallocate state.imageResources
	for ( int fallbackIndex = 0; fallbackIndex < 4; ++fallbackIndex ) {
		fallbackResources[ fallbackIndex ] = FindVulkanImageResource( state,
			fallbackImages[ fallbackIndex ] );
		if ( fallbackResources[ fallbackIndex ] == NULL ) {
			return false;
		}
	}
	const void* owners[ VULKAN_MATERIAL_TEXTURES ] = {
		&state.currentRenderResource, fallbackImages[ 0 ], fallbackImages[ 1 ],
		fallbackImages[ 2 ], fallbackImages[ 3 ]
	};
	VkImageView views[ VULKAN_MATERIAL_TEXTURES ] = {
		state.currentRenderResource.view, fallbackResources[ 0 ]->view,
		fallbackResources[ 1 ]->view, fallbackResources[ 2 ]->view,
		fallbackResources[ 3 ]->view
	};
	VkSampler samplers[ VULKAN_MATERIAL_TEXTURES ] = {
		state.currentRenderResource.sampler, fallbackResources[ 0 ]->sampler,
		fallbackResources[ 1 ]->sampler, fallbackResources[ 2 ]->sampler,
		fallbackResources[ 3 ]->sampler
	};
	const int descriptorHashKey = sdVulkanPointerHash::Hash(
		state.materialDescriptorHash, sceneAlbedoOwner );
	for ( int descriptorIndex = state.materialDescriptorHash.GetFirst(
		descriptorHashKey ); descriptorIndex != idHashIndex::NULL_INDEX;
		descriptorIndex = state.materialDescriptorHash.GetNext( descriptorIndex ) ) {
		if ( descriptorIndex < 0 || descriptorIndex >= state.materialDescriptors.Num() ) {
			continue;
		}
		sdVulkanMaterialDescriptor& cached = state.materialDescriptors[ descriptorIndex ];
		if ( cached.owner != sceneAlbedoOwner ) {
			continue;
		}
		bool matches = true;
		for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
			if ( cached.imageOwners[ imageIndex ] != owners[ imageIndex ] ||
				cached.imageViews[ imageIndex ] != views[ imageIndex ] ) {
				matches = false;
				break;
			}
		}
		if ( matches ) {
			cached.lastUsedFrame = state.frameSerial;
			descriptorSet = cached.descriptorSet;
			return true;
		}
	}
	sdVulkanMaterialDescriptor albedoDescriptor;
	memset( &albedoDescriptor, 0, sizeof( albedoDescriptor ) );
	albedoDescriptor.owner = sceneAlbedoOwner;
	albedoDescriptor.lastUsedFrame = state.frameSerial;
	if ( !AllocateMaterialDescriptorSet( state, albedoDescriptor.descriptorSet, "scene albedo" ) ) {
		return false;
	}
	VkDescriptorImageInfo imageInfos[ VULKAN_MATERIAL_TEXTURES ];
	VkWriteDescriptorSet writes[ VULKAN_MATERIAL_TEXTURES ];
	memset( imageInfos, 0, sizeof( imageInfos ) );
	memset( writes, 0, sizeof( writes ) );
	for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES; ++imageIndex ) {
		albedoDescriptor.imageOwners[ imageIndex ] = owners[ imageIndex ];
		albedoDescriptor.imageViews[ imageIndex ] = views[ imageIndex ];
		imageInfos[ imageIndex ].sampler = samplers[ imageIndex ];
		imageInfos[ imageIndex ].imageView = views[ imageIndex ];
		imageInfos[ imageIndex ].imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		writes[ imageIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ imageIndex ].dstSet = albedoDescriptor.descriptorSet;
		writes[ imageIndex ].dstBinding = imageIndex;
		writes[ imageIndex ].descriptorCount = 1;
		writes[ imageIndex ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
		writes[ imageIndex ].pImageInfo = &imageInfos[ imageIndex ];
	}
	state.UpdateDescriptorSets( state.device, VULKAN_MATERIAL_TEXTURES, writes, 0, NULL );
	const int albedoDescriptorIndex = state.materialDescriptors.Append( albedoDescriptor );
	state.materialDescriptorHash.Add( descriptorHashKey, albedoDescriptorIndex );
	descriptorSet = albedoDescriptor.descriptorSet;
	return true;
}

// Restarts the frame's rendering on the swapchain image, keeping its colour,
// depth and stencil (after a copy or an offscreen pass ended it).
void BeginSwapchainRendering( sdVulkanBackendState& state, VkCommandBuffer commandBuffer ) {
	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = state.swapchainViews[ state.imageIndex ];
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = state.depthViews[ state.imageIndex ];
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingAttachmentInfo stencilAttachment;
	memset( &stencilAttachment, 0, sizeof( stencilAttachment ) );
	stencilAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	stencilAttachment.imageView = state.depthViews[ state.imageIndex ];
	stencilAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	stencilAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	stencilAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = state.extent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	renderingInfo.pDepthAttachment = &depthAttachment;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state.CmdBeginRendering( commandBuffer, &renderingInfo );
}

bool CopyCurrentRender( sdVulkanBackendState& state ) {
	if ( !state.frameActive || !state.swapchainTransferSource ||
		state.currentRenderResource.image == VK_NULL_HANDLE ||
		state.CmdCopyImage == NULL ) {
		return false;
	}
	sdVulkanFrame& frame = state.frames[ state.frameIndex ];
	state.CmdEndRendering( frame.commandBuffer );

	VkImageMemoryBarrier2 toTransfer[ 2 ];
	memset( toTransfer, 0, sizeof( toTransfer ) );
	toTransfer[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toTransfer[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toTransfer[ 0 ].srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	toTransfer[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	toTransfer[ 0 ].dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
	toTransfer[ 0 ].oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	toTransfer[ 0 ].newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
	toTransfer[ 0 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer[ 0 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer[ 0 ].image = state.swapchainImages[ state.imageIndex ];
	toTransfer[ 0 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toTransfer[ 0 ].subresourceRange.levelCount = 1;
	toTransfer[ 0 ].subresourceRange.layerCount = 1;
	toTransfer[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toTransfer[ 1 ].srcStageMask = state.currentRenderInitialized ?
		VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT : VK_PIPELINE_STAGE_2_NONE;
	toTransfer[ 1 ].srcAccessMask = state.currentRenderInitialized ?
		VK_ACCESS_2_SHADER_SAMPLED_READ_BIT : VK_ACCESS_2_NONE;
	toTransfer[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	toTransfer[ 1 ].dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	toTransfer[ 1 ].oldLayout = state.currentRenderInitialized ?
		VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	toTransfer[ 1 ].newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	toTransfer[ 1 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer[ 1 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer[ 1 ].image = state.currentRenderResource.image;
	toTransfer[ 1 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toTransfer[ 1 ].subresourceRange.levelCount = 1;
	toTransfer[ 1 ].subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 2;
	dependencyInfo.pImageMemoryBarriers = toTransfer;
	state.CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );

	VkImageCopy copyRegion;
	memset( &copyRegion, 0, sizeof( copyRegion ) );
	copyRegion.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	copyRegion.srcSubresource.layerCount = 1;
	copyRegion.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	copyRegion.dstSubresource.layerCount = 1;
	copyRegion.extent.width = state.extent.width;
	copyRegion.extent.height = state.extent.height;
	copyRegion.extent.depth = 1;
	state.CmdCopyImage( frame.commandBuffer,
		state.swapchainImages[ state.imageIndex ], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
		state.currentRenderResource.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
		1, &copyRegion );

	VkImageMemoryBarrier2 fromTransfer[ 2 ];
	memset( fromTransfer, 0, sizeof( fromTransfer ) );
	fromTransfer[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	fromTransfer[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	fromTransfer[ 0 ].srcAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
	fromTransfer[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	fromTransfer[ 0 ].dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	fromTransfer[ 0 ].oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
	fromTransfer[ 0 ].newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	fromTransfer[ 0 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromTransfer[ 0 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromTransfer[ 0 ].image = state.swapchainImages[ state.imageIndex ];
	fromTransfer[ 0 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	fromTransfer[ 0 ].subresourceRange.levelCount = 1;
	fromTransfer[ 0 ].subresourceRange.layerCount = 1;
	fromTransfer[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	fromTransfer[ 1 ].srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	fromTransfer[ 1 ].srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	fromTransfer[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT;
	fromTransfer[ 1 ].dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	fromTransfer[ 1 ].oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	fromTransfer[ 1 ].newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	fromTransfer[ 1 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromTransfer[ 1 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromTransfer[ 1 ].image = state.currentRenderResource.image;
	fromTransfer[ 1 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	fromTransfer[ 1 ].subresourceRange.levelCount = 1;
	fromTransfer[ 1 ].subresourceRange.layerCount = 1;
	dependencyInfo.pImageMemoryBarriers = fromTransfer;
	// the depth/stencil attachment is stored by the ended rendering and loaded
	// by the restarted one
	VkMemoryBarrier2 depthBarrier;
	memset( &depthBarrier, 0, sizeof( depthBarrier ) );
	depthBarrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
	depthBarrier.srcStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	depthBarrier.srcAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	depthBarrier.dstStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	depthBarrier.dstAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	dependencyInfo.memoryBarrierCount = 1;
	dependencyInfo.pMemoryBarriers = &depthBarrier;
	state.CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );

	BeginSwapchainRendering( state, frame.commandBuffer );
	state.currentRenderInitialized = true;
	return true;
}

// _postProcessBuffer_0 and _1 at a quarter of the screen, as
// RB_ARB2_SetupPostProcessingFrameBuffer sizes them, with a descriptor set per
// glow pass: the downsample reads _currentRender, the blurs read one buffer each,
// and the glow reads _currentRender and buffer 1.
bool EnsureGlowBuffers( sdVulkanBackendState& state ) {
	VkExtent2D size;
	size.width = Max( 1u, state.extent.width >> 2 );
	size.height = Max( 1u, state.extent.height >> 2 );
	if ( state.glowDescriptorSets[ 3 ] != VK_NULL_HANDLE &&
		state.glowBuffers[ 0 ].width == static_cast< int >( size.width ) &&
		state.glowBuffers[ 0 ].height == static_cast< int >( size.height ) ) {
		return true;
	}
	// the swapchain's recreation frees them, after the device is idle
	DestroyGlowBuffers( state );
	for ( int i = 0; i < 2; ++i ) {
		if ( !CreateCurrentRenderResource( state, VK_FORMAT_R8G8B8A8_UNORM, size,
			state.glowBuffers[ i ], VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT ) ) {
			DestroyGlowBuffers( state );
			return false;
		}
	}
	// binding 1 (blurImage) is read by the glow only; the buffer passes bind
	// _currentRender there, never the buffer they render to
	const sdVulkanImageResource* sources[ 4 ][ 2 ] = {
		{ &state.currentRenderResource, &state.currentRenderResource },
		{ &state.glowBuffers[ 1 ], &state.currentRenderResource },
		{ &state.glowBuffers[ 0 ], &state.currentRenderResource },
		{ &state.currentRenderResource, &state.glowBuffers[ 1 ] }
	};
	for ( int setIndex = 0; setIndex < 4; ++setIndex ) {
		VkDescriptorSetAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
		allocateInfo.descriptorPool = state.guiDescriptorPool;
		allocateInfo.descriptorSetCount = 1;
		allocateInfo.pSetLayouts = &state.guiDescriptorSetLayout;
		if ( !CheckVulkanResult( state.AllocateDescriptorSets( state.device,
			&allocateInfo, &state.glowDescriptorSets[ setIndex ] ),
			"vkAllocateDescriptorSets(glow)" ) ) {
			state.glowDescriptorSets[ setIndex ] = VK_NULL_HANDLE;
			DestroyGlowBuffers( state );
			return false;
		}
		// glow.frag reads bindings 0 and 1 only
		VkDescriptorImageInfo imageInfos[ 2 ];
		VkWriteDescriptorSet writes[ 2 ];
		memset( imageInfos, 0, sizeof( imageInfos ) );
		memset( writes, 0, sizeof( writes ) );
		for ( int binding = 0; binding < 2; ++binding ) {
			imageInfos[ binding ].sampler = sources[ setIndex ][ binding ]->sampler;
			imageInfos[ binding ].imageView = sources[ setIndex ][ binding ]->view;
			imageInfos[ binding ].imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
			writes[ binding ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
			writes[ binding ].dstSet = state.glowDescriptorSets[ setIndex ];
			writes[ binding ].dstBinding = binding;
			writes[ binding ].descriptorCount = 1;
			writes[ binding ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
			writes[ binding ].pImageInfo = &imageInfos[ binding ];
		}
		state.UpdateDescriptorSets( state.device, 2, writes, 0, NULL );
	}
	return true;
}

// One full-target triangle of glow.frag with these push constants.
void DrawGlowTriangle( sdVulkanBackendState& state, VkCommandBuffer commandBuffer,
	VkPipeline pipeline, VkDescriptorSet descriptorSet, uint32_t width, uint32_t height,
	const float* pushConstants, int pushConstantFloats ) {
	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = 0.0f;
	viewport.width = static_cast< float >( width );
	viewport.height = static_cast< float >( height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state.CmdSetViewport( commandBuffer, 0, 1, &viewport );
	VkRect2D scissor;
	memset( &scissor, 0, sizeof( scissor ) );
	scissor.extent.width = width;
	scissor.extent.height = height;
	state.CmdSetScissor( commandBuffer, 0, 1, &scissor );
	state.CmdBindPipeline( commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
	state.CmdBindDescriptorSets( commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		state.guiPipelineLayout, 0, 1, &descriptorSet, 0, NULL );
	state.CmdPushConstants( commandBuffer, state.guiPipelineLayout,
		VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
		sizeof( float ) * pushConstantFloats, pushConstants );
	state.CmdDraw( commandBuffer, 3, 1, 0, 0 );
}

// A glow pass into one of the quarter-size buffers, which is then left for the
// next pass to sample.
void DrawGlowBufferPass( sdVulkanBackendState& state, VkCommandBuffer commandBuffer,
	int target, VkDescriptorSet descriptorSet, const float* pushConstants,
	int pushConstantFloats ) {
	sdVulkanImageResource& buffer = state.glowBuffers[ target ];
	VkImageMemoryBarrier2 barrier;
	memset( &barrier, 0, sizeof( barrier ) );
	barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barrier.srcStageMask = state.glowBufferInitialized[ target ] ?
		VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT : VK_PIPELINE_STAGE_2_NONE;
	barrier.srcAccessMask = state.glowBufferInitialized[ target ] ?
		VK_ACCESS_2_SHADER_SAMPLED_READ_BIT : VK_ACCESS_2_NONE;
	barrier.dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	barrier.dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	barrier.oldLayout = state.glowBufferInitialized[ target ] ?
		VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	barrier.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.image = buffer.image;
	barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	barrier.subresourceRange.levelCount = 1;
	barrier.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &barrier;
	state.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = buffer.view;
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent.width = buffer.width;
	renderingInfo.renderArea.extent.height = buffer.height;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	state.CmdBeginRendering( commandBuffer, &renderingInfo );
	DrawGlowTriangle( state, commandBuffer, state.glowBufferPipeline, descriptorSet,
		buffer.width, buffer.height, pushConstants, pushConstantFloats );
	state.CmdEndRendering( commandBuffer );

	barrier.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	barrier.srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	barrier.dstStageMask = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT;
	barrier.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	barrier.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	state.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
	state.glowBufferInitialized[ target ] = true;
}

} // namespace

sdVulkanBackend vulkanBackend;

sdVulkanBackend::sdVulkanBackend() : state( NULL ) {
}

sdVulkanBackend::~sdVulkanBackend() {
	Shutdown();
}

bool sdVulkanBackend::Init( void* nativeWindow, int width, int height ) {
	Shutdown();
	if ( nativeWindow == NULL ) {
		common->Warning( "Vulkan initialization requires a valid window handle" );
		return false;
	}
	state = new sdVulkanBackendState;
	state->nativeWindow = nativeWindow;

	common->Printf( "---------- Vulkan Init ----------\n" );
	if ( !LoadVulkanLibrary( *state ) ||
		!CreateVulkanInstance( *state ) ||
		!CreateVulkanSurface( *state ) ||
		!SelectPhysicalDevice( *state ) ) {
		Shutdown();
		return false;
	}
	sdRayTracingDeviceConfiguration rayTracingConfiguration;
	idStr rayTracingReason;
	bool rayTracingAvailable = R_RayTracingConfigureDevice( state->GetPhysicalDeviceFeatures2,
		state->EnumerateDeviceExtensionProperties, state->physicalDevice,
		rayTracingConfiguration, rayTracingReason );
	if ( !rayTracingAvailable ) {
		common->Printf( "Vulkan ray tracing unavailable: %s (falling back to rasterization)\n", rayTracingReason.c_str() );
	}
	if ( rayTracingAvailable && !SurfaceSupportsRayTracingImages( *state, state->physicalDevice ) ) {
		common->Printf( "Vulkan surface does not support ray tracing images (falling back to rasterization)\n" );
		rayTracingAvailable = false;
	}
	// Buffers may request the ray-tracing usage bits, and the swapchain storage
	// images, only when the device was created with bufferDeviceAddress and the
	// acceleration-structure extension.
	state->rayTracingDeviceEnabled = rayTracingAvailable;
	if ( !CreateLogicalDevice( *state, rayTracingConfiguration, rayTracingAvailable ) ||
		!CreateSwapchain( *state, width, height ) ||
		!CreateGuiResources( *state ) ||
		!CreateFrames( *state ) ) {
		Shutdown();
		return false;
	}
	if ( rayTracingAvailable ) {
		sdRayTracingVulkanContext rayTracingContext;
		memset( &rayTracingContext, 0, sizeof( rayTracingContext ) );
		rayTracingContext.physicalDevice = state->physicalDevice;
		rayTracingContext.device = state->device;
		rayTracingContext.memoryProperties = state->memoryProperties;
		rayTracingContext.colorFormat = state->swapchainFormat;
		rayTracingContext.depthFormat = state->depthStencilFormat != VK_FORMAT_UNDEFINED ?
			state->depthStencilFormat : VK_FORMAT_D32_SFLOAT_S8_UINT;
		rayTracingContext.frameCount = NUM_VULKAN_FRAMES;
		rayTracingContext.GetDeviceProcAddr = state->GetDeviceProcAddr;
		if ( !R_RayTracingInit( rayTracingContext ) ) {
			common->Printf( "Vulkan ray tracing initialization failed (falling back to rasterization)\n" );
		}
	}
	common->Printf( "Vulkan device: %s\n", state->deviceName );
	common->Printf( "---------------------------------\n" );
	return true;
}

extern unsigned int r_shadowLightViews;
extern unsigned int r_shadowLightViewsWithCasters;
void R_PrintInteractionCacheMetrics();	// tr_light.cpp

void sdVulkanBackend::Shutdown() {
	if ( state == NULL ) {
		return;
	}
	common->Printf( "Vulkan frames presented: %u, world draws: %u, GUI draws: %u, shadow draws: %u, light interactions: %u\n",
		state->framesPresented, state->worldDrawCalls, state->guiDrawCalls,
		state->worldShadowDrawCalls, state->worldInteractionDrawCalls );
	R_PrintInteractionCacheMetrics();
	common->Printf( "Vulkan shadow lights: %u shadow-casting light views, %u with shadow casters\n",
		r_shadowLightViews, r_shadowLightViewsWithCasters );
	common->Printf( "Vulkan memory allocations: %u live, %u peak (device limit %u); "
		"%d buffer resources in %d shared blocks, %d images live\n",
		r_vkMemoryAllocations, r_vkMemoryAllocationPeak, state->maxMemoryAllocationCount,
		state->bufferResources.Num(), LiveBufferMemoryBlocks( *state ),
		state->imageResources.Num() );
	common->Printf( "Vulkan world coverage: %u attempts, %u recorded views, %u surfaces, %u cache-ready, "
		"%u material-ready, %u resource-ready\n", state->worldViewAttempts, state->worldViews,
		state->worldSurfaceCandidates, state->worldCacheReady,
		state->worldMaterialReady, state->worldResourceReady );
	common->Printf( "Vulkan world skips: %u geometry, %u cache, %u material, %u resource; "
		"special draws: %u depth, %u MegaTexture, %u CPU-skinned, %u GPU-skinned, %u sky, "
		"%u extra stages, %u water, %u stuff (%u water descriptor misses)\n",
		state->worldMissingGeometry, state->worldMissingCache,
		state->worldMissingMaterial, state->worldMissingResource,
		state->worldDepthDrawCalls,
		state->worldMegaDrawCalls, state->worldSkinnedDrawCalls,
		state->worldGpuSkinnedDrawCalls,
		state->worldSkyDrawCalls, state->worldStageDrawCalls,
		state->worldWaterDrawCalls, state->worldStuffDrawCalls,
		state->worldWaterDescriptorMisses );
	if ( state->device != VK_NULL_HANDLE && state->DeviceWaitIdle != NULL ) {
		state->DeviceWaitIdle( state->device );
	}
	R_RayTracingShutdown();
	DestroyToolWindows( *state );
	DestroyAllToolRenderTargets( *state );
	DestroyAllImageResources( *state );
	ClearPendingBufferUploads( *state );
	DestroyAllBufferResources( *state );
	DestroyBufferMemoryBlocks( *state );
	DestroyGuiResources( *state );
	DestroyFrames( *state );
	DestroySwapchain( *state );
	if ( state->device != VK_NULL_HANDLE && state->DestroyDevice != NULL ) {
		state->DestroyDevice( state->device, NULL );
	}
	state->device = VK_NULL_HANDLE;
	if ( state->surface != VK_NULL_HANDLE && state->DestroySurfaceKHR != NULL ) {
		state->DestroySurfaceKHR( state->instance, state->surface, NULL );
	}
	state->surface = VK_NULL_HANDLE;
	if ( state->debugMessenger != VK_NULL_HANDLE &&
		state->DestroyDebugUtilsMessengerEXT != NULL ) {
		state->DestroyDebugUtilsMessengerEXT( state->instance,
			state->debugMessenger, NULL );
	}
	state->debugMessenger = VK_NULL_HANDLE;
	if ( state->instance != VK_NULL_HANDLE && state->DestroyInstance != NULL ) {
		state->DestroyInstance( state->instance, NULL );
	}
	state->instance = VK_NULL_HANDLE;
	if ( state->vulkanLibrary != NULL ) {
#if defined( _WIN32 )
		FreeLibrary( static_cast< HMODULE >( state->vulkanLibrary ) );
#else
		SDL_Vulkan_UnloadLibrary();
#endif
		state->vulkanLibrary = NULL;
	}
	delete state;
	state = NULL;
}

bool sdVulkanBackend::BeginFrame( int width, int height ) {
	if ( state == NULL || state->device == VK_NULL_HANDLE || state->frameActive ||
		state->toolFrameActive ) {
		return false;
	}
#if defined( _WIN32 )
	RECT clientRect;
	if ( GetClientRect( static_cast< HWND >( state->nativeWindow ), &clientRect ) ) {
		width = clientRect.right - clientRect.left;
		height = clientRect.bottom - clientRect.top;
	}
#else
	int pxWidth = 0, pxHeight = 0;
	if ( SDL_GetWindowSizeInPixels( static_cast< SDL_Window* >( state->nativeWindow ), &pxWidth, &pxHeight ) ) {
		if ( pxWidth > 0 && pxHeight > 0 ) {
			width = pxWidth;
			height = pxHeight;
		}
	}
#endif
	if ( width <= 0 || height <= 0 ) {
		return false;
	}
	const int swapInterval = cvarSystem->GetCVarInteger( "r_swapInterval" );
	if ( state->extent.width != static_cast< unsigned int >( width ) ||
		state->extent.height != static_cast< unsigned int >( height ) ||
		state->swapInterval != swapInterval ) {
		state->swapchainDirty = true;
	}
	if ( state->swapchainDirty ) {
		RENDER_METRIC_SCOPE( "Vulkan recreate swapchain" );
		state->DeviceWaitIdle( state->device );
		if ( !CreateSwapchain( *state, width, height ) ) {
			return false;
		}
	}

	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	{
		RENDER_METRIC_SCOPE( "Vulkan wait for frame fence" );
		if ( r_vkLogFrameSync.GetBool() ) {
			pendingFenceBeginUsec = static_cast< long long >( Sys_GetClockTicks() * 1000000.0 / Sys_ClockTicksPerSecond() );
		}
		const uint64_t fenceStart = SyncNowUsec();
		if ( !CheckVulkanResult( state->WaitForFences( state->device, 1,
			&frame.fence, VK_TRUE, UINT64_MAX ), "vkWaitForFences" ) ) {
			return false;
		}
		pendingFenceUsec = SyncNowUsec() - fenceStart + pendingAheadFenceUsec;
		pendingAheadFenceUsec = 0;
	}
	{
		RENDER_METRIC_SCOPE( "Vulkan retire frame resources" );
		DestroyFrameRetiredResources( *state, state->frameIndex );
	}
	{
		RENDER_METRIC_SCOPE( "Ray tracing BeginFrame" );
		R_RayTracingBeginFrame( state->frameIndex );
	}
	frame.guiVertexOffset = 0;
	frame.guiIndexOffset = 0;
	frame.guiVertexOverflowWarned = false;
	frame.guiIndexOverflowWarned = false;
	frame.imageUploadOffset = 0;
	frame.imageUploadRecorded = false;
	frame.imageUploadOverflowWarned = false;
	frame.bufferUploadOffset = 0;
	frame.bufferUploadOverflowWarned = false;
	frame.jointOffset = 0;
	frame.jointOverflowWarned = false;
	VkResult acquireResult;
	{
		RENDER_METRIC_SCOPE( "Vulkan acquire swapchain image" );
		const uint64_t acquireStart = SyncNowUsec();
		acquireResult = state->AcquireNextImageKHR( state->device,
			state->swapchain, UINT64_MAX, frame.imageAvailable, VK_NULL_HANDLE,
			&state->imageIndex );
		pendingAcquireUsec = SyncNowUsec() - acquireStart;
		if ( acquireResult == VK_ERROR_OUT_OF_DATE_KHR ) {
			state->DeviceWaitIdle( state->device );
			if ( !CreateSwapchain( *state, width, height ) ) {
				return false;
			}
			const uint64_t reacquireStart = SyncNowUsec();
			acquireResult = state->AcquireNextImageKHR( state->device, state->swapchain,
				UINT64_MAX, frame.imageAvailable, VK_NULL_HANDLE, &state->imageIndex );
			pendingAcquireUsec += SyncNowUsec() - reacquireStart;
		}
	}
	if ( acquireResult != VK_SUCCESS && acquireResult != VK_SUBOPTIMAL_KHR ) {
		CheckVulkanResult( acquireResult, "vkAcquireNextImageKHR" );
		return false;
	}
	if ( acquireResult == VK_SUBOPTIMAL_KHR ) {
		state->swapchainDirty = true;
	}

	{
		RENDER_METRIC_SCOPE( "Vulkan reset command frame" );
		const bool fenceReset = CheckVulkanResult( state->ResetFences( state->device, 1, &frame.fence ), "vkResetFences" );
		const uint64_t poolStart = SyncNowUsec();
		const bool poolReset = fenceReset && ( frame.commandPoolReset || CheckVulkanResult( state->ResetCommandPool(
				state->device, frame.commandPool, 0 ), "vkResetCommandPool" ) );
		pendingPoolUsec = SyncNowUsec() - poolStart + ( frame.commandPoolReset ? pendingAheadPoolUsec : 0 );
		frame.commandPoolReset = false;
		pendingAheadPoolUsec = 0;
		if ( !fenceReset || !poolReset ) {
			RestoreSignaledFence( *state, frame );
			return false;
		}
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		if ( !CheckVulkanResult( state->BeginCommandBuffer( frame.commandBuffer,
			&beginInfo ), "vkBeginCommandBuffer" ) ) {
			RestoreSignaledFence( *state, frame );
			return false;
		}
		if ( !CheckVulkanResult( state->BeginCommandBuffer(
			frame.imageUploadCommandBuffer, &beginInfo ),
			"vkBeginCommandBuffer(image uploads)" ) ) {
			RestoreSignaledFence( *state, frame );
			return false;
		}
	}

	RENDER_METRIC_SCOPE( "Vulkan begin render pass" );
	VkImageMemoryBarrier2 toColor;
	memset( &toColor, 0, sizeof( toColor ) );
	toColor.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	// The acquire semaphore is waited on at COLOR_ATTACHMENT_OUTPUT; starting the
	// transition's scope at the same stage orders it after the presentation
	// engine has released the image (sync validation: WRITE_AFTER_READ against
	// vkAcquireNextImageKHR with srcStageMask NONE).
	toColor.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toColor.srcAccessMask = VK_ACCESS_2_NONE;
	toColor.dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toColor.dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	toColor.oldLayout = state->imageInitialized[ state->imageIndex ] ?
		VK_IMAGE_LAYOUT_PRESENT_SRC_KHR : VK_IMAGE_LAYOUT_UNDEFINED;
	toColor.newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	toColor.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toColor.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toColor.image = state->swapchainImages[ state->imageIndex ];
	toColor.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toColor.subresourceRange.levelCount = 1;
	toColor.subresourceRange.layerCount = 1;
	VkImageMemoryBarrier2 depthBarrier;
	memset( &depthBarrier, 0, sizeof( depthBarrier ) );
	depthBarrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	depthBarrier.srcStageMask = state->depthInitialized[ state->imageIndex ] ?
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT : VK_PIPELINE_STAGE_2_NONE;
	depthBarrier.srcAccessMask = state->depthInitialized[ state->imageIndex ] ?
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT : VK_ACCESS_2_NONE;
	depthBarrier.dstStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	depthBarrier.dstAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	depthBarrier.oldLayout = state->depthInitialized[ state->imageIndex ] ?
		VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	depthBarrier.newLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthBarrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	depthBarrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	depthBarrier.image = state->depthImages[ state->imageIndex ];
	depthBarrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT |
		VK_IMAGE_ASPECT_STENCIL_BIT;
	depthBarrier.subresourceRange.levelCount = 1;
	depthBarrier.subresourceRange.layerCount = 1;
	VkImageMemoryBarrier2 frameBarriers[ 2 ] = { toColor, depthBarrier };
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 2;
	dependencyInfo.pImageMemoryBarriers = frameBarriers;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );

	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = state->swapchainViews[ state->imageIndex ];
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	colorAttachment.clearValue.color.float32[ 0 ] = 0.04f;
	colorAttachment.clearValue.color.float32[ 1 ] = 0.05f;
	colorAttachment.clearValue.color.float32[ 2 ] = 0.07f;
	colorAttachment.clearValue.color.float32[ 3 ] = 1.0f;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = state->extent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = state->depthViews[ state->imageIndex ];
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	depthAttachment.clearValue.depthStencil.depth = 1.0f;
	renderingInfo.pDepthAttachment = &depthAttachment;
	VkRenderingAttachmentInfo stencilAttachment;
	memset( &stencilAttachment, 0, sizeof( stencilAttachment ) );
	stencilAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	stencilAttachment.imageView = state->depthViews[ state->imageIndex ];
	stencilAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	stencilAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	stencilAttachment.storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
	stencilAttachment.clearValue.depthStencil.stencil = 0;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state->CmdBeginRendering( frame.commandBuffer, &renderingInfo );

	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( state->extent.height );
	viewport.width = static_cast< float >( state->extent.width );
	viewport.height = -static_cast< float >( state->extent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );
	SetVulkanPolygonOffset( *state, frame.commandBuffer, NULL, NULL );
	VkRect2D scissor;
	memset( &scissor, 0, sizeof( scissor ) );
	scissor.extent = state->extent;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &scissor );
	state->frameActive = true;
	state->glowAtmosphere = NULL;
	// uploads made since the last frame, ahead of this frame's draws
	FlushPendingBufferUploadsIntoFrame( *state );
	return true;
}

// Waits for the frame that copied the swapchain image, then writes it as a
// top-left-origin 32-bit TGA (BGRA, alpha forced opaque) and frees the buffer.
static void WriteVulkanScreenshot( sdVulkanBackendState& state, VkFence fence ) {
	const uint32_t width = state.screenshotWidth;
	const uint32_t height = state.screenshotHeight;
	void* mapped = NULL;
	if ( CheckVulkanResult( state.WaitForFences( state.device, 1, &fence, VK_TRUE, UINT64_MAX ),
			"vkWaitForFences(screenshot)" ) &&
		CheckVulkanResult( state.MapMemory( state.device, state.screenshotMemory, 0, VK_WHOLE_SIZE,
			0, &mapped ), "vkMapMemory(screenshot)" ) ) {
		const bool rgbOrder = state.screenshotFormat == VK_FORMAT_R8G8B8A8_UNORM ||
			state.screenshotFormat == VK_FORMAT_R8G8B8A8_SRGB;
		const size_t pixelBytes = static_cast< size_t >( width ) * height * 4;
		byte* file = static_cast< byte* >( Mem_Alloc( pixelBytes + 18 ) );
		memset( file, 0, 18 );
		file[ 2 ] = 2;		// uncompressed true-colour
		file[ 12 ] = width & 255;
		file[ 13 ] = ( width >> 8 ) & 255;
		file[ 14 ] = height & 255;
		file[ 15 ] = ( height >> 8 ) & 255;
		file[ 16 ] = 32;
		file[ 17 ] = 0x28;	// 8 alpha bits, top-left origin
		const byte* source = static_cast< const byte* >( mapped );
		byte* dest = file + 18;
		for ( size_t i = 0; i < pixelBytes; i += 4 ) {
			dest[ i + 0 ] = source[ i + ( rgbOrder ? 2 : 0 ) ];
			dest[ i + 1 ] = source[ i + 1 ];
			dest[ i + 2 ] = source[ i + ( rgbOrder ? 0 : 2 ) ];
			dest[ i + 3 ] = 255;
		}
		state.UnmapMemory( state.device, state.screenshotMemory );
		// a raw path, outside the filesystem's search paths: this is a NOCHEAT
		// developer and test tool, not something a server or map can set
		FILE* out = fopen( state.screenshotPath.c_str(), "wb" );
		if ( out != NULL ) {
			fwrite( file, 1, pixelBytes + 18, out );
			fclose( out );
			common->Printf( "r_vkScreenshot: wrote %ux%u to %s\n", width, height,
				state.screenshotPath.c_str() );
		} else {
			common->Warning( "r_vkScreenshot: could not open '%s'", state.screenshotPath.c_str() );
		}
		Mem_Free( file );
	}
	state.DestroyBuffer( state.device, state.screenshotBuffer, NULL );
	state.FreeMemory( state.device, state.screenshotMemory, NULL );
	state.screenshotBuffer = VK_NULL_HANDLE;
	state.screenshotMemory = VK_NULL_HANDLE;
}

// A recorded screenshot whose frame was never submitted: free its buffer, or it
// would block every later r_vkScreenshot.
static void DiscardVulkanScreenshot( sdVulkanBackendState& state ) {
	if ( state.screenshotBuffer != VK_NULL_HANDLE ) {
		state.DestroyBuffer( state.device, state.screenshotBuffer, NULL );
		state.screenshotBuffer = VK_NULL_HANDLE;
	}
	if ( state.screenshotMemory != VK_NULL_HANDLE ) {
		state.FreeMemory( state.device, state.screenshotMemory, NULL );
		state.screenshotMemory = VK_NULL_HANDLE;
	}
}

void sdVulkanBackend::EndFrame( bool ) {
	if ( state == NULL || !state->frameActive ) {
		return;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	bool screenshotRecorded = false;
	{
		RENDER_METRIC_SCOPE( "Vulkan finish command buffer" );
		state->CmdEndRendering( frame.commandBuffer );

	// r_vkScreenshot: copy the finished frame to a host buffer before presenting it.
	if ( r_vkScreenshot.GetString()[ 0 ] != '\0' && state->screenshotBuffer == VK_NULL_HANDLE ) {
		state->screenshotPath = r_vkScreenshot.GetString();
		r_vkScreenshot.SetString( "" );
		const VkDeviceSize bytes = static_cast< VkDeviceSize >( state->extent.width ) *
			state->extent.height * 4;
		// the buffer is sized for 4 bytes a pixel and written as 8-bit BGRA
		const bool eightBitSwapchain = state->swapchainFormat == VK_FORMAT_B8G8R8A8_UNORM ||
			state->swapchainFormat == VK_FORMAT_B8G8R8A8_SRGB ||
			state->swapchainFormat == VK_FORMAT_R8G8B8A8_UNORM ||
			state->swapchainFormat == VK_FORMAT_R8G8B8A8_SRGB;
		if ( !state->swapchainTransferSource || state->CmdCopyImageToBuffer == NULL ) {
			common->Warning( "r_vkScreenshot: the swapchain cannot be read back on this surface" );
		} else if ( !eightBitSwapchain ) {
			common->Warning( "r_vkScreenshot: swapchain format %d is not 8-bit RGBA/BGRA; not captured",
				static_cast< int >( state->swapchainFormat ) );
		} else if ( CreateBufferAllocation( *state, bytes, VK_BUFFER_USAGE_TRANSFER_DST_BIT,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			state->screenshotBuffer, state->screenshotMemory ) ) {
			VkImageMemoryBarrier2 toTransfer;
			memset( &toTransfer, 0, sizeof( toTransfer ) );
			toTransfer.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
			toTransfer.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
			toTransfer.srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
			toTransfer.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
			toTransfer.dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
			toTransfer.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
			toTransfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
			toTransfer.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toTransfer.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toTransfer.image = state->swapchainImages[ state->imageIndex ];
			toTransfer.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			toTransfer.subresourceRange.levelCount = 1;
			toTransfer.subresourceRange.layerCount = 1;
			VkDependencyInfo transferDependency;
			memset( &transferDependency, 0, sizeof( transferDependency ) );
			transferDependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
			transferDependency.imageMemoryBarrierCount = 1;
			transferDependency.pImageMemoryBarriers = &toTransfer;
			state->CmdPipelineBarrier2( frame.commandBuffer, &transferDependency );
			VkBufferImageCopy region;
			memset( &region, 0, sizeof( region ) );
			region.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			region.imageSubresource.layerCount = 1;
			region.imageExtent.width = state->extent.width;
			region.imageExtent.height = state->extent.height;
			region.imageExtent.depth = 1;
			state->CmdCopyImageToBuffer( frame.commandBuffer,
				state->swapchainImages[ state->imageIndex ], VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
				state->screenshotBuffer, 1, &region );
			// the fence alone does not make transfer writes visible to the host
			VkBufferMemoryBarrier2 toHost;
			memset( &toHost, 0, sizeof( toHost ) );
			toHost.sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER_2;
			toHost.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
			toHost.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
			toHost.dstStageMask = VK_PIPELINE_STAGE_2_HOST_BIT;
			toHost.dstAccessMask = VK_ACCESS_2_HOST_READ_BIT;
			toHost.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toHost.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toHost.buffer = state->screenshotBuffer;
			toHost.size = VK_WHOLE_SIZE;
			VkDependencyInfo hostDependency;
			memset( &hostDependency, 0, sizeof( hostDependency ) );
			hostDependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
			hostDependency.bufferMemoryBarrierCount = 1;
			hostDependency.pBufferMemoryBarriers = &toHost;
			state->CmdPipelineBarrier2( frame.commandBuffer, &hostDependency );
			state->screenshotWidth = state->extent.width;
			state->screenshotHeight = state->extent.height;
			state->screenshotFormat = state->swapchainFormat;
		}
	}
	screenshotRecorded = state->screenshotBuffer != VK_NULL_HANDLE;

	VkImageMemoryBarrier2 toPresent;
	memset( &toPresent, 0, sizeof( toPresent ) );
	toPresent.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toPresent.srcStageMask = screenshotRecorded ? VK_PIPELINE_STAGE_2_TRANSFER_BIT :
		VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toPresent.srcAccessMask = screenshotRecorded ? VK_ACCESS_2_TRANSFER_READ_BIT :
		VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	toPresent.dstStageMask = VK_PIPELINE_STAGE_2_NONE;
	toPresent.dstAccessMask = VK_ACCESS_2_NONE;
	toPresent.oldLayout = screenshotRecorded ? VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL :
		VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	toPresent.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
	toPresent.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toPresent.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toPresent.image = state->swapchainImages[ state->imageIndex ];
	toPresent.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toPresent.subresourceRange.levelCount = 1;
	toPresent.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &toPresent;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );
		if ( !CheckVulkanResult( state->EndCommandBuffer( frame.commandBuffer ),
			"vkEndCommandBuffer" ) ) {
			RestoreSignaledFence( *state, frame );
			DiscardVulkanScreenshot( *state );
			state->frameActive = false;
			return;
		}
		if ( !CheckVulkanResult( state->EndCommandBuffer(
			frame.imageUploadCommandBuffer ),
			"vkEndCommandBuffer(image uploads)" ) ) {
			RestoreSignaledFence( *state, frame );
			DiscardVulkanScreenshot( *state );
			state->frameActive = false;
			return;
		}
	}

	VkSemaphoreSubmitInfo waitInfo;
	memset( &waitInfo, 0, sizeof( waitInfo ) );
	waitInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
	waitInfo.semaphore = frame.imageAvailable;
	waitInfo.stageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	VkCommandBufferSubmitInfo commandInfos[ 2 ];
	memset( commandInfos, 0, sizeof( commandInfos ) );
	unsigned int commandInfoCount = 0;
	if ( frame.imageUploadRecorded ) {
		commandInfos[ commandInfoCount ].sType =
			VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
		commandInfos[ commandInfoCount ].commandBuffer =
			frame.imageUploadCommandBuffer;
		++commandInfoCount;
	}
	commandInfos[ commandInfoCount ].sType =
		VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
	commandInfos[ commandInfoCount ].commandBuffer = frame.commandBuffer;
	++commandInfoCount;
	VkSemaphoreSubmitInfo signalInfo;
	memset( &signalInfo, 0, sizeof( signalInfo ) );
	signalInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
	signalInfo.semaphore = state->presentSemaphores[ state->imageIndex ];
	signalInfo.stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
	VkSubmitInfo2 submitInfo;
	memset( &submitInfo, 0, sizeof( submitInfo ) );
	submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
	submitInfo.waitSemaphoreInfoCount = 1;
	submitInfo.pWaitSemaphoreInfos = &waitInfo;
	submitInfo.commandBufferInfoCount = commandInfoCount;
	submitInfo.pCommandBufferInfos = commandInfos;
	submitInfo.signalSemaphoreInfoCount = 1;
	submitInfo.pSignalSemaphoreInfos = &signalInfo;
	{
		RENDER_METRIC_SCOPE( "Vulkan queue submit" );
		const uint64_t submitStart = SyncNowUsec();
		if ( !CheckVulkanResult( state->QueueSubmit2( state->graphicsQueue, 1,
			&submitInfo, frame.fence ), "vkQueueSubmit2" ) ) {
			RestoreSignaledFence( *state, frame );
			DiscardVulkanScreenshot( *state );
			state->frameActive = false;
			return;
		}
		pendingSubmitUsec = SyncNowUsec() - submitStart;
	}
	state->imageInitialized[ state->imageIndex ] = 1;
	state->depthInitialized[ state->imageIndex ] = 1;
	if ( screenshotRecorded ) {
		WriteVulkanScreenshot( *state, frame.fence );
	}

	VkPresentInfoKHR presentInfo;
	memset( &presentInfo, 0, sizeof( presentInfo ) );
	presentInfo.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;
	presentInfo.waitSemaphoreCount = 1;
	presentInfo.pWaitSemaphores = &state->presentSemaphores[ state->imageIndex ];
	presentInfo.swapchainCount = 1;
	presentInfo.pSwapchains = &state->swapchain;
	presentInfo.pImageIndices = &state->imageIndex;
	VkResult presentResult;
	{
		RENDER_METRIC_SCOPE( "Vulkan present" );
		const uint64_t presentStart = SyncNowUsec();
		presentResult = state->QueuePresentKHR( state->graphicsQueue, &presentInfo );
		const uint64_t presentUsec = SyncNowUsec() - presentStart;
		syncPresentUsec += presentUsec;
		if ( r_vkLogFrameSync.GetBool() ) {
			common->Printf( "FRAME_SYNC: us=%lld fence_us=%llu acquire_us=%llu pool_us=%llu submit_us=%llu present_us=%llu\n",
				pendingFenceBeginUsec, static_cast< unsigned long long >( pendingFenceUsec ),
				static_cast< unsigned long long >( pendingAcquireUsec ),
				static_cast< unsigned long long >( pendingPoolUsec ),
				static_cast< unsigned long long >( pendingSubmitUsec ),
				static_cast< unsigned long long >( presentUsec ) );
		}
		syncFenceUsec += pendingFenceUsec;
		syncAcquireUsec += pendingAcquireUsec;
		syncSubmitUsec += pendingSubmitUsec;
		syncFrames++;
	}
	if ( presentResult == VK_ERROR_OUT_OF_DATE_KHR || presentResult == VK_SUBOPTIMAL_KHR ) {
		state->swapchainDirty = true;
	} else {
		if ( CheckVulkanResult( presentResult, "vkQueuePresentKHR" ) ) {
			state->framesPresented++;
		}
	}
	state->frameActive = false;
	state->frameIndex = ( state->frameIndex + 1 ) % NUM_VULKAN_FRAMES;
	state->frameSerial++;

	// r_vkPrepareAhead: the next frame's fence wait and command-pool reset happen now, before
	// the next frame samples input and ClientUpdateView computes its fraction, not in BeginFrame
	// between that fraction and the draw. The fence is left signaled (BeginFrame resets it), so a
	// frame that never begins leaves nothing to restore.
	if ( r_vkPrepareAhead.GetBool() ) {
		sdVulkanFrame& next = state->frames[ state->frameIndex ];
		RENDER_METRIC_SCOPE( "Vulkan prepare next frame" );
		const uint64_t aheadStart = SyncNowUsec();
		if ( CheckVulkanResult( state->WaitForFences( state->device, 1, &next.fence, VK_TRUE, UINT64_MAX ),
				"vkWaitForFences(ahead)" ) ) {
			const uint64_t poolStart = SyncNowUsec();
			pendingAheadFenceUsec = poolStart - aheadStart;
			next.commandPoolReset = CheckVulkanResult( state->ResetCommandPool( state->device,
				next.commandPool, 0 ), "vkResetCommandPool(ahead)" );
			pendingAheadPoolUsec = SyncNowUsec() - poolStart;
		}
	}
}

bool sdVulkanBackend::BeginToolWindow( void* nativeWindow, int width, int height,
	const float clearColor[ 4 ] ) {
#if !defined( _WIN32 )
	return false;
#else
	if ( state == NULL || state->device == VK_NULL_HANDLE || nativeWindow == NULL ||
		state->frameActive || state->toolFrameActive ) {
		return false;
	}
	// tool windows draw outside the main frame: fill the buffers they may use now
	FlushPendingBufferUploadsNow( *state );
	HWND window = static_cast< HWND >( nativeWindow );
	RECT clientRect;
	if ( GetClientRect( window, &clientRect ) ) {
		width = clientRect.right - clientRect.left;
		height = clientRect.bottom - clientRect.top;
	}
	if ( width <= 0 || height <= 0 ) {
		return false;
	}
	sdVulkanBackendState::sdToolWindow* tool = NULL;
	for ( int i = 0; i < state->toolWindows.Num(); ++i ) {
		if ( state->toolWindows[ i ]->window == window ) {
			tool = state->toolWindows[ i ];
			break;
		}
	}
	if ( tool == NULL ) {
		tool = new sdVulkanBackendState::sdToolWindow;
		tool->window = window;
		VkWin32SurfaceCreateInfoKHR surfaceInfo;
		memset( &surfaceInfo, 0, sizeof( surfaceInfo ) );
		surfaceInfo.sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR;
		surfaceInfo.hinstance = GetModuleHandleA( NULL );
		surfaceInfo.hwnd = window;
		if ( !CheckVulkanResult( state->CreateWin32SurfaceKHR( state->instance,
			&surfaceInfo, NULL, &tool->surface ),
			"vkCreateWin32SurfaceKHR(Radiant)" ) ) {
			delete tool;
			return false;
		}
		state->toolWindows.Append( tool );
	}
	VkExtent2D requestedExtent;
	requestedExtent.width = static_cast< unsigned int >( width );
	requestedExtent.height = static_cast< unsigned int >( height );
	VkSurfaceCapabilitiesKHR currentCapabilities;
	memset( &currentCapabilities, 0, sizeof( currentCapabilities ) );
	if ( state->GetPhysicalDeviceSurfaceCapabilitiesKHR( state->physicalDevice,
		tool->surface, &currentCapabilities ) == VK_SUCCESS ) {
		if ( currentCapabilities.currentExtent.width != UINT_MAX ) {
			requestedExtent = currentCapabilities.currentExtent;
		} else {
			requestedExtent.width = ClampUnsigned( requestedExtent.width,
				currentCapabilities.minImageExtent.width,
				currentCapabilities.maxImageExtent.width );
			requestedExtent.height = ClampUnsigned( requestedExtent.height,
				currentCapabilities.minImageExtent.height,
				currentCapabilities.maxImageExtent.height );
		}
	}
	if ( tool->extent.width != requestedExtent.width ||
		tool->extent.height != requestedExtent.height ) {
		tool->dirty = true;
	}
	if ( tool->dirty || tool->swapchain == VK_NULL_HANDLE ) {
		state->DeviceWaitIdle( state->device );
		if ( !CreateToolSwapchain( *state, *tool, width, height ) ) {
			return false;
		}
	}
	if ( state->retiredToolDepthResources.Num() != 0 ) {
		state->DeviceWaitIdle( state->device );
		DestroyRetiredResources( *state );
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	if ( !CheckVulkanResult( state->WaitForFences( state->device, 1,
		&frame.fence, VK_TRUE, UINT64_MAX ), "vkWaitForFences(Radiant)" ) ) {
		return false;
	}
	frame.guiVertexOffset = 0;
	frame.guiIndexOffset = 0;
	frame.guiVertexOverflowWarned = false;
	frame.guiIndexOverflowWarned = false;
	VkResult acquireResult = state->AcquireNextImageKHR( state->device,
		tool->swapchain, UINT64_MAX, frame.imageAvailable, VK_NULL_HANDLE,
		&state->toolImageIndex );
	if ( acquireResult == VK_ERROR_OUT_OF_DATE_KHR ) {
		state->DeviceWaitIdle( state->device );
		if ( !CreateToolSwapchain( *state, *tool, width, height ) ) {
			return false;
		}
		acquireResult = state->AcquireNextImageKHR( state->device, tool->swapchain,
			UINT64_MAX, frame.imageAvailable, VK_NULL_HANDLE,
			&state->toolImageIndex );
	}
	if ( acquireResult != VK_SUCCESS && acquireResult != VK_SUBOPTIMAL_KHR ) {
		CheckVulkanResult( acquireResult, "vkAcquireNextImageKHR(Radiant)" );
		return false;
	}
	if ( acquireResult == VK_SUBOPTIMAL_KHR ) {
		tool->dirty = true;
	}
	if ( !CheckVulkanResult( state->ResetFences( state->device, 1, &frame.fence ),
		"vkResetFences(Radiant)" ) ||
		!CheckVulkanResult( state->ResetCommandPool( state->device,
			frame.commandPool, 0 ), "vkResetCommandPool(Radiant)" ) ) {
		RestoreSignaledFence( *state, frame );
		return false;
	}
	frame.commandPoolReset = false;
	VkCommandBufferBeginInfo beginInfo;
	memset( &beginInfo, 0, sizeof( beginInfo ) );
	beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
	beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
	if ( !CheckVulkanResult( state->BeginCommandBuffer( frame.commandBuffer,
		&beginInfo ), "vkBeginCommandBuffer(Radiant)" ) ) {
		RestoreSignaledFence( *state, frame );
		return false;
	}
	VkImageMemoryBarrier2 barriers[ 2 ];
	memset( barriers, 0, sizeof( barriers ) );
	barriers[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	// Chained to the acquire semaphore's wait stage, as in the game window.
	barriers[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	barriers[ 0 ].srcAccessMask = VK_ACCESS_2_NONE;
	barriers[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	barriers[ 0 ].dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	barriers[ 0 ].oldLayout = tool->imageInitialized[ state->toolImageIndex ] ?
		VK_IMAGE_LAYOUT_PRESENT_SRC_KHR : VK_IMAGE_LAYOUT_UNDEFINED;
	barriers[ 0 ].newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	barriers[ 0 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].image = tool->images[ state->toolImageIndex ];
	barriers[ 0 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	barriers[ 0 ].subresourceRange.levelCount = 1;
	barriers[ 0 ].subresourceRange.layerCount = 1;
	barriers[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barriers[ 1 ].srcStageMask = tool->depthInitialized[ state->toolImageIndex ] ?
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT : VK_PIPELINE_STAGE_2_NONE;
	barriers[ 1 ].srcAccessMask = tool->depthInitialized[ state->toolImageIndex ] ?
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT : VK_ACCESS_2_NONE;
	barriers[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	barriers[ 1 ].dstAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	barriers[ 1 ].oldLayout = tool->depthInitialized[ state->toolImageIndex ] ?
		VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	barriers[ 1 ].newLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	barriers[ 1 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].image = tool->depthImages[ state->toolImageIndex ];
	barriers[ 1 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT |
		VK_IMAGE_ASPECT_STENCIL_BIT;
	barriers[ 1 ].subresourceRange.levelCount = 1;
	barriers[ 1 ].subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 2;
	dependencyInfo.pImageMemoryBarriers = barriers;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );
	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = tool->views[ state->toolImageIndex ];
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	for ( int i = 0; i < 4; ++i ) {
		colorAttachment.clearValue.color.float32[ i ] = clearColor != NULL ?
			clearColor[ i ] : ( i == 3 ? 1.0f : 0.0f );
	}
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = tool->depthViews[ state->toolImageIndex ];
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	depthAttachment.clearValue.depthStencil.depth = 1.0f;
	VkRenderingAttachmentInfo stencilAttachment;
	memset( &stencilAttachment, 0, sizeof( stencilAttachment ) );
	stencilAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	stencilAttachment.imageView = tool->depthViews[ state->toolImageIndex ];
	stencilAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	stencilAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	stencilAttachment.storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
	stencilAttachment.clearValue.depthStencil.stencil = 0;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = tool->extent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	renderingInfo.pDepthAttachment = &depthAttachment;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state->CmdBeginRendering( frame.commandBuffer, &renderingInfo );
	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( tool->extent.height );
	viewport.width = static_cast< float >( tool->extent.width );
	viewport.height = -static_cast< float >( tool->extent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );
	memset( &state->toolScissor, 0, sizeof( state->toolScissor ) );
	state->toolScissor.extent = tool->extent;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &state->toolScissor );
	state->activeToolWindow = tool;
	state->activeToolRenderTarget = NULL;
	state->toolRenderExtent = tool->extent;
	state->toolImageOwner = NULL;
	state->toolFrameActive = true;
	return true;
#endif
}

static unsigned int ToolRenderTargetAllocationSize( int requested ) {
	unsigned int result = 1;
	const unsigned int value = static_cast< unsigned int >( Max( requested, 1 ) );
	while ( result < value && result < ( 1u << 30 ) ) {
		result <<= 1;
	}
	return result;
}

static bool CreateToolRenderTargetResources( sdVulkanBackendState& state,
	sdVulkanBackendState::sdToolRenderTarget& target,
	VkExtent2D allocationExtent ) {
	sdVulkanImageResource color;
	memset( &color, 0, sizeof( color ) );
	color.owner = target.owner;
	color.width = static_cast< int >( allocationExtent.width );
	color.height = static_cast< int >( allocationExtent.height );
	color.mipLevels = 1;

	VkImageCreateInfo imageInfo;
	memset( &imageInfo, 0, sizeof( imageInfo ) );
	imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
	imageInfo.imageType = VK_IMAGE_TYPE_2D;
	imageInfo.format = state.swapchainFormat;
	imageInfo.extent.width = allocationExtent.width;
	imageInfo.extent.height = allocationExtent.height;
	imageInfo.extent.depth = 1;
	imageInfo.mipLevels = 1;
	imageInfo.arrayLayers = 1;
	imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
	imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
	imageInfo.usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT |
		VK_IMAGE_USAGE_SAMPLED_BIT;
	imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	if ( !CheckVulkanResult( state.CreateImage( state.device, &imageInfo, NULL,
		&color.image ), "vkCreateImage(Radiant viewport)" ) ) {
		return false;
	}
	VkMemoryRequirements requirements;
	state.GetImageMemoryRequirements( state.device, color.image, &requirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( state, requirements.memoryTypeBits,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, memoryType ) ) {
		DestroyImageResource( state, color );
		return false;
	}
	VkMemoryAllocateInfo allocation;
	memset( &allocation, 0, sizeof( allocation ) );
	allocation.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	allocation.allocationSize = requirements.size;
	allocation.memoryTypeIndex = memoryType;
	if ( !CheckVulkanResult( state.AllocateMemory( state.device, &allocation,
		NULL, &color.memory ), "vkAllocateMemory(Radiant viewport)" ) ||
		!CheckVulkanResult( state.BindImageMemory( state.device, color.image,
			color.memory, 0 ), "vkBindImageMemory(Radiant viewport)" ) ) {
		DestroyImageResource( state, color );
		return false;
	}
	VkImageViewCreateInfo viewInfo;
	memset( &viewInfo, 0, sizeof( viewInfo ) );
	viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
	viewInfo.image = color.image;
	viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
	viewInfo.format = state.swapchainFormat;
	viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	viewInfo.subresourceRange.levelCount = 1;
	viewInfo.subresourceRange.layerCount = 1;
	if ( !CheckVulkanResult( state.CreateImageView( state.device, &viewInfo,
		NULL, &color.view ), "vkCreateImageView(Radiant viewport)" ) ) {
		DestroyImageResource( state, color );
		return false;
	}
	VkSamplerCreateInfo samplerInfo;
	memset( &samplerInfo, 0, sizeof( samplerInfo ) );
	samplerInfo.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
	samplerInfo.magFilter = VK_FILTER_LINEAR;
	samplerInfo.minFilter = VK_FILTER_LINEAR;
	samplerInfo.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
	samplerInfo.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.maxLod = 0.0f;
	if ( !CheckVulkanResult( state.CreateSampler( state.device, &samplerInfo,
		NULL, &color.sampler ), "vkCreateSampler(Radiant viewport)" ) ||
		!AllocateImageDescriptor( state, color ) ) {
		DestroyImageResource( state, color );
		return false;
	}

	idList< VkImage > depthImages;
	idList< VkDeviceMemory > depthMemory;
	idList< VkImageView > depthViews;
	if ( !CreateDepthResources( state, 1, allocationExtent, depthImages,
		depthMemory, depthViews ) ) {
		DestroyImageResource( state, color );
		return false;
	}
	target.allocationExtent = allocationExtent;
	target.depthImage = depthImages[ 0 ];
	target.depthMemory = depthMemory[ 0 ];
	target.depthView = depthViews[ 0 ];
	target.initialized = false;
	const int imageIndex = state.imageResources.Append( color );
	state.imageResourceLookup.Set( color.owner, imageIndex );
	return true;
}

bool sdVulkanBackend::BeginToolRenderTarget( const void* owner, int width,
	int height, const float clearColor[ 4 ] ) {
	if ( state == NULL || owner == NULL || width <= 0 || height <= 0 ||
		!state->toolFrameActive || state->activeToolWindow == NULL ||
		state->activeToolRenderTarget != NULL ) {
		return false;
	}
	sdVulkanBackendState::sdToolRenderTarget* target = NULL;
	for ( int i = 0; i < state->toolRenderTargets.Num(); ++i ) {
		if ( state->toolRenderTargets[ i ]->owner == owner ) {
			target = state->toolRenderTargets[ i ];
			break;
		}
	}
	if ( target == NULL ) {
		target = new sdVulkanBackendState::sdToolRenderTarget;
		target->owner = owner;
		state->toolRenderTargets.Append( target );
	}
	const VkExtent2D requestedExtent = {
		static_cast< unsigned int >( width ),
		static_cast< unsigned int >( height )
	};
	const bool needsAllocation = target->allocationExtent.width < requestedExtent.width ||
		target->allocationExtent.height < requestedExtent.height ||
		FindVulkanImageResource( *state, owner ) == NULL ||
		target->depthView == VK_NULL_HANDLE;
	if ( needsAllocation ) {
		if ( target->depthImage != VK_NULL_HANDLE ||
			target->depthMemory != VK_NULL_HANDLE || target->depthView != VK_NULL_HANDLE ) {
			sdVulkanToolDepthResource retired = { target->depthImage,
				target->depthMemory, target->depthView };
			state->retiredToolDepthResources.Append( retired );
			target->depthImage = VK_NULL_HANDLE;
			target->depthMemory = VK_NULL_HANDLE;
			target->depthView = VK_NULL_HANDLE;
		}
		DestroyImage( owner );
		VkExtent2D allocationExtent;
		allocationExtent.width = ToolRenderTargetAllocationSize( width );
		allocationExtent.height = ToolRenderTargetAllocationSize( height );
		if ( !CreateToolRenderTargetResources( *state, *target,
			allocationExtent ) ) {
			return false;
		}
	}
	target->contentExtent = requestedExtent;
	sdVulkanImageResource* color = FindVulkanImageResource( *state, owner );
	if ( color == NULL ) {
		return false;
	}

	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	state->CmdEndRendering( frame.commandBuffer );
	VkImageMemoryBarrier2 barriers[ 2 ];
	memset( barriers, 0, sizeof( barriers ) );
	barriers[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barriers[ 0 ].srcStageMask = target->initialized ?
		VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT : VK_PIPELINE_STAGE_2_NONE;
	barriers[ 0 ].srcAccessMask = target->initialized ?
		VK_ACCESS_2_SHADER_SAMPLED_READ_BIT : VK_ACCESS_2_NONE;
	barriers[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	barriers[ 0 ].dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	barriers[ 0 ].oldLayout = target->initialized ?
		VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	barriers[ 0 ].newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	barriers[ 0 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].image = color->image;
	barriers[ 0 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	barriers[ 0 ].subresourceRange.levelCount = 1;
	barriers[ 0 ].subresourceRange.layerCount = 1;
	barriers[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barriers[ 1 ].srcStageMask = target->initialized ?
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT : VK_PIPELINE_STAGE_2_NONE;
	barriers[ 1 ].srcAccessMask = target->initialized ?
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT : VK_ACCESS_2_NONE;
	barriers[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
		VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	barriers[ 1 ].dstAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	barriers[ 1 ].oldLayout = target->initialized ?
		VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL : VK_IMAGE_LAYOUT_UNDEFINED;
	barriers[ 1 ].newLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	barriers[ 1 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].image = target->depthImage;
	barriers[ 1 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT |
		VK_IMAGE_ASPECT_STENCIL_BIT;
	barriers[ 1 ].subresourceRange.levelCount = 1;
	barriers[ 1 ].subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 2;
	dependencyInfo.pImageMemoryBarriers = barriers;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );

	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = color->view;
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	for ( int component = 0; component < 4; ++component ) {
		colorAttachment.clearValue.color.float32[ component ] = clearColor != NULL ?
			clearColor[ component ] : ( component == 3 ? 1.0f : 0.0f );
	}
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = target->depthView;
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	depthAttachment.clearValue.depthStencil.depth = 1.0f;
	VkRenderingAttachmentInfo stencilAttachment;
	memset( &stencilAttachment, 0, sizeof( stencilAttachment ) );
	stencilAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	stencilAttachment.imageView = target->depthView;
	stencilAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	stencilAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR;
	stencilAttachment.storeOp = VK_ATTACHMENT_STORE_OP_DONT_CARE;
	stencilAttachment.clearValue.depthStencil.stencil = 0;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = requestedExtent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	renderingInfo.pDepthAttachment = &depthAttachment;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state->CmdBeginRendering( frame.commandBuffer, &renderingInfo );
	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( requestedExtent.height );
	viewport.width = static_cast< float >( requestedExtent.width );
	viewport.height = -static_cast< float >( requestedExtent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );
	memset( &state->toolScissor, 0, sizeof( state->toolScissor ) );
	state->toolScissor.extent = requestedExtent;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &state->toolScissor );
	state->activeToolRenderTarget = target;
	state->toolRenderExtent = requestedExtent;
	state->toolImageOwner = NULL;
	return true;
}

void sdVulkanBackend::EndToolRenderTarget() {
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL || state->activeToolRenderTarget == NULL ) {
		return;
	}
	sdVulkanBackendState::sdToolRenderTarget* target =
		state->activeToolRenderTarget;
	sdVulkanImageResource* color = FindVulkanImageResource( *state,
		target->owner );
	if ( color == NULL ) {
		state->activeToolRenderTarget = NULL;
		return;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	state->CmdEndRendering( frame.commandBuffer );
	VkImageMemoryBarrier2 toSample;
	memset( &toSample, 0, sizeof( toSample ) );
	toSample.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toSample.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toSample.srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	toSample.dstStageMask = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT;
	toSample.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	toSample.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	toSample.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	toSample.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toSample.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toSample.image = color->image;
	toSample.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toSample.subresourceRange.levelCount = 1;
	toSample.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &toSample;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );
	target->initialized = true;
	state->activeToolRenderTarget = NULL;
	state->toolRenderExtent = state->activeToolWindow->extent;

	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = state->activeToolWindow->views[
		state->toolImageIndex ];
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = state->activeToolWindow->depthViews[
		state->toolImageIndex ];
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingAttachmentInfo stencilAttachment;
	memset( &stencilAttachment, 0, sizeof( stencilAttachment ) );
	stencilAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	stencilAttachment.imageView = state->activeToolWindow->depthViews[
		state->toolImageIndex ];
	stencilAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	stencilAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	stencilAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = state->toolRenderExtent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	renderingInfo.pDepthAttachment = &depthAttachment;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state->CmdBeginRendering( frame.commandBuffer, &renderingInfo );
	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( state->toolRenderExtent.height );
	viewport.width = static_cast< float >( state->toolRenderExtent.width );
	viewport.height = -static_cast< float >( state->toolRenderExtent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );
	memset( &state->toolScissor, 0, sizeof( state->toolScissor ) );
	state->toolScissor.extent = state->toolRenderExtent;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &state->toolScissor );
	state->toolImageOwner = NULL;
}

void sdVulkanBackend::DestroyToolRenderTarget( const void* owner ) {
	if ( state == NULL || owner == NULL ) {
		return;
	}
	for ( int i = 0; i < state->toolRenderTargets.Num(); ++i ) {
		sdVulkanBackendState::sdToolRenderTarget* target =
			state->toolRenderTargets[ i ];
		if ( target == NULL || target->owner != owner ) {
			continue;
		}
		if ( target == state->activeToolRenderTarget ) {
			EndToolRenderTarget();
		}
		sdVulkanToolDepthResource depth = { target->depthImage,
			target->depthMemory, target->depthView };
		if ( state->frameActive || state->toolFrameActive ) {
			state->retiredToolDepthResources.Append( depth );
		} else {
			state->DeviceWaitIdle( state->device );
			DestroyToolDepthResource( *state, depth );
		}
		delete target;
		state->toolRenderTargets.RemoveIndexFast( i );
		break;
	}
	DestroyImage( owner );
}

bool sdVulkanBackend::GetToolRenderTargetSize( const void* owner, int& width,
	int& height, int& textureWidth, int& textureHeight ) const {
	width = height = textureWidth = textureHeight = 0;
	if ( state == NULL || owner == NULL ) {
		return false;
	}
	for ( int i = 0; i < state->toolRenderTargets.Num(); ++i ) {
		const sdVulkanBackendState::sdToolRenderTarget* target =
			state->toolRenderTargets[ i ];
		if ( target != NULL && target->owner == owner ) {
			width = static_cast< int >( target->contentExtent.width );
			height = static_cast< int >( target->contentExtent.height );
			textureWidth = static_cast< int >( target->allocationExtent.width );
			textureHeight = static_cast< int >( target->allocationExtent.height );
			return width > 0 && height > 0 && textureWidth > 0 && textureHeight > 0;
		}
	}
	return false;
}

bool sdVulkanBackend::DrawToolTriangles( const sdVulkanToolVertex* vertices,
	int vertexCount, bool depthTest, bool blend ) {
	if ( state == NULL || !state->toolFrameActive || vertices == NULL ||
		vertexCount < 3 || state->activeToolWindow == NULL ) {
		return false;
	}
	const void* imageOwner = state->toolImageOwner;
	if ( imageOwner == NULL && globalImages != NULL ) {
		imageOwner = globalImages->whiteImage;
	}
	const sdVulkanImageResource* imageResource =
		FindVulkanImageResource( *state, imageOwner );
	if ( imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
		return false;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( vertexCount ) *
		sizeof( sdVulkanToolVertex );
	const VkDeviceSize vertexOffset = ( frame.guiVertexOffset + 15 ) & ~15ULL;
	if ( vertexOffset + vertexBytes > VULKAN_GUI_VERTEX_BYTES ) {
		if ( !frame.guiVertexOverflowWarned ) {
			common->Warning( "Radiant Vulkan vertex stream exceeded %u MiB; remaining draws are skipped",
				static_cast< unsigned int >( VULKAN_GUI_VERTEX_BYTES / ( 1024 * 1024 ) ) );
			frame.guiVertexOverflowWarned = true;
		}
		return false;
	}
	memcpy( static_cast< byte* >( frame.guiVertexMapped ) + vertexOffset,
		vertices, static_cast< size_t >( vertexBytes ) );
	frame.guiVertexOffset = vertexOffset + vertexBytes;
	VkPipeline pipeline = depthTest ?
		( blend ? state->toolDepthBlendPipeline : state->toolDepthPipeline ) :
		( blend ? state->toolBlendPipeline : state->toolPipeline );
	state->CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		pipeline );
	// Tool pipelines inherit the renderer's dynamic depth-bias state. Radiant
	// uses unbiased geometry unless its compatibility layer explicitly adds an
	// offset, so establish the required neutral value after every pipeline bind.
	state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
	state->CmdBindDescriptorSets( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
		&imageResource->descriptorSet, 0, NULL );
	state->CmdBindVertexBuffers( frame.commandBuffer, 0, 1,
		&frame.guiVertexBuffer, &vertexOffset );
	state->CmdDraw( frame.commandBuffer, vertexCount, 1, 0, 0 );
	return true;
}

bool sdVulkanBackend::DrawToolIndexed( const sdVulkanToolVertex* vertices,
	int vertexCount, const unsigned int* indices, int indexCount,
	bool depthTest, bool blend ) {
	if ( state == NULL || !state->toolFrameActive || vertices == NULL ||
		indices == NULL || vertexCount < 3 || indexCount < 3 ||
		state->activeToolWindow == NULL ) {
		return false;
	}
	const void* imageOwner = state->toolImageOwner;
	if ( imageOwner == NULL && globalImages != NULL ) {
		imageOwner = globalImages->whiteImage;
	}
	const sdVulkanImageResource* imageResource =
		FindVulkanImageResource( *state, imageOwner );
	if ( imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
		return false;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( vertexCount ) *
		sizeof( sdVulkanToolVertex );
	const VkDeviceSize indexBytes = static_cast< VkDeviceSize >( indexCount ) *
		sizeof( unsigned int );
	const VkDeviceSize vertexOffset = ( frame.guiVertexOffset + 15 ) & ~15ULL;
	const VkDeviceSize indexOffset = ( frame.guiIndexOffset + 15 ) & ~15ULL;
	if ( vertexOffset + vertexBytes > VULKAN_GUI_VERTEX_BYTES ) {
		if ( !frame.guiVertexOverflowWarned ) {
			common->Warning( "Radiant Vulkan vertex stream exceeded %u MiB; remaining draws are skipped",
				static_cast< unsigned int >( VULKAN_GUI_VERTEX_BYTES / ( 1024 * 1024 ) ) );
			frame.guiVertexOverflowWarned = true;
		}
		return false;
	}
	if ( indexOffset + indexBytes > VULKAN_GUI_INDEX_BYTES ) {
		if ( !frame.guiIndexOverflowWarned ) {
			common->Warning( "Radiant Vulkan index stream exceeded %u MiB; remaining draws are skipped",
				static_cast< unsigned int >( VULKAN_GUI_INDEX_BYTES / ( 1024 * 1024 ) ) );
			frame.guiIndexOverflowWarned = true;
		}
		return false;
	}
	memcpy( static_cast< byte* >( frame.guiVertexMapped ) + vertexOffset,
		vertices, static_cast< size_t >( vertexBytes ) );
	memcpy( static_cast< byte* >( frame.guiIndexMapped ) + indexOffset,
		indices, static_cast< size_t >( indexBytes ) );
	frame.guiVertexOffset = vertexOffset + vertexBytes;
	frame.guiIndexOffset = indexOffset + indexBytes;
	VkPipeline pipeline = depthTest ?
		( blend ? state->toolDepthBlendPipeline : state->toolDepthPipeline ) :
		( blend ? state->toolBlendPipeline : state->toolPipeline );
	state->CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		pipeline );
	state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
	state->CmdBindDescriptorSets( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
		&imageResource->descriptorSet, 0, NULL );
	state->CmdBindVertexBuffers( frame.commandBuffer, 0, 1,
		&frame.guiVertexBuffer, &vertexOffset );
	state->CmdBindIndexBuffer( frame.commandBuffer, frame.guiIndexBuffer,
		indexOffset, VK_INDEX_TYPE_UINT32 );
	state->CmdDrawIndexed( frame.commandBuffer, indexCount, 1, 0, 0, 0 );
	return true;
}

bool sdVulkanBackend::DrawToolCachedIndexed( const void* vertexCacheHandle,
	const void* indexCacheHandle, int vertexCount, int indexCount,
	const float* modelViewProjection, const float* color, float texCoordScale,
	bool depthTest, bool blend, bool wireframe ) {
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL || vertexCacheHandle == NULL ||
		indexCacheHandle == NULL || vertexCount < 3 || indexCount < 3 ||
		modelViewProjection == NULL || color == NULL ) {
		return false;
	}
	const vertCache_t* vertexCacheBlock =
		static_cast< const vertCache_t* >( vertexCacheHandle );
	const vertCache_t* indexCacheBlock =
		static_cast< const vertCache_t* >( indexCacheHandle );
	const void* vertexOwner = vertexCacheBlock->backendBuffer != NULL ?
		vertexCacheBlock->backendBuffer : vertexCacheBlock;
	const void* indexOwner = indexCacheBlock->backendBuffer != NULL ?
		indexCacheBlock->backendBuffer : indexCacheBlock;
	const sdVulkanBufferResource* vertexResource =
		FindVulkanBufferResource( *state, vertexOwner );
	const sdVulkanBufferResource* indexResource =
		FindVulkanBufferResource( *state, indexOwner );
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( vertexCount ) *
		sizeof( idDrawVert );
	const VkDeviceSize indexBytes = static_cast< VkDeviceSize >( indexCount ) *
		sizeof( glIndex_t );
	if ( vertexResource == NULL || indexResource == NULL ||
		vertexCacheBlock->offset < 0 || indexCacheBlock->offset < 0 ||
		static_cast< VkDeviceSize >( vertexCacheBlock->offset ) + vertexBytes >
			vertexResource->bytes ||
		static_cast< VkDeviceSize >( indexCacheBlock->offset ) + indexBytes >
			indexResource->bytes ) {
		return false;
	}

	// Orthographic model wireframes show topology, not material shading.  Sample
	// white so their color cannot inherit the preceding fixed-function texture.
	const void* imageOwner = wireframe && globalImages != NULL ?
		globalImages->whiteImage : state->toolImageOwner;
	if ( imageOwner == NULL && globalImages != NULL ) {
		imageOwner = globalImages->whiteImage;
	}
	const sdVulkanImageResource* imageResource =
		FindVulkanImageResource( *state, imageOwner );
	if ( imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
		return false;
	}
	VkPipeline pipeline;
	if ( wireframe ) {
		pipeline = depthTest ?
			( blend ? state->toolModelWireDepthBlendPipeline :
				state->toolModelWireDepthPipeline ) :
			( blend ? state->toolModelWireBlendPipeline :
				state->toolModelWirePipeline );
	} else {
		pipeline = depthTest ?
			( blend ? state->toolModelDepthBlendPipeline :
				state->toolModelDepthPipeline ) :
			( blend ? state->toolModelBlendPipeline : state->toolModelPipeline );
	}
	if ( pipeline == VK_NULL_HANDLE ) {
		return false;
	}

	float pushConstants[32];
	memset( pushConstants, 0, sizeof( pushConstants ) );
	memcpy( pushConstants, modelViewProjection, sizeof( float ) * 16 );
	memcpy( pushConstants + 16, color, sizeof( float ) * 4 );
	pushConstants[20] = 1.0f;
	pushConstants[25] = 1.0f;
	pushConstants[28] = 0.0f; // Radiant uses its current color, not vertex color.
	pushConstants[29] = texCoordScale;

	sdVulkanFrame& frame = state->frames[state->frameIndex];
	state->CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		pipeline );
	state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
	state->CmdBindDescriptorSets( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
		&imageResource->descriptorSet, 0, NULL );
	state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
		VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
		sizeof( pushConstants ), pushConstants );
	const VkDeviceSize vertexOffset = vertexCacheBlock->offset;
	state->CmdBindVertexBuffers( frame.commandBuffer, 0, 1,
		&vertexResource->buffer, &vertexOffset );
	state->CmdBindIndexBuffer( frame.commandBuffer, indexResource->buffer,
		indexCacheBlock->offset, sizeof( glIndex_t ) == 2 ?
		VK_INDEX_TYPE_UINT16 : VK_INDEX_TYPE_UINT32 );
	state->CmdDrawIndexed( frame.commandBuffer, indexCount, 1, 0, 0, 0 );
	return true;
}

void sdVulkanBackend::SetToolScissor( int x, int y, int width, int height ) {
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL ) {
		return;
	}
	const int targetWidth = static_cast< int >( state->toolRenderExtent.width );
	const int targetHeight = static_cast< int >( state->toolRenderExtent.height );
	const int left = idMath::ClampInt( 0, targetWidth, x );
	const int top = idMath::ClampInt( 0, targetHeight, targetHeight - y - height );
	const int right = idMath::ClampInt( left, targetWidth, x + width );
	const int bottom = idMath::ClampInt( top, targetHeight, targetHeight - y );
	state->toolScissor.offset.x = left;
	state->toolScissor.offset.y = top;
	state->toolScissor.extent.width = right - left;
	state->toolScissor.extent.height = bottom - top;
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &state->toolScissor );
}

void sdVulkanBackend::SetToolImage( const void* imageOwner ) {
	if ( state != NULL ) {
		state->toolImageOwner = imageOwner;
	}
}

void sdVulkanBackend::ClearToolRegion( const float color[ 4 ], bool clearColor,
	bool clearDepth ) {
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL || ( !clearColor && !clearDepth ) ) {
		return;
	}
	VkClearAttachment attachments[ 2 ];
	memset( attachments, 0, sizeof( attachments ) );
	int attachmentCount = 0;
	if ( clearColor ) {
		VkClearAttachment& attachment = attachments[ attachmentCount++ ];
		attachment.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		attachment.colorAttachment = 0;
		for ( int component = 0; component < 4; ++component ) {
			attachment.clearValue.color.float32[ component ] = color != NULL ?
				color[ component ] : ( component == 3 ? 1.0f : 0.0f );
		}
	}
	if ( clearDepth ) {
		VkClearAttachment& attachment = attachments[ attachmentCount++ ];
		attachment.aspectMask = VK_IMAGE_ASPECT_DEPTH_BIT;
		attachment.clearValue.depthStencil.depth = 1.0f;
	}
	VkClearRect clearRect;
	memset( &clearRect, 0, sizeof( clearRect ) );
	clearRect.rect = state->toolScissor;
	clearRect.layerCount = 1;
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	state->CmdClearAttachments( frame.commandBuffer, attachmentCount,
		attachments, 1, &clearRect );
}

bool sdVulkanBackend::GetActiveToolWindowExtent( int& width, int& height ) const {
	width = height = 0;
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL ) {
		return false;
	}
	width = static_cast< int >( state->activeToolWindow->extent.width );
	height = static_cast< int >( state->activeToolWindow->extent.height );
	return width > 0 && height > 0;
}

void sdVulkanBackend::EndToolWindow() {
	if ( state == NULL || !state->toolFrameActive ||
		state->activeToolWindow == NULL ) {
		return;
	}
	if ( state->activeToolRenderTarget != NULL ) {
		EndToolRenderTarget();
	}
	sdVulkanBackendState::sdToolWindow* tool = state->activeToolWindow;
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	state->CmdEndRendering( frame.commandBuffer );
	VkImageMemoryBarrier2 toPresent;
	memset( &toPresent, 0, sizeof( toPresent ) );
	toPresent.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toPresent.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	toPresent.srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
	toPresent.dstStageMask = VK_PIPELINE_STAGE_2_NONE;
	toPresent.dstAccessMask = VK_ACCESS_2_NONE;
	toPresent.oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	toPresent.newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR;
	toPresent.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toPresent.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toPresent.image = tool->images[ state->toolImageIndex ];
	toPresent.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toPresent.subresourceRange.levelCount = 1;
	toPresent.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &toPresent;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );
	if ( !CheckVulkanResult( state->EndCommandBuffer( frame.commandBuffer ),
		"vkEndCommandBuffer(Radiant)" ) ) {
		RestoreSignaledFence( *state, frame );
		state->toolFrameActive = false;
		state->activeToolWindow = NULL;
		return;
	}
	VkSemaphoreSubmitInfo waitInfo;
	memset( &waitInfo, 0, sizeof( waitInfo ) );
	waitInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
	waitInfo.semaphore = frame.imageAvailable;
	waitInfo.stageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
	VkCommandBufferSubmitInfo commandInfo;
	memset( &commandInfo, 0, sizeof( commandInfo ) );
	commandInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_SUBMIT_INFO;
	commandInfo.commandBuffer = frame.commandBuffer;
	VkSemaphoreSubmitInfo signalInfo;
	memset( &signalInfo, 0, sizeof( signalInfo ) );
	signalInfo.sType = VK_STRUCTURE_TYPE_SEMAPHORE_SUBMIT_INFO;
	signalInfo.semaphore = tool->presentSemaphores[ state->toolImageIndex ];
	signalInfo.stageMask = VK_PIPELINE_STAGE_2_ALL_COMMANDS_BIT;
	VkSubmitInfo2 submitInfo;
	memset( &submitInfo, 0, sizeof( submitInfo ) );
	submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO_2;
	submitInfo.waitSemaphoreInfoCount = 1;
	submitInfo.pWaitSemaphoreInfos = &waitInfo;
	submitInfo.commandBufferInfoCount = 1;
	submitInfo.pCommandBufferInfos = &commandInfo;
	submitInfo.signalSemaphoreInfoCount = 1;
	submitInfo.pSignalSemaphoreInfos = &signalInfo;
	if ( !CheckVulkanResult( state->QueueSubmit2( state->graphicsQueue, 1,
		&submitInfo, frame.fence ), "vkQueueSubmit2(Radiant)" ) ) {
		RestoreSignaledFence( *state, frame );
		state->toolFrameActive = false;
		state->activeToolWindow = NULL;
		return;
	}
	tool->imageInitialized[ state->toolImageIndex ] = 1;
	tool->depthInitialized[ state->toolImageIndex ] = 1;
	VkPresentInfoKHR presentInfo;
	memset( &presentInfo, 0, sizeof( presentInfo ) );
	presentInfo.sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR;
	presentInfo.waitSemaphoreCount = 1;
	presentInfo.pWaitSemaphores = &tool->presentSemaphores[ state->toolImageIndex ];
	presentInfo.swapchainCount = 1;
	presentInfo.pSwapchains = &tool->swapchain;
	presentInfo.pImageIndices = &state->toolImageIndex;
	const VkResult presentResult = state->QueuePresentKHR( state->graphicsQueue,
		&presentInfo );
	if ( presentResult == VK_ERROR_OUT_OF_DATE_KHR ||
		presentResult == VK_SUBOPTIMAL_KHR ) {
		tool->dirty = true;
	} else {
		CheckVulkanResult( presentResult, "vkQueuePresentKHR(Radiant)" );
	}
	state->toolFrameActive = false;
	state->activeToolWindow = NULL;
	state->activeToolRenderTarget = NULL;
	state->toolRenderExtent.width = state->toolRenderExtent.height = 0;
	state->toolImageOwner = NULL;
	state->frameIndex = ( state->frameIndex + 1 ) % NUM_VULKAN_FRAMES;
	state->frameSerial++;
}

bool sdVulkanBackend::IsToolWindowActive() const {
	return state != NULL && state->toolFrameActive;
}

void sdVulkanBackend::WaitIdle() {
	if ( state != NULL && state->device != VK_NULL_HANDLE ) {
		state->DeviceWaitIdle( state->device );
	}
}

void RecordInitialImageUploadCommands( sdVulkanBackendState& uploadState,
	VkCommandBuffer commandBuffer, VkBuffer stagingBuffer,
	VkDeviceSize stagingOffset, sdVulkanImageResource& resource,
	int width, int height, int mipLevels ) {
	VkImageMemoryBarrier2 toTransfer;
	memset( &toTransfer, 0, sizeof( toTransfer ) );
	toTransfer.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toTransfer.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
	toTransfer.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	toTransfer.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	toTransfer.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	toTransfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	toTransfer.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	toTransfer.image = resource.image;
	toTransfer.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	toTransfer.subresourceRange.levelCount = static_cast< unsigned int >( mipLevels );
	toTransfer.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &toTransfer;
	uploadState.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

	VkBufferImageCopy copyRegion;
	memset( &copyRegion, 0, sizeof( copyRegion ) );
	copyRegion.bufferOffset = stagingOffset;
	copyRegion.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	copyRegion.imageSubresource.layerCount = 1;
	copyRegion.imageExtent.width = static_cast< unsigned int >( width );
	copyRegion.imageExtent.height = static_cast< unsigned int >( height );
	copyRegion.imageExtent.depth = 1;
	uploadState.CmdCopyBufferToImage( commandBuffer, stagingBuffer,
		resource.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copyRegion );

	int mipWidth = width;
	int mipHeight = height;
	for ( int level = 1; level < mipLevels; ++level ) {
		VkImageMemoryBarrier2 toSource;
		memset( &toSource, 0, sizeof( toSource ) );
		toSource.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		toSource.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toSource.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toSource.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toSource.dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
		toSource.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toSource.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
		toSource.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toSource.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toSource.image = resource.image;
		toSource.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		toSource.subresourceRange.baseMipLevel = level - 1;
		toSource.subresourceRange.levelCount = 1;
		toSource.subresourceRange.layerCount = 1;
		dependencyInfo.pImageMemoryBarriers = &toSource;
		uploadState.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

		const int nextWidth = Max( 1, mipWidth >> 1 );
		const int nextHeight = Max( 1, mipHeight >> 1 );
		VkImageBlit blit;
		memset( &blit, 0, sizeof( blit ) );
		blit.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		blit.srcSubresource.mipLevel = level - 1;
		blit.srcSubresource.layerCount = 1;
		blit.srcOffsets[ 1 ].x = mipWidth;
		blit.srcOffsets[ 1 ].y = mipHeight;
		blit.srcOffsets[ 1 ].z = 1;
		blit.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		blit.dstSubresource.mipLevel = level;
		blit.dstSubresource.layerCount = 1;
		blit.dstOffsets[ 1 ].x = nextWidth;
		blit.dstOffsets[ 1 ].y = nextHeight;
		blit.dstOffsets[ 1 ].z = 1;
		uploadState.CmdBlitImage( commandBuffer, resource.image,
			VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, resource.image,
			VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit, VK_FILTER_LINEAR );
		mipWidth = nextWidth;
		mipHeight = nextHeight;
	}

	VkImageMemoryBarrier2 shaderBarriers[ 2 ];
	memset( shaderBarriers, 0, sizeof( shaderBarriers ) );
	unsigned int shaderBarrierCount = 0;
	if ( mipLevels > 1 ) {
		VkImageMemoryBarrier2& fromSource = shaderBarriers[ shaderBarrierCount++ ];
		fromSource.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		fromSource.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		fromSource.srcAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
		fromSource.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		fromSource.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		fromSource.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
		fromSource.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		fromSource.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromSource.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromSource.image = resource.image;
		fromSource.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		fromSource.subresourceRange.levelCount = mipLevels - 1;
		fromSource.subresourceRange.layerCount = 1;
	}
	VkImageMemoryBarrier2& fromDestination =
		shaderBarriers[ shaderBarrierCount++ ];
	fromDestination.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	fromDestination.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	fromDestination.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	fromDestination.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
	fromDestination.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	fromDestination.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	fromDestination.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	fromDestination.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromDestination.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	fromDestination.image = resource.image;
	fromDestination.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	fromDestination.subresourceRange.baseMipLevel = mipLevels - 1;
	fromDestination.subresourceRange.levelCount = 1;
	fromDestination.subresourceRange.layerCount = 1;
	dependencyInfo.imageMemoryBarrierCount = shaderBarrierCount;
	dependencyInfo.pImageMemoryBarriers = shaderBarriers;
	uploadState.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
}

// Creates a sampled 2D image with its memory, view, sampler and descriptor.
static bool CreateSampledImageResource( sdVulkanBackendState& state, const void* owner,
	VkFormat format, VkComponentMapping swizzle, int width, int height, int mipLevels,
	VkImageUsageFlags usage, bool linearFilter, bool repeat, sdVulkanImageResource& resource ) {
	memset( &resource, 0, sizeof( resource ) );
	resource.owner = owner;
	resource.width = width;
	resource.height = height;
	resource.mipLevels = mipLevels;

	VkImageCreateInfo imageInfo;
	memset( &imageInfo, 0, sizeof( imageInfo ) );
	imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
	imageInfo.imageType = VK_IMAGE_TYPE_2D;
	imageInfo.format = format;
	imageInfo.extent.width = static_cast< unsigned int >( width );
	imageInfo.extent.height = static_cast< unsigned int >( height );
	imageInfo.extent.depth = 1;
	imageInfo.mipLevels = static_cast< unsigned int >( mipLevels );
	imageInfo.arrayLayers = 1;
	imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
	imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
	imageInfo.usage = usage;
	imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	if ( !CheckVulkanResult( state.CreateImage( state.device, &imageInfo,
		NULL, &resource.image ), "vkCreateImage" ) ) {
		return false;
	}
	VkMemoryRequirements imageRequirements;
	state.GetImageMemoryRequirements( state.device, resource.image,
		&imageRequirements );
	unsigned int imageMemoryType = UINT_MAX;
	if ( !FindMemoryType( state, imageRequirements.memoryTypeBits,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, imageMemoryType ) ) {
		common->Warning( "No device-local Vulkan image memory type is available" );
		DestroyImageResource( state, resource );
		return false;
	}
	VkMemoryAllocateInfo imageAllocateInfo;
	memset( &imageAllocateInfo, 0, sizeof( imageAllocateInfo ) );
	imageAllocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	imageAllocateInfo.allocationSize = imageRequirements.size;
	imageAllocateInfo.memoryTypeIndex = imageMemoryType;
	if ( !CheckVulkanResult( state.AllocateMemory( state.device,
		&imageAllocateInfo, NULL, &resource.memory ), "vkAllocateMemory(image)" ) ||
		!CheckVulkanResult( state.BindImageMemory( state.device, resource.image,
			resource.memory, 0 ), "vkBindImageMemory" ) ) {
		DestroyImageResource( state, resource );
		return false;
	}

	VkImageViewCreateInfo viewInfo;
	memset( &viewInfo, 0, sizeof( viewInfo ) );
	viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
	viewInfo.image = resource.image;
	viewInfo.viewType = VK_IMAGE_VIEW_TYPE_2D;
	viewInfo.format = imageInfo.format;
	viewInfo.components = swizzle;
	viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	viewInfo.subresourceRange.levelCount = static_cast< unsigned int >( mipLevels );
	viewInfo.subresourceRange.layerCount = 1;
	if ( !CheckVulkanResult( state.CreateImageView( state.device, &viewInfo,
		NULL, &resource.view ), "vkCreateImageView(image)" ) ) {
		DestroyImageResource( state, resource );
		return false;
	}

	VkSamplerCreateInfo samplerInfo;
	memset( &samplerInfo, 0, sizeof( samplerInfo ) );
	samplerInfo.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
	samplerInfo.magFilter = linearFilter ? VK_FILTER_LINEAR : VK_FILTER_NEAREST;
	samplerInfo.minFilter = linearFilter ? VK_FILTER_LINEAR : VK_FILTER_NEAREST;
	samplerInfo.mipmapMode = linearFilter ? VK_SAMPLER_MIPMAP_MODE_LINEAR :
		VK_SAMPLER_MIPMAP_MODE_NEAREST;
	samplerInfo.addressModeU = repeat ? VK_SAMPLER_ADDRESS_MODE_REPEAT :
		VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeV = samplerInfo.addressModeU;
	samplerInfo.addressModeW = samplerInfo.addressModeU;
	samplerInfo.minLod = 0.0f;
	samplerInfo.maxLod = static_cast< float >( mipLevels - 1 );
	samplerInfo.maxAnisotropy = 1.0f;
	if ( !CheckVulkanResult( state.CreateSampler( state.device, &samplerInfo,
		NULL, &resource.sampler ), "vkCreateSampler" ) ) {
		DestroyImageResource( state, resource );
		return false;
	}
	if ( !AllocateImageDescriptor( state, resource ) ) {
		DestroyImageResource( state, resource );
		return false;
	}

	return true;
}

bool sdVulkanBackend::UploadImage2D( const void* owner,
	const unsigned char* rgba, int width, int height, int mipLevels,
	bool linearFilter, bool repeat ) {
	RENDER_METRIC_SCOPE( "Vulkan create/upload image" );
	if ( state == NULL || state->device == VK_NULL_HANDLE || owner == NULL ||
		rgba == NULL || width <= 0 || height <= 0 ) {
		return false;
	}
	int maximumMipLevels = 1;
	for ( int size = Max( width, height ); size > 1; size >>= 1 ) {
		maximumMipLevels++;
	}
	mipLevels = idMath::ClampInt( 1, maximumMipLevels, mipLevels );
	DestroyImage( owner );

	sdVulkanImageResource resource;
	VkComponentMapping identity;
	identity.r = identity.g = identity.b = identity.a = VK_COMPONENT_SWIZZLE_IDENTITY;
	VkImageUsageFlags usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
	if ( mipLevels > 1 ) {
		usage |= VK_IMAGE_USAGE_TRANSFER_SRC_BIT;
	}
	if ( !CreateSampledImageResource( *state, owner, VK_FORMAT_R8G8B8A8_UNORM, identity,
		width, height, mipLevels, usage, linearFilter, repeat, resource ) ) {
		return false;
	}

	const VkDeviceSize uploadBytes = static_cast< VkDeviceSize >( width ) *
		static_cast< VkDeviceSize >( height ) * 4;
	if ( state->frameActive ) {
		sdVulkanFrame& frame = state->frames[ state->frameIndex ];
		VkBuffer stagingBuffer = frame.imageUploadBuffer;
		VkDeviceSize stagingOffset = ( frame.imageUploadOffset + 15 ) & ~15ULL;
		if ( frame.imageUploadMapped != NULL &&
			stagingOffset + uploadBytes <= VULKAN_IMAGE_UPLOAD_BYTES ) {
			memcpy( static_cast< byte* >( frame.imageUploadMapped ) + stagingOffset,
				rgba, static_cast< size_t >( uploadBytes ) );
			frame.imageUploadOffset = stagingOffset + uploadBytes;
		} else {
			if ( !frame.imageUploadOverflowWarned ) {
				common->Warning( "Vulkan image upload arena exhausted (%u MiB); using fence-retired spill buffers",
					static_cast< unsigned int >(
						VULKAN_IMAGE_UPLOAD_BYTES / ( 1024 * 1024 ) ) );
				frame.imageUploadOverflowWarned = true;
			}
			sdVulkanBufferResource spill;
			memset( &spill, 0, sizeof( spill ) );
			spill.bytes = uploadBytes;
			if ( !CreateBufferAllocation( *state, uploadBytes,
				VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
				VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
					VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
				spill.buffer, spill.memory ) ||
				!CheckVulkanResult( state->MapMemory( state->device, spill.memory,
					0, uploadBytes, 0, &spill.mapped ),
					"vkMapMemory(image upload spill)" ) ) {
				DestroyBufferResource( *state, spill );
				DestroyImageResource( *state, resource );
				return false;
			}
			memcpy( spill.mapped, rgba, static_cast< size_t >( uploadBytes ) );
			state->UnmapMemory( state->device, spill.memory );
			spill.mapped = NULL;
			stagingBuffer = spill.buffer;
			stagingOffset = 0;
			state->frameRetiredBufferResources[ state->frameIndex ].Append( spill );
		}
		RecordInitialImageUploadCommands( *state,
			frame.imageUploadCommandBuffer, stagingBuffer, stagingOffset,
			resource, width, height, mipLevels );
		frame.imageUploadRecorded = true;
		const int imageIndex = state->imageResources.Append( resource );
		state->imageResourceLookup.Set( resource.owner, imageIndex );
		return true;
	}
	VkBuffer stagingBuffer = VK_NULL_HANDLE;
	VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
	if ( !CreateBufferAllocation( *state, uploadBytes,
		VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		stagingBuffer, stagingMemory ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	void* mapped = NULL;
	if ( !CheckVulkanResult( state->MapMemory( state->device, stagingMemory, 0,
		uploadBytes, 0, &mapped ), "vkMapMemory(image upload)" ) ) {
		state->DestroyBuffer( state->device, stagingBuffer, NULL );
		state->FreeMemory( state->device, stagingMemory, NULL );
		DestroyImageResource( *state, resource );
		return false;
	}
	memcpy( mapped, rgba, static_cast< size_t >( uploadBytes ) );
	state->UnmapMemory( state->device, stagingMemory );

	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
	poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
	poolInfo.queueFamilyIndex = state->queueFamilyIndex;
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	bool uploadSucceeded = CheckVulkanResult( state->CreateCommandPool(
		state->device, &poolInfo, NULL, &commandPool ),
		"vkCreateCommandPool(upload)" );
	if ( uploadSucceeded ) {
		VkCommandBufferAllocateInfo commandAllocateInfo;
		memset( &commandAllocateInfo, 0, sizeof( commandAllocateInfo ) );
		commandAllocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		commandAllocateInfo.commandPool = commandPool;
		commandAllocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		commandAllocateInfo.commandBufferCount = 1;
		uploadSucceeded = CheckVulkanResult( state->AllocateCommandBuffers(
			state->device, &commandAllocateInfo, &commandBuffer ),
			"vkAllocateCommandBuffers(upload)" );
	}
	if ( uploadSucceeded ) {
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		uploadSucceeded = CheckVulkanResult( state->BeginCommandBuffer(
			commandBuffer, &beginInfo ), "vkBeginCommandBuffer(upload)" );
	}
	if ( uploadSucceeded ) {
		VkImageMemoryBarrier2 toTransfer;
		memset( &toTransfer, 0, sizeof( toTransfer ) );
		toTransfer.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		toTransfer.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
		toTransfer.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toTransfer.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toTransfer.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
		toTransfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toTransfer.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.image = resource.image;
		toTransfer.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		toTransfer.subresourceRange.levelCount = static_cast< unsigned int >( mipLevels );
		toTransfer.subresourceRange.layerCount = 1;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.imageMemoryBarrierCount = 1;
		dependencyInfo.pImageMemoryBarriers = &toTransfer;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

		VkBufferImageCopy copyRegion;
		memset( &copyRegion, 0, sizeof( copyRegion ) );
		copyRegion.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		copyRegion.imageSubresource.layerCount = 1;
		copyRegion.imageExtent.width = static_cast< unsigned int >( width );
		copyRegion.imageExtent.height = static_cast< unsigned int >( height );
		copyRegion.imageExtent.depth = 1;
		state->CmdCopyBufferToImage( commandBuffer, stagingBuffer,
			resource.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copyRegion );

		int mipWidth = width;
		int mipHeight = height;
		for ( int level = 1; level < mipLevels; ++level ) {
			VkImageMemoryBarrier2 toSource;
			memset( &toSource, 0, sizeof( toSource ) );
			toSource.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
			toSource.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
			toSource.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
			toSource.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
			toSource.dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
			toSource.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
			toSource.newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
			toSource.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toSource.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			toSource.image = resource.image;
			toSource.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			toSource.subresourceRange.baseMipLevel = level - 1;
			toSource.subresourceRange.levelCount = 1;
			toSource.subresourceRange.layerCount = 1;
			dependencyInfo.pImageMemoryBarriers = &toSource;
			state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

			const int nextWidth = Max( 1, mipWidth >> 1 );
			const int nextHeight = Max( 1, mipHeight >> 1 );
			VkImageBlit blit;
			memset( &blit, 0, sizeof( blit ) );
			blit.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			blit.srcSubresource.mipLevel = level - 1;
			blit.srcSubresource.layerCount = 1;
			blit.srcOffsets[ 1 ].x = mipWidth;
			blit.srcOffsets[ 1 ].y = mipHeight;
			blit.srcOffsets[ 1 ].z = 1;
			blit.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			blit.dstSubresource.mipLevel = level;
			blit.dstSubresource.layerCount = 1;
			blit.dstOffsets[ 1 ].x = nextWidth;
			blit.dstOffsets[ 1 ].y = nextHeight;
			blit.dstOffsets[ 1 ].z = 1;
			state->CmdBlitImage( commandBuffer, resource.image,
				VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, resource.image,
				VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &blit, VK_FILTER_LINEAR );
			mipWidth = nextWidth;
			mipHeight = nextHeight;
		}

		VkImageMemoryBarrier2 shaderBarriers[ 2 ];
		memset( shaderBarriers, 0, sizeof( shaderBarriers ) );
		unsigned int shaderBarrierCount = 0;
		if ( mipLevels > 1 ) {
			VkImageMemoryBarrier2& fromSource = shaderBarriers[ shaderBarrierCount++ ];
			fromSource.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
			fromSource.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
			fromSource.srcAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
			fromSource.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
			fromSource.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
			fromSource.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
			fromSource.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
			fromSource.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			fromSource.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
			fromSource.image = resource.image;
			fromSource.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			fromSource.subresourceRange.levelCount = mipLevels - 1;
			fromSource.subresourceRange.layerCount = 1;
		}
		VkImageMemoryBarrier2& fromDestination = shaderBarriers[ shaderBarrierCount++ ];
		fromDestination.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		fromDestination.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		fromDestination.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		fromDestination.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		fromDestination.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		fromDestination.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		fromDestination.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		fromDestination.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromDestination.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromDestination.image = resource.image;
		fromDestination.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		fromDestination.subresourceRange.baseMipLevel = mipLevels - 1;
		fromDestination.subresourceRange.levelCount = 1;
		fromDestination.subresourceRange.layerCount = 1;
		dependencyInfo.imageMemoryBarrierCount = shaderBarrierCount;
		dependencyInfo.pImageMemoryBarriers = shaderBarriers;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		uploadSucceeded = SubmitImmediate( *state, commandPool, commandBuffer );
		commandPool = VK_NULL_HANDLE;
	}
	if ( commandPool != VK_NULL_HANDLE ) {
		state->DestroyCommandPool( state->device, commandPool, NULL );
	}
	state->DestroyBuffer( state->device, stagingBuffer, NULL );
	state->FreeMemory( state->device, stagingMemory, NULL );
	if ( !uploadSucceeded ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	const int imageIndex = state->imageResources.Append( resource );
	state->imageResourceLookup.Set( resource.owner, imageIndex );
	return true;
}

namespace {

struct vulkanBlockFormatInfo_t {
	VkFormat			format;
	int					blockBytes;
	VkComponentMapping	swizzle;
};

// The view swizzles make each format sample as DecodeDDSLevelRGBA's RGBA does (RXGB
// with its alpha moved into red, ATI1 as luminance), except ATI2, whose two blocks
// are swapped so a normal's X is in red; the Vulkan decode fallback swaps likewise.
// Without imageViewFormatSwizzle, SupportsBlockFormat refuses the swizzled formats.
bool GetVulkanBlockFormat( vulkanBlockFormat_t blockFormat, vulkanBlockFormatInfo_t& info ) {
	const VkComponentSwizzle I = VK_COMPONENT_SWIZZLE_IDENTITY;
	info.swizzle.r = info.swizzle.g = info.swizzle.b = info.swizzle.a = I;
	switch ( blockFormat ) {
		case VBF_BC1_RGB: info.format = VK_FORMAT_BC1_RGB_UNORM_BLOCK; info.blockBytes = 8; return true;
		case VBF_BC1_RGBA: info.format = VK_FORMAT_BC1_RGBA_UNORM_BLOCK; info.blockBytes = 8; return true;
		case VBF_BC2: info.format = VK_FORMAT_BC2_UNORM_BLOCK; info.blockBytes = 16; return true;
		case VBF_BC3: info.format = VK_FORMAT_BC3_UNORM_BLOCK; info.blockBytes = 16; return true;
		case VBF_BC3_RXGB:
			// RXGB keeps red in the alpha block: (A, G, B, 1)
			info.format = VK_FORMAT_BC3_UNORM_BLOCK;
			info.blockBytes = 16;
			info.swizzle.r = VK_COMPONENT_SWIZZLE_A;
			info.swizzle.a = VK_COMPONENT_SWIZZLE_ONE;
			return true;
		case VBF_BC4:
			// ATI1 / LATC1 decodes to (L, L, L, 1)
			info.format = VK_FORMAT_BC4_UNORM_BLOCK;
			info.blockBytes = 8;
			info.swizzle.g = VK_COMPONENT_SWIZZLE_R;
			info.swizzle.b = VK_COMPONENT_SWIZZLE_R;
			info.swizzle.a = VK_COMPONENT_SWIZZLE_ONE;
			return true;
		case VBF_BC5:
			// ATI2 keeps a normal's Y in its first block and X in its second: retail
			// uploads it as LATC2, and its programs read X from alpha (the second
			// block) and Y from green (the first).  BC5 samples the blocks as (R, G),
			// so swap them to give (X, Y, 0, 1).
			info.format = VK_FORMAT_BC5_UNORM_BLOCK;
			info.blockBytes = 16;
			info.swizzle.r = VK_COMPONENT_SWIZZLE_G;
			info.swizzle.g = VK_COMPONENT_SWIZZLE_R;
			return true;
		default:
			return false;
	}
}

VkDeviceSize VulkanBlockLevelBytes( int width, int height, int blockBytes ) {
	return static_cast< VkDeviceSize >( ( width + 3 ) / 4 ) *
		static_cast< VkDeviceSize >( ( height + 3 ) / 4 ) *
		static_cast< VkDeviceSize >( blockBytes );
}

// Copies every level of a block-compressed image from the staging buffer and
// leaves the whole image ready for sampling.  No mipmaps are generated: the
// levels come from the file.
void RecordBlockImageUploadCommands( sdVulkanBackendState& uploadState,
	VkCommandBuffer commandBuffer, VkBuffer stagingBuffer,
	VkDeviceSize stagingOffset, const sdVulkanImageResource& resource,
	int blockBytes ) {
	VkImageMemoryBarrier2 barrier;
	memset( &barrier, 0, sizeof( barrier ) );
	barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barrier.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
	barrier.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	barrier.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barrier.image = resource.image;
	barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	barrier.subresourceRange.levelCount = static_cast< unsigned int >( resource.mipLevels );
	barrier.subresourceRange.layerCount = 1;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.imageMemoryBarrierCount = 1;
	dependencyInfo.pImageMemoryBarriers = &barrier;
	uploadState.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );

	VkBufferImageCopy regions[ 16 ];
	int mipWidth = resource.width;
	int mipHeight = resource.height;
	VkDeviceSize offset = stagingOffset;
	for ( int level = 0; level < resource.mipLevels; ++level ) {
		VkBufferImageCopy& region = regions[ level ];
		memset( &region, 0, sizeof( region ) );
		region.bufferOffset = offset;
		region.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		region.imageSubresource.mipLevel = static_cast< unsigned int >( level );
		region.imageSubresource.layerCount = 1;
		region.imageExtent.width = static_cast< unsigned int >( mipWidth );
		region.imageExtent.height = static_cast< unsigned int >( mipHeight );
		region.imageExtent.depth = 1;
		offset += VulkanBlockLevelBytes( mipWidth, mipHeight, blockBytes );
		mipWidth = Max( 1, mipWidth >> 1 );
		mipHeight = Max( 1, mipHeight >> 1 );
	}
	uploadState.CmdCopyBufferToImage( commandBuffer, stagingBuffer, resource.image,
		VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
		static_cast< unsigned int >( resource.mipLevels ), regions );

	barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
	barrier.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	uploadState.CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
}

}

bool sdVulkanBackend::SupportsBlockFormat( vulkanBlockFormat_t blockFormat, bool linearFilter ) const {
	vulkanBlockFormatInfo_t info;
	if ( state == NULL || state->device == VK_NULL_HANDLE ||
		!state->textureCompressionBC || !GetVulkanBlockFormat( blockFormat, info ) ) {
		return false;
	}
	const VkComponentSwizzle I = VK_COMPONENT_SWIZZLE_IDENTITY;
	if ( !state->imageViewFormatSwizzle && ( info.swizzle.r != I || info.swizzle.g != I ||
		info.swizzle.b != I || info.swizzle.a != I ) ) {
		return false;
	}
	VkFormatProperties properties;
	state->GetPhysicalDeviceFormatProperties( state->physicalDevice, info.format, &properties );
	VkFormatFeatureFlags needed = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT |
		VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
	if ( linearFilter ) {
		// textureCompressionBC already guarantees it; checked for the sampler's sake
		needed |= VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT;
	}
	return ( properties.optimalTilingFeatures & needed ) == needed;
}

bool sdVulkanBackend::UploadBlockImage2D( const void* owner, vulkanBlockFormat_t blockFormat,
	const unsigned char* blocks, size_t blockDataBytes, int width, int height,
	int mipLevels, bool linearFilter, bool repeat ) {
	RENDER_METRIC_SCOPE( "Vulkan create/upload image" );
	vulkanBlockFormatInfo_t info;
	if ( owner == NULL || blocks == NULL || width <= 0 || height <= 0 ||
		mipLevels < 1 || !SupportsBlockFormat( blockFormat, linearFilter ) ||
		!GetVulkanBlockFormat( blockFormat, info ) ) {
		return false;
	}
	int maximumMipLevels = 1;
	for ( int size = Max( width, height ); size > 1; size >>= 1 ) {
		maximumMipLevels++;
	}
	mipLevels = Min( mipLevels, maximumMipLevels );	// a header may claim more levels than the size has
	if ( mipLevels > 16 ) {
		return false;	// 65536 texels or more: past RecordBlockImageUploadCommands' 16 regions
	}
	VkDeviceSize uploadBytes = 0;
	for ( int level = 0, w = width, h = height; level < mipLevels; ++level ) {
		uploadBytes += VulkanBlockLevelBytes( w, h, info.blockBytes );
		w = Max( 1, w >> 1 );
		h = Max( 1, h >> 1 );
	}
	if ( uploadBytes > static_cast< VkDeviceSize >( blockDataBytes ) ) {
		return false;
	}
	DestroyImage( owner );

	sdVulkanImageResource resource;
	if ( !CreateSampledImageResource( *state, owner, info.format, info.swizzle,
		width, height, mipLevels, VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT,
		linearFilter, repeat, resource ) ) {
		return false;
	}
	resource.blockCompressed = true;

	VkBuffer stagingBuffer = VK_NULL_HANDLE;
	VkDeviceSize stagingOffset = 0;
	if ( state->frameActive ) {
		sdVulkanFrame& frame = state->frames[ state->frameIndex ];
		stagingBuffer = frame.imageUploadBuffer;
		stagingOffset = ( frame.imageUploadOffset + 15 ) & ~15ULL;
		if ( frame.imageUploadMapped != NULL &&
			stagingOffset + uploadBytes <= VULKAN_IMAGE_UPLOAD_BYTES ) {
			memcpy( static_cast< byte* >( frame.imageUploadMapped ) + stagingOffset,
				blocks, static_cast< size_t >( uploadBytes ) );
			frame.imageUploadOffset = stagingOffset + uploadBytes;
		} else {
			if ( !frame.imageUploadOverflowWarned ) {
				common->Warning( "Vulkan image upload arena exhausted (%u MiB); using fence-retired spill buffers",
					static_cast< unsigned int >(
						VULKAN_IMAGE_UPLOAD_BYTES / ( 1024 * 1024 ) ) );
				frame.imageUploadOverflowWarned = true;
			}
			sdVulkanBufferResource spill;
			memset( &spill, 0, sizeof( spill ) );
			spill.bytes = uploadBytes;
			if ( !CreateBufferAllocation( *state, uploadBytes,
				VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
				VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
					VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
				spill.buffer, spill.memory ) ||
				!CheckVulkanResult( state->MapMemory( state->device, spill.memory,
					0, uploadBytes, 0, &spill.mapped ),
					"vkMapMemory(block image upload spill)" ) ) {
				DestroyBufferResource( *state, spill );
				DestroyImageResource( *state, resource );
				return false;
			}
			memcpy( spill.mapped, blocks, static_cast< size_t >( uploadBytes ) );
			state->UnmapMemory( state->device, spill.memory );
			spill.mapped = NULL;
			stagingBuffer = spill.buffer;
			stagingOffset = 0;
			state->frameRetiredBufferResources[ state->frameIndex ].Append( spill );
		}
		RecordBlockImageUploadCommands( *state, frame.imageUploadCommandBuffer,
			stagingBuffer, stagingOffset, resource, info.blockBytes );
		frame.imageUploadRecorded = true;
		const int imageIndex = state->imageResources.Append( resource );
		state->imageResourceLookup.Set( resource.owner, imageIndex );
		return true;
	}

	VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
	void* mapped = NULL;
	if ( !CreateBufferAllocation( *state, uploadBytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		stagingBuffer, stagingMemory ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	if ( !CheckVulkanResult( state->MapMemory( state->device, stagingMemory, 0,
		uploadBytes, 0, &mapped ), "vkMapMemory(block image upload)" ) ) {
		state->DestroyBuffer( state->device, stagingBuffer, NULL );
		state->FreeMemory( state->device, stagingMemory, NULL );
		DestroyImageResource( *state, resource );
		return false;
	}
	memcpy( mapped, blocks, static_cast< size_t >( uploadBytes ) );
	state->UnmapMemory( state->device, stagingMemory );

	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
	poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
	poolInfo.queueFamilyIndex = state->queueFamilyIndex;
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	bool uploadSucceeded = CheckVulkanResult( state->CreateCommandPool(
		state->device, &poolInfo, NULL, &commandPool ), "vkCreateCommandPool(block upload)" );
	if ( uploadSucceeded ) {
		VkCommandBufferAllocateInfo commandAllocateInfo;
		memset( &commandAllocateInfo, 0, sizeof( commandAllocateInfo ) );
		commandAllocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		commandAllocateInfo.commandPool = commandPool;
		commandAllocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		commandAllocateInfo.commandBufferCount = 1;
		uploadSucceeded = CheckVulkanResult( state->AllocateCommandBuffers(
			state->device, &commandAllocateInfo, &commandBuffer ),
			"vkAllocateCommandBuffers(block upload)" );
	}
	if ( uploadSucceeded ) {
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		uploadSucceeded = CheckVulkanResult( state->BeginCommandBuffer(
			commandBuffer, &beginInfo ), "vkBeginCommandBuffer(block upload)" );
	}
	if ( uploadSucceeded ) {
		RecordBlockImageUploadCommands( *state, commandBuffer, stagingBuffer, 0,
			resource, info.blockBytes );
		uploadSucceeded = SubmitImmediate( *state, commandPool, commandBuffer );
		commandPool = VK_NULL_HANDLE;
	}
	if ( commandPool != VK_NULL_HANDLE ) {
		state->DestroyCommandPool( state->device, commandPool, NULL );
	}
	state->DestroyBuffer( state->device, stagingBuffer, NULL );
	state->FreeMemory( state->device, stagingMemory, NULL );
	if ( !uploadSucceeded ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	const int imageIndex = state->imageResources.Append( resource );
	state->imageResourceLookup.Set( resource.owner, imageIndex );
	return true;
}

bool sdVulkanBackend::UploadImageCube( const void* owner,
	const unsigned char* const rgba[ 6 ], int size, bool linearFilter ) {
	RENDER_METRIC_SCOPE( "Vulkan create/upload cube image" );
	if ( state == NULL || state->device == VK_NULL_HANDLE || owner == NULL ||
		rgba == NULL || size <= 0 ) {
		return false;
	}
	for ( int face = 0; face < 6; ++face ) {
		if ( rgba[ face ] == NULL ) {
			return false;
		}
	}
	DestroyImage( owner );
	sdVulkanImageResource resource;
	memset( &resource, 0, sizeof( resource ) );
	resource.owner = owner;
	resource.width = size;
	resource.height = size;
	resource.mipLevels = 1;
	resource.cubeMap = true;
	VkImageCreateInfo imageInfo;
	memset( &imageInfo, 0, sizeof( imageInfo ) );
	imageInfo.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
	imageInfo.flags = VK_IMAGE_CREATE_CUBE_COMPATIBLE_BIT;
	imageInfo.imageType = VK_IMAGE_TYPE_2D;
	imageInfo.format = VK_FORMAT_R8G8B8A8_UNORM;
	imageInfo.extent.width = size;
	imageInfo.extent.height = size;
	imageInfo.extent.depth = 1;
	imageInfo.mipLevels = 1;
	imageInfo.arrayLayers = 6;
	imageInfo.samples = VK_SAMPLE_COUNT_1_BIT;
	imageInfo.tiling = VK_IMAGE_TILING_OPTIMAL;
	imageInfo.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT |
		VK_IMAGE_USAGE_SAMPLED_BIT;
	imageInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	imageInfo.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
	if ( !CheckVulkanResult( state->CreateImage( state->device, &imageInfo,
		NULL, &resource.image ), "vkCreateImage(cube)" ) ) {
		return false;
	}
	VkMemoryRequirements imageRequirements;
	state->GetImageMemoryRequirements( state->device, resource.image,
		&imageRequirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( *state, imageRequirements.memoryTypeBits,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, memoryType ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	VkMemoryAllocateInfo memoryInfo;
	memset( &memoryInfo, 0, sizeof( memoryInfo ) );
	memoryInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	memoryInfo.allocationSize = imageRequirements.size;
	memoryInfo.memoryTypeIndex = memoryType;
	if ( !CheckVulkanResult( state->AllocateMemory( state->device, &memoryInfo,
		NULL, &resource.memory ), "vkAllocateMemory(cube)" ) ||
		!CheckVulkanResult( state->BindImageMemory( state->device, resource.image,
			resource.memory, 0 ), "vkBindImageMemory(cube)" ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	VkImageViewCreateInfo viewInfo;
	memset( &viewInfo, 0, sizeof( viewInfo ) );
	viewInfo.sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO;
	viewInfo.image = resource.image;
	viewInfo.viewType = VK_IMAGE_VIEW_TYPE_CUBE;
	viewInfo.format = VK_FORMAT_R8G8B8A8_UNORM;
	viewInfo.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	viewInfo.subresourceRange.levelCount = 1;
	viewInfo.subresourceRange.layerCount = 6;
	if ( !CheckVulkanResult( state->CreateImageView( state->device, &viewInfo,
		NULL, &resource.view ), "vkCreateImageView(cube)" ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	VkSamplerCreateInfo samplerInfo;
	memset( &samplerInfo, 0, sizeof( samplerInfo ) );
	samplerInfo.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
	samplerInfo.magFilter = linearFilter ? VK_FILTER_LINEAR : VK_FILTER_NEAREST;
	samplerInfo.minFilter = samplerInfo.magFilter;
	samplerInfo.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
	samplerInfo.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.maxLod = 0.0f;
	if ( !CheckVulkanResult( state->CreateSampler( state->device, &samplerInfo,
		NULL, &resource.sampler ), "vkCreateSampler(cube)" ) ||
		!AllocateImageDescriptor( *state, resource ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	const VkDeviceSize faceBytes = static_cast< VkDeviceSize >( size ) *
		static_cast< VkDeviceSize >( size ) * 4;
	const VkDeviceSize uploadBytes = faceBytes * 6;
	if ( state->frameActive ) {
		sdVulkanFrame& frame = state->frames[ state->frameIndex ];
		VkBuffer stagingBuffer = frame.imageUploadBuffer;
		VkDeviceSize stagingOffset = ( frame.imageUploadOffset + 15 ) & ~15ULL;
		byte* mapped = NULL;
		if ( frame.imageUploadMapped != NULL &&
			stagingOffset + uploadBytes <= VULKAN_IMAGE_UPLOAD_BYTES ) {
			mapped = static_cast< byte* >( frame.imageUploadMapped ) + stagingOffset;
			frame.imageUploadOffset = stagingOffset + uploadBytes;
		} else {
			if ( !frame.imageUploadOverflowWarned ) {
				common->Warning( "Vulkan image upload arena exhausted (%u MiB); using fence-retired spill buffers",
					static_cast< unsigned int >(
						VULKAN_IMAGE_UPLOAD_BYTES / ( 1024 * 1024 ) ) );
				frame.imageUploadOverflowWarned = true;
			}
			sdVulkanBufferResource spill;
			memset( &spill, 0, sizeof( spill ) );
			spill.bytes = uploadBytes;
			if ( !CreateBufferAllocation( *state, uploadBytes,
				VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
				VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
					VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
				spill.buffer, spill.memory ) ||
				!CheckVulkanResult( state->MapMemory( state->device, spill.memory,
					0, uploadBytes, 0, &spill.mapped ),
					"vkMapMemory(cube upload spill)" ) ) {
				DestroyBufferResource( *state, spill );
				DestroyImageResource( *state, resource );
				return false;
			}
			mapped = static_cast< byte* >( spill.mapped );
			stagingBuffer = spill.buffer;
			stagingOffset = 0;
			for ( int face = 0; face < 6; ++face ) {
				memcpy( mapped + faceBytes * face, rgba[ face ],
					static_cast< size_t >( faceBytes ) );
			}
			state->UnmapMemory( state->device, spill.memory );
			spill.mapped = NULL;
			state->frameRetiredBufferResources[ state->frameIndex ].Append( spill );
			mapped = NULL;
		}
		if ( mapped != NULL ) {
			for ( int face = 0; face < 6; ++face ) {
				memcpy( mapped + faceBytes * face, rgba[ face ],
					static_cast< size_t >( faceBytes ) );
			}
		}

		VkImageMemoryBarrier2 barrier;
		memset( &barrier, 0, sizeof( barrier ) );
		barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		barrier.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
		barrier.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		barrier.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
		barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		barrier.image = resource.image;
		barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		barrier.subresourceRange.levelCount = 1;
		barrier.subresourceRange.layerCount = 6;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.imageMemoryBarrierCount = 1;
		dependencyInfo.pImageMemoryBarriers = &barrier;
		state->CmdPipelineBarrier2( frame.imageUploadCommandBuffer, &dependencyInfo );
		VkBufferImageCopy copyRegions[ 6 ];
		memset( copyRegions, 0, sizeof( copyRegions ) );
		for ( int face = 0; face < 6; ++face ) {
			copyRegions[ face ].bufferOffset = stagingOffset + faceBytes * face;
			copyRegions[ face ].imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			copyRegions[ face ].imageSubresource.baseArrayLayer = face;
			copyRegions[ face ].imageSubresource.layerCount = 1;
			copyRegions[ face ].imageExtent.width = size;
			copyRegions[ face ].imageExtent.height = size;
			copyRegions[ face ].imageExtent.depth = 1;
		}
		state->CmdCopyBufferToImage( frame.imageUploadCommandBuffer,
			stagingBuffer, resource.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
			6, copyRegions );
		barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		barrier.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		state->CmdPipelineBarrier2( frame.imageUploadCommandBuffer, &dependencyInfo );
		frame.imageUploadRecorded = true;
		const int imageIndex = state->imageResources.Append( resource );
		state->imageResourceLookup.Set( resource.owner, imageIndex );
		return true;
	}
	VkBuffer stagingBuffer = VK_NULL_HANDLE;
	VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
	if ( !CreateBufferAllocation( *state, uploadBytes,
		VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		stagingBuffer, stagingMemory ) ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	void* mapped = NULL;
	if ( !CheckVulkanResult( state->MapMemory( state->device, stagingMemory, 0,
		uploadBytes, 0, &mapped ), "vkMapMemory(cube upload)" ) ) {
		state->DestroyBuffer( state->device, stagingBuffer, NULL );
		state->FreeMemory( state->device, stagingMemory, NULL );
		DestroyImageResource( *state, resource );
		return false;
	}
	for ( int face = 0; face < 6; ++face ) {
		memcpy( static_cast< byte* >( mapped ) + faceBytes * face,
			rgba[ face ], static_cast< size_t >( faceBytes ) );
	}
	state->UnmapMemory( state->device, stagingMemory );
	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	VkCommandPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
	poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
	poolInfo.queueFamilyIndex = state->queueFamilyIndex;
	bool succeeded = CheckVulkanResult( state->CreateCommandPool( state->device,
		&poolInfo, NULL, &commandPool ), "vkCreateCommandPool(cube upload)" );
	if ( succeeded ) {
		VkCommandBufferAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		allocateInfo.commandPool = commandPool;
		allocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		allocateInfo.commandBufferCount = 1;
		succeeded = CheckVulkanResult( state->AllocateCommandBuffers( state->device,
			&allocateInfo, &commandBuffer ), "vkAllocateCommandBuffers(cube upload)" );
	}
	if ( succeeded ) {
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		succeeded = CheckVulkanResult( state->BeginCommandBuffer( commandBuffer,
			&beginInfo ), "vkBeginCommandBuffer(cube upload)" );
	}
	if ( succeeded ) {
		VkImageMemoryBarrier2 barrier;
		memset( &barrier, 0, sizeof( barrier ) );
		barrier.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		barrier.srcStageMask = VK_PIPELINE_STAGE_2_NONE;
		barrier.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		barrier.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
		barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		barrier.image = resource.image;
		barrier.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		barrier.subresourceRange.levelCount = 1;
		barrier.subresourceRange.layerCount = 6;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.imageMemoryBarrierCount = 1;
		dependencyInfo.pImageMemoryBarriers = &barrier;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		VkBufferImageCopy copyRegions[ 6 ];
		memset( copyRegions, 0, sizeof( copyRegions ) );
		for ( int face = 0; face < 6; ++face ) {
			copyRegions[ face ].bufferOffset = faceBytes * face;
			copyRegions[ face ].imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
			copyRegions[ face ].imageSubresource.baseArrayLayer = face;
			copyRegions[ face ].imageSubresource.layerCount = 1;
			copyRegions[ face ].imageExtent.width = size;
			copyRegions[ face ].imageExtent.height = size;
			copyRegions[ face ].imageExtent.depth = 1;
		}
		state->CmdCopyBufferToImage( commandBuffer, stagingBuffer, resource.image,
			VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 6, copyRegions );
		barrier.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		barrier.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		barrier.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		barrier.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		succeeded = SubmitImmediate( *state, commandPool, commandBuffer );
		commandPool = VK_NULL_HANDLE;
	}
	if ( commandPool != VK_NULL_HANDLE ) {
		state->DestroyCommandPool( state->device, commandPool, NULL );
	}
	state->DestroyBuffer( state->device, stagingBuffer, NULL );
	state->FreeMemory( state->device, stagingMemory, NULL );
	if ( !succeeded ) {
		DestroyImageResource( *state, resource );
		return false;
	}
	const int imageIndex = state->imageResources.Append( resource );
	state->imageResourceLookup.Set( resource.owner, imageIndex );
	return true;
}

bool sdVulkanBackend::CanQueueImageUpload( int bytes ) const {
	if ( bytes < 0 || state == NULL ) {
		return false;
	}
	if ( !state->frameActive ) {
		return true;
	}
	const sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const VkDeviceSize alignedOffset = ( frame.imageUploadOffset + 15 ) & ~15ULL;
	return frame.imageUploadMapped != NULL &&
		alignedOffset + static_cast< VkDeviceSize >( bytes ) <=
		VULKAN_IMAGE_UPLOAD_BYTES;
}

bool sdVulkanBackend::UpdateImage2D( const void* owner, int mipLevel,
	int x, int y, int width, int height, const unsigned char* rgba ) {
	if ( state == NULL || owner == NULL || rgba == NULL || mipLevel < 0 ||
		x < 0 || y < 0 || width <= 0 || height <= 0 ) {
		return false;
	}
	sdVulkanImageResource* resource = FindVulkanImageResource( *state, owner );
	if ( resource == NULL || mipLevel >= resource->mipLevels || resource->blockCompressed ) {
		return false;
	}
	const int mipWidth = Max( 1, resource->width >> mipLevel );
	const int mipHeight = Max( 1, resource->height >> mipLevel );
	if ( x + width > mipWidth || y + height > mipHeight ) {
		return false;
	}
	const VkDeviceSize uploadBytes = static_cast< VkDeviceSize >( width ) *
		static_cast< VkDeviceSize >( height ) * 4;
	if ( state->frameActive ) {
		sdVulkanFrame& frame = state->frames[ state->frameIndex ];
		const VkDeviceSize uploadOffset =
			( frame.imageUploadOffset + 15 ) & ~15ULL;
		if ( frame.imageUploadMapped == NULL ||
			uploadOffset + uploadBytes > VULKAN_IMAGE_UPLOAD_BYTES ) {
			if ( !frame.imageUploadOverflowWarned ) {
				common->Warning( "Vulkan image upload arena exhausted (%u MiB)",
					static_cast< unsigned int >(
						VULKAN_IMAGE_UPLOAD_BYTES / ( 1024 * 1024 ) ) );
				frame.imageUploadOverflowWarned = true;
			}
			return false;
		}
		memcpy( static_cast< byte* >( frame.imageUploadMapped ) + uploadOffset,
			rgba, static_cast< size_t >( uploadBytes ) );

		VkImageMemoryBarrier2 toTransfer;
		memset( &toTransfer, 0, sizeof( toTransfer ) );
		toTransfer.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		toTransfer.srcStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		toTransfer.srcAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		toTransfer.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toTransfer.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toTransfer.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		toTransfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toTransfer.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.image = resource->image;
		toTransfer.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		toTransfer.subresourceRange.baseMipLevel = mipLevel;
		toTransfer.subresourceRange.levelCount = 1;
		toTransfer.subresourceRange.layerCount = 1;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.imageMemoryBarrierCount = 1;
		dependencyInfo.pImageMemoryBarriers = &toTransfer;
		state->CmdPipelineBarrier2( frame.imageUploadCommandBuffer,
			&dependencyInfo );

		VkBufferImageCopy copy;
		memset( &copy, 0, sizeof( copy ) );
		copy.bufferOffset = uploadOffset;
		copy.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		copy.imageSubresource.mipLevel = mipLevel;
		copy.imageSubresource.layerCount = 1;
		copy.imageOffset.x = x;
		copy.imageOffset.y = y;
		copy.imageExtent.width = width;
		copy.imageExtent.height = height;
		copy.imageExtent.depth = 1;
		state->CmdCopyBufferToImage( frame.imageUploadCommandBuffer,
			frame.imageUploadBuffer, resource->image,
			VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy );

		VkImageMemoryBarrier2 toShader = toTransfer;
		toShader.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toShader.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toShader.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		toShader.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		toShader.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toShader.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		dependencyInfo.pImageMemoryBarriers = &toShader;
		state->CmdPipelineBarrier2( frame.imageUploadCommandBuffer,
			&dependencyInfo );
		frame.imageUploadOffset = uploadOffset + uploadBytes;
		frame.imageUploadRecorded = true;
		return true;
	}
	VkBuffer stagingBuffer = VK_NULL_HANDLE;
	VkDeviceMemory stagingMemory = VK_NULL_HANDLE;
	if ( !CreateBufferAllocation( *state, uploadBytes,
		VK_BUFFER_USAGE_TRANSFER_SRC_BIT,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		stagingBuffer, stagingMemory ) ) {
		return false;
	}
	void* mapped = NULL;
	bool succeeded = CheckVulkanResult( state->MapMemory( state->device,
		stagingMemory, 0, uploadBytes, 0, &mapped ),
		"vkMapMemory(image update)" );
	if ( succeeded ) {
		memcpy( mapped, rgba, static_cast< size_t >( uploadBytes ) );
		state->UnmapMemory( state->device, stagingMemory );
	}
	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	if ( succeeded ) {
		VkCommandPoolCreateInfo poolInfo;
		memset( &poolInfo, 0, sizeof( poolInfo ) );
		poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
		poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
		poolInfo.queueFamilyIndex = state->queueFamilyIndex;
		succeeded = CheckVulkanResult( state->CreateCommandPool( state->device,
			&poolInfo, NULL, &commandPool ), "vkCreateCommandPool(image update)" );
	}
	if ( succeeded ) {
		VkCommandBufferAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		allocateInfo.commandPool = commandPool;
		allocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		allocateInfo.commandBufferCount = 1;
		succeeded = CheckVulkanResult( state->AllocateCommandBuffers(
			state->device, &allocateInfo, &commandBuffer ),
			"vkAllocateCommandBuffers(image update)" );
	}
	if ( succeeded ) {
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		succeeded = CheckVulkanResult( state->BeginCommandBuffer( commandBuffer,
			&beginInfo ), "vkBeginCommandBuffer(image update)" );
	}
	if ( succeeded ) {
		VkImageMemoryBarrier2 toTransfer;
		memset( &toTransfer, 0, sizeof( toTransfer ) );
		toTransfer.sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
		toTransfer.srcStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		toTransfer.srcAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		toTransfer.dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toTransfer.dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toTransfer.oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		toTransfer.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toTransfer.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer.image = resource->image;
		toTransfer.subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		toTransfer.subresourceRange.baseMipLevel = mipLevel;
		toTransfer.subresourceRange.levelCount = 1;
		toTransfer.subresourceRange.layerCount = 1;
		VkDependencyInfo dependencyInfo;
		memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
		dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
		dependencyInfo.imageMemoryBarrierCount = 1;
		dependencyInfo.pImageMemoryBarriers = &toTransfer;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		VkBufferImageCopy copy;
		memset( &copy, 0, sizeof( copy ) );
		copy.imageSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		copy.imageSubresource.mipLevel = mipLevel;
		copy.imageSubresource.layerCount = 1;
		copy.imageOffset.x = x;
		copy.imageOffset.y = y;
		copy.imageExtent.width = width;
		copy.imageExtent.height = height;
		copy.imageExtent.depth = 1;
		state->CmdCopyBufferToImage( commandBuffer, stagingBuffer,
			resource->image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy );
		VkImageMemoryBarrier2 toShader = toTransfer;
		toShader.srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
		toShader.srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
		toShader.dstStageMask = VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT;
		toShader.dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		toShader.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
		toShader.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		dependencyInfo.pImageMemoryBarriers = &toShader;
		state->CmdPipelineBarrier2( commandBuffer, &dependencyInfo );
		succeeded = SubmitImmediate( *state, commandPool, commandBuffer );
		commandPool = VK_NULL_HANDLE;
	}
	if ( commandPool != VK_NULL_HANDLE ) {
		state->DestroyCommandPool( state->device, commandPool, NULL );
	}
	state->DestroyBuffer( state->device, stagingBuffer, NULL );
	state->FreeMemory( state->device, stagingMemory, NULL );
	return succeeded;
}

void sdVulkanBackend::DestroyImage( const void* owner ) {
	if ( state == NULL || state->device == VK_NULL_HANDLE || owner == NULL ) {
		return;
	}
	for ( int i = 0; i < state->imageResources.Num(); ++i ) {
		if ( state->imageResources[ i ].owner == owner ) {
			sdVulkanImageResource removed = state->imageResources[ i ];
			// Composite material descriptors do not own their sampled images.  Once
			// any constituent image is replaced, never return the old descriptor
			// again: its VkImageView remains the destroyed view even if Vulkan later
			// reuses the same numeric handle for the replacement.  The set may already
			// be referenced by this frame's command buffer, so it is freed once this
			// frame has completed, with the image itself (#262); the entry leaves the
			// cache now.
			const unsigned int setRetireFrame = state->frameActive ||
				state->toolFrameActive ? state->frameIndex :
				( state->frameIndex + NUM_VULKAN_FRAMES - 1 ) % NUM_VULKAN_FRAMES;
			for ( int descriptorIndex = state->materialDescriptors.Num() - 1;
				descriptorIndex >= 0; --descriptorIndex ) {
				const sdVulkanMaterialDescriptor& descriptor =
					state->materialDescriptors[ descriptorIndex ];
				bool usesRemovedImage = false;
				for ( int imageIndex = 0; imageIndex < VULKAN_MATERIAL_TEXTURES;
					++imageIndex ) {
					if ( descriptor.imageOwners[ imageIndex ] == owner ||
						descriptor.imageViews[ imageIndex ] == removed.view ) {
						usesRemovedImage = true;
						break;
					}
				}
				if ( usesRemovedImage ) {
					state->frameRetiredMaterialSets[ setRetireFrame ].Append( descriptor.descriptorSet );
					RemoveMaterialDescriptor( *state, descriptorIndex );
				}
			}
			// the same for the front-end ambient programs' sets, in their own pool (#258)
			for ( int descriptorIndex = state->frontEndDescriptors.Num() - 1;
				descriptorIndex >= 0; --descriptorIndex ) {
				const sdVulkanFrontEndDescriptor& descriptor =
					state->frontEndDescriptors[ descriptorIndex ];
				bool usesRemovedImage = false;
				for ( int imageIndex = 0; imageIndex < VULKAN_FRONTEND_TEXTURES;
					++imageIndex ) {
					if ( descriptor.imageOwners[ imageIndex ] == owner ||
						descriptor.imageViews[ imageIndex ] == removed.view ) {
						usesRemovedImage = true;
						break;
					}
				}
				if ( usesRemovedImage ) {
					state->frameRetiredFrontEndSets[ setRetireFrame ].Append( descriptor.descriptorSet );
					RemoveFrontEndDescriptor( *state, descriptorIndex );
				}
			}
			const int lastIndex = state->imageResources.Num() - 1;
			const void* movedOwner = i != lastIndex ?
				state->imageResources[ lastIndex ].owner : NULL;
			state->imageResourceLookup.Remove( owner );
			state->imageResources.RemoveIndexFast( i );
			if ( movedOwner != NULL ) {
				state->imageResourceLookup.Set( movedOwner, i );
			}
			const unsigned int retireFrame = state->frameActive ||
				state->toolFrameActive ? state->frameIndex :
				( state->frameIndex + NUM_VULKAN_FRAMES - 1 ) % NUM_VULKAN_FRAMES;
			state->frameRetiredImageResources[ retireFrame ].Append( removed );
			return;
		}
	}
}

bool sdVulkanBackend::UploadBuffer( const void* owner, const void* data,
	int bytes, bool indexBuffer, bool frameTemporary ) {
	RENDER_METRIC_SCOPE( "Vulkan create/upload buffer" );
	if ( state == NULL || state->device == VK_NULL_HANDLE || owner == NULL ||
		data == NULL || bytes <= 0 ) {
		return false;
	}
	DestroyBuffer( owner );
	sdVulkanBufferResource resource;
	memset( &resource, 0, sizeof( resource ) );
	resource.owner = owner;
	resource.bytes = static_cast< VkDeviceSize >( bytes );
	resource.indexBuffer = indexBuffer;
	// Frame-temporary allocations share one backing buffer for vertices and
	// indexes.  Static allocations use the same neutral capability so a cache
	// block can be rebound by the reconstructed interaction/deform paths without
	// creating a Vulkan buffer whose usage disagrees with the eventual bind.
	// The ray-tracing bits are only valid when the device enabled their features.
	VkBufferUsageFlags usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT |
		VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT;
	if ( state->rayTracingDeviceEnabled ) {
		usage |= VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT |
			VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR;
	}
	bool uploaded = false;
	if ( frameTemporary && CreatePooledBuffer( *state, usage,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		resource ) ) {
		memcpy( resource.mapped, data, static_cast< size_t >( resource.bytes ) );
		uploaded = true;
	}
	if ( !uploaded && CreatePooledBuffer( *state, usage,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, resource ) ) {
		uploaded = UploadBufferBytes( *state, resource.buffer, 0, data,
			resource.bytes, indexBuffer );
	}
	if ( !uploaded ) {
		DestroyBufferResource( *state, resource );
		return false;
	}
	const int resourceIndex = state->bufferResources.Append( resource );
	state->bufferResourceLookup.Set( resource.owner, resourceIndex );
	return true;
}

bool sdVulkanBackend::UpdateBuffer( const void* owner, int offset,
	const void* data, int bytes ) {
	if ( state == NULL || owner == NULL || data == NULL || offset < 0 ||
		bytes <= 0 ) {
		return false;
	}
	sdVulkanResourceLookup::Iterator found =
		state->bufferResourceLookup.Find( owner );
	if ( found != state->bufferResourceLookup.End() && found->second >= 0 &&
		found->second < state->bufferResources.Num() ) {
		sdVulkanBufferResource& resource = state->bufferResources[ found->second ];
		if ( static_cast< VkDeviceSize >( offset ) + bytes > resource.bytes ) {
			common->Warning( "Vulkan buffer update exceeds its allocation" );
			return false;
		}
		if ( resource.mapped != NULL ) {
			memcpy( static_cast< byte* >( resource.mapped ) + offset, data,
				static_cast< size_t >( bytes ) );
			return true;
		}
		return UploadBufferBytes( *state, resource.buffer, offset, data, bytes,
			resource.indexBuffer );
	}
	return false;
}

void sdVulkanBackend::DestroyBuffer( const void* owner ) {
	if ( state == NULL || state->device == VK_NULL_HANDLE || owner == NULL ) {
		return;
	}
	// UploadBuffer calls this for every vertex-cache block, so find the resource
	// through the lookup rather than scanning every live buffer.
	sdVulkanResourceLookup::Iterator found =
		state->bufferResourceLookup.Find( owner );
	if ( found == state->bufferResourceLookup.End() || found->second < 0 ||
		found->second >= state->bufferResources.Num() ||
		state->bufferResources[ found->second ].owner != owner ) {
		return;
	}
	const int i = found->second;
	sdVulkanBufferResource removed = state->bufferResources[ i ];
	const int lastIndex = state->bufferResources.Num() - 1;
	const void* movedOwner = i != lastIndex ?
		state->bufferResources[ lastIndex ].owner : NULL;
	state->bufferResourceLookup.Remove( owner );
	state->bufferResources.RemoveIndexFast( i );
	if ( movedOwner != NULL ) {
		state->bufferResourceLookup.Set( movedOwner, i );
	}
	const unsigned int retireFrame = state->frameActive ||
		state->toolFrameActive ? state->frameIndex :
		( state->frameIndex + NUM_VULKAN_FRAMES - 1 ) % NUM_VULKAN_FRAMES;
	state->frameRetiredBufferResources[ retireFrame ].Append( removed );
	DropPendingBufferUploads( *state, removed.buffer );
}

// The projection and viewport depth range the base pass draws a surface with
// (DrawView's per-space submission): the world in [r_depthRangeStartDefault, 1],
// a weapon in [0, r_depthRangeWeaponHackEnd] with its own field of view and
// depth scale.  Interactions and shadow volumes drawn with any other range or
// projection do not meet the base pass's depth and leak onto occluded surfaces.
static void BuildVulkanSurfaceTransform( const sdVulkanBackendState& state, const viewDef_s* view,
	const viewEntity_s* space, float mvp[ 16 ], VkViewport& surfaceViewport ) {
	float surfaceProjection[ 16 ];
	memcpy( surfaceProjection, view->projectionMatrix, sizeof( surfaceProjection ) );
	float surfaceMinDepth = idMath::ClampFloat( 0.0f, 1.0f,
		cvarSystem->GetCVarFloat( "r_depthRangeStartDefault" ) );
	float surfaceMaxDepth = 1.0f;
	if ( space->weaponDepthHack ) {
		const float zNear = Max( cvarSystem->GetCVarFloat( "r_znear" ), 0.001f );
		surfaceMinDepth = 0.0f;
		surfaceMaxDepth = idMath::ClampFloat( 0.0f, 1.0f,
			cvarSystem->GetCVarFloat( "r_depthRangeWeaponHackEnd" ) );
		const float weaponFovX = space->weaponDepthHackFOV_x;
		const float weaponFovY = space->weaponDepthHackFOV_y;
		if ( weaponFovX > 0.0f && weaponFovY > 0.0f ) {
			const float xMax = zNear * idMath::Tan( weaponFovX * idMath::M_DEG2RAD * 0.5f );
			const float yMax = zNear * idMath::Tan( weaponFovY * idMath::M_DEG2RAD * 0.5f );
			surfaceProjection[ 0 ] = zNear / Max( xMax, 0.001f );
			surfaceProjection[ 5 ] = zNear / Max( yMax, 0.001f );
			surfaceProjection[ 8 ] = 0.0f;
			surfaceProjection[ 9 ] = 0.0f;
		}
		surfaceProjection[ 14 ] *= cvarSystem->GetCVarFloat( "r_depthRangeWeaponHackScale" );
	}
	if ( space->modelDepthHack != 0.0f ) {
		// as the base pass: a model depth hack replaces the weapon's projection
		memcpy( surfaceProjection, view->projectionMatrix, sizeof( surfaceProjection ) );
		surfaceProjection[ 14 ] -= space->modelDepthHack;
		surfaceMinDepth = idMath::ClampFloat( 0.0f, 1.0f,
			cvarSystem->GetCVarFloat( "r_depthRangeStartDefault" ) );
		surfaceMaxDepth = 1.0f;
	}
	surfaceViewport.x = static_cast< float >( view->viewport.x1 );
	surfaceViewport.y = static_cast< float >( state.extent.height - view->viewport.y1 );
	surfaceViewport.width = static_cast< float >( Max( 1, view->viewport.x2 - view->viewport.x1 + 1 ) );
	surfaceViewport.height = -static_cast< float >( Max( 1, view->viewport.y2 - view->viewport.y1 + 1 ) );
	surfaceViewport.minDepth = surfaceMinDepth;
	surfaceViewport.maxDepth = Max( surfaceMinDepth, surfaceMaxDepth );
	for ( int column = 0; column < 4; ++column ) {
		for ( int row = 0; row < 4; ++row ) {
			mvp[ column * 4 + row ] =
				surfaceProjection[ row ] * space->modelViewMatrix[ column * 4 ] +
				surfaceProjection[ 4 + row ] * space->modelViewMatrix[ column * 4 + 1 ] +
				surfaceProjection[ 8 + row ] * space->modelViewMatrix[ column * 4 + 2 ] +
				surfaceProjection[ 12 + row ] * space->modelViewMatrix[ column * 4 + 3 ];
		}
		mvp[ column * 4 + 2 ] = 0.5f * ( mvp[ column * 4 + 2 ] + mvp[ column * 4 + 3 ] );
	}
}

void DrawVulkanShadowSurface( sdVulkanBackendState& state, const drawSurf_s* surface,
	const viewLight_s* light, const viewDef_s* view ) {
	if ( surface == NULL || surface->geo == NULL || surface->space == NULL || view == NULL ) {
		return;
	}
	const srfTriangles_t* tri = surface->geo;

	bool external = false;
	// The front end gives each light shadow volume geometry (tr_light.cpp's
	// CreateShadowVolumeGeometry, #124) with its no-caps counts; a count of 0
	// draws nothing.  Drawing the surface's own triangles instead smeared them to
	// infinity.
	int numIndexes = ShadowIndexCount( surface, light, external );
	if ( numIndexes <= 0 ) {
		return;
	}

	if ( tri->shadowCache == NULL && tri->shadowVertexes != NULL && tri->numVerts > 0 ) {
		vertexCache.Alloc( tri->shadowVertexes, tri->numVerts * sizeof( shadowCache_t ),
			const_cast<vertCache_t**>( &tri->shadowCache ), false );
	}
	if ( tri->indexCache == NULL && tri->indexes != NULL && tri->numIndexes > 0 ) {
		vertexCache.Alloc( tri->indexes, tri->numIndexes * sizeof( glIndex_t ),
			const_cast<vertCache_t**>( &tri->indexCache ), true );
	}
	if ( tri->shadowCache == NULL || tri->indexCache == NULL ) {
		return;
	}
	if ( tri->shadowCache->tag != TAG_TEMP ) {
		vertexCache.Touch( const_cast<vertCache_t*>( tri->shadowCache ) );
	}
	if ( tri->indexCache->tag != TAG_TEMP ) {
		vertexCache.Touch( const_cast<vertCache_t*>( tri->indexCache ) );
	}

	const void* vertexOwner = tri->shadowCache->backendBuffer != NULL ?
		tri->shadowCache->backendBuffer : tri->shadowCache;
	const void* indexOwner = tri->indexCache->backendBuffer != NULL ?
		tri->indexCache->backendBuffer : tri->indexCache;
	const sdVulkanBufferResource* vertexResource = FindVulkanBufferResource( state, vertexOwner );
	if ( vertexResource == NULL ) {
		vertexResource = FindVulkanBufferResource( state, tri->shadowCache );
	}
	const sdVulkanBufferResource* indexResource = FindVulkanBufferResource( state, indexOwner );
	if ( indexResource == NULL ) {
		indexResource = FindVulkanBufferResource( state, tri->indexCache );
	}
	if ( vertexResource == NULL || indexResource == NULL ) {
		return;
	}

	const VkDeviceSize vertexOffset = tri->shadowCache->offset;
	const VkDeviceSize indexOffset = tri->indexCache->offset;
	const VkDeviceSize indexBytes = static_cast< VkDeviceSize >( numIndexes ) * sizeof( glIndex_t );
	if ( tri->shadowCache->offset < 0 || tri->indexCache->offset < 0 ||
		 vertexOffset >= vertexResource->bytes ||
		 indexOffset + indexBytes > indexResource->bytes ) {
		return;
	}

	VkPipeline pipeline = external ? state.worldShadowPassPipeline : state.worldShadowFailPipeline;
	if ( pipeline == VK_NULL_HANDLE ) {
		return;
	}

	idScreenRect scissorRect = surface->scissorRect;
	if ( light != NULL ) {
		scissorRect.Intersect( light->scissorRect );
	}
	int scissorWidth = Max( 0, scissorRect.x2 - scissorRect.x1 + 1 );
	int scissorHeight = Max( 0, scissorRect.y2 - scissorRect.y1 + 1 );
	if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
		return;
	}
	VkRect2D vkScissor;
	vkScissor.offset.x = view->viewport.x1 + scissorRect.x1;
	vkScissor.offset.y = static_cast< int >( state.extent.height ) -
		( view->viewport.y1 + scissorRect.y1 + scissorHeight );
	if ( vkScissor.offset.x < 0 ) {
		scissorWidth -= Min( scissorWidth, -vkScissor.offset.x );
		vkScissor.offset.x = 0;
	}
	if ( vkScissor.offset.y < 0 ) {
		scissorHeight -= Min( scissorHeight, -vkScissor.offset.y );
		vkScissor.offset.y = 0;
	}
	scissorWidth = Min( scissorWidth,
		static_cast< int >( state.extent.width ) - vkScissor.offset.x );
	scissorHeight = Min( scissorHeight,
		static_cast< int >( state.extent.height ) - vkScissor.offset.y );
	if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
		return;
	}
	vkScissor.extent.width = static_cast< uint32_t >( scissorWidth );
	vkScissor.extent.height = static_cast< uint32_t >( scissorHeight );

	float mvp[ 16 ];
	VkViewport surfaceViewport;
	BuildVulkanSurfaceTransform( state, view, surface->space, mvp, surfaceViewport );

	sdVulkanFrame& frame = state.frames[ state.frameIndex ];
	state.CmdSetViewport( frame.commandBuffer, 0, 1, &surfaceViewport );
	state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
	state.CmdSetScissor( frame.commandBuffer, 0, 1, &vkScissor );
	// glPolygonOffset( factor, -offset ) as RB_ARB2_StencilShadowPass: the factor
	// is the slope bias, the negated offset the constant one
	state.CmdSetDepthBias( frame.commandBuffer,
		-r_shadowPolygonOffset.GetFloat(), 0.0f, r_shadowPolygonFactor.GetFloat() );
	state.CmdBindVertexBuffers( frame.commandBuffer, 0, 1, &vertexResource->buffer, &vertexOffset );
	state.CmdBindIndexBuffer( frame.commandBuffer, indexResource->buffer, indexOffset, VK_INDEX_TYPE_UINT16 );
	float shadowConstants[ 20 ];
	memcpy( shadowConstants, mvp, sizeof( mvp ) );
	{
		const float* m = surface->space->modelMatrix;	// axis rows 0-2, 4-6, 8-10, origin 12-14
		const idVec3 delta = light->globalLightOrigin - idVec3( m[ 12 ], m[ 13 ], m[ 14 ] );
		shadowConstants[ 16 ] = delta.x * m[ 0 ] + delta.y * m[ 1 ] + delta.z * m[ 2 ];
		shadowConstants[ 17 ] = delta.x * m[ 4 ] + delta.y * m[ 5 ] + delta.z * m[ 6 ];
		shadowConstants[ 18 ] = delta.x * m[ 8 ] + delta.y * m[ 9 ] + delta.z * m[ 10 ];
		shadowConstants[ 19 ] = 1.0f;
	}
	state.CmdPushConstants( frame.commandBuffer, state.guiPipelineLayout,
		VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
		sizeof( shadowConstants ), shadowConstants );
	state.CmdDrawIndexed( frame.commandBuffer, numIndexes, 1, 0, 0, 0 );
	state.worldShadowDrawCalls++;
}

// An instanced render entity (sdInstStatic, the maps' misc_instance_spawner trees
// and shrubs) draws its model once per space->insts entry, at that instance's
// origin and axis; the entity's own transform is the identity (#246).  OpenGL
// hands each instance to the *_instance programs (SetInstanceAttributes); the
// raster path here draws a copy of the surface per instance, whose space carries
// the instance's model matrix.  The copies live until the next view.
class sdVulkanInstanceSurfaces {
public:
	void Begin( const viewDef_s* drawView ) {
		view = drawView;
		spaces.clear();
		surfaces.clear();
	}
	// Calls draw( surface ) for an ordinary surface, or once per visible instance.
	template< typename DrawFunction >
	void ForEach( const drawSurf_s* surface, DrawFunction&& draw ) {
		if ( surface == NULL || surface->space == NULL || surface->space->numInsts <= 0 ||
			surface->space->insts == NULL || view == NULL ) {
			draw( surface );
			return;
		}
		const viewEntity_s* space = surface->space;
		std::vector< viewEntity_s >& instanceSpaces = spaces[ space ];
		if ( instanceSpaces.empty() ) {
			instanceSpaces.resize( space->numInsts );
			for ( int i = 0; i < space->numInsts; ++i ) {
				const sdInstInfo& instance = space->insts[ i ];
				viewEntity_s& instanceSpace = instanceSpaces[ i ];
				instanceSpace = *space;
				instanceSpace.numInsts = 0;
				instanceSpace.insts = NULL;
				float instanceMatrix[ 16 ];
				R_AxisToModelMatrix( instance.inst.axis, instance.inst.origin, instanceMatrix );
				R_MultMatrix( instanceMatrix, space->modelMatrix, instanceSpace.modelMatrix );
				R_MultMatrix( instanceSpace.modelMatrix, view->worldSpace.modelViewMatrix,
					instanceSpace.modelViewMatrix );
			}
		}
		for ( int i = 0; i < space->numInsts; ++i ) {
			const sdInstInfo& instance = space->insts[ i ];
			if ( !R_DistanceVisibility( instance.fadeOrigin, idMath::Ftoi( instance.maxVisDist ),
				idMath::Ftoi( instance.minVisDist ), view ) ) {
				continue;
			}
			if ( surface->geo != NULL && R_CullLocalBoxToViewdef( surface->geo->bounds,
				instanceSpaces[ i ].modelMatrix, view ) ) {
				continue;
			}
			surfaces.push_back( *surface );
			surfaces.back().space = &instanceSpaces[ i ];
			draw( &surfaces.back() );
		}
	}
private:
	const viewDef_s* view = NULL;
	std::unordered_map< const viewEntity_s*, std::vector< viewEntity_s > > spaces;
	std::deque< drawSurf_s > surfaces;
};
static sdVulkanInstanceSurfaces vulkanInstanceSurfaces;
struct vulkanExpandedSurface_t {
	int					index;
	const drawSurf_s*	surface;
	// the view's own surface; ray tracing expands its instances itself, unculled,
	// so it takes this once per surface (rayTracingSource) and not each copy
	const drawSurf_s*	original;
	bool				rayTracingSource;
};

// fog != NULL draws the atmosphere fog over the surface instead (no light; fog.frag,
// #233): fog[ 0..2 ] the fog colour, fog[ 3 ] and fog[ 4 ] fogDepths' z and w.
void DrawVulkanLightInteraction( sdVulkanBackendState& state, const drawSurf_s* surface,
	const viewLight_s* light, const viewDef_s* view, bool unshadowed, const float* fog,
	const float* ambient, bool amblitShadowSplit ) {
	// fog (fog.frag) and ambient (ambient_cube.frag) redraw the surface without a light
	const bool lightPass = fog == NULL && ambient == NULL;
	if ( surface == NULL || surface->geo == NULL || surface->space == NULL ||
		 surface->material == NULL || ( light == NULL && lightPass ) || view == NULL ) {
		return;
	}
	const srfTriangles_t* tri = surface->geo;
	const vertCache_t* vertexCacheBlock = tri->ambientCache;
	const vertCache_t* indexCacheBlock = tri->indexCache;
	if ( vertexCacheBlock == NULL && tri->ambientSurface != NULL ) {
		vertexCacheBlock = tri->ambientSurface->ambientCache;
	}
	if ( indexCacheBlock == NULL && tri->ambientSurface != NULL ) {
		indexCacheBlock = tri->ambientSurface->indexCache;
	}
	if ( vertexCacheBlock == NULL && tri->verts != NULL && tri->numVerts > 0 ) {
		vertexCache.Alloc( tri->verts, tri->numVerts * sizeof( idDrawVert ),
			const_cast<vertCache_t**>( &tri->ambientCache ), false );
		vertexCacheBlock = tri->ambientCache;
	}
	if ( indexCacheBlock == NULL && tri->indexes != NULL && tri->numIndexes > 0 ) {
		vertexCache.Alloc( tri->indexes, tri->numIndexes * sizeof( glIndex_t ),
			const_cast<vertCache_t**>( &tri->indexCache ), true );
		indexCacheBlock = tri->indexCache;
	}
	if ( vertexCacheBlock == NULL || indexCacheBlock == NULL ) {
		return;
	}
	if ( vertexCacheBlock->tag != TAG_TEMP ) {
		vertexCache.Touch( const_cast<vertCache_t*>( vertexCacheBlock ) );
	}
	if ( indexCacheBlock->tag != TAG_TEMP ) {
		vertexCache.Touch( const_cast<vertCache_t*>( indexCacheBlock ) );
	}

	const void* vertexOwner = vertexCacheBlock->backendBuffer != NULL ?
		vertexCacheBlock->backendBuffer : vertexCacheBlock;
	const void* indexOwner = indexCacheBlock->backendBuffer != NULL ?
		indexCacheBlock->backendBuffer : indexCacheBlock;
	const sdVulkanBufferResource* vertexResource = FindVulkanBufferResource( state, vertexOwner );
	if ( vertexResource == NULL ) {
		vertexResource = FindVulkanBufferResource( state, vertexCacheBlock );
	}
	const sdVulkanBufferResource* indexResource = FindVulkanBufferResource( state, indexOwner );
	if ( indexResource == NULL ) {
		indexResource = FindVulkanBufferResource( state, indexCacheBlock );
	}
	if ( vertexResource == NULL || indexResource == NULL ) {
		return;
	}

	const VkDeviceSize vertexOffset = vertexCacheBlock->offset;
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( tri->numVerts ) * sizeof( idDrawVert );
	const VkDeviceSize indexOffset = indexCacheBlock->offset;
	const VkDeviceSize indexBytes = static_cast< VkDeviceSize >( tri->numIndexes ) * sizeof( glIndex_t );
	if ( vertexCacheBlock->offset < 0 || indexCacheBlock->offset < 0 ||
		 vertexOffset + vertexBytes > vertexResource->bytes ||
		 indexOffset + indexBytes > indexResource->bytes ) {
		return;
	}

	idScreenRect scissorRect = surface->scissorRect;
	if ( light != NULL ) {	// a light pass, or an amblit surface's shadow scale under it
		scissorRect.Intersect( light->scissorRect );
	}
	int scissorWidth = Max( 0, scissorRect.x2 - scissorRect.x1 + 1 );
	int scissorHeight = Max( 0, scissorRect.y2 - scissorRect.y1 + 1 );
	if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
		return;
	}
	VkRect2D vkScissor;
	vkScissor.offset.x = view->viewport.x1 + scissorRect.x1;
	vkScissor.offset.y = static_cast< int >( state.extent.height ) -
		( view->viewport.y1 + scissorRect.y1 + scissorHeight );
	if ( vkScissor.offset.x < 0 ) {
		scissorWidth -= Min( scissorWidth, -vkScissor.offset.x );
		vkScissor.offset.x = 0;
	}
	if ( vkScissor.offset.y < 0 ) {
		scissorHeight -= Min( scissorHeight, -vkScissor.offset.y );
		vkScissor.offset.y = 0;
	}
	scissorWidth = Min( scissorWidth,
		static_cast< int >( state.extent.width ) - vkScissor.offset.x );
	scissorHeight = Min( scissorHeight,
		static_cast< int >( state.extent.height ) - vkScissor.offset.y );
	if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
		return;
	}
	vkScissor.extent.width = static_cast< uint32_t >( scissorWidth );
	vkScissor.extent.height = static_cast< uint32_t >( scissorHeight );

	idImage* selectedImage = NULL;
	const materialStage_t* stage = NULL;
	if ( surface->material != NULL ) {
		for ( int stageIdx = 0; stageIdx < surface->material->GetNumStages(); ++stageIdx ) {
			const materialStage_t* testStage = surface->material->GetStage( stageIdx );
			if ( testStage != NULL && testStage->numTextures > 0 && testStage->textures[ 0 ].image != NULL ) {
				stage = testStage;
				selectedImage = testStage->textures[ 0 ].image;
				break;
			}
		}
	}
	if ( selectedImage == NULL && globalImages != NULL ) {
		selectedImage = globalImages->whiteImage;
	}
	VkDescriptorSet descriptorSet = VK_NULL_HANDLE;
	if ( stage != NULL && selectedImage != NULL ) {
		// the ambient pass takes the front-end set, which adds the specular cube
		if ( ambient != NULL ) {
			GetVulkanFrontEndDescriptor( state, *stage, selectedImage, surface->space, descriptorSet );
		} else {
			GetVulkanMaterialDescriptor( state, *stage, selectedImage, surface->space, descriptorSet );
		}
	}
	if ( ambient != NULL && descriptorSet == VK_NULL_HANDLE ) {
		return;	// the fallback sets carry a black cube: keep the flat ambient
	}
	if ( descriptorSet == VK_NULL_HANDLE && selectedImage != NULL ) {
		if ( !selectedImage->IsLoaded() ) {
			selectedImage->BindFragment();
		}
		const sdVulkanImageResource* imageResource = FindVulkanImageResource( state, selectedImage );
		if ( imageResource != NULL ) {
			descriptorSet = imageResource->descriptorSet;
		}
	}
	if ( descriptorSet == VK_NULL_HANDLE && globalImages != NULL && globalImages->whiteImage != NULL ) {
		const sdVulkanImageResource* whiteRes = FindVulkanImageResource( state, globalImages->whiteImage );
		if ( whiteRes != NULL ) {
			descriptorSet = whiteRes->descriptorSet;
		}
	}
	if ( descriptorSet == VK_NULL_HANDLE ) {
		return;
	}

	const bool litInteraction = state.worldInteractionUnshadowedPipeline != VK_NULL_HANDLE;
	bool megaTextureSurface = false;
	for ( int stageIndex = 0; surface->material != NULL &&
		stageIndex < surface->material->GetNumStages(); ++stageIndex ) {
		const materialStage_t* megaStage = surface->material->GetStage( stageIndex );
		if ( megaStage != NULL && megaStage->megaTexture != NULL ) {
			megaTextureSurface = true;
			break;
		}
	}
	// MegaTexture terrain reads its albedo from the light block's snapshot
	VkDescriptorSet sceneAlbedoSet = VK_NULL_HANDLE;
	const bool sceneAlbedoInteraction = lightPass && litInteraction && megaTextureSurface &&
		state.sceneAlbedoCopied && GetVulkanSceneAlbedoDescriptor( state, sceneAlbedoSet );
	if ( lightPass && litInteraction && megaTextureSurface && !sceneAlbedoInteraction ) {
		return;	// no albedo snapshot (no TRANSFER_SRC swapchain): ambient only, not white
	}
	VkPipeline pipeline = unshadowed ?
		( litInteraction ? state.worldInteractionUnshadowedPipeline : state.worldMaterialAddPipeline ) :
		state.worldInteractionPipeline;
	// A GPU-skinned surface is drawn skinned, with the same joint palette as the
	// base pass; its bind pose would miss the base pass's depth.
	const bool hardwareSkinned = litInteraction && tri->hardwareSkinnedSurface &&
		tri->joints != NULL && tri->numJoints > 0 && tri->numJoints <= MAX_JOINTS_PER_MESH &&
		tri->weightCache != NULL && state.worldInteractionSkinnedPipeline != VK_NULL_HANDLE;
	const sdVulkanBufferResource* weightResource = NULL;
	if ( hardwareSkinned ) {
		const vertCache_t* weightCacheBlock = tri->weightCache;
		weightResource = FindVulkanBufferResource( state, weightCacheBlock->backendBuffer != NULL ?
			weightCacheBlock->backendBuffer : weightCacheBlock );
		if ( weightResource == NULL || weightCacheBlock->offset < 0 ||
			static_cast< VkDeviceSize >( weightCacheBlock->offset ) +
				static_cast< VkDeviceSize >( tri->numVerts ) * sizeof( vertWeight_t ) >
				weightResource->bytes ) {
			return;
		}
		pipeline = unshadowed ? state.worldInteractionSkinnedUnshadowedPipeline :
			state.worldInteractionSkinnedPipeline;
	}
	if ( fog != NULL ) {
		pipeline = hardwareSkinned ? state.worldFogSkinnedPipeline : state.worldFogPipeline;
		if ( !litInteraction || pipeline == VK_NULL_HANDLE ) {
			return;
		}
	} else if ( ambient != NULL ) {
		const bool selfIllumAdd = ambient[ 2 ] > 0.5f && ambient[ 2 ] < 1.5f;
		pipeline = hardwareSkinned ?
			( selfIllumAdd ? state.worldAmbientCubeAddSkinnedPipeline : state.worldAmbientCubeSkinnedPipeline ) :
			( selfIllumAdd ? state.worldAmbientCubeAddPipeline : state.worldAmbientCubePipeline );
		if ( ambient[ 2 ] > 1.5f ) {
			pipeline = hardwareSkinned ? state.worldAmbientShadowSkinnedPipeline : state.worldAmbientShadowPipeline;
		}
		if ( !litInteraction || pipeline == VK_NULL_HANDLE ) {
			return;
		}
	}
	if ( pipeline == VK_NULL_HANDLE ) {
		pipeline = state.worldMaterialAddPipeline != VK_NULL_HANDLE ?
			state.worldMaterialAddPipeline : state.worldAddPipeline;
	}
	if ( pipeline == VK_NULL_HANDLE ) {
		return;
	}

	float mvp[ 16 ];
	VkViewport surfaceViewport;
	BuildVulkanSurfaceTransform( state, view, surface->space, mvp, surfaceViewport );

	float pushConstants[ 40 ];
	memset( pushConstants, 0, sizeof( pushConstants ) );
	memcpy( pushConstants, mvp, sizeof( mvp ) );
	idVec3 lightColor( 1.0f, 1.0f, 1.0f );
	bool lightColorResolved = !lightPass;
	if ( lightPass && light->material != NULL && light->lightRegisters != NULL ) {
		for ( int stageIndex = 0; stageIndex < light->material->GetNumStages(); ++stageIndex ) {
			const materialStage_t* lightStage = light->material->GetStage( stageIndex );
			if ( lightStage == NULL || lightStage->depthStage ||
				 light->lightRegisters[ lightStage->conditionRegister ] == 0.0f ) {
				continue;
			}
			const float lightScale = cvarSystem != NULL ?
				cvarSystem->GetCVarFloat( "r_lightScale" ) * light->fadeFraction : light->fadeFraction;
			if ( lightStage->colorVector != NULL ) {
				// each channel limited to 1 before r_lightScale, as the OpenGL interaction (#212)
				lightColor.Set(
					idMath::ClampFloat( 0.0f, 1.0f, light->lightRegisters[ lightStage->colorVector->registers[ 0 ] ] ) * lightScale,
					idMath::ClampFloat( 0.0f, 1.0f, light->lightRegisters[ lightStage->colorVector->registers[ 1 ] ] ) * lightScale,
					idMath::ClampFloat( 0.0f, 1.0f, light->lightRegisters[ lightStage->colorVector->registers[ 2 ] ] ) * lightScale );
			} else {
				lightColor.Set( lightScale, lightScale, lightScale );
			}
			lightColor.x = idMath::ClampFloat( 0.0f, 8.0f, lightColor.x );
			lightColor.y = idMath::ClampFloat( 0.0f, 8.0f, lightColor.y );
			lightColor.z = idMath::ClampFloat( 0.0f, 8.0f, lightColor.z );
			lightColorResolved = true;
			break;
		}
	}
	if ( litInteraction && !lightColorResolved ) {
		return;	// every stage switched off: as the ray path, the light adds nothing
	}
	pushConstants[ 16 ] = lightColor.x;
	pushConstants[ 17 ] = lightColor.y;
	pushConstants[ 18 ] = lightColor.z;
	pushConstants[ 19 ] = 1.0f;
	pushConstants[ 20 ] = 1.0f;
	pushConstants[ 25 ] = 1.0f;
	pushConstants[ 28 ] = fog == NULL && VulkanStageUsesVertexColor( stage ) ? 1.0f : 0.0f;
	pushConstants[ 29 ] = surface->material->TestMaterialFlag( MF_LOWRANGEUVCOMPRESS ) ?
		ST_TO_FLOAT_LOWRANGE : ST_TO_FLOAT;
	EncodeModelRotation( surface->space->modelMatrix, pushConstants[ 23 ], pushConstants[ 27 ] );
	bool notLitTerrain = false;
	bool notLitShadowSplit = false;
	bool amblitShadow = false;	// #265: an amblit surface's sun term, split as the terrain's
	idVec3 amblitShadowColor( 1.0f, 1.0f, 1.0f );
	idVec3 notLitShadowColor( 1.0f, 1.0f, 1.0f );
	if ( litInteraction ) {
		// interaction.vert/.frag: the light in the surface's model space, with
		// the terms of raytracing/lighting.comp (N.L, (1 - d/r)^2, a directional
		// sun) and retail's specular; the surface colour is the stage's diffuse
		// only.  The colour slot carries the view origin in model space instead
		// (the specular's view vector) and in w the stage's specular factor; for
		// scene-albedo terrain, which has no specular, rgb stays 1 (the notlit
		// term's scale) and w = -1 marks it.
		pushConstants[ 16 ] = pushConstants[ 17 ] = pushConstants[ 18 ] = 1.0f;
		if ( !sceneAlbedoInteraction ) {
			const float* m = surface->space->modelMatrix;	// axis rows 0-2, 4-6, 8-10, origin 12-14
			const idVec3 delta = view->renderView.vieworg - idVec3( m[ 12 ], m[ 13 ], m[ 14 ] );
			pushConstants[ 16 ] = delta.x * m[ 0 ] + delta.y * m[ 1 ] + delta.z * m[ 2 ];
			pushConstants[ 17 ] = delta.x * m[ 4 ] + delta.y * m[ 5 ] + delta.z * m[ 6 ];
			pushConstants[ 18 ] = delta.x * m[ 8 ] + delta.y * m[ 9 ] + delta.z * m[ 10 ];
			// retail scales the specular by the stage's $specularColor (draw_new.cpp
			// multiplies it by the light colour; one factor here, its mean), and its
			// foliage programs have none
			float specularFactor = 1.0f;
			if ( view->renderWorld != NULL && !view->renderWorld->HasMap() ) {
				// a GUI's world (the limbo and menu models) draws no interaction
				// specular, as OpenGL since #251 (compat-ledger row 079)
				specularFactor = 0.0f;
			} else if ( stage != NULL && stage->renderProgram != NULL &&
				idStr::Icmpn( stage->renderProgram->GetName(), "interaction/foliage", 19 ) == 0 ) {
				specularFactor = 0.0f;
			} else if ( stage != NULL && surface->materialRegisters != NULL ) {
				for ( int vectorIndex = 0; vectorIndex < stage->numVectors; ++vectorIndex ) {
					const stageVector_t& vector = stage->vectors[ vectorIndex ];
					if ( vector.renderBinding != NULL &&
						idStr::Icmp( vector.renderBinding->GetName(), "specularColor" ) == 0 ) {
						specularFactor = Max( 0.0f, ( surface->materialRegisters[ vector.registers[ 0 ] ] +
							surface->materialRegisters[ vector.registers[ 1 ] ] +
							surface->materialRegisters[ vector.registers[ 2 ] ] ) / 3.0f );
						break;
					}
				}
			}
			pushConstants[ 19 ] = specularFactor;
		} else {
			pushConstants[ 19 ] = -1.0f;
		}
		if ( !sceneAlbedoInteraction && stage != NULL && stage->hasAlphaTest && surface->materialRegisters != NULL ) {
			pushConstants[ 30 ] = surface->materialRegisters[ stage->alphaTestRegister ];
			pushConstants[ 31 ] = 1.0f;
		}
		const bool directional = lightPass && ( light == view->atmosphereLight ||
			( light->lightDef != NULL && light->lightDef->flags.atmosphereLight ) );
		if ( fog != NULL ) {
			// fog.frag: the view origin in model space, fogDepths and the fog colour
			const float* m = surface->space->modelMatrix;	// axis rows 0-2, 4-6, 8-10, origin 12-14
			const idVec3 delta = view->renderView.vieworg - idVec3( m[ 12 ], m[ 13 ], m[ 14 ] );
			pushConstants[ 32 ] = delta.x * m[ 0 ] + delta.y * m[ 1 ] + delta.z * m[ 2 ];
			pushConstants[ 33 ] = delta.x * m[ 4 ] + delta.y * m[ 5 ] + delta.z * m[ 6 ];
			pushConstants[ 34 ] = delta.x * m[ 8 ] + delta.y * m[ 9 ] + delta.z * m[ 10 ];
			pushConstants[ 35 ] = fog[ 3 ];
		} else if ( ambient != NULL ) {
			// ambient_cube.frag: $ambientBrightness, $ambientScale.x, the mode
			// (0: the ambient scale, 1: the specular) and 1 to add the
			// self-illumination; the colour slot holds the view origin, as above,
			// and ambient[ 4..7 ] the flat amblit ambient (below)
			pushConstants[ 32 ] = ambient[ 0 ];
			pushConstants[ 33 ] = ambient[ 1 ];
			pushConstants[ 34 ] = ambient[ 2 ];
			pushConstants[ 35 ] = ambient[ 3 ];
		} else if ( directional ) {
			idVec3 sunDirection( 0.35f, 0.45f, 0.82f );
			if ( view->atmosphere != NULL ) {
				sunDirection = view->atmosphere->GetSunDirection();
			}
			sunDirection.Normalize();
			const float* m = surface->space->modelMatrix;	// axis rows 0-2, 4-6, 8-10
			const idVec3 localDirection(
				sunDirection.x * m[ 0 ] + sunDirection.y * m[ 1 ] + sunDirection.z * m[ 2 ],
				sunDirection.x * m[ 4 ] + sunDirection.y * m[ 5 ] + sunDirection.z * m[ 6 ],
				sunDirection.x * m[ 8 ] + sunDirection.y * m[ 9 ] + sunDirection.z * m[ 10 ] );
			pushConstants[ 32 ] = localDirection.x;
			pushConstants[ 33 ] = localDirection.y;
			pushConstants[ 34 ] = localDirection.z;
			pushConstants[ 35 ] = 0.0f;
		} else {
			const float radius = Max( Max( idMath::Fabs( light->lightRadius.x ),
				idMath::Fabs( light->lightRadius.y ) ), idMath::Fabs( light->lightRadius.z ) );
			if ( radius <= 1.0f ) {
				return;
			}
			const float* m = surface->space->modelMatrix;	// origin at 12-14
			const idVec3 delta = light->globalLightOrigin - idVec3( m[ 12 ], m[ 13 ], m[ 14 ] );
			const idVec3 localOrigin(
				delta.x * m[ 0 ] + delta.y * m[ 1 ] + delta.z * m[ 2 ],
				delta.x * m[ 4 ] + delta.y * m[ 5 ] + delta.z * m[ 6 ],
				delta.x * m[ 8 ] + delta.y * m[ 9 ] + delta.z * m[ 10 ] );
			pushConstants[ 32 ] = localOrigin.x;
			pushConstants[ 33 ] = localOrigin.y;
			pushConstants[ 34 ] = localOrigin.z;
			pushConstants[ 35 ] = radius;
		}
		// Under the atmosphere sun with r_megaDrawMethod != 0 (retail 1.5's default
		// is 3), retail draws MegaTexture terrain with the bakedamb programs'
		// amblitVersion, megaTexture/notlit_lit: albedo x lerp( 0.35, $diffuseColor,
		// 1 ), no N.L and no separate ambient, the megatexture's baked lighting being
		// the shading (R_DrawMTInteractions).  The light block has already scaled the
		// pixel to albedo x ambient, so the interaction adds the rest.
		// Only the view's own sun: a second atmosphere light would add the term twice.
		notLitTerrain = lightPass && megaTextureSurface && sceneAlbedoInteraction &&
			light == view->atmosphereLight && r_megaDrawMethod.GetInteger() != 0;
		// #265: an amblit surface (not terrain) under the shadowed sun.  Retail draws
		// its sun term before the shadow volumes and then scales its shadowed pixels,
		// sun included, by minSpecShadowColor; the light block has already scaled the
		// ambient there, so the sun term goes in as minSpecShadowColor x sun
		// everywhere, then the rest where unshadowed.
		amblitShadow = lightPass && amblitShadowSplit && !unshadowed && !megaTextureSurface &&
			light->lightDef != NULL && ( hardwareSkinned ? state.worldInteractionSkinnedUnshadowedPipeline :
				state.worldInteractionUnshadowedPipeline ) != VK_NULL_HANDLE;
		if ( amblitShadow ) {
			const unsigned int packed = light->lightDef->minSpecShadowColor;	// sdColor4::PackColor
			amblitShadowColor.Set( ( packed & 0xFF ) / 255.0f, ( ( packed >> 8 ) & 0xFF ) / 255.0f,
				( ( packed >> 16 ) & 0xFF ) / 255.0f );
		}
		if ( notLitTerrain ) {
			pushConstants[ 30 ] = cvarSystem->GetCVarFloat( "r_rayTracingAmbient" );	// the light block's ambient
			if ( !unshadowed && light->lightDef != NULL ) {
				// Retail then multiplies the sun's shadowed pixels by the atmosphere's
				// minSpecShadowColor (draw_new.cpp, shadow/multiplitive).  Here the
				// terrain's add is split in two: minSpecShadowColor x notlit_lit
				// everywhere, then the rest where the stencil says unshadowed.
				const unsigned int packed = light->lightDef->minSpecShadowColor;	// sdColor4::PackColor
				notLitShadowColor.Set( ( packed & 0xFF ) / 255.0f, ( ( packed >> 8 ) & 0xFF ) / 255.0f,
					( ( packed >> 16 ) & 0xFF ) / 255.0f );
				notLitShadowSplit = true;
			}
		}
		if ( lightPass && megaTextureSurface && !notLitTerrain ) {
			// Retail's megatexture/interaction_bakedamb* scale the light by the
			// stage's $parameters.w (valley: 0.5): the terrain's baked lighting
			// already carries half the sun, and its ambient pass removes
			// $parameters.z of it where the sun faces (the 0.34 ambient here).
			for ( int stageIndex = 0; stageIndex < surface->material->GetNumStages(); ++stageIndex ) {
				const materialStage_t* megaStage = surface->material->GetStage( stageIndex );
				if ( megaStage == NULL || megaStage->megaTexture == NULL || megaStage->renderProgram == NULL ||
					idStr::FindText( megaStage->renderProgram->GetName(), "bakedamb", false ) < 0 ) {
					continue;
				}
				for ( int vectorIndex = 0; vectorIndex < megaStage->numVectors; ++vectorIndex ) {
					const stageVector_t& vector = megaStage->vectors[ vectorIndex ];
					if ( vector.renderBinding != NULL && surface->materialRegisters != NULL &&
						idStr::Icmp( vector.renderBinding->GetName(), "parameters" ) == 0 ) {
						lightColor *= surface->materialRegisters[ vector.registers[ 3 ] ];
					}
				}
				break;
			}
		}
		pushConstants[ 36 ] = fog != NULL ? fog[ 0 ] : lightColor.x;
		pushConstants[ 37 ] = fog != NULL ? fog[ 1 ] : lightColor.y;
		pushConstants[ 38 ] = fog != NULL ? fog[ 2 ] : lightColor.z;
		// w: 1 for the sun, + 2 for a two-sided material (lit from either side), + 4 for
		// notlit_lit terrain; fog: fogDepths.w
		pushConstants[ 39 ] = fog != NULL ? fog[ 4 ] : ( directional ? 1.0f : 0.0f ) +
			( surface->material->GetCullType() == CT_TWO_SIDED ? 2.0f : 0.0f ) +
			( notLitTerrain ? 4.0f : 0.0f );
		if ( ambient != NULL ) {
			// ambient_cube.frag's flatAmbient: the amblit colour, and 1 to use it
			pushConstants[ 36 ] = ambient[ 4 ];
			pushConstants[ 37 ] = ambient[ 5 ];
			pushConstants[ 38 ] = ambient[ 6 ];
			pushConstants[ 39 ] = ambient[ 7 ];
		}
	}

	sdVulkanFrame& frame = state.frames[ state.frameIndex ];
	state.CmdSetViewport( frame.commandBuffer, 0, 1, &surfaceViewport );
	state.CmdSetScissor( frame.commandBuffer, 0, 1, &vkScissor );
	state.CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
	state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
	const VkPipelineLayout pipelineLayout = ambient != NULL ? state.frontEndPipelineLayout :
		state.guiPipelineLayout;
	state.CmdBindDescriptorSets( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		pipelineLayout, 0, 1, sceneAlbedoInteraction ? &sceneAlbedoSet : &descriptorSet,
		0, NULL );
	if ( hardwareSkinned ) {
		// the palette goes into this frame's joint arena, bound at set 1 as the
		// base pass binds it
		if ( frame.jointMapped == NULL || frame.jointDescriptorSet == VK_NULL_HANDLE ) {
			return;
		}
		const VkDeviceSize alignment = state.jointUniformAlignment > 1 ?
			state.jointUniformAlignment : 1;
		const VkDeviceSize stride = ( VULKAN_JOINT_PALETTE_BYTES + alignment - 1 ) /
			alignment * alignment;
		const VkDeviceSize jointOffset = frame.jointOffset;
		if ( jointOffset + VULKAN_JOINT_PALETTE_BYTES > VULKAN_JOINT_BUFFER_BYTES ) {
			if ( !frame.jointOverflowWarned ) {
				common->Warning( "Vulkan joint palette arena overflowed; increase VULKAN_JOINT_BUFFER_BYTES" );
				frame.jointOverflowWarned = true;
			}
			return;
		}
		memcpy( static_cast< byte* >( frame.jointMapped ) + jointOffset, tri->joints,
			tri->numJoints * sizeof( idJointMat ) );
		const unsigned int dynamicOffset = static_cast< unsigned int >( jointOffset );
		state.CmdBindDescriptorSets( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
			pipelineLayout, 1, 1, &frame.jointDescriptorSet, 1, &dynamicOffset );
		frame.jointOffset += stride;
		const VkBuffer buffers[ 2 ] = { vertexResource->buffer, weightResource->buffer };
		const VkDeviceSize offsets[ 2 ] = { vertexOffset,
			static_cast< VkDeviceSize >( tri->weightCache->offset ) };
		state.CmdBindVertexBuffers( frame.commandBuffer, 0, 2, buffers, offsets );
	} else {
		state.CmdBindVertexBuffers( frame.commandBuffer, 0, 1, &vertexResource->buffer, &vertexOffset );
	}
	state.CmdBindIndexBuffer( frame.commandBuffer, indexResource->buffer, indexOffset, VK_INDEX_TYPE_UINT16 );
	if ( notLitShadowSplit ) {
		// notlit_lit terrain under shadows: color.rgb scales the notlit term.  First,
		// unstenciled, minSpecShadowColor x notlit less the ambient already there ...
		state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
			state.worldInteractionUnshadowedPipeline );
		pushConstants[ 16 ] = notLitShadowColor.x;
		pushConstants[ 17 ] = notLitShadowColor.y;
		pushConstants[ 18 ] = notLitShadowColor.z;
		state.CmdPushConstants( frame.commandBuffer, state.guiPipelineLayout,
			VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
			sizeof( float ) * 40, pushConstants );
		state.CmdDrawIndexed( frame.commandBuffer, tri->numIndexes, 1, 0, 0, 0 );
		state.worldDrawCalls++;
		state.worldInteractionDrawCalls++;
		// ... then, where unshadowed, the remaining ( 1 - minSpecShadowColor ) x notlit
		state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
		pushConstants[ 16 ] = 1.0f - notLitShadowColor.x;
		pushConstants[ 17 ] = 1.0f - notLitShadowColor.y;
		pushConstants[ 18 ] = 1.0f - notLitShadowColor.z;
		pushConstants[ 30 ] = 0.0f;
	}
	if ( amblitShadow ) {
		const VkPipeline unshadowedPipeline = hardwareSkinned ?
			state.worldInteractionSkinnedUnshadowedPipeline : state.worldInteractionUnshadowedPipeline;
		const idVec3 sunColor( pushConstants[ 36 ], pushConstants[ 37 ], pushConstants[ 38 ] );
		state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, unshadowedPipeline );
		pushConstants[ 36 ] = sunColor.x * amblitShadowColor.x;
		pushConstants[ 37 ] = sunColor.y * amblitShadowColor.y;
		pushConstants[ 38 ] = sunColor.z * amblitShadowColor.z;
		state.CmdPushConstants( frame.commandBuffer, pipelineLayout,
			VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
			sizeof( float ) * 40, pushConstants );
		state.CmdDrawIndexed( frame.commandBuffer, tri->numIndexes, 1, 0, 0, 0 );
		state.worldDrawCalls++;
		state.worldInteractionDrawCalls++;
		state.CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
		pushConstants[ 36 ] = sunColor.x * ( 1.0f - amblitShadowColor.x );
		pushConstants[ 37 ] = sunColor.y * ( 1.0f - amblitShadowColor.y );
		pushConstants[ 38 ] = sunColor.z * ( 1.0f - amblitShadowColor.z );
	}
	state.CmdPushConstants( frame.commandBuffer, pipelineLayout,
		VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
		sizeof( float ) * ( litInteraction ? 40 : 32 ), pushConstants );
	state.CmdDrawIndexed( frame.commandBuffer, tri->numIndexes, 1, 0, 0, 0 );
	state.worldDrawCalls++;
	if ( lightPass ) {
		state.worldInteractionDrawCalls++;
	}
}

void sdVulkanBackend::DrawView( const viewDef_s* view ) {
	if ( state == NULL || state->worldPipeline == VK_NULL_HANDLE || view == NULL ) {
		return;
	}
	RENDER_METRIC_SCOPE( "Vulkan DrawView CPU" );
	state->worldViewAttempts++;
	// ETQW can submit the 3D render world before idRenderSystem::BeginFrame;
	// OpenGL's immediate path tolerated that ordering.  Start the Vulkan command
	// buffer on first scene use so the later BeginFrame call simply observes an
	// already-active frame and EndFrame still owns submission/presentation.
	if ( !state->frameActive && !BeginFrame( 0, 0 ) ) {
		return;
	}
	// the postprocess glow later in the frame takes this view's parameters, as
	// OpenGL's render bindings do (a GUI world without an atmosphere keeps them)
	if ( view->atmosphere != NULL ) {
		state->glowAtmosphere = view->atmosphere;
	}
	state->worldViews++;
	vulkanInstanceSurfaces.Begin( view );
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const int viewportWidth = Max( 1, view->viewport.x2 - view->viewport.x1 + 1 );
	const int viewportHeight = Max( 1, view->viewport.y2 - view->viewport.y1 + 1 );
	VkViewport viewport;
	viewport.x = static_cast< float >( view->viewport.x1 );
	viewport.y = static_cast< float >( state->extent.height - view->viewport.y1 );
	viewport.width = static_cast< float >( viewportWidth );
	viewport.height = -static_cast< float >( viewportHeight );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );

	// The OpenGL back end built the atmosphere from a dedicated material pass.
	// Establish the authored sky gradient first so pixels not covered by world
	// geometry no longer expose the swapchain clear color.  This intentionally
	// precedes depth-tested surfaces; clouds and the sun remain later passes.
	// Only a map's world has a sky: a GUI's render world (the limbo menu's player
	// model, UIRenderWorld) has an atmosphere for its lighting but no sky, and
	// filling its viewport drew an opaque box behind the model (#187).
	if ( view->atmosphere != NULL && state->skyPipeline != VK_NULL_HANDLE &&
		view->renderWorld != NULL && view->renderWorld->HasMap() ) {
		RENDER_METRIC_SCOPE( "Vulkan sky" );
		idImage* skyImage = view->atmosphere->GetSkyGradientImage();
		if ( skyImage != NULL && !skyImage->IsLoaded() ) {
			skyImage->BindFragment();
		}
		const sdVulkanImageResource* skyResource =
			skyImage != NULL && skyImage->IsLoaded() && !skyImage->defaulted ?
			FindVulkanImageResource( *state, skyImage ) : NULL;
		if ( skyResource != NULL && skyResource->descriptorSet != VK_NULL_HANDLE ) {
			VkRect2D skyScissor;
			skyScissor.offset.x = idMath::ClampInt( 0,
				static_cast< int >( state->extent.width ), view->viewport.x1 );
			skyScissor.offset.y = idMath::ClampInt( 0,
				static_cast< int >( state->extent.height ),
				static_cast< int >( state->extent.height ) -
				( view->viewport.y1 + viewportHeight ) );
			skyScissor.extent.width = static_cast< unsigned int >( Min( viewportWidth,
				static_cast< int >( state->extent.width ) - skyScissor.offset.x ) );
			skyScissor.extent.height = static_cast< unsigned int >( Min( viewportHeight,
				static_cast< int >( state->extent.height ) - skyScissor.offset.y ) );
			if ( skyScissor.extent.width > 0 && skyScissor.extent.height > 0 ) {
				float skyPushConstants[ 32 ];
				memset( skyPushConstants, 0, sizeof( skyPushConstants ) );
				const idVec3& fogColor = view->atmosphere->GetFogColor();
				skyPushConstants[ 16 ] = fogColor.x;
				skyPushConstants[ 17 ] = fogColor.y;
				skyPushConstants[ 18 ] = fogColor.z;
				skyPushConstants[ 19 ] = 1.0f;
				state->CmdSetScissor( frame.commandBuffer, 0, 1, &skyScissor );
				state->CmdBindPipeline( frame.commandBuffer,
					VK_PIPELINE_BIND_POINT_GRAPHICS, state->skyPipeline );
				state->CmdBindDescriptorSets( frame.commandBuffer,
					VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
					&skyResource->descriptorSet, 0, NULL );
				state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
					VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
					sizeof( skyPushConstants ), skyPushConstants );
				state->CmdDraw( frame.commandBuffer, 3, 1, 0, 0 );
				state->worldSkyDrawCalls++;
			}
		}
	}

	if ( view->drawSurfs == NULL || view->numDrawSurfs <= 0 ) {
		return;
	}
	state->worldSurfaceCandidates += view->numDrawSurfs;
	bool currentRenderCopied = false;
	idList< sdRayTracingGeometry >& rayTracingGeometries =
		state->rayTracingGeometryScratch;
	idList< sdRayTracingGeometry >& rayTracingWaterGeometries =
		state->rayTracingWaterGeometryScratch;
	rayTracingGeometries.SetNum( 0, false );
	rayTracingWaterGeometries.SetNum( 0, false );
	const bool debugMaterials = r_vkDebugMaterials.GetBool();
	const float defaultDepthStart = idMath::ClampFloat( 0.0f, 1.0f,
		cvarSystem->GetCVarFloat( "r_depthRangeStartDefault" ) );
	const float weaponDepthEnd = idMath::ClampFloat( 0.0f, 1.0f,
		cvarSystem->GetCVarFloat( "r_depthRangeWeaponHackEnd" ) );
	const float zNear = Max( cvarSystem->GetCVarFloat( "r_znear" ), 0.001f );
	const float weaponDepthScale = cvarSystem->GetCVarFloat(
		"r_depthRangeWeaponHackScale" );
	const int megaFadeMilliseconds = Max( 0,
		cvarSystem->GetCVarInteger( "r_megaFadeTime" ) );
	idVec3 worldSunDirection( 0.35f, 0.45f, 0.82f );
	if ( view->atmosphere != NULL ) {
		worldSunDirection = view->atmosphere->GetSunDirection();
	}

	vulkanSurfaceSelections.SetNum( view->numDrawSurfs );
	memset( vulkanSurfaceSelectionCaches, 0,
		sizeof( vulkanSurfaceSelectionCaches ) );
	vulkanSurfaceAnalysisContext_t surfaceAnalysisContext;
	surfaceAnalysisContext.view = view;
	surfaceAnalysisContext.selections = vulkanSurfaceSelections.Begin();
	rendererJobs.ParallelFor( view->numDrawSurfs, 4,
		"Analyze Vulkan surfaces", AnalyzeVulkanSurfaces,
		&surfaceAnalysisContext );
	struct materialDescriptorCacheEntry_t {
		const materialStage_t*	stage;
		idImage*				selectedImage;
		idImage*				ambientImage;
		VkDescriptorSet		descriptorSet;
	};
	const int MATERIAL_DESCRIPTOR_CACHE_SIZE = 128;
	materialDescriptorCacheEntry_t materialDescriptorCache[
		MATERIAL_DESCRIPTOR_CACHE_SIZE ];
	memset( materialDescriptorCache, 0, sizeof( materialDescriptorCache ) );
	struct spaceSubmissionCacheEntry_t {
		const viewEntity_s*	space;
		VkRect2D			scissor;
		int				inputScissor[ 4 ];
		VkViewport			viewport;
		float				mvp[ 16 ];
		idVec3				localSunDirection;
		idVec3				localViewOrigin;
		float				modelRotation[ 2 ];
		float				sunDirection[ 2 ];
		bool				visible;
	};
	const int SPACE_SUBMISSION_CACHE_SIZE = 256;
	spaceSubmissionCacheEntry_t spaceSubmissionCache[
		SPACE_SUBMISSION_CACHE_SIZE ];
	memset( spaceSubmissionCache, 0, sizeof( spaceSubmissionCache ) );
	const float polygonOffsetUnits = r_offsetUnits.GetFloat();
	const float polygonOffsetFactor = r_offsetFactor.GetFloat();
	float submittedPolygonOffset = 0.0f;
	auto SetPolygonOffset = [&]( const idMaterial* material,
		const materialStage_t* stage ) {
		float polygonOffset = 0.0f;
		if ( material != NULL && material->TestMaterialFlag( MF_POLYGONOFFSET ) ) {
			polygonOffset = material->GetPolygonOffset();
		}
		if ( stage != NULL && stage->privatePolygonOffset != 0.0f ) {
			polygonOffset = stage->privatePolygonOffset;
		}
		if ( polygonOffset != submittedPolygonOffset ) {
			state->CmdSetDepthBias( frame.commandBuffer,
				polygonOffset != 0.0f ? polygonOffsetUnits * polygonOffset : 0.0f,
				0.0f,
				polygonOffset != 0.0f ? polygonOffsetFactor : 0.0f );
			submittedPolygonOffset = polygonOffset;
		}
	};
	auto GetCachedMaterialDescriptor = [&]( const materialStage_t& stage,
		idImage* selectedImage, const viewEntity_s* space,
		VkDescriptorSet& descriptorSet ) {
		idImage* ambientImage = globalImages != NULL ?
			globalImages->blackCubeMapImage : NULL;
		if ( space != NULL && space->ambientCubeMap != NULL &&
			space->ambientCubeMap->GetAmbientCubeMap() != NULL ) {
			ambientImage = space->ambientCubeMap->GetAmbientCubeMap();
		}
		const size_t stageKey = reinterpret_cast< size_t >( &stage );
		const size_t imageKey = reinterpret_cast< size_t >( selectedImage );
		const size_t ambientKey = reinterpret_cast< size_t >( ambientImage );
		const int cacheIndex = static_cast< int >( ( ( stageKey >> 4 ) ^
			( imageKey >> 5 ) ^ ( ambientKey >> 4 ) ) &
			( MATERIAL_DESCRIPTOR_CACHE_SIZE - 1 ) );
		materialDescriptorCacheEntry_t& cached =
			materialDescriptorCache[ cacheIndex ];
		if ( cached.stage == &stage && cached.selectedImage == selectedImage &&
			cached.ambientImage == ambientImage &&
			cached.descriptorSet != VK_NULL_HANDLE ) {
			descriptorSet = cached.descriptorSet;
			return true;
		}
		if ( !GetVulkanMaterialDescriptor( *state, stage, selectedImage,
			space, descriptorSet ) ) {
			return false;
		}
		cached.stage = &stage;
		cached.selectedImage = selectedImage;
		cached.ambientImage = ambientImage;
		cached.descriptorSet = descriptorSet;
		return true;
	};
	VkPipeline submittedPipeline = VK_NULL_HANDLE;
	VkDescriptorSet submittedDescriptorSet = VK_NULL_HANDLE;
	VkViewport submittedViewport = viewport;
	bool submittedViewportValid = true;
	VkRect2D submittedScissor;
	bool submittedScissorValid = false;
	VkBuffer submittedVertexBuffer = VK_NULL_HANDLE;
	VkDeviceSize submittedVertexOffset = 0;
	VkBuffer submittedWeightBuffer = VK_NULL_HANDLE;
	VkDeviceSize submittedWeightOffset = 0;
	VkBuffer submittedIndexBuffer = VK_NULL_HANDLE;
	VkDeviceSize submittedIndexOffset = 0;
	unsigned int submittedJointOffset = UINT_MAX;
	bool submitHardwareSkinning = false;
	auto ResolveSkinnedPipeline = [&]( VkPipeline pipeline ) {
		if ( !submitHardwareSkinning ) return pipeline;
		if ( pipeline == state->worldPipeline ) return state->worldSkinnedPipeline;
		if ( pipeline == state->worldDepthPipeline ) return state->worldSkinnedDepthPipeline;
		if ( pipeline == state->worldAlphaPipeline ) return state->worldSkinnedAlphaPipeline;
		if ( pipeline == state->worldAddPipeline ) return state->worldSkinnedAddPipeline;
		if ( pipeline == state->worldAlphaAddPipeline ) return state->worldSkinnedAlphaAddPipeline;
		if ( pipeline == state->worldMultiplyPipeline ) return state->worldSkinnedMultiplyPipeline;
		if ( pipeline == state->worldMegaPipeline ) return state->worldSkinnedMegaPipeline;
		if ( pipeline == state->worldAtmospherePipeline ) return state->worldSkinnedAtmospherePipeline;
		if ( pipeline == state->worldMaterialPipeline ) return state->worldSkinnedMaterialPipeline;
		if ( pipeline == state->worldMaterialAlphaPipeline ) return state->worldSkinnedMaterialAlphaPipeline;
		if ( pipeline == state->worldMaterialAddPipeline ) return state->worldSkinnedMaterialAddPipeline;
		if ( pipeline == state->worldWaterPipeline ) return state->worldSkinnedWaterPipeline;
		if ( pipeline == state->worldHeatHazePipeline ) return state->worldSkinnedHeatHazePipeline;
		if ( pipeline == state->worldSkyboxPipeline ) return state->worldSkinnedSkyboxPipeline;
		if ( pipeline == state->worldSkyboxAlphaPipeline ) return state->worldSkinnedSkyboxAlphaPipeline;
		if ( pipeline == state->worldSkyboxAddPipeline ) return state->worldSkinnedSkyboxAddPipeline;
		return pipeline;
	};
	auto BindPipeline = [&]( VkPipeline pipeline ) {
		pipeline = ResolveSkinnedPipeline( pipeline );
		if ( pipeline != submittedPipeline ) {
			state->CmdBindPipeline( frame.commandBuffer,
				VK_PIPELINE_BIND_POINT_GRAPHICS, pipeline );
			submittedPipeline = pipeline;
		}
	};
	auto BindJointPalette = [&]( const idJointMat* joints, int numJoints ) {
		if ( joints == NULL || numJoints <= 0 || numJoints > MAX_JOINTS_PER_MESH ||
			frame.jointMapped == NULL || frame.jointDescriptorSet == VK_NULL_HANDLE ) {
			return false;
		}
		const VkDeviceSize alignment = state->jointUniformAlignment > 1 ?
			state->jointUniformAlignment : 1;
		const VkDeviceSize stride = ( VULKAN_JOINT_PALETTE_BYTES + alignment - 1 ) /
			alignment * alignment;
		const VkDeviceSize offset = frame.jointOffset;
		if ( offset + VULKAN_JOINT_PALETTE_BYTES > VULKAN_JOINT_BUFFER_BYTES ) {
			if ( !frame.jointOverflowWarned ) {
				common->Warning( "Vulkan joint palette arena overflowed; increase VULKAN_JOINT_BUFFER_BYTES" );
				frame.jointOverflowWarned = true;
			}
			return false;
		}
		memcpy( static_cast< byte* >( frame.jointMapped ) + offset, joints,
			numJoints * sizeof( idJointMat ) );
		const unsigned int dynamicOffset = static_cast< unsigned int >( offset );
		if ( dynamicOffset != submittedJointOffset ) {
			state->CmdBindDescriptorSets( frame.commandBuffer,
				VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 1, 1,
				&frame.jointDescriptorSet, 1, &dynamicOffset );
			submittedJointOffset = dynamicOffset;
		}
		frame.jointOffset += stride;
		return true;
	};
	auto BindDescriptorSet = [&]( VkDescriptorSet descriptorSet ) {
		if ( descriptorSet != submittedDescriptorSet ) {
			state->CmdBindDescriptorSets( frame.commandBuffer,
				VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
				&descriptorSet, 0, NULL );
			submittedDescriptorSet = descriptorSet;
		}
	};
	auto SetViewport = [&]( const VkViewport& newViewport ) {
		if ( !submittedViewportValid || submittedViewport.x != newViewport.x ||
			submittedViewport.y != newViewport.y ||
			submittedViewport.width != newViewport.width ||
			submittedViewport.height != newViewport.height ||
			submittedViewport.minDepth != newViewport.minDepth ||
			submittedViewport.maxDepth != newViewport.maxDepth ) {
			state->CmdSetViewport( frame.commandBuffer, 0, 1, &newViewport );
			submittedViewport = newViewport;
			submittedViewportValid = true;
		}
	};
	auto SetScissor = [&]( const VkRect2D& newScissor ) {
		if ( !submittedScissorValid ||
			submittedScissor.offset.x != newScissor.offset.x ||
			submittedScissor.offset.y != newScissor.offset.y ||
			submittedScissor.extent.width != newScissor.extent.width ||
			submittedScissor.extent.height != newScissor.extent.height ) {
			state->CmdSetScissor( frame.commandBuffer, 0, 1, &newScissor );
			submittedScissor = newScissor;
			submittedScissorValid = true;
		}
	};
	auto BindGeometry = [&]( VkBuffer vertexBuffer, VkDeviceSize vertexOffset,
		VkBuffer weightBuffer, VkDeviceSize weightOffset,
		VkBuffer indexBuffer, VkDeviceSize indexOffset ) {
		if ( vertexBuffer != submittedVertexBuffer ||
			vertexOffset != submittedVertexOffset ||
			weightBuffer != submittedWeightBuffer ||
			weightOffset != submittedWeightOffset ) {
			VkBuffer vertexBuffers[ 2 ] = { vertexBuffer, weightBuffer };
			VkDeviceSize vertexOffsets[ 2 ] = { vertexOffset, weightOffset };
			const unsigned int bindingCount = weightBuffer != VK_NULL_HANDLE ? 2 : 1;
			state->CmdBindVertexBuffers( frame.commandBuffer, 0, bindingCount,
				vertexBuffers, vertexOffsets );
			submittedVertexBuffer = vertexBuffer;
			submittedVertexOffset = vertexOffset;
			submittedWeightBuffer = weightBuffer;
			submittedWeightOffset = weightOffset;
		}
		if ( indexBuffer != submittedIndexBuffer ||
			indexOffset != submittedIndexOffset ) {
			state->CmdBindIndexBuffer( frame.commandBuffer, indexBuffer,
				indexOffset, VK_INDEX_TYPE_UINT16 );
			submittedIndexBuffer = indexBuffer;
			submittedIndexOffset = indexOffset;
		}
	};

	{
		RENDER_METRIC_SCOPE( "Vulkan surface submission" );
	// The rasterized lighting (no ray-query device) scales the opaque base by the
	// ambient term and adds each light's interactions.  It runs once the sorted
	// surfaces pass SS_DECAL, so GUIs, refraction, translucent, additive and
	// post-process surfaces are drawn over the lit scene instead of being scaled
	// by the ambient term too.
	// clears the view's stencil: the ambient and fog passes mark the pixels they
	// have done there
	auto ClearViewStencil = [&]() {
		VkClearAttachment stencilClear;
		memset( &stencilClear, 0, sizeof( stencilClear ) );
		stencilClear.aspectMask = VK_IMAGE_ASPECT_STENCIL_BIT;
		stencilClear.clearValue.depthStencil.stencil = 0;
		VkClearRect stencilRect;
		memset( &stencilRect, 0, sizeof( stencilRect ) );
		const int viewX1 = view->viewport.x1;
		const int viewX2 = view->viewport.x2;
		const int viewY1 = view->viewport.y1;
		const int viewY2 = view->viewport.y2;
		stencilRect.rect.offset.x = Max( 0, viewX1 );
		stencilRect.rect.offset.y = Max( 0, static_cast< int >( state->extent.height ) - ( viewY2 + 1 ) );
		stencilRect.rect.extent.width = static_cast< uint32_t >( Max( 0, Min( viewX2 - viewX1 + 1,
			static_cast< int >( state->extent.width ) - stencilRect.rect.offset.x ) ) );
		stencilRect.rect.extent.height = static_cast< uint32_t >( Max( 0, Min( viewY2 - viewY1 + 1,
			static_cast< int >( state->extent.height ) - stencilRect.rect.offset.y ) ) );
		stencilRect.layerCount = 1;
		state->CmdClearAttachments( frame.commandBuffer, 1, &stencilClear, 1, &stencilRect );
	};
	bool rasterLightingDone = false;
	auto RunRasterLighting = [&]() {
		if ( view->viewLights != NULL && !R_RayTracingIsInitialized() ) {
			RENDER_METRIC_SCOPE( "Vulkan light interactions" );
			// Snapshot the unlit opaque base for MegaTexture terrain before the ambient
			// pass scales it (CopyCurrentRender restarts rendering; dynamic state stays).
			state->sceneAlbedoCopied = state->worldInteractionUnshadowedPipeline != VK_NULL_HANDLE &&
				CopyCurrentRender( *state );
			if ( state->sceneAlbedoCopied ) {
				currentRenderCopied = true;
			}
			// Shadow volumes, the ambient scale and world interactions use the world's
			// depth range; the last base-pass draw may have left a weapon's.
			VkViewport worldViewport = viewport;
			worldViewport.minDepth = defaultDepthStart;
			worldViewport.maxDepth = 1.0f;
			state->CmdSetViewport( frame.commandBuffer, 0, 1, &worldViewport );
			submittedViewportValid = false;
			if ( state->worldLightAmbientPipeline != VK_NULL_HANDLE &&
				state->worldInteractionUnshadowedPipeline != VK_NULL_HANDLE ) {
				// The opaque base holds albedo (material.frag); scale it by the ambient
				// term so the interactions below complete albedo * (ambient + direct).
				//
				// Retail's ambient pass (interaction/basic_ambient_fragment.inc): a lit
				// surface whose entity has an ambient cube map takes albedo * the cube along
				// its normal * $ambientBrightness * $ambientScale.x, plus its
				// self-illumination unscaled.  That pass goes first: it multiplies the
				// pixel (blend 2 * src * dst, so up to twice the base) rather than
				// replacing it, so decals and extra stages already blended in keep their
				// colour, and marks the stencil so a pixel is scaled once.  The flat
				// r_rayTracingAmbient scale then takes the unmarked pixels: MegaTexture
				// terrain, whose retail ambient (megatexture/ambient_fragment.inc,
				// bakedamb) takes no cube, and surfaces without a cube.
				ClearViewStencil();
				const float ambientScale = cvarSystem->GetCVarFloat( "r_ambientScale" );
				for ( int surfaceIndex = 0; state->worldAmbientCubePipeline != VK_NULL_HANDLE &&
					surfaceIndex < view->numDrawSurfs; ++surfaceIndex ) {
					const drawSurf_s* surf = view->drawSurfs[ surfaceIndex ];
					if ( surf == NULL || surf->material == NULL || surf->space == NULL ) {
						continue;
					}
					const idMaterial* material = surf->material;
					if ( material->GetSort() >= static_cast< float >( SS_DECAL ) ) {
						break;
					}
					const sdDeclAmbientCubeMap* cubeMap = surf->space->ambientCubeMap;
					if ( cubeMap == NULL || cubeMap->GetAmbientCubeMap() == NULL ||
						material->GetSort() == SS_SUBVIEW || material->Coverage() == MC_TRANSLUCENT ||
						!material->ReceivesLighting() ) {
						continue;
					}
					bool megaTexture = false;
					bool selfIllum = false;
					bool specular = false;
					for ( int stageIndex = 0; stageIndex < material->GetNumStages(); ++stageIndex ) {
						const materialStage_t* stage = material->GetStage( stageIndex );
						megaTexture |= stage->megaTexture != NULL;
						for ( int textureIndex = 0; textureIndex < stage->numTextures; ++textureIndex ) {
							const sdDeclRenderBinding* binding = stage->textures[ textureIndex ].renderBinding;
							selfIllum |= binding != NULL && idStr::Icmp( binding->GetName(), "selfillummap" ) == 0;
							specular |= binding != NULL && rbinds != NULL && binding == rbinds->specularMap;
						}
					}
					if ( megaTexture ) {
						continue;
					}
					// Under the sun with r_megaDrawMethod != 0, retail draws the ambient of
					// a surface the sun lights (its ambSurf bit, which OpenGL's
					// R_FillDepthAmbient skips) in the sun's amblit pass: diffuse * vertex
					// colour * $ambientAvgColor (minSpecAmbientColor), with no cube,
					// $ambientScale or specular cube (interaction/basic_fragment.inc).
					const viewEntity_s* space = surf->space;
					const bool ambientLit = r_megaDrawMethod.GetInteger() != 0 && space->ambSurf != NULL &&
						surf->surfID >= 0 && surf->surfID < space->maxSurfID &&
						( space->ambSurf[ surf->surfID >> 5 ] & ( 1u << ( surf->surfID & 31 ) ) ) != 0;
					const idVec3 flatAmbient = cubeMap->GetMinSpecAmbientColor();
					// the additive draw: retail's specular cube term, where there are a
					// specular map and cube, and the self-illumination
					specular = specular && !ambientLit && cubeMap->GetSpecularCubeMap() != NULL;
					const float flat = ambientLit ? 1.0f : 0.0f;
					const float scaleTerms[ 8 ] = { cubeMap->GetBrightness(), ambientScale, 0.0f, 0.0f,
						flatAmbient.x, flatAmbient.y, flatAmbient.z, flat };
					const float addTerms[ 8 ] = { cubeMap->GetBrightness(), ambientScale, 1.0f,
						selfIllum ? 1.0f : 0.0f, flatAmbient.x, flatAmbient.y, flatAmbient.z, flat };
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, NULL, view, true, NULL, scaleTerms );
						if ( ( selfIllum || specular ) && state->worldAmbientCubeAddPipeline != VK_NULL_HANDLE ) {
							DrawVulkanLightInteraction( *state, drawSurf, NULL, view, true, NULL, addTerms );
						}
					} );
				}
				VkRect2D fullScissor;
				fullScissor.offset.x = view->viewport.x1;
				fullScissor.offset.y = static_cast< int >( state->extent.height ) - ( view->viewport.y2 + 1 );
				fullScissor.extent.width = static_cast< uint32_t >( view->viewport.x2 - view->viewport.x1 + 1 );
				fullScissor.extent.height = static_cast< uint32_t >( view->viewport.y2 - view->viewport.y1 + 1 );
				if ( fullScissor.offset.y < 0 ) {
					fullScissor.offset.y = 0;
				}
				// the ambient passes set their own viewports (a weapon's depth range)
				state->CmdSetViewport( frame.commandBuffer, 0, 1, &worldViewport );
				float ambientConstants[ 32 ];
				memset( ambientConstants, 0, sizeof( ambientConstants ) );
				const float ambient = cvarSystem->GetCVarFloat( "r_rayTracingAmbient" );	// shared with lighting.comp
				ambientConstants[ 16 ] = ambientConstants[ 17 ] = ambientConstants[ 18 ] = ambient;
				ambientConstants[ 19 ] = 1.0f;
				state->CmdSetScissor( frame.commandBuffer, 0, 1, &fullScissor );
				state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
				state->CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
					state->worldLightAmbientPipeline );
				state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
					VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
					sizeof( ambientConstants ), ambientConstants );
				state->CmdDraw( frame.commandBuffer, 3, 1, 0, 0 );
				submittedPipeline = VK_NULL_HANDLE;
			}
			for ( const viewLight_s* light = view->viewLights; light != NULL; light = light->next ) {
				if ( light->culled || light->material == NULL || light->material->IsFogLight() || light->material->IsBlendLight() ) {
					continue;
				}
				if ( light->lightDef != NULL && light->lightDef->flags.atmosphereLight && r_skipAtmosInteractions.GetBool() ) {
					continue;
				}
				if ( light->localInteractions == NULL && light->globalInteractions == NULL &&
					 light->translucentInteractions == NULL && light->globalShadows == NULL &&
					 light->localShadows == NULL && light->mtInteractions == NULL ) {
					continue;
				}

				// Intersect light scissor with viewport
				idScreenRect lightScissor = light->scissorRect;
				int scissorWidth = Max( 0, lightScissor.x2 - lightScissor.x1 + 1 );
				int scissorHeight = Max( 0, lightScissor.y2 - lightScissor.y1 + 1 );
				if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
					continue;
				}
				VkRect2D vkLightScissor;
				vkLightScissor.offset.x = view->viewport.x1 + lightScissor.x1;
				vkLightScissor.offset.y = static_cast< int >( state->extent.height ) -
					( view->viewport.y1 + lightScissor.y1 + scissorHeight );
				if ( vkLightScissor.offset.x < 0 ) {
					scissorWidth -= Min( scissorWidth, -vkLightScissor.offset.x );
					vkLightScissor.offset.x = 0;
				}
				if ( vkLightScissor.offset.y < 0 ) {
					scissorHeight -= Min( scissorHeight, -vkLightScissor.offset.y );
					vkLightScissor.offset.y = 0;
				}
				scissorWidth = Min( scissorWidth,
					static_cast< int >( state->extent.width ) - vkLightScissor.offset.x );
				scissorHeight = Min( scissorHeight,
					static_cast< int >( state->extent.height ) - vkLightScissor.offset.y );
				if ( scissorWidth <= 0 || scissorHeight <= 0 ) {
					continue;
				}
				vkLightScissor.extent.width = static_cast< uint32_t >( scissorWidth );
				vkLightScissor.extent.height = static_cast< uint32_t >( scissorHeight );

				const bool lightHasShadows = r_shadows.GetBool() && r_shadowPass.GetBool() &&
					( ( light->globalShadows != NULL || light->localShadows != NULL ) ||
					  ( light->material != NULL && light->material->LightCastsShadows() &&
					    ( light->lightDef == NULL || !light->lightDef->flags.noShadows ) ) );
				if ( lightHasShadows ) {
					state->CmdSetViewport( frame.commandBuffer, 0, 1, &worldViewport );
					VkClearAttachment clearAttachment;
					memset( &clearAttachment, 0, sizeof( clearAttachment ) );
					clearAttachment.aspectMask = VK_IMAGE_ASPECT_STENCIL_BIT;
					// OpenGL's glClearStencil( 1 << ( stencilBits - 1 ) ): the shadow
					// count's 0, which the interaction pipeline tests against
					clearAttachment.clearValue.depthStencil.stencil = 128;

					VkClearRect clearRect;
					memset( &clearRect, 0, sizeof( clearRect ) );
					clearRect.rect = vkLightScissor;
					clearRect.baseArrayLayer = 0;
					clearRect.layerCount = 1;

					state->CmdClearAttachments( frame.commandBuffer, 1, &clearAttachment, 1, &clearRect );

					if ( light->globalShadows != NULL ) {
						for ( const drawSurf_s* surf = light->globalShadows; surf != NULL; surf = surf->nextOnLight ) {
							DrawVulkanShadowSurface( *state, surf, light, view );
						}
					} else if ( light->localShadows == NULL ) {
						// The front end builds no shadow lists for this path's lights: every
						// shadow-casting surface in view is extruded here (vulkan-shadows
						// counts them; without this loop it sees none).
						for ( int i = 0; i < view->numDrawSurfs; ++i ) {
							const drawSurf_s* surf = view->drawSurfs[ i ];
							if ( surf != NULL && surf->material != NULL && surf->material->SurfaceCastsShadow() &&
								 surf->geo != NULL && ( surf->geo->verts != NULL || surf->geo->shadowVertexes != NULL || surf->geo->shadowCache != NULL ) ) {
								DrawVulkanShadowSurface( *state, surf, light, view );
							}
						}
					}
					// No barrier before the interactions: the stencil clear, the shadow
					// volumes' stencil writes and the interactions' stencil tests are all
					// in this rendering instance, where depth/stencil operations (and
					// vkCmdClearAttachments) execute in rasterization order.
				}

				// Render localInteractions
				for ( const drawSurf_s* surf = light->localInteractions; surf != NULL; surf = surf->nextOnLight ) {
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, light, view, !lightHasShadows ); } );
				}

				// Render localShadows (a weapon interaction above may have left its
				// depth range in the viewport; each volume sets its own)
				if ( lightHasShadows && light->localShadows != NULL ) {
					for ( const drawSurf_s* surf = light->localShadows; surf != NULL; surf = surf->nextOnLight ) {
						DrawVulkanShadowSurface( *state, surf, light, view );
					}
				}

				// Render mtInteractions (atmosphere interactions on MegaTexture terrains,
				// and the amblit surfaces under the sun).  Under a shadowed sun retail
				// draws them unstenciled, then the shadow volumes, then multiplies their
				// shadowed pixels by minSpecShadowColor (R_DrawMTInteractions,
				// shadow/multiplitive): (ambient + sun) x minSpecShadowColor (#265).
				// Here each amblit surface's shadowed ambient is scaled first (not a
				// decal's: the surface under it scales that pixel once), then its sun
				// term goes in split; the terrain keeps its own split.
				const bool amblitShadow = lightHasShadows && light == view->atmosphereLight &&
					r_megaDrawMethod.GetInteger() != 0 && light->lightDef != NULL &&
					state->worldAmbientShadowPipeline != VK_NULL_HANDLE;
				if ( amblitShadow ) {
					const unsigned int packed = light->lightDef->minSpecShadowColor;	// sdColor4::PackColor
					const float shadowTerms[ 8 ] = { 1.0f, 1.0f, 2.0f, 0.0f, ( packed & 0xFF ) / 255.0f,
						( ( packed >> 8 ) & 0xFF ) / 255.0f, ( ( packed >> 16 ) & 0xFF ) / 255.0f, 1.0f };
					for ( const drawSurf_s* surf = light->mtInteractions; surf != NULL; surf = surf->nextOnLight ) {
						const idMaterial* material = surf->material;
						if ( material == NULL || material->GetSort() >= static_cast< float >( SS_DECAL ) ) {
							continue;
						}
						bool megaTexture = false;
						for ( int stageIndex = 0; stageIndex < material->GetNumStages(); ++stageIndex ) {
							megaTexture |= material->GetStage( stageIndex )->megaTexture != NULL;
						}
						if ( megaTexture ) {
							continue;
						}
						vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
							DrawVulkanLightInteraction( *state, drawSurf, light, view, true, NULL, shadowTerms ); } );
					}
				}
				for ( const drawSurf_s* surf = light->mtInteractions; surf != NULL; surf = surf->nextOnLight ) {
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, light, view, !lightHasShadows,
							NULL, NULL, amblitShadow ); } );
				}

				// Render globalInteractions
				for ( const drawSurf_s* surf = light->globalInteractions; surf != NULL; surf = surf->nextOnLight ) {
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, light, view, !lightHasShadows ); } );
				}

				// Render translucentInteractions
				for ( const drawSurf_s* surf = light->translucentInteractions; surf != NULL; surf = surf->nextOnLight ) {
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, light, view, true ); } );
				}
			}
			// The atmosphere fog (#233).  Retail fogs inside its lit programs: the
			// ambient pass lerps toward the fog colour by f = (distance - fogStart) /
			// (fogEnd - fogStart) and every additive light pass scales by 1 - f, so a
			// lit pixel ends as lit * (1 - f) + fogColor * f.  Draw that over the summed
			// lighting of each surface the lighting covered (sorted up to SS_DECAL),
			// with retail's choice of surfaces (draw_atmosphere.cpp's
			// SurfaceReceivesAtmosphere).  r_AtmospherePostprocess zeroes the
			// programs' fog in retail, as here.
			if ( view->atmosphere != NULL && state->worldFogPipeline != VK_NULL_HANDLE &&
				!cvarSystem->GetCVarBool( "r_AtmospherePostprocess" ) &&
				view->atmosphere->GetFogEnd() > view->atmosphere->GetFogStart() ) {
				const idVec3& fogColor = view->atmosphere->GetFogColor();
				const float fogStart = view->atmosphere->GetFogStart();
				const float fogRange = view->atmosphere->GetFogEnd() - fogStart;
				const float fog[ 5 ] = { fogColor.x, fogColor.y, fogColor.z, 1.0f / fogRange, -fogStart / fogRange };
				// each pixel is fogged once, by the first surface that reaches it (the
				// fog pipelines test stencil 0 and increment it): a decal or another
				// coplanar lit surface over it would fog it a second time.  Retail's
				// atmosphere pass does the same (r_noDoubleAtmosphere).
				ClearViewStencil();
				for ( int i = 0; i < view->numDrawSurfs; ++i ) {
					const drawSurf_s* surf = view->drawSurfs[ i ];
					if ( surf == NULL || surf->material == NULL ) {
						continue;
					}
					const idMaterial* material = surf->material;
					if ( material->GetSort() > static_cast< float >( SS_DECAL ) ) {
						break;
					}
					if ( material->TestMaterialFlag( MF_NOATMOSPHERE ) ||
						( !material->TestMaterialFlag( MF_FORCEATMOSPHERE ) &&
						( material->GetSort() == SS_SUBVIEW || material->Coverage() == MC_TRANSLUCENT ||
						!material->ReceivesLighting() ) ) ) {
						continue;
					}
					vulkanInstanceSurfaces.ForEach( surf, [&]( const drawSurf_s* drawSurf ) {
						DrawVulkanLightInteraction( *state, drawSurf, NULL, view, true, fog ); } );
				}
			}
			// _currentRender now holds the unlit snapshot: a later refraction
			// surface must copy the lit scene again.  Without a snapshot the
			// weapon-boundary copy, if any, is still the right one.
			if ( state->sceneAlbedoCopied ) {
				currentRenderCopied = false;
			}
			state->sceneAlbedoCopied = false;
			// Shadow volumes leave their slope bias set; every world pipeline
			// enables depth bias, so clear it for the surfaces that follow.
			state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
			submittedPolygonOffset = 0.0f;
			// the block binds pipelines, descriptors, buffers and dynamic state
			// directly; the surface loop's cached submission state is stale after it
			submittedPipeline = VK_NULL_HANDLE;
			submittedDescriptorSet = VK_NULL_HANDLE;
			submittedViewportValid = false;
			submittedScissorValid = false;
			submittedVertexBuffer = VK_NULL_HANDLE;
			submittedWeightBuffer = VK_NULL_HANDLE;
			submittedIndexBuffer = VK_NULL_HANDLE;
			submittedJointOffset = UINT_MAX;
		}
	};
	// the base pass draws an instanced surface once per instance (#246)
	idList< vulkanExpandedSurface_t > expandedSurfaces;
	expandedSurfaces.SetGranularity( 1024 );
	for ( int surfaceIndex = 0; surfaceIndex < view->numDrawSurfs; ++surfaceIndex ) {
		const drawSurf_s* original = view->drawSurfs[ surfaceIndex ];
		bool first = true;
		vulkanInstanceSurfaces.ForEach( original, [&]( const drawSurf_s* drawSurf ) {
			vulkanExpandedSurface_t& expanded = expandedSurfaces.Alloc();
			expanded.index = surfaceIndex;
			expanded.surface = drawSurf;
			expanded.original = original;
			expanded.rayTracingSource = first;
			first = false;
		} );
	}
	for ( int expandedIndex = 0; expandedIndex < expandedSurfaces.Num(); ++expandedIndex ) {
		const int surfaceIndex = expandedSurfaces[ expandedIndex ].index;
		const drawSurf_s* surface = expandedSurfaces[ expandedIndex ].surface;
		const drawSurf_s* rayTracingSurface = expandedSurfaces[ expandedIndex ].rayTracingSource ?
			expandedSurfaces[ expandedIndex ].original : NULL;
		if ( !rasterLightingDone && surface != NULL && surface->material != NULL &&
			surface->material->GetSort() > static_cast< float >( SS_DECAL ) ) {
			RunRasterLighting();
			rasterLightingDone = true;
		}
		const vulkanSurfaceSelection_t& preparedSelection =
			vulkanSurfaceSelections[ surfaceIndex ];
		if ( preparedSelection.status == VULKAN_SURFACE_MISSING_GEOMETRY ) {
			state->worldMissingGeometry++;
			continue;
		}
		if ( preparedSelection.status == VULKAN_SURFACE_MISSING_CACHE ) {
			state->worldMissingCache++;
			continue;
		}
		state->worldCacheReady++;
		if ( debugMaterials ) {
			ReportVulkanDrawMaterial( *state, *surface );
		}

		const materialStage_t* selectedStage = preparedSelection.stage;
		idImage* selectedImage = preparedSelection.image;
		idMegaTexture* selectedMegaTexture = preparedSelection.megaTexture;
		const bool selectedAtmosphereStage = preparedSelection.atmosphere;
		const bool selectedWaterStage = preparedSelection.water;
		const bool selectedHeatHazeStage = preparedSelection.heatHaze;
		const bool selectedStuffGrassStage = preparedSelection.stuffGrass;
		const bool selectedSkyboxStage = preparedSelection.skybox;
		const bool selectedFoggyWaterUndersideStage =
			preparedSelection.foggyWaterUnderside;
		const bool selectedMaterialTextures = preparedSelection.materialTextures;
		if ( selectedStage == NULL || selectedImage == NULL ) {
			ReportMissingVulkanMaterial( *state, *surface );
			state->worldMissingMaterial++;
			continue;
		}
		if ( selectedMegaTexture != NULL ) {
			selectedMegaTexture->UpdateMapping( view->renderWorld );
			selectedMegaTexture->SetMappingForSurface( surface->geo );
			selectedMegaTexture->UpdateForViewOrigin(
				view->renderView.vieworg, view->renderView.time );
		}
		if ( !selectedImage->IsLoaded() ) {
			selectedImage->BindFragment();
		}
		if ( !selectedImage->IsLoaded() || selectedImage->defaulted ) {
			ReportMissingVulkanMaterial( *state, *surface );
			state->worldMissingMaterial++;
			continue;
		}
		state->worldMaterialReady++;
		SetPolygonOffset( surface->material, selectedStage );
		if ( selectedFoggyWaterUndersideStage ) {
			const idVec3 translatedViewOrigin(
				view->renderView.vieworg.x - surface->space->modelMatrix[ 12 ],
				view->renderView.vieworg.y - surface->space->modelMatrix[ 13 ],
				view->renderView.vieworg.z - surface->space->modelMatrix[ 14 ] );
			const idVec3 localViewOrigin(
				translatedViewOrigin.x * surface->space->modelMatrix[ 0 ] +
					translatedViewOrigin.y * surface->space->modelMatrix[ 1 ] +
					translatedViewOrigin.z * surface->space->modelMatrix[ 2 ],
				translatedViewOrigin.x * surface->space->modelMatrix[ 4 ] +
					translatedViewOrigin.y * surface->space->modelMatrix[ 5 ] +
					translatedViewOrigin.z * surface->space->modelMatrix[ 6 ],
				translatedViewOrigin.x * surface->space->modelMatrix[ 8 ] +
					translatedViewOrigin.y * surface->space->modelMatrix[ 9 ] +
					translatedViewOrigin.z * surface->space->modelMatrix[ 10 ] );
			// These surfaces are the explicitly generated back sides of horizontal
			// water sheets.  The legacy program and face culling made them visible
			// only from underwater.  Drawing their _white helper texture through the
			// generic Vulkan fallback instead covered the real refraction surface.
			if ( localViewOrigin.z > surface->geo->bounds[ 1 ].z + 0.5f ) {
				continue;
			}
		}

		const vertCache_t* vertexCacheBlock = surface->geo->ambientCache;
		const vertCache_t* indexCacheBlock = surface->geo->indexCache;
		const bool hardwareSkinned = surface->geo->hardwareSkinnedSurface &&
			surface->geo->joints != NULL && surface->geo->numJoints > 0 &&
			surface->geo->numJoints <= MAX_JOINTS_PER_MESH &&
			surface->geo->weightCache != NULL;
		const vertCache_t* weightCacheBlock = hardwareSkinned ?
			surface->geo->weightCache : NULL;
		submitHardwareSkinning = hardwareSkinned;
		const void* vertexOwner = vertexCacheBlock->backendBuffer != NULL ?
			vertexCacheBlock->backendBuffer : vertexCacheBlock;
		const void* indexOwner = indexCacheBlock->backendBuffer != NULL ?
			indexCacheBlock->backendBuffer : indexCacheBlock;
		const void* weightOwner = weightCacheBlock != NULL ?
			( weightCacheBlock->backendBuffer != NULL ?
			  weightCacheBlock->backendBuffer : weightCacheBlock ) : NULL;
		const sdVulkanBufferResource* vertexResource =
			FindVulkanBufferResource( *state, vertexOwner );
		const sdVulkanBufferResource* indexResource =
			FindVulkanBufferResource( *state, indexOwner );
		const sdVulkanBufferResource* weightResource = hardwareSkinned ?
			FindVulkanBufferResource( *state, weightOwner ) : NULL;
		const sdVulkanImageResource* imageResource =
			FindVulkanImageResource( *state, selectedImage );
		if ( vertexResource == NULL || indexResource == NULL ||
			( hardwareSkinned && weightResource == NULL ) ||
			imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
			state->worldMissingResource++;
			continue;
		}
		// Combined material descriptor creation may load additional images and
		// grow imageResources.  Keep the Vulkan handle by value rather than a
		// pointer into that growable array.
		const VkDescriptorSet selectedImageDescriptorSet =
			imageResource->descriptorSet;
		if ( rayTracingSurface != NULL && !hardwareSkinned && !selectedWaterStage &&
			surface->material->Coverage() != MC_TRANSLUCENT &&
			surface->material->SurfaceCastsShadow() ) {
			sdRayTracingGeometry geometry;
			memset( &geometry, 0, sizeof( geometry ) );
			// Streamed surfaces (streamVertexCache) sit at a new frame-arena offset
			// every frame, so their acceleration structures cannot be cached.
			geometry.deforming = surface->geo->deformedSurface ||
				surface->geo->streamVertexCache ||
				surface->geo->deformedSurfaceInstance ||
				surface->geo->hardwareSkinnedSurface ||
				surface->geo->frameAlloced || surface->geo->weightCache != NULL ||
				( rayTracingSurface->space->model != NULL && idStr::Icmp(
					rayTracingSurface->space->model->Name(), "_MD5_Snapshot_" ) == 0 );
			geometry.geometryOwner = geometry.deforming &&
				rayTracingSurface->space->entityDef != NULL ?
				static_cast< const void* >( rayTracingSurface->space->entityDef ) :
				static_cast< const void* >( surface->geo );
			geometry.surfaceId = surface->surfID;
			geometry.vertexBuffer = vertexResource->buffer;
			geometry.vertexOffset = vertexCacheBlock->offset;
			geometry.vertexCount = surface->geo->numVerts;
			geometry.indexBuffer = indexResource->buffer;
			geometry.indexOffset = indexCacheBlock->offset;
			geometry.indexCount = surface->geo->numIndexes;
			const float* modelMatrix = rayTracingSurface->space->modelMatrix;
			geometry.transform[ 0 ] = modelMatrix[ 0 ];
			geometry.transform[ 1 ] = modelMatrix[ 4 ];
			geometry.transform[ 2 ] = modelMatrix[ 8 ];
			geometry.transform[ 3 ] = modelMatrix[ 12 ];
			geometry.transform[ 4 ] = modelMatrix[ 1 ];
			geometry.transform[ 5 ] = modelMatrix[ 5 ];
			geometry.transform[ 6 ] = modelMatrix[ 9 ];
			geometry.transform[ 7 ] = modelMatrix[ 13 ];
			geometry.transform[ 8 ] = modelMatrix[ 2 ];
			geometry.transform[ 9 ] = modelMatrix[ 6 ];
			geometry.transform[ 10 ] = modelMatrix[ 10 ];
			geometry.transform[ 11 ] = modelMatrix[ 14 ];
			AppendRayTracingInstances( rayTracingGeometries, geometry,
				rayTracingSurface->space );
		}
		state->worldResourceReady++;
		VkDescriptorSet materialDescriptorSet = VK_NULL_HANDLE;
		const bool materialTexturesAvailable = selectedMegaTexture == NULL &&
			!selectedAtmosphereStage && !selectedWaterStage &&
			!selectedHeatHazeStage &&
			state->worldMaterialPipeline != VK_NULL_HANDLE &&
			selectedMaterialTextures &&
			GetCachedMaterialDescriptor( *selectedStage, selectedImage,
				surface->space,
				materialDescriptorSet );
		VkDescriptorSet waterDescriptorSet = VK_NULL_HANDLE;
		const bool waterTexturesAvailable = selectedWaterStage &&
			state->worldWaterPipeline != VK_NULL_HANDLE &&
			GetVulkanWaterDescriptor( *state, *selectedStage, waterDescriptorSet );
		if ( selectedWaterStage && !waterTexturesAvailable ) {
			state->worldWaterDescriptorMisses++;
			// A water stage's generic selected image is normally its white
			// specular lookup.  It is never a meaningful visual fallback.
			continue;
		}
		if ( rayTracingSurface != NULL && !hardwareSkinned && selectedWaterStage ) {
			sdRayTracingGeometry geometry;
			memset( &geometry, 0, sizeof( geometry ) );
			// Streamed surfaces (streamVertexCache) sit at a new frame-arena offset
			// every frame, so their acceleration structures cannot be cached.
			geometry.deforming = surface->geo->deformedSurface ||
				surface->geo->streamVertexCache ||
				surface->geo->deformedSurfaceInstance ||
				surface->geo->hardwareSkinnedSurface ||
				surface->geo->frameAlloced || surface->geo->weightCache != NULL ||
				( rayTracingSurface->space->model != NULL && idStr::Icmp(
					rayTracingSurface->space->model->Name(), "_MD5_Snapshot_" ) == 0 );
			geometry.geometryOwner = geometry.deforming &&
				rayTracingSurface->space->entityDef != NULL ?
				static_cast< const void* >( rayTracingSurface->space->entityDef ) :
				static_cast< const void* >( surface->geo );
			geometry.surfaceId = surface->surfID;
			geometry.vertexBuffer = vertexResource->buffer;
			geometry.vertexOffset = vertexCacheBlock->offset;
			geometry.vertexCount = surface->geo->numVerts;
			geometry.indexBuffer = indexResource->buffer;
			geometry.indexOffset = indexCacheBlock->offset;
			geometry.indexCount = surface->geo->numIndexes;
			const float* modelMatrix = rayTracingSurface->space->modelMatrix;
			geometry.transform[ 0 ] = modelMatrix[ 0 ];
			geometry.transform[ 1 ] = modelMatrix[ 4 ];
			geometry.transform[ 2 ] = modelMatrix[ 8 ];
			geometry.transform[ 3 ] = modelMatrix[ 12 ];
			geometry.transform[ 4 ] = modelMatrix[ 1 ];
			geometry.transform[ 5 ] = modelMatrix[ 5 ];
			geometry.transform[ 6 ] = modelMatrix[ 9 ];
			geometry.transform[ 7 ] = modelMatrix[ 13 ];
			geometry.transform[ 8 ] = modelMatrix[ 2 ];
			geometry.transform[ 9 ] = modelMatrix[ 6 ];
			geometry.transform[ 10 ] = modelMatrix[ 10 ];
			geometry.transform[ 11 ] = modelMatrix[ 14 ];
			idVec3 localWaterNormal( 0.0f, 0.0f, 1.0f );
			if ( surface->geo->facePlanesCalculated &&
				surface->geo->facePlanes != NULL ) {
				localWaterNormal = surface->geo->facePlanes[ 0 ].Normal();
			} else if ( surface->geo->verts != NULL &&
				surface->geo->indexes != NULL ) {
				for ( int triangleIndex = 0;
					triangleIndex + 2 < surface->geo->numIndexes;
					triangleIndex += 3 ) {
					const glIndex_t index0 = surface->geo->indexes[ triangleIndex + 0 ];
					const glIndex_t index1 = surface->geo->indexes[ triangleIndex + 1 ];
					const glIndex_t index2 = surface->geo->indexes[ triangleIndex + 2 ];
					if ( index0 >= surface->geo->numVerts ||
						index1 >= surface->geo->numVerts ||
						index2 >= surface->geo->numVerts ) {
						continue;
					}
					localWaterNormal =
						( surface->geo->verts[ index1 ].xyz -
							surface->geo->verts[ index0 ].xyz ).Cross(
							surface->geo->verts[ index2 ].xyz -
							surface->geo->verts[ index0 ].xyz );
					if ( localWaterNormal.Normalize() > 0.001f ) {
						break;
					}
				}
			}
			idVec3 worldWaterNormal(
				modelMatrix[ 0 ] * localWaterNormal.x +
					modelMatrix[ 4 ] * localWaterNormal.y +
					modelMatrix[ 8 ] * localWaterNormal.z,
				modelMatrix[ 1 ] * localWaterNormal.x +
					modelMatrix[ 5 ] * localWaterNormal.y +
					modelMatrix[ 9 ] * localWaterNormal.z,
				modelMatrix[ 2 ] * localWaterNormal.x +
					modelMatrix[ 6 ] * localWaterNormal.y +
					modelMatrix[ 10 ] * localWaterNormal.z );
			if ( worldWaterNormal.Normalize() <= 0.001f ) {
				worldWaterNormal.Set( 0.0f, 0.0f, 1.0f );
			}
			geometry.normal[ 0 ] = worldWaterNormal.x;
			geometry.normal[ 1 ] = worldWaterNormal.y;
			geometry.normal[ 2 ] = worldWaterNormal.z;
			geometry.normal[ 3 ] = 0.0f;
			AppendRayTracingInstances( rayTracingWaterGeometries, geometry,
				rayTracingSurface->space );
		}
		VkDescriptorSet heatHazeDescriptorSet = VK_NULL_HANDLE;
		const bool heatHazeTexturesAvailable = selectedHeatHazeStage &&
			state->worldHeatHazePipeline != VK_NULL_HANDLE &&
			GetVulkanHeatHazeDescriptor( *state, *selectedStage, selectedImage,
				heatHazeDescriptorSet );
		VkDescriptorSet skyboxDescriptorSet = VK_NULL_HANDLE;
		const bool skyboxTexturesAvailable = selectedSkyboxStage &&
			state->worldSkyboxPipeline != VK_NULL_HANDLE &&
			GetVulkanSkyboxDescriptor( *state, *selectedStage,
				skyboxDescriptorSet );
		if ( selectedSkyboxStage && !skyboxTexturesAvailable ) {
			// The side strip alone is not a valid fallback for an authored dome;
			// without its top image the seam becomes a broken sky sheet.
			continue;
		}

		const size_t spaceKey = reinterpret_cast< size_t >( surface->space );
		const int spaceCacheIndex = static_cast< int >( ( ( spaceKey >> 4 ) ^
			( spaceKey >> 13 ) ) & ( SPACE_SUBMISSION_CACHE_SIZE - 1 ) );
		spaceSubmissionCacheEntry_t& spaceCache =
			spaceSubmissionCache[ spaceCacheIndex ];
		const bool sameSpace = spaceCache.space == surface->space &&
			spaceCache.inputScissor[ 0 ] == surface->scissorRect.x1 &&
			spaceCache.inputScissor[ 1 ] == surface->scissorRect.y1 &&
			spaceCache.inputScissor[ 2 ] == surface->scissorRect.x2 &&
			spaceCache.inputScissor[ 3 ] == surface->scissorRect.y2;
		if ( !sameSpace ) {
			memset( &spaceCache, 0, sizeof( spaceCache ) );
			spaceCache.space = surface->space;
			spaceCache.inputScissor[ 0 ] = surface->scissorRect.x1;
			spaceCache.inputScissor[ 1 ] = surface->scissorRect.y1;
			spaceCache.inputScissor[ 2 ] = surface->scissorRect.x2;
			spaceCache.inputScissor[ 3 ] = surface->scissorRect.y2;
			int scissorWidth = Max( 1,
				surface->scissorRect.x2 - surface->scissorRect.x1 + 1 );
			int scissorHeight = Max( 1,
				surface->scissorRect.y2 - surface->scissorRect.y1 + 1 );
			spaceCache.scissor.offset.x =
				view->viewport.x1 + surface->scissorRect.x1;
			spaceCache.scissor.offset.y = static_cast< int >( state->extent.height ) -
				( view->viewport.y1 + surface->scissorRect.y1 + scissorHeight );
			if ( spaceCache.scissor.offset.x < 0 ) {
				scissorWidth -= Min( scissorWidth, -spaceCache.scissor.offset.x );
				spaceCache.scissor.offset.x = 0;
			}
			if ( spaceCache.scissor.offset.y < 0 ) {
				scissorHeight -= Min( scissorHeight, -spaceCache.scissor.offset.y );
				spaceCache.scissor.offset.y = 0;
			}
			scissorWidth = Min( scissorWidth,
				static_cast< int >( state->extent.width ) -
				spaceCache.scissor.offset.x );
			scissorHeight = Min( scissorHeight,
				static_cast< int >( state->extent.height ) -
				spaceCache.scissor.offset.y );
			spaceCache.visible = scissorWidth > 0 && scissorHeight > 0;
			if ( spaceCache.visible ) {
				spaceCache.scissor.extent.width =
					static_cast< unsigned int >( scissorWidth );
				spaceCache.scissor.extent.height =
					static_cast< unsigned int >( scissorHeight );
				float surfaceProjection[ 16 ];
				memcpy( surfaceProjection, view->projectionMatrix,
					sizeof( surfaceProjection ) );
				float surfaceMinDepth = defaultDepthStart;
				float surfaceMaxDepth = 1.0f;
				if ( surface->space->weaponDepthHack ) {
					surfaceMinDepth = 0.0f;
					surfaceMaxDepth = weaponDepthEnd;
					const float weaponFovX = surface->space->weaponDepthHackFOV_x;
					const float weaponFovY = surface->space->weaponDepthHackFOV_y;
					if ( weaponFovX > 0.0f && weaponFovY > 0.0f ) {
						const float xMax = zNear * idMath::Tan(
							weaponFovX * idMath::M_DEG2RAD * 0.5f );
						const float yMax = zNear * idMath::Tan(
							weaponFovY * idMath::M_DEG2RAD * 0.5f );
						surfaceProjection[ 0 ] = zNear / Max( xMax, 0.001f );
						surfaceProjection[ 5 ] = zNear / Max( yMax, 0.001f );
						surfaceProjection[ 8 ] = 0.0f;
						surfaceProjection[ 9 ] = 0.0f;
					}
					surfaceProjection[ 14 ] *= weaponDepthScale;
				}
				if ( surface->space->modelDepthHack != 0.0f ) {
					memcpy( surfaceProjection, view->projectionMatrix,
						sizeof( surfaceProjection ) );
					surfaceProjection[ 14 ] -= surface->space->modelDepthHack;
					surfaceMinDepth = defaultDepthStart;
					surfaceMaxDepth = 1.0f;
				}
				spaceCache.viewport = viewport;
				spaceCache.viewport.minDepth = surfaceMinDepth;
				spaceCache.viewport.maxDepth =
					Max( surfaceMinDepth, surfaceMaxDepth );
				for ( int column = 0; column < 4; ++column ) {
					for ( int row = 0; row < 4; ++row ) {
						spaceCache.mvp[ column * 4 + row ] =
							surfaceProjection[ row ] * surface->space->modelViewMatrix[ column * 4 ] +
							surfaceProjection[ 4 + row ] * surface->space->modelViewMatrix[ column * 4 + 1 ] +
							surfaceProjection[ 8 + row ] * surface->space->modelViewMatrix[ column * 4 + 2 ] +
							surfaceProjection[ 12 + row ] * surface->space->modelViewMatrix[ column * 4 + 3 ];
					}
					spaceCache.mvp[ column * 4 + 2 ] = 0.5f *
						( spaceCache.mvp[ column * 4 + 2 ] +
						  spaceCache.mvp[ column * 4 + 3 ] );
				}
				spaceCache.localSunDirection.Set(
					worldSunDirection.x * surface->space->modelMatrix[ 0 ] + worldSunDirection.y * surface->space->modelMatrix[ 1 ] + worldSunDirection.z * surface->space->modelMatrix[ 2 ],
					worldSunDirection.x * surface->space->modelMatrix[ 4 ] + worldSunDirection.y * surface->space->modelMatrix[ 5 ] + worldSunDirection.z * surface->space->modelMatrix[ 6 ],
					worldSunDirection.x * surface->space->modelMatrix[ 8 ] + worldSunDirection.y * surface->space->modelMatrix[ 9 ] + worldSunDirection.z * surface->space->modelMatrix[ 10 ] );
				const idVec3 translatedViewOrigin(
					view->renderView.vieworg.x - surface->space->modelMatrix[ 12 ],
					view->renderView.vieworg.y - surface->space->modelMatrix[ 13 ],
					view->renderView.vieworg.z - surface->space->modelMatrix[ 14 ] );
				spaceCache.localViewOrigin.Set(
					translatedViewOrigin.x * surface->space->modelMatrix[ 0 ] + translatedViewOrigin.y * surface->space->modelMatrix[ 1 ] + translatedViewOrigin.z * surface->space->modelMatrix[ 2 ],
					translatedViewOrigin.x * surface->space->modelMatrix[ 4 ] + translatedViewOrigin.y * surface->space->modelMatrix[ 5 ] + translatedViewOrigin.z * surface->space->modelMatrix[ 6 ],
					translatedViewOrigin.x * surface->space->modelMatrix[ 8 ] + translatedViewOrigin.y * surface->space->modelMatrix[ 9 ] + translatedViewOrigin.z * surface->space->modelMatrix[ 10 ] );
				EncodeModelRotation( surface->space->modelMatrix,
					spaceCache.modelRotation[ 0 ], spaceCache.modelRotation[ 1 ] );
				EncodeOctahedralDirection( spaceCache.localSunDirection,
					spaceCache.sunDirection[ 0 ], spaceCache.sunDirection[ 1 ] );
			}
		}
		if ( !spaceCache.visible ) continue;
		SetScissor( spaceCache.scissor );
		SetViewport( spaceCache.viewport );

		float pushConstants[ 32 ];
		memset( pushConstants, 0, sizeof( pushConstants ) );
		memcpy( pushConstants, spaceCache.mvp, sizeof( spaceCache.mvp ) );
		pushConstants[ 16 ] = pushConstants[ 17 ] =
			pushConstants[ 18 ] = pushConstants[ 19 ] = 1.0f;
		if ( selectedStage->colorVector != NULL ) {
			for ( int component = 0; component < 4; ++component ) {
				pushConstants[ 16 + component ] = surface->materialRegisters[
					selectedStage->colorVector->registers[ component ] ];
			}
		}
		pushConstants[ 20 ] = 1.0f;
		pushConstants[ 25 ] = 1.0f;
		if ( selectedStage->diffuseTextureMatrix != NULL ) {
			const int ( *matrix )[ 3 ] = selectedStage->diffuseTextureMatrix->matrix;
			pushConstants[ 20 ] = surface->materialRegisters[ matrix[ 0 ][ 0 ] ];
			pushConstants[ 21 ] = surface->materialRegisters[ matrix[ 0 ][ 1 ] ];
			pushConstants[ 22 ] = surface->materialRegisters[ matrix[ 0 ][ 2 ] ];
			pushConstants[ 24 ] = surface->materialRegisters[ matrix[ 1 ][ 0 ] ];
			pushConstants[ 25 ] = surface->materialRegisters[ matrix[ 1 ][ 1 ] ];
			pushConstants[ 26 ] = surface->materialRegisters[ matrix[ 1 ][ 2 ] ];
		}
		if ( selectedWaterStage && selectedStage->numTextureMatrices > 0 ) {
			const int ( *matrix )[ 3 ] = selectedStage->textureMatrices[ 0 ].matrix;
			pushConstants[ 20 ] = surface->materialRegisters[ matrix[ 0 ][ 0 ] ];
			pushConstants[ 21 ] = surface->materialRegisters[ matrix[ 0 ][ 1 ] ];
			pushConstants[ 22 ] = surface->materialRegisters[ matrix[ 0 ][ 2 ] ];
			pushConstants[ 24 ] = surface->materialRegisters[ matrix[ 1 ][ 0 ] ];
			pushConstants[ 25 ] = surface->materialRegisters[ matrix[ 1 ][ 1 ] ];
			pushConstants[ 26 ] = surface->materialRegisters[ matrix[ 1 ][ 2 ] ];
		}
		if ( materialTexturesAvailable ) {
			pushConstants[ 23 ] = spaceCache.modelRotation[ 0 ];
			pushConstants[ 27 ] = spaceCache.modelRotation[ 1 ];
		} else {
			pushConstants[ 23 ] = spaceCache.sunDirection[ 0 ];
			pushConstants[ 27 ] = spaceCache.sunDirection[ 1 ];
		}
		if ( selectedAtmosphereStage ) {
			pushConstants[ 20 ] = spaceCache.localViewOrigin.x;
			pushConstants[ 21 ] = spaceCache.localViewOrigin.y;
			pushConstants[ 22 ] = spaceCache.localViewOrigin.z;
		}
		pushConstants[ 29 ] = surface->material->TestMaterialFlag(
			MF_LOWRANGEUVCOMPRESS ) ? ST_TO_FLOAT_LOWRANGE : ST_TO_FLOAT;
		if ( selectedWaterStage ) {
			// Water needs a view ray for proper fresnel reflection.  The water
			// shader does not use the generic stage color, so pass the camera in
			// local surface space through that slot and reserve the spare scalar
			// components for ETQW's authored water bindings.
			pushConstants[ 16 ] = spaceCache.localViewOrigin.x;
			pushConstants[ 17 ] = spaceCache.localViewOrigin.y;
			pushConstants[ 18 ] = spaceCache.localViewOrigin.z;
			pushConstants[ 19 ] = 0.78f;
			pushConstants[ 23 ] = 0.05f;
			pushConstants[ 27 ] = 5.0f;
			pushConstants[ 28 ] = 0.5f;
			pushConstants[ 30 ] = 1.0f;
			pushConstants[ 31 ] = 1.0f;
			for ( int vectorIndex = 0; vectorIndex < selectedStage->numVectors;
				++vectorIndex ) {
				const stageVector_t& vector = selectedStage->vectors[ vectorIndex ];
				if ( vector.renderBinding == NULL ) {
					continue;
				}
				const char* bindingName = vector.renderBinding->GetName();
				if ( idStr::Icmp( bindingName, "water_distortion" ) == 0 ) {
					pushConstants[ 23 ] = surface->materialRegisters[
						vector.registers[ 0 ] ];
					pushConstants[ 31 ] = surface->materialRegisters[
						vector.registers[ 2 ] ];
				} else if ( idStr::Icmp( bindingName, "water_fresnel" ) == 0 ) {
					pushConstants[ 27 ] = surface->materialRegisters[
						vector.registers[ 0 ] ];
				} else if ( idStr::Icmp( bindingName, "water_lerp" ) == 0 ) {
					pushConstants[ 28 ] = surface->materialRegisters[
						vector.registers[ 0 ] ];
				} else if ( idStr::Icmp( bindingName, "water_glare" ) == 0 ) {
					pushConstants[ 30 ] = surface->materialRegisters[
						vector.registers[ 0 ] ];
				}
			}
		} else if ( selectedSkyboxStage ) {
			for ( int vectorIndex = 0; vectorIndex < selectedStage->numVectors;
				++vectorIndex ) {
				const stageVector_t& vector = selectedStage->vectors[ vectorIndex ];
				if ( vector.renderBinding == NULL ||
					idStr::Icmp( vector.renderBinding->GetName(),
						"skies_cloudColor" ) != 0 ) {
					continue;
				}
				for ( int component = 0; component < 4; ++component ) {
					pushConstants[ 16 + component ] = surface->materialRegisters[
						vector.registers[ component ] ];
				}
				break;
			}
		} else {
			pushConstants[ 28 ] = VulkanStageUsesVertexColor( selectedStage ) ? 1.0f : 0.0f;
			if ( selectedStage->hasAlphaTest || selectedStuffGrassStage ) {
				pushConstants[ 30 ] = selectedStage->hasAlphaTest ?
					surface->materialRegisters[ selectedStage->alphaTestRegister ] : 0.4f;
				// 2: also discard the grass's vertex-alpha fade against the dither
				pushConstants[ 31 ] = selectedStuffGrassStage ? 2.0f : 1.0f;
			}
			if ( selectedStuffGrassStage ) {
				// retail's stuff/grass light, for the up-facing grass normals: the ambient
				// cube times its brightness (its flat colour here) plus the sun term
				// clamp( ( N.sun * 0.5 + 0.5 ) * sunColor * r_lightScale ), which the ARB
				// program passes in the clamped primary colour (#256)
				const sdDeclAmbientCubeMap* cubeMap = surface->space->ambientCubeMap;
				idVec3 light = cubeMap != NULL ? cubeMap->GetMinSpecAmbientColor() * cubeMap->GetBrightness() : vec3_zero;
				if ( view->atmosphere != NULL ) {
					idVec3 sunDirection = view->atmosphere->GetSunDirection();
					sunDirection.Normalize();
					const idVec3 sun = view->atmosphere->GetSunColor() *
						( cvarSystem->GetCVarFloat( "r_lightScale" ) * ( sunDirection.z * 0.5f + 0.5f ) );
					for ( int component = 0; component < 3; ++component ) {
						light[ component ] += idMath::ClampFloat( 0.0f, 1.0f, sun[ component ] );
					}
				}
				for ( int component = 0; component < 3; ++component ) {
					pushConstants[ 16 + component ] *= light[ component ];
				}
			}
		}

		const VkDeviceSize vertexOffset = vertexCacheBlock->offset;
		const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >(
			surface->geo->numVerts ) * sizeof( idDrawVert );
		const VkDeviceSize indexBytes = static_cast< VkDeviceSize >(
			surface->geo->numIndexes ) * sizeof( glIndex_t );
		const VkDeviceSize weightOffset = hardwareSkinned ?
			weightCacheBlock->offset : 0;
		const VkDeviceSize weightBytes = hardwareSkinned ?
			static_cast< VkDeviceSize >( surface->geo->numVerts ) *
				sizeof( vertWeight_t ) : 0;
		if ( vertexCacheBlock->offset < 0 || indexCacheBlock->offset < 0 ||
			( hardwareSkinned && weightCacheBlock->offset < 0 ) ||
			vertexOffset + vertexBytes > vertexResource->bytes ||
			( hardwareSkinned && weightOffset + weightBytes > weightResource->bytes ) ||
			static_cast< VkDeviceSize >( indexCacheBlock->offset ) + indexBytes >
				indexResource->bytes ) {
			state->worldMissingResource++;
			continue;
		}
		if ( hardwareSkinned && !BindJointPalette( surface->geo->joints,
			surface->geo->numJoints ) ) {
			state->worldMissingResource++;
			continue;
		}
		BindGeometry( vertexResource->buffer, vertexOffset,
			hardwareSkinned ? weightResource->buffer : VK_NULL_HANDLE,
			weightOffset,
			indexResource->buffer, indexCacheBlock->offset );
		if ( surface->space->weaponDepthHack && !currentRenderCopied &&
			CopyCurrentRender( *state ) ) {
			// Refraction is sorted after ordinary opaque surfaces, including the
			// first-person model.  Snapshot at the boundary before weapon-depth-hack
			// geometry so the gun draws normally in front of the finished water but
			// is not baked into the distorted scene texture.  The raster light
			// block runs after the gun and snapshots the unlit scene, so on a
			// device without ray queries refraction re-copies with the gun in.
			currentRenderCopied = true;
		}
		// Each material vertex program is compiled independently. Requiring the
		// color pass to reproduce a separate ambient prepass at bit-exact EQUAL
		// depth left thin black seams and polygon-sized holes on terrain, models,
		// and their water reflections. Opaque base pipelines now write depth while
		// shading, so there is one authoritative rasterization per surface.
		const bool requiresDepthPrepass = false;
		if ( requiresDepthPrepass ) {
			BindPipeline( state->worldDepthPipeline );
			BindDescriptorSet( imageResource->descriptorSet );
			state->CmdPushConstants( frame.commandBuffer,
				state->guiPipelineLayout,
				VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
				sizeof( pushConstants ), pushConstants );
			state->CmdDrawIndexed( frame.commandBuffer,
				surface->geo->numIndexes, 1, 0, 0, 0 );
			state->worldDrawCalls++;
			state->worldDepthDrawCalls++;
		}
		if ( selectedMegaTexture != NULL && state->worldMegaPipeline != VK_NULL_HANDLE ) {
			BindPipeline( state->worldMegaPipeline );
			const int shaderLevels = Min( selectedMegaTexture->GetNumLevels(), 6 );
			for ( int shaderLevel = 0; shaderLevel < shaderLevels; ++shaderLevel ) {
				idMegaTextureLevel* level = selectedMegaTexture->GetLevel(
					selectedMegaTexture->GetNumLevels() - shaderLevel - 1 );
				if ( level == NULL || level->GetImage() == NULL ||
					( shaderLevel != 0 && !level->ImageIsValid() ) ) {
					continue;
				}
				idImage* levelImage = level->GetImage();
				if ( !levelImage->IsLoaded() ) {
					levelImage->BindFragment();
				}
				const sdVulkanImageResource* levelResource =
					FindVulkanImageResource( *state, levelImage );
				if ( levelResource == NULL || levelResource->descriptorSet == VK_NULL_HANDLE ) {
					continue;
				}
				float megaPushConstants[ 32 ];
				memcpy( megaPushConstants, pushConstants, sizeof( megaPushConstants ) );
				const float* levelParms = level->GetParms();
				megaPushConstants[ 20 ] = levelParms[ 0 ];
				megaPushConstants[ 21 ] = levelParms[ 1 ];
				megaPushConstants[ 22 ] = levelParms[ 2 ];
				megaPushConstants[ 23 ] = levelParms[ 3 ];
				megaPushConstants[ 24 ] = static_cast< float >( 1 << shaderLevel );
				megaPushConstants[ 25 ] = 1.0f;
				megaPushConstants[ 26 ] = shaderLevel == 0 ? 0.0f : 1.0f;
				megaPushConstants[ 30 ] = pushConstants[ 23 ];
				megaPushConstants[ 31 ] = pushConstants[ 27 ];
				// megaLevelParams.w: $megaDetailTextureParams.x, the detail texture's
				// repeat over the megatexture (0: no detail, r_detailTexture 0)
				megaPushConstants[ 27 ] = idMegaTexture::r_detailTexture.GetBool() && rbinds != NULL ?
					rbinds->megaDetailTextureParams->GetVector()[ 0 ] : 0.0f;
				if ( shaderLevel != 0 ) {
					megaPushConstants[ 25 ] = megaFadeMilliseconds > 0 ?
						idMath::ClampFloat( 0.0f, 1.0f,
							( view->renderView.time - level->GetFadeTime() ) /
							static_cast< float >( megaFadeMilliseconds ) ) : 1.0f;
				}
				VkDescriptorSet megaDescriptorSet = VK_NULL_HANDLE;
				if ( state->megaDetailPipeline && !submitHardwareSkinning &&
					GetVulkanMegaDescriptor( *state, level, levelImage, megaDescriptorSet ) ) {
					state->CmdBindDescriptorSets( frame.commandBuffer,
						VK_PIPELINE_BIND_POINT_GRAPHICS, state->frontEndPipelineLayout, 0, 1,
						&megaDescriptorSet, 0, NULL );
					// a set 0 of another layout disturbs the material set and the joint palette
					submittedDescriptorSet = VK_NULL_HANDLE;
					submittedJointOffset = UINT_MAX;
					state->CmdPushConstants( frame.commandBuffer, state->frontEndPipelineLayout,
						VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
						sizeof( megaPushConstants ), megaPushConstants );
				} else if ( state->megaDetailPipeline && !submitHardwareSkinning ) {
					static bool megaDescriptorWarned = false;
					if ( !megaDescriptorWarned ) {
						common->Warning( "Vulkan: no MegaTexture detail descriptor for a level of '%s'; the level is skipped",
							surface->material->GetName() );
						megaDescriptorWarned = true;
					}
					continue;	// the pipeline expects the detail set; draw nothing rather than mismatch
				} else {
					BindDescriptorSet( levelResource->descriptorSet );
					state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
						VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
						sizeof( megaPushConstants ), megaPushConstants );
				}
				state->CmdDrawIndexed( frame.commandBuffer, surface->geo->numIndexes,
					1, 0, 0, 0 );
				state->worldDrawCalls++;
				state->worldMegaDrawCalls++;
			}
		} else {
			VkPipeline materialPipeline = state->worldPipeline;
			VkDescriptorSet drawDescriptorSet = selectedImageDescriptorSet;
			const int blendBits = selectedStage->drawStateBits & 0xFF;
			// Retail's front-end ambient programs light themselves from the entity's
			// ambient cube map (frontend.frag); the rest of this path expects lights.
			const int frontEndMode = preparedSelection.frontEndMode;
			VkPipeline frontEndPipeline = VK_NULL_HANDLE;
			if ( frontEndMode == FRONTEND_AMBIENT_DECAL + 1 ) {
				frontEndPipeline = state->worldFrontEndPipeline;
			} else if ( frontEndMode == FRONTEND_AMBIENT_CLOUD + 1 ) {
				frontEndPipeline = state->worldFrontEndAlphaPipeline;
			} else if ( frontEndMode == FRONTEND_ATMOSPHERE + 1 ) {
				frontEndPipeline = state->worldFrontEndAddPipeline;
			}
			// Each pipeline bakes one cull: a stage's own cullFace overrides the
			// material's (retail's atmosphere stage says cullFace front, parsed as
			// CT_BACK_SIDED).  Any other cull takes the generic path.
			const cullType_t frontEndCull = selectedStage->cullType != CT_INVALID ?
				selectedStage->cullType : surface->material->GetCullType();
			const cullType_t frontEndPipelineCull =
				frontEndMode == FRONTEND_ATMOSPHERE + 1 ? CT_BACK_SIDED : CT_FRONT_SIDED;
			VkDescriptorSet frontEndDescriptorSet = VK_NULL_HANDLE;
			if ( frontEndPipeline != VK_NULL_HANDLE && !submitHardwareSkinning &&
				frontEndCull == frontEndPipelineCull &&
				!view->isMirror &&
				GetVulkanFrontEndDescriptor( *state, *selectedStage, selectedImage,
					surface->space, frontEndDescriptorSet ) ) {
				float frontEndPushConstants[ 32 ];
				memcpy( frontEndPushConstants, pushConstants, sizeof( frontEndPushConstants ) );
				// $parameters (retail's default binding value is 1)
				frontEndPushConstants[ 16 ] = frontEndPushConstants[ 17 ] =
					frontEndPushConstants[ 18 ] = frontEndPushConstants[ 19 ] = 1.0f;
				for ( int vectorIndex = 0; vectorIndex < selectedStage->numVectors;
					++vectorIndex ) {
					const stageVector_t& vector = selectedStage->vectors[ vectorIndex ];
					if ( vector.renderBinding != NULL &&
						idStr::Icmp( vector.renderBinding->GetName(), "parameters" ) == 0 ) {
						for ( int component = 0; component < 4; ++component ) {
							frontEndPushConstants[ 16 + component ] =
								surface->materialRegisters[ vector.registers[ component ] ];
						}
					}
				}
				frontEndPushConstants[ 20 ] = 1.0f;
				frontEndPushConstants[ 21 ] = 0.0f;
				frontEndPushConstants[ 22 ] = 0.0f;
				frontEndPushConstants[ 24 ] = 0.0f;
				frontEndPushConstants[ 25 ] = 1.0f;
				frontEndPushConstants[ 26 ] = 0.0f;
				if ( selectedStage->diffuseTextureMatrix != NULL ) {
					const int ( *matrix )[ 3 ] = selectedStage->diffuseTextureMatrix->matrix;
					for ( int column = 0; column < 3; ++column ) {
						frontEndPushConstants[ 20 + column ] =
							surface->materialRegisters[ matrix[ 0 ][ column ] ];
						frontEndPushConstants[ 24 + column ] =
							surface->materialRegisters[ matrix[ 1 ][ column ] ];
					}
				}
				frontEndPushConstants[ 23 ] = spaceCache.modelRotation[ 0 ];
				frontEndPushConstants[ 27 ] = spaceCache.modelRotation[ 1 ];
				// 0: no vertex colour, 1: modulate, 2: inverse modulate (1 - colour)
				frontEndPushConstants[ 28 ] = selectedStage->vertexColor == SVC_MODULATE ? 1.0f :
					( selectedStage->vertexColor == SVC_INVERSE_MODULATE ? 2.0f : 0.0f );
				const sdDeclAmbientCubeMap* cubeMap = surface->space->ambientCubeMap;
				frontEndPushConstants[ 30 ] = cubeMap != NULL ? cubeMap->GetBrightness() : 1.0f;
				frontEndPushConstants[ 31 ] = static_cast< float >( frontEndMode - 1 );
				BindPipeline( frontEndPipeline );
				state->CmdBindDescriptorSets( frame.commandBuffer,
					VK_PIPELINE_BIND_POINT_GRAPHICS, state->frontEndPipelineLayout, 0, 1,
					&frontEndDescriptorSet, 0, NULL );
				// a set 0 of another layout disturbs the material set and the joint palette
				submittedDescriptorSet = VK_NULL_HANDLE;
				submittedJointOffset = UINT_MAX;
				state->CmdPushConstants( frame.commandBuffer, state->frontEndPipelineLayout,
					VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
					sizeof( frontEndPushConstants ), frontEndPushConstants );
				state->CmdDrawIndexed( frame.commandBuffer, surface->geo->numIndexes,
					1, 0, 0, 0 );
				state->worldDrawCalls++;
				continue;
			}
			if ( skyboxTexturesAvailable ) {
				materialPipeline = state->worldSkyboxPipeline;
				if ( blendBits == 0x65 &&
					state->worldSkyboxAlphaPipeline != VK_NULL_HANDLE ) {
					materialPipeline = state->worldSkyboxAlphaPipeline;
				} else if ( blendBits == 0x20 &&
					state->worldSkyboxAddPipeline != VK_NULL_HANDLE ) {
					materialPipeline = state->worldSkyboxAddPipeline;
				}
				drawDescriptorSet = skyboxDescriptorSet;
			} else if ( selectedHeatHazeStage ) {
				if ( !heatHazeTexturesAvailable ||
					( !currentRenderCopied && !CopyCurrentRender( *state ) ) ) {
					// A postprocess normal map is never a valid visible fallback.
					// If this surface cannot snapshot the scene, omit it instead of
					// covering the framebuffer with an opaque gray quad.
					continue;
				}
				currentRenderCopied = true;
				materialPipeline = state->worldHeatHazePipeline;
				drawDescriptorSet = heatHazeDescriptorSet;
			} else if ( waterTexturesAvailable ) {
				if ( !currentRenderCopied && !CopyCurrentRender( *state ) ) {
					// Refraction water is fully composed from the scene snapshot.  It
					// has no useful flat-color fallback if the copy is unavailable.
					continue;
				}
				currentRenderCopied = true;
				materialPipeline = state->worldWaterPipeline;
				drawDescriptorSet = waterDescriptorSet;
			} else if ( selectedAtmosphereStage &&
				state->worldAtmospherePipeline != VK_NULL_HANDLE ) {
				materialPipeline = state->worldAtmospherePipeline;
			} else if ( materialTexturesAvailable && blendBits == 0x65 &&
				state->worldMaterialAlphaPipeline != VK_NULL_HANDLE ) {
				materialPipeline = state->worldMaterialAlphaPipeline;
				drawDescriptorSet = materialDescriptorSet;
			} else if ( materialTexturesAvailable && blendBits == 0x20 &&
				state->worldMaterialAddPipeline != VK_NULL_HANDLE ) {
				materialPipeline = state->worldMaterialAddPipeline;
				drawDescriptorSet = materialDescriptorSet;
			} else if ( selectedStuffGrassStage ) {
				// Grass is declared translucent to control sorting, but its stage is
				// alpha-to-coverage + writeDepth.  With a single-sample swapchain the
				// faithful fallback is an alpha-tested opaque/depth-writing pass, not
				// source-alpha blending of the whole polygon card.
				materialPipeline = state->worldPipeline;
			} else if ( materialTexturesAvailable && ( blendBits == 0x00 ||
				blendBits == 0x01 ) ) {
				materialPipeline = state->worldMaterialPipeline;
				drawDescriptorSet = materialDescriptorSet;
			} else if ( blendBits == 0x65 ) {
				materialPipeline = state->worldAlphaPipeline;
			} else if ( blendBits == 0x20 ) {
				materialPipeline = state->worldAddPipeline;
			} else if ( blendBits == 0x25 ) {
				materialPipeline = state->worldAlphaAddPipeline;
			} else if ( blendBits == 0x03 ) {
				materialPipeline = state->worldMultiplyPipeline;
				pushConstants[ 30 ] = StageUsesCoverageFade( *selectedStage ) ?
					idMath::ClampFloat( 0.0f, 1.0f,
						surface->space->coverage ) : 1.0f;
				pushConstants[ 31 ] = 0.0f;
			} else if ( surface->material->Coverage() == MC_TRANSLUCENT ) {
				materialPipeline = state->worldAlphaPipeline;
			}
			BindPipeline( materialPipeline );
			BindDescriptorSet( drawDescriptorSet );
			state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
				VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
				sizeof( pushConstants ), pushConstants );
			state->CmdDrawIndexed( frame.commandBuffer, surface->geo->numIndexes,
				1, 0, 0, 0 );
			state->worldDrawCalls++;
			if ( waterTexturesAvailable ) {
				state->worldWaterDrawCalls++;
			}
			if ( selectedStuffGrassStage ) {
				state->worldStuffDrawCalls++;
			}
			if ( selectedHeatHazeStage ) {
				continue;
			}

			// Opaque interaction materials (including basic_detail/basic_detailwm)
			// are one complete surface program. Replaying another declaration stage
			// over the same triangles gives the two rasterizations slightly different
			// visibility at close range and looks like LOD Z fighting. Their diffuse,
			// bump, specular, and self-illumination inputs are already folded into the
			// selected material descriptor above. Multi-stage replay is retained only
			// for genuinely translucent effects, where declaration-order blending is
			// part of the material rather than a second opaque surface pass. Water has
			// its own fully composed refraction shader and must never be replayed here.
			const bool allowAdditionalStages =
				surface->material->Coverage() == MC_TRANSLUCENT &&
				!selectedWaterStage;
			for ( int stageIndex = 0; allowAdditionalStages &&
				stageIndex < surface->material->GetNumStages(); ++stageIndex ) {
				const materialStage_t* overlayStage =
					surface->material->GetStage( stageIndex );
				if ( overlayStage == selectedStage || overlayStage->megaTexture != NULL ||
					surface->materialRegisters[ overlayStage->conditionRegister ] == 0.0f ) {
					continue;
				}
				const int overlayBlendBits = overlayStage->drawStateBits & 0xFF;
				if ( overlayBlendBits != 0x65 && overlayBlendBits != 0x20 &&
					overlayBlendBits != 0x25 && overlayBlendBits != 0x03 ) {
					continue;
				}
				idImage* overlayImage = NULL;
				int overlayImageScore = -0x7fffffff;
				for ( int textureIndex = 0; textureIndex < overlayStage->numTextures;
					++textureIndex ) {
					const stageTexture_t& texture = overlayStage->textures[ textureIndex ];
					if ( texture.image == NULL || texture.image->defaulted ||
						( globalImages != NULL && ( texture.image == globalImages->blackImage ||
						  texture.image == globalImages->grayImage ) ) ) {
						continue;
					}
					int score = 10;
					if ( rbinds != NULL && texture.renderBinding == rbinds->diffuseMap ) {
						score = 100;
					} else if ( rbinds != NULL && texture.renderBinding == rbinds->map ) {
						score = 90;
					}
					if ( score > overlayImageScore ) {
						overlayImage = texture.image;
						overlayImageScore = score;
					}
				}
				if ( overlayImage == NULL ) {
					continue;
				}
				if ( !overlayImage->IsLoaded() ) {
					overlayImage->BindFragment();
				}
				const sdVulkanImageResource* overlayResource =
					FindVulkanImageResource( *state, overlayImage );
				if ( overlayResource == NULL ||
					overlayResource->descriptorSet == VK_NULL_HANDLE ) {
					continue;
				}
				float overlayPushConstants[ 32 ];
				memcpy( overlayPushConstants, pushConstants,
					sizeof( overlayPushConstants ) );
				overlayPushConstants[ 16 ] = overlayPushConstants[ 17 ] =
					overlayPushConstants[ 18 ] = overlayPushConstants[ 19 ] = 1.0f;
				if ( overlayStage->colorVector != NULL ) {
					for ( int component = 0; component < 4; ++component ) {
						overlayPushConstants[ 16 + component ] =
							surface->materialRegisters[
								overlayStage->colorVector->registers[ component ] ];
					}
				}
				overlayPushConstants[ 20 ] = 1.0f;
				overlayPushConstants[ 21 ] = 0.0f;
				overlayPushConstants[ 22 ] = 0.0f;
				overlayPushConstants[ 24 ] = 0.0f;
				overlayPushConstants[ 25 ] = 1.0f;
				overlayPushConstants[ 26 ] = 0.0f;
				if ( overlayStage->diffuseTextureMatrix != NULL ) {
					const int ( *matrix )[ 3 ] =
						overlayStage->diffuseTextureMatrix->matrix;
					overlayPushConstants[ 20 ] = surface->materialRegisters[ matrix[ 0 ][ 0 ] ];
					overlayPushConstants[ 21 ] = surface->materialRegisters[ matrix[ 0 ][ 1 ] ];
					overlayPushConstants[ 22 ] = surface->materialRegisters[ matrix[ 0 ][ 2 ] ];
					overlayPushConstants[ 24 ] = surface->materialRegisters[ matrix[ 1 ][ 0 ] ];
					overlayPushConstants[ 25 ] = surface->materialRegisters[ matrix[ 1 ][ 1 ] ];
					overlayPushConstants[ 26 ] = surface->materialRegisters[ matrix[ 1 ][ 2 ] ];
				}
				overlayPushConstants[ 28 ] =
					overlayStage->vertexColor == SVC_MODULATE ? 1.0f : 0.0f;
				overlayPushConstants[ 30 ] = overlayStage->hasAlphaTest ?
					surface->materialRegisters[ overlayStage->alphaTestRegister ] : 0.0f;
				overlayPushConstants[ 31 ] = overlayStage->hasAlphaTest ? 1.0f : 0.0f;

				VkPipeline overlayPipeline = state->worldAlphaPipeline;
				if ( overlayBlendBits == 0x20 ) {
					overlayPipeline = state->worldAddPipeline;
				} else if ( overlayBlendBits == 0x25 ) {
					overlayPipeline = state->worldAlphaAddPipeline;
				} else if ( overlayBlendBits == 0x03 ) {
					overlayPipeline = state->worldMultiplyPipeline;
					overlayPushConstants[ 30 ] =
						StageUsesCoverageFade( *overlayStage ) ?
						idMath::ClampFloat( 0.0f, 1.0f,
							surface->space->coverage ) : 1.0f;
					overlayPushConstants[ 31 ] = 0.0f;
				}
				VkDescriptorSet overlayDescriptorSet = overlayResource->descriptorSet;
				if ( StageUsesVulkanMaterialTextures( *overlayStage ) ) {
					VkDescriptorSet combinedDescriptorSet = VK_NULL_HANDLE;
					if ( GetCachedMaterialDescriptor( *overlayStage,
						overlayImage, surface->space, combinedDescriptorSet ) ) {
						EncodeModelRotation( surface->space->modelMatrix,
							overlayPushConstants[ 23 ],
							overlayPushConstants[ 27 ] );
						if ( overlayBlendBits == 0x65 ) {
							overlayPipeline = state->worldMaterialAlphaPipeline;
							overlayDescriptorSet = combinedDescriptorSet;
						} else if ( overlayBlendBits == 0x20 ) {
							overlayPipeline = state->worldMaterialAddPipeline;
							overlayDescriptorSet = combinedDescriptorSet;
						}
					}
				}
				BindPipeline( overlayPipeline );
				SetPolygonOffset( surface->material, overlayStage );
				BindDescriptorSet( overlayDescriptorSet );
				state->CmdPushConstants( frame.commandBuffer,
					state->guiPipelineLayout,
					VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
					sizeof( overlayPushConstants ), overlayPushConstants );
				state->CmdDrawIndexed( frame.commandBuffer,
					surface->geo->numIndexes, 1, 0, 0, 0 );
				state->worldDrawCalls++;
				state->worldStageDrawCalls++;
			}
		}
		if ( hardwareSkinned ) {
			state->worldGpuSkinnedDrawCalls++;
		} else if ( surface->space->model != NULL &&
			idStr::Icmp( surface->space->model->Name(), "_MD5_Snapshot_" ) == 0 ) {
			state->worldSkinnedDrawCalls++;
		}
	}
	if ( !rasterLightingDone ) {	// every surface was opaque or a decal
		RunRasterLighting();
	}
	}

	if ( rayTracingGeometries.Num() != 0 && R_RayTracingIsInitialized() ) {
		RENDER_METRIC_SCOPE( "Vulkan ray tracing" );
		sdRayTracingViewContext rayTracingView;
		memset( &rayTracingView, 0, sizeof( rayTracingView ) );
		rayTracingView.commandBuffer = frame.commandBuffer;
		rayTracingView.frameIndex = state->frameIndex;
		rayTracingView.colorImage = state->swapchainImages[ state->imageIndex ];
		rayTracingView.colorImageView = state->swapchainViews[ state->imageIndex ];
		rayTracingView.depthImage = state->depthImages[ state->imageIndex ];
		rayTracingView.depthImageView = state->depthViews[ state->imageIndex ];
		rayTracingView.depthSampleView = state->depthSampleViews.Num() > static_cast< int >( state->imageIndex ) ?
			state->depthSampleViews[ state->imageIndex ] : VK_NULL_HANDLE;
		if ( currentRenderCopied && state->currentRenderInitialized ) {
			rayTracingView.reflectionImage = state->currentRenderResource.image;
			rayTracingView.reflectionImageView = state->currentRenderResource.view;
			rayTracingView.reflectionSampler = state->currentRenderResource.sampler;
		}
		rayTracingView.framebufferExtent = state->extent;
		rayTracingView.viewport.offset.x = idMath::ClampInt( 0,
			static_cast< int >( state->extent.width ), view->viewport.x1 );
		rayTracingView.viewport.offset.y = idMath::ClampInt( 0,
			static_cast< int >( state->extent.height ),
			static_cast< int >( state->extent.height ) -
			( view->viewport.y1 + viewportHeight ) );
		rayTracingView.viewport.extent.width = static_cast< unsigned int >(
			Min( viewportWidth, static_cast< int >( state->extent.width ) -
				rayTracingView.viewport.offset.x ) );
		rayTracingView.viewport.extent.height = static_cast< unsigned int >(
			Min( viewportHeight, static_cast< int >( state->extent.height ) -
				rayTracingView.viewport.offset.y ) );
		R_RayTracingDrawView( view, rayTracingGeometries.Begin(),
			rayTracingGeometries.Num(), rayTracingWaterGeometries.Begin(),
			rayTracingWaterGeometries.Num(), rayTracingView );
	}
}

// guis/postprocess's postprocess/glow material (#233).  Retail's stages:
// downsample_glare (_currentRender -> _postProcessBuffer_1), blur_bilinear
// horizontally (-> 0) and vertically (-> 1), then the glow over the screen:
// clean * glare.x + blur * glare.y, saturation, contrast around 0.5, tint.  The
// programs are Cg, so this runs their ports in glow.frag with the parameters of
// the frame's view atmosphere, as OpenGL's render bindings carry them.
bool sdVulkanBackend::DrawPostProcessGlow() {
	if ( state == NULL || !state->frameActive || state->glowAtmosphere == NULL ||
		state->glowBufferPipeline == VK_NULL_HANDLE ||
		state->glowScreenPipeline == VK_NULL_HANDLE || r_vkSkipGui.GetBool() ) {
		return false;
	}
	// the glow samples _currentRender: without a TRANSFER_SRC swapchain there is no
	// copy, and its descriptor sets would name a null view (#272 review)
	if ( !state->swapchainTransferSource || state->currentRenderResource.view == VK_NULL_HANDLE ||
		state->currentRenderResource.sampler == VK_NULL_HANDLE ) {
		return false;
	}
	if ( !EnsureGlowBuffers( *state ) || !CopyCurrentRender( *state ) ) {
		return false;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const sdDeclAtmosphere::postProcessParms_t& post = state->glowAtmosphere->GetPostProcessParms();
	const float screenWidth = static_cast< float >( state->extent.width );
	const float screenHeight = static_cast< float >( state->extent.height );
	const float bufferWidth = static_cast< float >( state->glowBuffers[ 0 ].width );
	const float bufferHeight = static_cast< float >( state->glowBuffers[ 0 ].height );
	// glare, saturation and contrast, tint, source and target sizes, screen size
	// and blur direction, then the pass and r_vkGlowDebug
	float pushConstants[ 24 ] = {
		post.glareParms.x, post.glareParms.y, post.glareParms.z, post.glareParms.w,
		post.saturation, post.contrast, 0.0f, 0.0f,
		post.tint.x, post.tint.y, post.tint.z, 0.0f,
		screenWidth, screenHeight, bufferWidth, bufferHeight,
		screenWidth, screenHeight, 0.0f, 0.0f,
		0.0f, r_vkGlowDebug.GetBool() ? 1.0f : 0.0f, 0.0f, 0.0f
	};
	state->CmdEndRendering( frame.commandBuffer );
	// downsample_glare
	DrawGlowBufferPass( *state, frame.commandBuffer, 1, state->glowDescriptorSets[ 0 ],
		pushConstants, 24 );
	// blur_bilinear, "parameters 1, 0" then "parameters 0, 1"
	pushConstants[ 12 ] = bufferWidth;
	pushConstants[ 13 ] = bufferHeight;
	pushConstants[ 18 ] = 1.0f;
	pushConstants[ 20 ] = 1.0f;
	DrawGlowBufferPass( *state, frame.commandBuffer, 0, state->glowDescriptorSets[ 1 ],
		pushConstants, 24 );
	pushConstants[ 18 ] = 0.0f;
	pushConstants[ 19 ] = 1.0f;
	DrawGlowBufferPass( *state, frame.commandBuffer, 1, state->glowDescriptorSets[ 2 ],
		pushConstants, 24 );
	// the glow, back on the swapchain image (colour and depth as the frame left them)
	VkMemoryBarrier2 attachmentBarrier;
	memset( &attachmentBarrier, 0, sizeof( attachmentBarrier ) );
	attachmentBarrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
	attachmentBarrier.srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT |
		VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT | VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
	attachmentBarrier.srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	attachmentBarrier.dstStageMask = attachmentBarrier.srcStageMask;
	attachmentBarrier.dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT | VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
		VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
	VkDependencyInfo dependencyInfo;
	memset( &dependencyInfo, 0, sizeof( dependencyInfo ) );
	dependencyInfo.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependencyInfo.memoryBarrierCount = 1;
	dependencyInfo.pMemoryBarriers = &attachmentBarrier;
	state->CmdPipelineBarrier2( frame.commandBuffer, &dependencyInfo );
	BeginSwapchainRendering( *state, frame.commandBuffer );
	pushConstants[ 12 ] = screenWidth;
	pushConstants[ 13 ] = screenHeight;
	pushConstants[ 14 ] = screenWidth;
	pushConstants[ 15 ] = screenHeight;
	pushConstants[ 20 ] = 2.0f;
	DrawGlowTriangle( *state, frame.commandBuffer, state->glowScreenPipeline,
		state->glowDescriptorSets[ 3 ], state->extent.width, state->extent.height,
		pushConstants, 24 );
	state->guiDrawCalls++;
	return true;
}

bool sdVulkanBackend::DrawGuiFan( const void* imageOwner,
	const sdVulkanGuiVertex* vertices, int vertexCount, const float* color,
	int drawStateBits, bool makeAlpha ) {
	if ( state == NULL || !state->frameActive || state->guiPipeline == VK_NULL_HANDLE ||
		imageOwner == NULL || vertices == NULL || vertexCount < 3 ||
		r_vkSkipGui.GetBool() ) {
		return false;
	}
	const sdVulkanImageResource* imageResource =
		FindVulkanImageResource( *state, imageOwner );
	if ( imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
		return false;
	}
	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( vertexCount ) *
		sizeof( sdVulkanGuiVertex );
	const VkDeviceSize vertexOffset = ( frame.guiVertexOffset + 15 ) & ~15ULL;
	if ( vertexOffset + vertexBytes > VULKAN_GUI_VERTEX_BYTES ) {
		if ( !frame.guiVertexOverflowWarned ) {
			common->Warning( "Vulkan GUI vertex stream exceeded %u MiB; remaining draws are skipped",
				static_cast< unsigned int >( VULKAN_GUI_VERTEX_BYTES / ( 1024 * 1024 ) ) );
			frame.guiVertexOverflowWarned = true;
		}
		return false;
	}
	memcpy( static_cast< byte* >( frame.guiVertexMapped ) + vertexOffset,
		vertices, static_cast< size_t >( vertexBytes ) );
	frame.guiVertexOffset = vertexOffset + vertexBytes;

	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( state->extent.height );
	viewport.width = static_cast< float >( state->extent.width );
	viewport.height = -static_cast< float >( state->extent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );
	VkRect2D scissor;
	memset( &scissor, 0, sizeof( scissor ) );
	scissor.extent = state->extent;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &scissor );
	VkPipeline guiPipeline = state->guiPipeline;
	const int blendBits = drawStateBits & 0xFF;
	if ( blendBits == 0x00 && state->guiOpaquePipeline != VK_NULL_HANDLE ) {
		guiPipeline = state->guiOpaquePipeline;
	} else if ( blendBits == 0x20 && state->guiAddPipeline != VK_NULL_HANDLE ) {
		guiPipeline = state->guiAddPipeline;
	} else if ( blendBits == 0x25 && state->guiAlphaAddPipeline != VK_NULL_HANDLE ) {
		guiPipeline = state->guiAlphaAddPipeline;
	} else if ( blendBits == 0x03 && state->guiMultiplyPipeline != VK_NULL_HANDLE ) {
		guiPipeline = state->guiMultiplyPipeline;
	}
	state->CmdBindPipeline( frame.commandBuffer, VK_PIPELINE_BIND_POINT_GRAPHICS,
		guiPipeline );
	state->CmdBindDescriptorSets( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
		&imageResource->descriptorSet, 0, NULL );
	// the third vec4 selects retail's trivialMakeAlpha* fragment program
	float pushConstants[ 12 ] = {
		2.0f / 640.0f, -2.0f / 480.0f, -1.0f, 1.0f,
		1.0f, 1.0f, 1.0f, 1.0f,
		makeAlpha ? 1.0f : 0.0f, 0.0f, 0.0f, 0.0f
	};
	if ( color != NULL ) {
		memcpy( pushConstants + 4, color, sizeof( float ) * 4 );
	}
	state->CmdPushConstants( frame.commandBuffer, state->guiPipelineLayout,
		VK_SHADER_STAGE_VERTEX_BIT | VK_SHADER_STAGE_FRAGMENT_BIT, 0,
		sizeof( pushConstants ), pushConstants );
	state->CmdBindVertexBuffers( frame.commandBuffer, 0, 1,
		&frame.guiVertexBuffer, &vertexOffset );
	state->CmdDraw( frame.commandBuffer, vertexCount, 1, 0, 0 );
	state->guiDrawCalls++;
	return true;
}

bool sdVulkanBackend::DrawOverlayTriangles( const void* imageOwner,
	const sdVulkanToolVertex* vertices, int vertexCount, int scissorX,
	int scissorY, int scissorWidth, int scissorHeight ) {
	if ( state == NULL || !state->frameActive || imageOwner == NULL ||
		vertices == NULL || vertexCount < 3 ||
		state->toolBlendPipeline == VK_NULL_HANDLE ) {
		return false;
	}
	const sdVulkanImageResource* imageResource =
		FindVulkanImageResource( *state, imageOwner );
	if ( imageResource == NULL || imageResource->descriptorSet == VK_NULL_HANDLE ) {
		return false;
	}

	sdVulkanFrame& frame = state->frames[ state->frameIndex ];
	const VkDeviceSize vertexBytes = static_cast< VkDeviceSize >( vertexCount ) *
		sizeof( sdVulkanToolVertex );
	const VkDeviceSize vertexOffset = ( frame.guiVertexOffset + 15 ) & ~15ULL;
	if ( vertexOffset + vertexBytes > VULKAN_GUI_VERTEX_BYTES ) {
		if ( !frame.guiVertexOverflowWarned ) {
			common->Warning( "Vulkan overlay vertex stream exceeded %u MiB; remaining draws are skipped",
				static_cast< unsigned int >( VULKAN_GUI_VERTEX_BYTES / ( 1024 * 1024 ) ) );
			frame.guiVertexOverflowWarned = true;
		}
		return false;
	}
	memcpy( static_cast< byte* >( frame.guiVertexMapped ) + vertexOffset,
		vertices, static_cast< size_t >( vertexBytes ) );
	frame.guiVertexOffset = vertexOffset + vertexBytes;

	VkViewport viewport;
	viewport.x = 0.0f;
	viewport.y = static_cast< float >( state->extent.height );
	viewport.width = static_cast< float >( state->extent.width );
	viewport.height = -static_cast< float >( state->extent.height );
	viewport.minDepth = 0.0f;
	viewport.maxDepth = 1.0f;
	state->CmdSetViewport( frame.commandBuffer, 0, 1, &viewport );

	const int framebufferWidth = static_cast< int >( state->extent.width );
	const int framebufferHeight = static_cast< int >( state->extent.height );
	const int left = idMath::ClampInt( 0, framebufferWidth, scissorX );
	const int top = idMath::ClampInt( 0, framebufferHeight, scissorY );
	const int right = idMath::ClampInt( left, framebufferWidth,
		scissorX + scissorWidth );
	const int bottom = idMath::ClampInt( top, framebufferHeight,
		scissorY + scissorHeight );
	if ( right <= left || bottom <= top ) {
		return false;
	}
	VkRect2D scissor;
	scissor.offset.x = left;
	scissor.offset.y = top;
	scissor.extent.width = right - left;
	scissor.extent.height = bottom - top;
	state->CmdSetScissor( frame.commandBuffer, 0, 1, &scissor );

	state->CmdBindPipeline( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->toolBlendPipeline );
	state->CmdSetDepthBias( frame.commandBuffer, 0.0f, 0.0f, 0.0f );
	state->CmdBindDescriptorSets( frame.commandBuffer,
		VK_PIPELINE_BIND_POINT_GRAPHICS, state->guiPipelineLayout, 0, 1,
		&imageResource->descriptorSet, 0, NULL );
	state->CmdBindVertexBuffers( frame.commandBuffer, 0, 1,
		&frame.guiVertexBuffer, &vertexOffset );
	state->CmdDraw( frame.commandBuffer, vertexCount, 1, 0, 0 );
	state->guiDrawCalls++;
	return true;
}

bool sdVulkanBackend::DrawGuiTriangles( const void* imageOwner,
	const sdVulkanToolVertex* vertices, int vertexCount ) {
	if ( state == NULL || r_vkSkipGui.GetBool() ) {
		return false;
	}
	return DrawOverlayTriangles( imageOwner, vertices, vertexCount, 0, 0,
		static_cast< int >( state->extent.width ),
		static_cast< int >( state->extent.height ) );
}

bool sdVulkanBackend::IsInitialized() const {
	return state != NULL && state->device != VK_NULL_HANDLE &&
		state->swapchain != VK_NULL_HANDLE;
}

bool sdVulkanBackend::IsFrameActive() const {
	return state != NULL && state->frameActive;
}

const char* sdVulkanBackend::GetDeviceName() const {
	return state != NULL ? state->deviceName : "";
}

const char* sdVulkanBackend::GetVendorString() const {
	return state != NULL ? state->vendorString : "";
}

const char* sdVulkanBackend::GetVersionString() const {
	return state != NULL ? state->versionString : "";
}

unsigned int sdVulkanBackend::GetVendorID() const {
	return state != NULL ? state->vendorID : 0;
}

namespace {
bool vulkanUnavailable = false;
}

void R_NotifyVulkanUnavailable() {
	vulkanUnavailable = true;
}

/*
==================
R_TakeFrameSyncTiming
==================
*/
void R_TakeFrameSyncTiming( frameSyncTiming_t& timing ) {
	timing.frames		= syncFrames.exchange( 0 );
	timing.fenceUsec	= syncFenceUsec.exchange( 0 );
	timing.acquireUsec	= syncAcquireUsec.exchange( 0 );
	timing.submitUsec	= syncSubmitUsec.exchange( 0 );
	timing.presentUsec	= syncPresentUsec.exchange( 0 );
}

bool R_UseVulkanBackend() {
	return !vulkanUnavailable && cvarSystem != NULL &&
		idStr::Icmp( cvarSystem->GetCVarString( "r_renderAPI" ), "vulkan" ) == 0;
}

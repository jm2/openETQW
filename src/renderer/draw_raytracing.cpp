// Copyright (C) 2007 Id Software, Inc.
//
// Hardware ray-query lighting for the Vulkan renderer.  This translation
// unit deliberately owns capability policy, acceleration structures,
// descriptors, and dispatch.  VulkanBackend.cpp only supplies live ETQW
// geometry handles and the current framebuffer.

#include "../framework/precompiled.h"
#pragma hdrstop

#include "draw_raytracing.h"
#include "RuntimeSpirvCompiler.h"
#include "VulkanSlowCall.h"
#include "draw_local.h"
#include "Material.h"
#include "../decllib/declAtmosphere.h"

namespace {

// Windows builds have shipped this path on by default since it was written, so
// they keep that default; _WIN64 covers both x64 and arm64, and arm64 devices
// that cannot ray trace fall back below.  Everywhere else the path has not been
// exercised, so it stays off and is opt-in through r_rayTracing; when it is
// requested the device capability check in R_RayTracingConfigureDevice decides,
// and a device without ray query falls back to rasterization with one log line.
#if defined( _WIN64 )
const char* RAY_TRACING_DEFAULT = "1";
#else
const char* RAY_TRACING_DEFAULT = "0";
#endif

const unsigned int MAX_RAY_TRACING_FRAMES = 4;
const unsigned int MAX_RAY_TRACING_LIGHTS = 1024;
const unsigned int MAX_RAY_TRACING_WATER_GEOMETRIES = 1024;
const unsigned int MAX_RAY_TRACING_VIEWS = 8;
const unsigned int RAY_TRACING_WATER_INSTANCE_BIT = 0x00800000u;
const unsigned int RAY_TRACING_SOLID_INSTANCE_MASK = 0x01u;
const unsigned int RAY_TRACING_WATER_INSTANCE_MASK = 0x02u;
const VkDeviceSize RAY_TRACING_VIEW_STRIDE = 64 * 1024;

// Every acceleration-structure build configuration this file records, one row each
// (#338). The run-time builds take their type, flags and mode from this table, and
// PrewarmAccelerationStructureBuilds builds every row at init: a driver that compiles
// its BVH-build shaders on the first build of a configuration (AMD's: about 650 ms on a
// cold per-executable shader cache) then does it at startup, not when the first
// deforming surface comes into view in a game. A configuration added here is prewarmed
// with no other change; one used without a row here does not compile.
enum rayTracingBuildKind_t {
	RAY_TRACING_BUILD_STATIC_BLAS,
	RAY_TRACING_BUILD_DEFORMING_BLAS,	// rebuilt every frame it is drawn
	RAY_TRACING_BUILD_TLAS,
	RAY_TRACING_BUILD_KINDS
};

struct sdRayTracingBuildConfig {
	const char*								name;
	VkAccelerationStructureTypeKHR			type;
	VkBuildAccelerationStructureFlagsKHR	flags;
	VkBuildAccelerationStructureModeKHR		mode;
};

const sdRayTracingBuildConfig RAY_TRACING_BUILD_CONFIGS[ RAY_TRACING_BUILD_KINDS ] = {
	{ "static BLAS", VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_TRACE_BIT_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR },
	{ "deforming BLAS", VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_BUILD_BIT_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR },
	{ "TLAS", VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_TRACE_BIT_KHR,
		VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR },
};

// The prewarm builds each configuration at several sizes, since a driver may choose its
// build path by primitive count: BLASes of up to 65536 triangles (a 128x256 grid of
// quads, 33153 vertices, so 16-bit indices reach them all) and TLASes of up to 4096
// instances.
const unsigned int RAY_TRACING_PREWARM_GRID_COLUMNS = 128;
const unsigned int RAY_TRACING_PREWARM_GRID_ROWS = 256;
const unsigned int RAY_TRACING_PREWARM_TRIANGLES[] = { 1, 64, 4096, 65536 };
const unsigned int RAY_TRACING_PREWARM_INSTANCES[] = { 1, 64, 4096 };
const int RAY_TRACING_PREWARM_TRIANGLE_RUNGS =
	sizeof( RAY_TRACING_PREWARM_TRIANGLES ) / sizeof( RAY_TRACING_PREWARM_TRIANGLES[ 0 ] );
const int RAY_TRACING_PREWARM_INSTANCE_RUNGS =
	sizeof( RAY_TRACING_PREWARM_INSTANCES ) / sizeof( RAY_TRACING_PREWARM_INSTANCES[ 0 ] );

// What a driver may compile a BVH build against: every field of the build and of its
// geometry except addresses and counts, plus the build's size bucket (PrewarmSizeBucket).
// The prewarm records the key of each build it makes, and NoteBuild looks up the key of
// each build made in play, so a play build that differs from every prewarmed one in any of
// these (a changed flag, a new geometry format, a build bypassing DescribeBuild, a size past
// the ladder) is reported.
struct sdRayTracingBuildKey {
	VkAccelerationStructureTypeKHR			type;
	VkBuildAccelerationStructureFlagsKHR	flags;
	VkBuildAccelerationStructureModeKHR		mode;
	unsigned int							geometryCount;
	VkGeometryTypeKHR						geometryType;	// of the first geometry
	VkGeometryFlagsKHR						geometryFlags;
	VkFormat								vertexFormat;	// triangles
	VkDeviceSize							vertexStride;
	VkIndexType								indexType;
	bool									transform;		// triangles: transformData set
	VkBool32								arrayOfPointers;	// instances
	int										sizeBucket;
};

const int MAX_RAY_TRACING_BUILD_KEYS = 64;

idCVar r_rayTracing(
	"r_rayTracing",
	RAY_TRACING_DEFAULT,
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_BOOL | CVAR_LATCH | CVAR_NOCHEAT,
	"use x64 Vulkan ray-query lighting and shadows; a renderer restart is required"
);

idCVar r_rayTracingPrewarmBuilds(
	"r_rayTracingPrewarmBuilds",
	"1",
	CVAR_RENDERER | CVAR_BOOL | CVAR_NOCHEAT,
	"at ray-tracing init, build every acceleration-structure configuration once, so a driver that compiles its "
	"build shaders on first use does it then and not in play (0 = off, the rt-build-prewarm test's control); "
	"read at init"
);

idCVar r_rayTracingShadowStrength(
	"r_rayTracingShadowStrength",
	"0.82",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_FLOAT | CVAR_NOCHEAT,
	"strength of ray-traced sun shadows",
	0.0f, 1.0f
);

idCVar r_rayTracingAmbient(
	"r_rayTracingAmbient",
	"0.34",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_FLOAT | CVAR_NOCHEAT,
	"ambient term used by the ray-traced lighting pass",
	0.0f, 2.0f
);

idCVar r_rayTracingWaterReflections(
	"r_rayTracingWaterReflections",
	"1",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_BOOL | CVAR_NOCHEAT,
	"enable ray-query reflections and animated ripple normals on water"
);

idCVar r_rayTracingWaterReflectionStrength(
	"r_rayTracingWaterReflectionStrength",
	"0.72",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_FLOAT | CVAR_NOCHEAT,
	"strength of ray-traced water reflections",
	0.0f, 1.5f
);

idCVar r_rayTracingWaterRippleStrength(
	"r_rayTracingWaterRippleStrength",
	"0.18",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_FLOAT | CVAR_NOCHEAT,
	"world-space ripple normal strength used by ray-traced water",
	0.0f, 0.5f
);

bool stencilFallbackSelected = false;
idStr stencilFallbackReason;

struct sdRayTracingBuffer {
	VkBuffer		buffer;
	VkDeviceMemory	memory;
	VkDeviceSize	capacity;
	void*			mapped;

	sdRayTracingBuffer() :
		buffer( VK_NULL_HANDLE ),
		memory( VK_NULL_HANDLE ),
		capacity( 0 ),
		mapped( NULL ) {
	}
};

struct sdRayTracingAccelerationStructure {
	VkAccelerationStructureKHR handle;
	sdRayTracingBuffer			storage;
	VkDeviceSize				capacity;

	sdRayTracingAccelerationStructure() :
		handle( VK_NULL_HANDLE ),
		capacity( 0 ) {
	}
};

struct sdRayTracingFrame {
	sdRayTracingBuffer instances;
	sdRayTracingBuffer scratch;
	sdRayTracingBuffer viewLighting;
	sdRayTracingAccelerationStructure tlas;
	VkDescriptorSet descriptorSet;
	unsigned int viewCount;
	bool usedAccelerationStructures;

	sdRayTracingFrame() :
		descriptorSet( VK_NULL_HANDLE ),
		viewCount( 0 ),
		usedAccelerationStructures( false ) {
	}
};

struct sdRayTracingBlasCacheEntry {
	const void*		geometryOwner;
	int				surfaceId;
	unsigned int	frameSlot;
	bool			deforming;
	bool			built;
	VkBuffer		vertexBuffer;
	VkDeviceSize	vertexOffset;
	unsigned int	vertexCount;
	VkBuffer		indexBuffer;
	VkDeviceSize	indexOffset;
	unsigned int	indexCount;
	unsigned long long buildSerial;
	sdRayTracingAccelerationStructure blas;

	sdRayTracingBlasCacheEntry() :
		geometryOwner( NULL ),
		surfaceId( 0 ),
		frameSlot( 0 ),
		deforming( false ),
		built( false ),
		vertexBuffer( VK_NULL_HANDLE ),
		vertexOffset( 0 ),
		vertexCount( 0 ),
		indexBuffer( VK_NULL_HANDLE ),
		indexOffset( 0 ),
		indexCount( 0 ),
		buildSerial( 0 ) {
	}
};

struct sdRayTracingViewData {
	float inverseViewProjection[ 16 ];
	float viewProjection[ 16 ];
	float viewOrigin[ 4 ];
	float viewport[ 4 ];
	float sunDirectionAmbient[ 4 ];
	float sunColorShadow[ 4 ];
	float waterParameters[ 4 ];
	unsigned int lightCounts[ 4 ];
	float fogColor[ 4 ];	// the atmosphere fog (#233): rgb, 1 when the view fogs
	float fogDepths[ 4 ];	// start, end, 1 / (end - start), -start / (end - start)
};

struct sdRayTracingLightData {
	float originRadius[ 4 ];
	float colorShadow[ 4 ];
};

struct sdRayTracingState {
	VkPhysicalDevice physicalDevice;
	VkDevice device;
	VkPhysicalDeviceMemoryProperties memoryProperties;
	VkFormat colorFormat;
	VkFormat depthFormat;
	unsigned int frameCount;

	VkDescriptorSetLayout descriptorSetLayout;
	VkDescriptorPool descriptorPool;
	VkPipelineLayout pipelineLayout;
	VkPipeline lightingPipeline;
	VkSampler depthSampler;
	sdRayTracingFrame frames[ MAX_RAY_TRACING_FRAMES ];
	idList< sdRayTracingBlasCacheEntry >* blasCache;
	unsigned long long buildSerial;
	VkQueue queue;					// the prewarm's one submit
	unsigned int queueFamilyIndex;
	// the keys of the builds prewarmed, then of each one first built in play
	sdRayTracingBuildKey buildKeys[ MAX_RAY_TRACING_BUILD_KEYS ];
	int buildKeyCount;

	PFN_vkGetDeviceProcAddr GetDeviceProcAddr;
	PFN_vkCreateBuffer CreateBuffer;
	PFN_vkDestroyBuffer DestroyBuffer;
	PFN_vkGetBufferMemoryRequirements GetBufferMemoryRequirements;
	PFN_vkAllocateMemory AllocateMemory;
	PFN_vkFreeMemory FreeMemory;
	PFN_vkBindBufferMemory BindBufferMemory;
	PFN_vkMapMemory MapMemory;
	PFN_vkUnmapMemory UnmapMemory;
	PFN_vkGetBufferDeviceAddress GetBufferDeviceAddress;
	PFN_vkCreateAccelerationStructureKHR CreateAccelerationStructureKHR;
	PFN_vkDestroyAccelerationStructureKHR DestroyAccelerationStructureKHR;
	PFN_vkGetAccelerationStructureBuildSizesKHR GetAccelerationStructureBuildSizesKHR;
	PFN_vkGetAccelerationStructureDeviceAddressKHR GetAccelerationStructureDeviceAddressKHR;
	PFN_vkCmdBuildAccelerationStructuresKHR CmdBuildAccelerationStructuresKHR;
	PFN_vkCreateDescriptorSetLayout CreateDescriptorSetLayout;
	PFN_vkDestroyDescriptorSetLayout DestroyDescriptorSetLayout;
	PFN_vkCreateDescriptorPool CreateDescriptorPool;
	PFN_vkDestroyDescriptorPool DestroyDescriptorPool;
	PFN_vkAllocateDescriptorSets AllocateDescriptorSets;
	PFN_vkUpdateDescriptorSets UpdateDescriptorSets;
	PFN_vkCreateSampler CreateSampler;
	PFN_vkDestroySampler DestroySampler;
	PFN_vkCreateShaderModule CreateShaderModule;
	PFN_vkDestroyShaderModule DestroyShaderModule;
	PFN_vkCreatePipelineLayout CreatePipelineLayout;
	PFN_vkDestroyPipelineLayout DestroyPipelineLayout;
	PFN_vkCreateComputePipelines CreateComputePipelines;
	PFN_vkDestroyPipeline DestroyPipeline;
	PFN_vkCmdPipelineBarrier2 CmdPipelineBarrier2;
	PFN_vkCmdEndRendering CmdEndRendering;
	PFN_vkCmdBeginRendering CmdBeginRendering;
	PFN_vkCmdBindPipeline CmdBindPipeline;
	PFN_vkCmdBindDescriptorSets CmdBindDescriptorSets;
	PFN_vkCmdPushConstants CmdPushConstants;
	PFN_vkCmdCopyImage CmdCopyImage;
	PFN_vkCmdDispatch CmdDispatch;
	PFN_vkCreateCommandPool CreateCommandPool;
	PFN_vkDestroyCommandPool DestroyCommandPool;
	PFN_vkAllocateCommandBuffers AllocateCommandBuffers;
	PFN_vkBeginCommandBuffer BeginCommandBuffer;
	PFN_vkEndCommandBuffer EndCommandBuffer;
	PFN_vkQueueSubmit QueueSubmit;
	PFN_vkQueueWaitIdle QueueWaitIdle;

	sdRayTracingState() {
		memset( this, 0, sizeof( *this ) );
	}
};

sdRayTracingState* rayTracingState = NULL;

bool HasExtension( const idList< VkExtensionProperties >& extensions,
	const char* name ) {
	for ( int extensionIndex = 0; extensionIndex < extensions.Num();
		++extensionIndex ) {
		if ( idStr::Cmp( extensions[ extensionIndex ].extensionName, name ) == 0 ) {
			return true;
		}
	}
	return false;
}

const char* RayTracingResultName( VkResult result ) {
	switch ( result ) {
		case VK_SUCCESS: return "VK_SUCCESS";
		case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
		case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
		case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
		case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
		case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
		case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
		default: return "unknown VkResult";
	}
}

bool CheckRayTracingResult( VkResult result, const char* operation ) {
	if ( result == VK_SUCCESS ) {
		return true;
	}
	common->Warning( "%s failed: %s (%d)", operation,
		RayTracingResultName( result ), static_cast< int >( result ) );
	return false;
}

template< typename T >
bool LoadRayTracingFunction( sdRayTracingState& state, const char* name,
	T& function ) {
	function = reinterpret_cast< T >( state.GetDeviceProcAddr( state.device, name ) );
	if ( function == NULL ) {
		common->Warning( "Ray tracing requires missing Vulkan function %s", name );
		return false;
	}
	return true;
}

bool LoadRayTracingFunctions( sdRayTracingState& state ) {
	bool loaded = true;
#define LOAD_RT_FUNCTION( name ) loaded = LoadRayTracingFunction( state, "vk" #name, state.name ) && loaded
	LOAD_RT_FUNCTION( CreateBuffer );
	LOAD_RT_FUNCTION( DestroyBuffer );
	LOAD_RT_FUNCTION( GetBufferMemoryRequirements );
	LOAD_RT_FUNCTION( AllocateMemory );
	LOAD_RT_FUNCTION( FreeMemory );
	LOAD_RT_FUNCTION( BindBufferMemory );
	LOAD_RT_FUNCTION( MapMemory );
	LOAD_RT_FUNCTION( UnmapMemory );
	LOAD_RT_FUNCTION( GetBufferDeviceAddress );
	LOAD_RT_FUNCTION( CreateAccelerationStructureKHR );
	LOAD_RT_FUNCTION( DestroyAccelerationStructureKHR );
	LOAD_RT_FUNCTION( GetAccelerationStructureBuildSizesKHR );
	LOAD_RT_FUNCTION( GetAccelerationStructureDeviceAddressKHR );
	LOAD_RT_FUNCTION( CmdBuildAccelerationStructuresKHR );
	LOAD_RT_FUNCTION( CreateDescriptorSetLayout );
	LOAD_RT_FUNCTION( DestroyDescriptorSetLayout );
	LOAD_RT_FUNCTION( CreateDescriptorPool );
	LOAD_RT_FUNCTION( DestroyDescriptorPool );
	LOAD_RT_FUNCTION( AllocateDescriptorSets );
	LOAD_RT_FUNCTION( UpdateDescriptorSets );
	LOAD_RT_FUNCTION( CreateSampler );
	LOAD_RT_FUNCTION( DestroySampler );
	LOAD_RT_FUNCTION( CreateShaderModule );
	LOAD_RT_FUNCTION( DestroyShaderModule );
	LOAD_RT_FUNCTION( CreatePipelineLayout );
	LOAD_RT_FUNCTION( DestroyPipelineLayout );
	LOAD_RT_FUNCTION( CreateComputePipelines );
	LOAD_RT_FUNCTION( DestroyPipeline );
	LOAD_RT_FUNCTION( CmdPipelineBarrier2 );
	LOAD_RT_FUNCTION( CmdEndRendering );
	LOAD_RT_FUNCTION( CmdBeginRendering );
	LOAD_RT_FUNCTION( CmdBindPipeline );
	LOAD_RT_FUNCTION( CmdBindDescriptorSets );
	LOAD_RT_FUNCTION( CmdPushConstants );
	LOAD_RT_FUNCTION( CmdCopyImage );
	LOAD_RT_FUNCTION( CmdDispatch );
	LOAD_RT_FUNCTION( CreateCommandPool );
	LOAD_RT_FUNCTION( DestroyCommandPool );
	LOAD_RT_FUNCTION( AllocateCommandBuffers );
	LOAD_RT_FUNCTION( BeginCommandBuffer );
	LOAD_RT_FUNCTION( EndCommandBuffer );
	LOAD_RT_FUNCTION( QueueSubmit );
	LOAD_RT_FUNCTION( QueueWaitIdle );
#undef LOAD_RT_FUNCTION
	if ( loaded ) {
		R_VkTrackMemoryFunctions( state.AllocateMemory, state.FreeMemory );
	}
	return loaded;
}

bool FindMemoryType( const sdRayTracingState& state, unsigned int typeBits,
	VkMemoryPropertyFlags required, unsigned int& typeIndex ) {
	for ( unsigned int index = 0;
		index < state.memoryProperties.memoryTypeCount; ++index ) {
		if ( ( typeBits & ( 1u << index ) ) != 0 &&
			( state.memoryProperties.memoryTypes[ index ].propertyFlags & required ) == required ) {
			typeIndex = index;
			return true;
		}
	}
	return false;
}

void DestroyBuffer( sdRayTracingState& state, sdRayTracingBuffer& buffer ) {
	if ( buffer.mapped != NULL ) {
		state.UnmapMemory( state.device, buffer.memory );
	}
	if ( buffer.buffer != VK_NULL_HANDLE ) {
		state.DestroyBuffer( state.device, buffer.buffer, NULL );
	}
	if ( buffer.memory != VK_NULL_HANDLE ) {
		state.FreeMemory( state.device, buffer.memory, NULL );
	}
	buffer = sdRayTracingBuffer();
}

bool CreateBuffer( sdRayTracingState& state, VkDeviceSize size,
	VkBufferUsageFlags usage, VkMemoryPropertyFlags memoryFlags,
	bool map, sdRayTracingBuffer& buffer ) {
	DestroyBuffer( state, buffer );
	size = Max< VkDeviceSize >( size, 256 );
	VkBufferCreateInfo bufferInfo;
	memset( &bufferInfo, 0, sizeof( bufferInfo ) );
	bufferInfo.sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO;
	bufferInfo.size = size;
	bufferInfo.usage = usage;
	bufferInfo.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
	if ( !CheckRayTracingResult( state.CreateBuffer( state.device, &bufferInfo,
		NULL, &buffer.buffer ), "vkCreateBuffer(ray tracing)" ) ) {
		return false;
	}
	VkMemoryRequirements requirements;
	state.GetBufferMemoryRequirements( state.device, buffer.buffer, &requirements );
	unsigned int memoryType = UINT_MAX;
	if ( !FindMemoryType( state, requirements.memoryTypeBits, memoryFlags,
		memoryType ) ) {
		common->Warning( "No Vulkan memory type supports ray-tracing buffer flags 0x%x",
			static_cast< unsigned int >( memoryFlags ) );
		DestroyBuffer( state, buffer );
		return false;
	}
	VkMemoryAllocateFlagsInfo flagsInfo;
	memset( &flagsInfo, 0, sizeof( flagsInfo ) );
	flagsInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO;
	flagsInfo.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
	VkMemoryAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
	allocateInfo.pNext = &flagsInfo;
	allocateInfo.allocationSize = requirements.size;
	allocateInfo.memoryTypeIndex = memoryType;
	if ( !CheckRayTracingResult( state.AllocateMemory( state.device,
		&allocateInfo, NULL, &buffer.memory ), "vkAllocateMemory(ray tracing)" ) ||
		!CheckRayTracingResult( state.BindBufferMemory( state.device,
			buffer.buffer, buffer.memory, 0 ), "vkBindBufferMemory(ray tracing)" ) ) {
		DestroyBuffer( state, buffer );
		return false;
	}
	buffer.capacity = size;
	if ( map && !CheckRayTracingResult( state.MapMemory( state.device,
		buffer.memory, 0, size, 0, &buffer.mapped ),
		"vkMapMemory(ray tracing)" ) ) {
		DestroyBuffer( state, buffer );
		return false;
	}
	return true;
}

VkDeviceAddress BufferAddress( const sdRayTracingState& state,
	VkBuffer buffer ) {
	VkBufferDeviceAddressInfo addressInfo;
	memset( &addressInfo, 0, sizeof( addressInfo ) );
	addressInfo.sType = VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO;
	addressInfo.buffer = buffer;
	return state.GetBufferDeviceAddress( state.device, &addressInfo );
}

void DestroyAccelerationStructure( sdRayTracingState& state,
	sdRayTracingAccelerationStructure& accelerationStructure ) {
	if ( accelerationStructure.handle != VK_NULL_HANDLE ) {
		state.DestroyAccelerationStructureKHR( state.device,
			accelerationStructure.handle, NULL );
	}
	DestroyBuffer( state, accelerationStructure.storage );
	accelerationStructure = sdRayTracingAccelerationStructure();
}

bool EnsureAccelerationStructure( sdRayTracingState& state,
	sdRayTracingAccelerationStructure& accelerationStructure,
	VkAccelerationStructureTypeKHR type, VkDeviceSize requiredSize,
	bool mayReallocate ) {
	if ( accelerationStructure.handle != VK_NULL_HANDLE &&
		accelerationStructure.capacity >= requiredSize ) {
		return true;
	}
	if ( !mayReallocate ) {
		return false;
	}
	DestroyAccelerationStructure( state, accelerationStructure );
	if ( !CreateBuffer( state, requiredSize,
		VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR |
		VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, false,
		accelerationStructure.storage ) ) {
		return false;
	}
	VkAccelerationStructureCreateInfoKHR createInfo;
	memset( &createInfo, 0, sizeof( createInfo ) );
	createInfo.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR;
	createInfo.buffer = accelerationStructure.storage.buffer;
	createInfo.size = requiredSize;
	createInfo.type = type;
	if ( !CheckRayTracingResult( state.CreateAccelerationStructureKHR(
		state.device, &createInfo, NULL, &accelerationStructure.handle ),
		"vkCreateAccelerationStructureKHR" ) ) {
		DestroyAccelerationStructure( state, accelerationStructure );
		return false;
	}
	accelerationStructure.capacity = requiredSize;
	return true;
}

bool EnsureBuffer( sdRayTracingState& state, sdRayTracingBuffer& buffer,
	VkDeviceSize requiredSize, VkBufferUsageFlags usage,
	VkMemoryPropertyFlags memoryFlags, bool map, bool mayReallocate ) {
	if ( buffer.buffer != VK_NULL_HANDLE && buffer.capacity >= requiredSize ) {
		return true;
	}
	if ( !mayReallocate ) {
		return false;
	}
	return CreateBuffer( state, requiredSize, usage, memoryFlags, map, buffer );
}

bool InvertMatrix( const float input[ 16 ], float output[ 16 ] ) {
	double augmented[ 4 ][ 8 ];
	for ( int row = 0; row < 4; ++row ) {
		for ( int column = 0; column < 4; ++column ) {
			augmented[ row ][ column ] = input[ column * 4 + row ];
			augmented[ row ][ column + 4 ] = row == column ? 1.0 : 0.0;
		}
	}
	for ( int column = 0; column < 4; ++column ) {
		int pivotRow = column;
		double pivotMagnitude = idMath::Fabs(
			static_cast< float >( augmented[ pivotRow ][ column ] ) );
		for ( int row = column + 1; row < 4; ++row ) {
			const double magnitude = idMath::Fabs(
				static_cast< float >( augmented[ row ][ column ] ) );
			if ( magnitude > pivotMagnitude ) {
				pivotMagnitude = magnitude;
				pivotRow = row;
			}
		}
		if ( pivotMagnitude < 1e-12 ) {
			return false;
		}
		if ( pivotRow != column ) {
			for ( int element = 0; element < 8; ++element ) {
				const double temporary = augmented[ column ][ element ];
				augmented[ column ][ element ] = augmented[ pivotRow ][ element ];
				augmented[ pivotRow ][ element ] = temporary;
			}
		}
		const double inversePivot = 1.0 / augmented[ column ][ column ];
		for ( int element = 0; element < 8; ++element ) {
			augmented[ column ][ element ] *= inversePivot;
		}
		for ( int row = 0; row < 4; ++row ) {
			if ( row == column ) {
				continue;
			}
			const double factor = augmented[ row ][ column ];
			for ( int element = 0; element < 8; ++element ) {
				augmented[ row ][ element ] -= factor * augmented[ column ][ element ];
			}
		}
	}
	for ( int row = 0; row < 4; ++row ) {
		for ( int column = 0; column < 4; ++column ) {
			output[ column * 4 + row ] =
				static_cast< float >( augmented[ row ][ column + 4 ] );
		}
	}
	return true;
}

void BuildVulkanViewProjection( const viewDef_s& view, float output[ 16 ] ) {
	for ( int column = 0; column < 4; ++column ) {
		for ( int row = 0; row < 4; ++row ) {
			output[ column * 4 + row ] =
				view.projectionMatrix[ 0 * 4 + row ] * view.worldSpace.modelViewMatrix[ column * 4 + 0 ] +
				view.projectionMatrix[ 1 * 4 + row ] * view.worldSpace.modelViewMatrix[ column * 4 + 1 ] +
				view.projectionMatrix[ 2 * 4 + row ] * view.worldSpace.modelViewMatrix[ column * 4 + 2 ] +
				view.projectionMatrix[ 3 * 4 + row ] * view.worldSpace.modelViewMatrix[ column * 4 + 3 ];
		}
	}
	for ( int column = 0; column < 4; ++column ) {
		output[ column * 4 + 2 ] = 0.5f *
			( output[ column * 4 + 2 ] + output[ column * 4 + 3 ] );
	}
}

bool ResolveLightColor( const viewLight_s& light, idVec3& color ) {
	if ( light.material == NULL || light.lightRegisters == NULL ) {
		return false;
	}
	for ( int stageIndex = 0; stageIndex < light.material->GetNumStages();
		++stageIndex ) {
		const materialStage_t* stage = light.material->GetStage( stageIndex );
		if ( stage == NULL || stage->depthStage ||
			light.lightRegisters[ stage->conditionRegister ] == 0.0f ) {
			continue;
		}
		const float lightScale = cvarSystem->GetCVarFloat( "r_lightScale" ) *
			light.fadeFraction;
		if ( stage->colorVector != NULL ) {
			// each channel limited to 1 before r_lightScale, as the OpenGL interaction (#212)
			color.Set(
				idMath::ClampFloat( 0.0f, 1.0f, light.lightRegisters[ stage->colorVector->registers[ 0 ] ] ) * lightScale,
				idMath::ClampFloat( 0.0f, 1.0f, light.lightRegisters[ stage->colorVector->registers[ 1 ] ] ) * lightScale,
				idMath::ClampFloat( 0.0f, 1.0f, light.lightRegisters[ stage->colorVector->registers[ 2 ] ] ) * lightScale );
		} else {
			color.Set( lightScale, lightScale, lightScale );
		}
		color.x = idMath::ClampFloat( 0.0f, 8.0f, color.x );
		color.y = idMath::ClampFloat( 0.0f, 8.0f, color.y );
		color.z = idMath::ClampFloat( 0.0f, 8.0f, color.z );
		return true;
	}
	return false;
}

bool WriteViewLighting( sdRayTracingFrame& frame,
	const viewDef_s& view, const sdRayTracingViewContext& context,
	VkDeviceSize offset ) {
	byte* destination = static_cast< byte* >( frame.viewLighting.mapped ) + offset;
	sdRayTracingViewData* viewData =
		reinterpret_cast< sdRayTracingViewData* >( destination );
	memset( viewData, 0, sizeof( *viewData ) );
	float viewProjection[ 16 ];
	BuildVulkanViewProjection( view, viewProjection );
	if ( !InvertMatrix( viewProjection, viewData->inverseViewProjection ) ) {
		return false;
	}
	memcpy( viewData->viewProjection, viewProjection, sizeof( viewProjection ) );
	viewData->viewOrigin[ 0 ] = view.renderView.vieworg.x;
	viewData->viewOrigin[ 1 ] = view.renderView.vieworg.y;
	viewData->viewOrigin[ 2 ] = view.renderView.vieworg.z;
	viewData->viewOrigin[ 3 ] = 1.0f;
	viewData->viewport[ 0 ] = static_cast< float >( context.viewport.offset.x );
	viewData->viewport[ 1 ] = static_cast< float >( context.viewport.offset.y );
	viewData->viewport[ 2 ] = static_cast< float >( context.viewport.extent.width );
	viewData->viewport[ 3 ] = static_cast< float >( context.viewport.extent.height );

	idVec3 sunDirection( 0.35f, 0.45f, 0.82f );
	if ( view.atmosphere != NULL ) {
		sunDirection = view.atmosphere->GetSunDirection();
	}
	sunDirection.Normalize();
	viewData->sunDirectionAmbient[ 0 ] = sunDirection.x;
	viewData->sunDirectionAmbient[ 1 ] = sunDirection.y;
	viewData->sunDirectionAmbient[ 2 ] = sunDirection.z;
	viewData->sunDirectionAmbient[ 3 ] = r_rayTracingAmbient.GetFloat();

	idVec3 sunColor( 0.76f, 0.73f, 0.67f );
	if ( view.atmosphereLight != NULL ) {
		idVec3 authoredSunColor;
		if ( ResolveLightColor( *view.atmosphereLight, authoredSunColor ) ) {
			sunColor = authoredSunColor;
		}
	}
	viewData->sunColorShadow[ 0 ] = sunColor.x;
	viewData->sunColorShadow[ 1 ] = sunColor.y;
	viewData->sunColorShadow[ 2 ] = sunColor.z;
	viewData->sunColorShadow[ 3 ] = r_rayTracingShadowStrength.GetFloat();
	viewData->waterParameters[ 0 ] = view.renderView.time * 0.001f;
	viewData->waterParameters[ 1 ] =
		r_rayTracingWaterReflections.GetBool() ?
		r_rayTracingWaterReflectionStrength.GetFloat() : 0.0f;
	viewData->waterParameters[ 2 ] = r_rayTracingWaterRippleStrength.GetFloat();
	viewData->waterParameters[ 3 ] = 100000.0f;
	// the fog retail's lit programs apply (fogDepths as tr_render.cpp sets it);
	// r_AtmospherePostprocess zeroes it there
	if ( view.atmosphere != NULL && !cvarSystem->GetCVarBool( "r_AtmospherePostprocess" ) &&
		view.atmosphere->GetFogEnd() > view.atmosphere->GetFogStart() ) {
		const idVec3& fogColor = view.atmosphere->GetFogColor();
		const float fogStart = view.atmosphere->GetFogStart();
		const float fogEnd = view.atmosphere->GetFogEnd();
		viewData->fogColor[ 0 ] = fogColor.x;
		viewData->fogColor[ 1 ] = fogColor.y;
		viewData->fogColor[ 2 ] = fogColor.z;
		viewData->fogColor[ 3 ] = 1.0f;
		viewData->fogDepths[ 0 ] = fogStart;
		viewData->fogDepths[ 1 ] = fogEnd;
		viewData->fogDepths[ 2 ] = 1.0f / ( fogEnd - fogStart );
		viewData->fogDepths[ 3 ] = -fogStart / ( fogEnd - fogStart );
	}

	sdRayTracingLightData* outputLights =
		reinterpret_cast< sdRayTracingLightData* >(
			destination + sizeof( sdRayTracingViewData ) );
	unsigned int lightCount = 0;
	for ( const viewLight_s* light = view.viewLights;
		light != NULL && lightCount < MAX_RAY_TRACING_LIGHTS;
		light = light->next ) {
		if ( light->culled || light == view.atmosphereLight ||
			light->lightDef == NULL || light->material == NULL ||
			light->material->IsFogLight() || light->material->IsBlendLight() ) {
			continue;
		}
		idVec3 color;
		if ( !ResolveLightColor( *light, color ) || color.LengthSqr() <= 0.000001f ) {
			continue;
		}
		const float radius = Max( Max( idMath::Fabs( light->lightRadius.x ),
			idMath::Fabs( light->lightRadius.y ) ),
			idMath::Fabs( light->lightRadius.z ) );
		if ( radius <= 1.0f ) {
			continue;
		}
		sdRayTracingLightData& output = outputLights[ lightCount++ ];
		output.originRadius[ 0 ] = light->globalLightOrigin.x;
		output.originRadius[ 1 ] = light->globalLightOrigin.y;
		output.originRadius[ 2 ] = light->globalLightOrigin.z;
		output.originRadius[ 3 ] = radius;
		output.colorShadow[ 0 ] = color.x;
		output.colorShadow[ 1 ] = color.y;
		output.colorShadow[ 2 ] = color.z;
		output.colorShadow[ 3 ] = light->lightDef->flags.noShadows ? 0.0f : 1.0f;
	}
	viewData->lightCounts[ 0 ] = lightCount;
	return true;
}

} // namespace

sdRayTracingDeviceConfiguration::sdRayTracingDeviceConfiguration() {
	memset( this, 0, sizeof( *this ) );
	bufferDeviceAddress.sType =
		VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_BUFFER_DEVICE_ADDRESS_FEATURES;
	accelerationStructure.sType =
		VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR;
	rayQuery.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR;
	bufferDeviceAddress.pNext = &accelerationStructure;
	accelerationStructure.pNext = &rayQuery;
}

bool R_RayTracingRequested() {
	return r_rayTracing.GetBool();
}

void R_RayTracingResetBackendSelection() {
	stencilFallbackSelected = false;
	stencilFallbackReason.Clear();
}

void R_RayTracingSelectStencilFallback( const char* reason ) {
	stencilFallbackSelected = true;
	stencilFallbackReason = reason != NULL ? reason : "ray tracing is unavailable";
	common->Printf( "Ray tracing disabled: %s\n", stencilFallbackReason.c_str() );
	common->Printf( "Using the OpenGL stencil-shadow lighting fallback.\n" );
}

bool R_RayTracingUsingStencilFallback() {
	return stencilFallbackSelected;
}

bool R_RayTracingConfigureDevice(
	PFN_vkGetPhysicalDeviceFeatures2 getFeatures,
	PFN_vkEnumerateDeviceExtensionProperties enumerateExtensions,
	VkPhysicalDevice physicalDevice,
	sdRayTracingDeviceConfiguration& configuration,
	idStr& reason ) {
	reason.Clear();
	if ( !R_RayTracingRequested() ) {
		reason = "r_rayTracing is disabled";
		return false;
	}
	unsigned int extensionCount = 0;
	if ( enumerateExtensions( physicalDevice, NULL, &extensionCount, NULL ) !=
		VK_SUCCESS ) {
		reason = "could not enumerate Vulkan device extensions";
		return false;
	}
	idList< VkExtensionProperties > extensions;
	extensions.SetNum( extensionCount );
	if ( extensionCount != 0 && enumerateExtensions( physicalDevice, NULL,
		&extensionCount, extensions.Begin() ) != VK_SUCCESS ) {
		reason = "could not read Vulkan device extensions";
		return false;
	}
	// Ray query is reported first: the other two are its Vulkan dependencies, so
	// a device without ray query would otherwise be told it lacks deferred host
	// operations, which reads as an unrelated capability problem.
	const char* requiredExtensions[ 3 ] = {
		VK_KHR_RAY_QUERY_EXTENSION_NAME,
		VK_KHR_ACCELERATION_STRUCTURE_EXTENSION_NAME,
		VK_KHR_DEFERRED_HOST_OPERATIONS_EXTENSION_NAME
	};
	for ( int extensionIndex = 0; extensionIndex < 3; ++extensionIndex ) {
		if ( !HasExtension( extensions, requiredExtensions[ extensionIndex ] ) ) {
			reason = va( "device does not expose %s",
				requiredExtensions[ extensionIndex ] );
			return false;
		}
		configuration.deviceExtensions[ configuration.deviceExtensionCount++ ] =
			requiredExtensions[ extensionIndex ];
	}

	VkPhysicalDeviceFeatures2 features;
	memset( &features, 0, sizeof( features ) );
	features.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2;
	features.pNext = &configuration.bufferDeviceAddress;
	getFeatures( physicalDevice, &features );
	if ( !configuration.bufferDeviceAddress.bufferDeviceAddress ) {
		reason = "buffer device addresses are not supported";
		return false;
	}
	if ( !configuration.accelerationStructure.accelerationStructure ) {
		reason = "Vulkan acceleration structures are not supported";
		return false;
	}
	if ( !configuration.rayQuery.rayQuery ) {
		reason = "Vulkan ray queries are not supported";
		return false;
	}
	if ( !features.features.shaderStorageImageReadWithoutFormat ||
		!features.features.shaderStorageImageWriteWithoutFormat ) {
		reason = "swapchain storage images without a fixed shader format are not supported";
		return false;
	}
	configuration.coreFeatures.shaderStorageImageReadWithoutFormat = VK_TRUE;
	configuration.coreFeatures.shaderStorageImageWriteWithoutFormat = VK_TRUE;
	configuration.bufferDeviceAddress.bufferDeviceAddressCaptureReplay = VK_FALSE;
	configuration.bufferDeviceAddress.bufferDeviceAddressMultiDevice = VK_FALSE;
	configuration.accelerationStructure.accelerationStructureCaptureReplay = VK_FALSE;
	configuration.accelerationStructure.accelerationStructureIndirectBuild = VK_FALSE;
	configuration.accelerationStructure.accelerationStructureHostCommands = VK_FALSE;
	configuration.accelerationStructure.descriptorBindingAccelerationStructureUpdateAfterBind = VK_FALSE;
	return true;
}

namespace {

bool CreateRayTracingDescriptors( sdRayTracingState& state ) {
	VkDescriptorSetLayoutBinding bindings[ 5 ];
	memset( bindings, 0, sizeof( bindings ) );
	bindings[ 0 ].binding = 0;
	bindings[ 0 ].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
	bindings[ 0 ].descriptorCount = 1;
	bindings[ 0 ].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	bindings[ 1 ].binding = 1;
	bindings[ 1 ].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
	bindings[ 1 ].descriptorCount = 1;
	bindings[ 1 ].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	bindings[ 2 ].binding = 2;
	bindings[ 2 ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	bindings[ 2 ].descriptorCount = 1;
	bindings[ 2 ].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	bindings[ 3 ].binding = 3;
	bindings[ 3 ].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
	bindings[ 3 ].descriptorCount = 1;
	bindings[ 3 ].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	bindings[ 4 ].binding = 4;
	bindings[ 4 ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	bindings[ 4 ].descriptorCount = 1;
	bindings[ 4 ].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	VkDescriptorSetLayoutCreateInfo layoutInfo;
	memset( &layoutInfo, 0, sizeof( layoutInfo ) );
	layoutInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO;
	layoutInfo.bindingCount = 5;
	layoutInfo.pBindings = bindings;
	if ( !CheckRayTracingResult( state.CreateDescriptorSetLayout( state.device,
		&layoutInfo, NULL, &state.descriptorSetLayout ),
		"vkCreateDescriptorSetLayout(ray tracing)" ) ) {
		return false;
	}

	VkDescriptorPoolSize poolSizes[ 4 ];
	memset( poolSizes, 0, sizeof( poolSizes ) );
	poolSizes[ 0 ].type = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
	poolSizes[ 0 ].descriptorCount = state.frameCount;
	poolSizes[ 1 ].type = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
	poolSizes[ 1 ].descriptorCount = state.frameCount;
	poolSizes[ 2 ].type = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	poolSizes[ 2 ].descriptorCount = state.frameCount * 2;
	poolSizes[ 3 ].type = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
	poolSizes[ 3 ].descriptorCount = state.frameCount;
	VkDescriptorPoolCreateInfo poolInfo;
	memset( &poolInfo, 0, sizeof( poolInfo ) );
	poolInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO;
	poolInfo.maxSets = state.frameCount;
	poolInfo.poolSizeCount = 4;
	poolInfo.pPoolSizes = poolSizes;
	if ( !CheckRayTracingResult( state.CreateDescriptorPool( state.device,
		&poolInfo, NULL, &state.descriptorPool ),
		"vkCreateDescriptorPool(ray tracing)" ) ) {
		return false;
	}

	idList< VkDescriptorSetLayout > layouts;
	layouts.SetNum( state.frameCount );
	for ( unsigned int frameIndex = 0; frameIndex < state.frameCount;
		++frameIndex ) {
		layouts[ frameIndex ] = state.descriptorSetLayout;
	}
	idList< VkDescriptorSet > descriptorSets;
	descriptorSets.SetNum( state.frameCount );
	VkDescriptorSetAllocateInfo allocateInfo;
	memset( &allocateInfo, 0, sizeof( allocateInfo ) );
	allocateInfo.sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO;
	allocateInfo.descriptorPool = state.descriptorPool;
	allocateInfo.descriptorSetCount = state.frameCount;
	allocateInfo.pSetLayouts = layouts.Begin();
	if ( !CheckRayTracingResult( state.AllocateDescriptorSets( state.device,
		&allocateInfo, descriptorSets.Begin() ),
		"vkAllocateDescriptorSets(ray tracing)" ) ) {
		return false;
	}
	for ( unsigned int frameIndex = 0; frameIndex < state.frameCount;
		++frameIndex ) {
		state.frames[ frameIndex ].descriptorSet = descriptorSets[ frameIndex ];
	}
	return true;
}

bool CompileRayTracingShader( sdRayTracingState& state,
	VkShaderModule& shaderModule ) {
	shaderModule = VK_NULL_HANDLE;
	const char* shaderName = "vkprogs/raytracing/lighting.comp";
	void* sourceBuffer = NULL;
	const int sourceLength = fileSystem->ReadFile( shaderName, &sourceBuffer,
		NULL, false );
	if ( sourceLength <= 0 || sourceBuffer == NULL ) {
		common->Warning( "Could not read %s", shaderName );
		return false;
	}
	sdSpirvCompileResult compiled;
	const bool compileSucceeded = R_CompileVulkanGLSL( shaderName,
		reinterpret_cast< const char* >( sourceBuffer ), sourceLength,
		SPIRV_SHADER_STAGE_COMPUTE, "ray-query-lighting-water-v2", false, compiled );
	fileSystem->FreeFile( sourceBuffer );
	if ( !compileSucceeded ) {
		common->Warning( "Ray-tracing shader compilation failed:\n%s",
			compiled.diagnostics.c_str() );
		return false;
	}
	VkShaderModuleCreateInfo moduleInfo;
	memset( &moduleInfo, 0, sizeof( moduleInfo ) );
	moduleInfo.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
	moduleInfo.codeSize = compiled.words.Num() * sizeof( unsigned int );
	moduleInfo.pCode = compiled.words.Begin();
	return CheckRayTracingResult( state.CreateShaderModule( state.device,
		&moduleInfo, NULL, &shaderModule ),
		"vkCreateShaderModule(ray tracing)" );
}

bool CreateRayTracingPipeline( sdRayTracingState& state ) {
	VkPushConstantRange pushConstantRange;
	memset( &pushConstantRange, 0, sizeof( pushConstantRange ) );
	pushConstantRange.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
	pushConstantRange.size = sizeof( unsigned int );
	VkPipelineLayoutCreateInfo layoutInfo;
	memset( &layoutInfo, 0, sizeof( layoutInfo ) );
	layoutInfo.sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO;
	layoutInfo.setLayoutCount = 1;
	layoutInfo.pSetLayouts = &state.descriptorSetLayout;
	layoutInfo.pushConstantRangeCount = 1;
	layoutInfo.pPushConstantRanges = &pushConstantRange;
	if ( !CheckRayTracingResult( state.CreatePipelineLayout( state.device,
		&layoutInfo, NULL, &state.pipelineLayout ),
		"vkCreatePipelineLayout(ray tracing)" ) ) {
		return false;
	}
	VkShaderModule shaderModule = VK_NULL_HANDLE;
	if ( !CompileRayTracingShader( state, shaderModule ) ) {
		return false;
	}
	VkPipelineShaderStageCreateInfo shaderStage;
	memset( &shaderStage, 0, sizeof( shaderStage ) );
	shaderStage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
	shaderStage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
	shaderStage.module = shaderModule;
	shaderStage.pName = "main";
	VkComputePipelineCreateInfo pipelineInfo;
	memset( &pipelineInfo, 0, sizeof( pipelineInfo ) );
	pipelineInfo.sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO;
	pipelineInfo.stage = shaderStage;
	pipelineInfo.layout = state.pipelineLayout;
	const bool created = CheckRayTracingResult( state.CreateComputePipelines(
		state.device, VK_NULL_HANDLE, 1, &pipelineInfo, NULL,
		&state.lightingPipeline ), "vkCreateComputePipelines(ray tracing)" );
	state.DestroyShaderModule( state.device, shaderModule, NULL );
	return created;
}

bool CreateRayTracingFrameBuffers( sdRayTracingState& state ) {
	const VkBufferUsageFlags hostAddressUsage =
		VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT |
		VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR;
	for ( unsigned int frameIndex = 0; frameIndex < state.frameCount;
		++frameIndex ) {
		sdRayTracingFrame& frame = state.frames[ frameIndex ];
		if ( !CreateBuffer( state, sizeof( VkAccelerationStructureInstanceKHR ),
			hostAddressUsage,
			VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
			VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
			true, frame.instances ) ||
			!CreateBuffer( state,
				RAY_TRACING_VIEW_STRIDE * MAX_RAY_TRACING_VIEWS,
				VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
				VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
				VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
				VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
				true, frame.viewLighting ) ) {
			return false;
		}
	}
	return true;
}

void DestroyRayTracingState( sdRayTracingState& state ) {
	if ( state.device == VK_NULL_HANDLE ) {
		return;
	}
	for ( unsigned int frameIndex = 0; frameIndex < state.frameCount;
		++frameIndex ) {
		sdRayTracingFrame& frame = state.frames[ frameIndex ];
		DestroyAccelerationStructure( state, frame.tlas );
		DestroyBuffer( state, frame.instances );
		DestroyBuffer( state, frame.scratch );
		DestroyBuffer( state, frame.viewLighting );
	}
	if ( state.blasCache != NULL ) {
		for ( int cacheIndex = 0; cacheIndex < state.blasCache->Num();
			++cacheIndex ) {
			DestroyAccelerationStructure( state,
				( *state.blasCache )[ cacheIndex ].blas );
		}
		delete state.blasCache;
		state.blasCache = NULL;
	}
	if ( state.lightingPipeline != VK_NULL_HANDLE ) {
		state.DestroyPipeline( state.device, state.lightingPipeline, NULL );
	}
	if ( state.pipelineLayout != VK_NULL_HANDLE ) {
		state.DestroyPipelineLayout( state.device, state.pipelineLayout, NULL );
	}
	if ( state.depthSampler != VK_NULL_HANDLE ) {
		state.DestroySampler( state.device, state.depthSampler, NULL );
	}
	if ( state.descriptorPool != VK_NULL_HANDLE ) {
		state.DestroyDescriptorPool( state.device, state.descriptorPool, NULL );
	}
	if ( state.descriptorSetLayout != VK_NULL_HANDLE ) {
		state.DestroyDescriptorSetLayout( state.device,
			state.descriptorSetLayout, NULL );
	}
}

bool CreateDepthSampler( sdRayTracingState& state ) {
	VkSamplerCreateInfo samplerInfo;
	memset( &samplerInfo, 0, sizeof( samplerInfo ) );
	samplerInfo.sType = VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO;
	samplerInfo.magFilter = VK_FILTER_NEAREST;
	samplerInfo.minFilter = VK_FILTER_NEAREST;
	samplerInfo.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
	samplerInfo.addressModeU = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeV = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
	samplerInfo.maxLod = 0.0f;
	return CheckRayTracingResult( state.CreateSampler( state.device,
		&samplerInfo, NULL, &state.depthSampler ),
		"vkCreateSampler(ray-tracing depth)" );
}

bool PrewarmAccelerationStructureBuilds( sdRayTracingState& state );

} // namespace

bool R_RayTracingInit( const sdRayTracingVulkanContext& context ) {
	R_RayTracingShutdown();
	if ( !R_RayTracingRequested() || context.device == VK_NULL_HANDLE ||
		context.physicalDevice == VK_NULL_HANDLE ||
		context.GetDeviceProcAddr == NULL || context.frameCount == 0 ||
		context.frameCount > MAX_RAY_TRACING_FRAMES ) {
		return false;
	}
	rayTracingState = new sdRayTracingState;
	sdRayTracingState& state = *rayTracingState;
	state.blasCache = new idList< sdRayTracingBlasCacheEntry >;
	state.blasCache->SetGranularity( 256 );
	state.physicalDevice = context.physicalDevice;
	state.device = context.device;
	state.memoryProperties = context.memoryProperties;
	state.colorFormat = context.colorFormat;
	state.depthFormat = context.depthFormat;
	state.frameCount = context.frameCount;
	state.GetDeviceProcAddr = context.GetDeviceProcAddr;
	state.queue = context.queue;
	state.queueFamilyIndex = context.queueFamilyIndex;
	if ( !LoadRayTracingFunctions( state ) ||
		!CreateRayTracingDescriptors( state ) ||
		!CreateDepthSampler( state ) ||
		!CreateRayTracingPipeline( state ) ||
		!CreateRayTracingFrameBuffers( state ) ) {
		R_RayTracingShutdown();
		return false;
	}
	common->Printf( "Ray tracing: Vulkan ray-query lighting active (%u frames)\n",
		state.frameCount );
	// an optimisation only: without it the first build of each configuration in play
	// may stall while the driver compiles (#338), so a failure is reported, not fatal
	if ( r_rayTracingPrewarmBuilds.GetBool() && !PrewarmAccelerationStructureBuilds( state ) ) {
		common->Warning( "Ray tracing: the acceleration-structure build prewarm failed; the first build of "
			"each kind in play may stall" );
	}
	return true;
}

void R_RayTracingShutdown() {
	if ( rayTracingState == NULL ) {
		return;
	}
	DestroyRayTracingState( *rayTracingState );
	delete rayTracingState;
	rayTracingState = NULL;
}

void R_RayTracingPurgeGeometryCache() {
	if ( rayTracingState == NULL || rayTracingState->blasCache == NULL ) {
		return;
	}
	sdRayTracingState& state = *rayTracingState;
	for ( int cacheIndex = 0; cacheIndex < state.blasCache->Num();
		++cacheIndex ) {
		DestroyAccelerationStructure( state,
			( *state.blasCache )[ cacheIndex ].blas );
	}
	state.blasCache->Clear();
	for ( unsigned int frameIndex = 0; frameIndex < state.frameCount;
		++frameIndex ) {
		state.frames[ frameIndex ].usedAccelerationStructures = false;
	}
}

bool R_RayTracingIsInitialized() {
	return rayTracingState != NULL &&
		rayTracingState->lightingPipeline != VK_NULL_HANDLE;
}

void R_RayTracingBeginFrame( unsigned int frameIndex ) {
	if ( rayTracingState == NULL || frameIndex >= rayTracingState->frameCount ) {
		return;
	}
	sdRayTracingFrame& frame = rayTracingState->frames[ frameIndex ];
	frame.viewCount = 0;
	frame.usedAccelerationStructures = false;
	rayTracingState->buildSerial++;
	if ( rayTracingState->buildSerial == 0 ) {
		rayTracingState->buildSerial = 1;
	}
}

namespace {

void AccelerationStructureBarrier( sdRayTracingState& state,
	VkCommandBuffer commandBuffer, VkPipelineStageFlags2 sourceStage,
	VkAccessFlags2 sourceAccess, VkPipelineStageFlags2 destinationStage,
	VkAccessFlags2 destinationAccess ) {
	VkMemoryBarrier2 barrier;
	memset( &barrier, 0, sizeof( barrier ) );
	barrier.sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER_2;
	barrier.srcStageMask = sourceStage;
	barrier.srcAccessMask = sourceAccess;
	barrier.dstStageMask = destinationStage;
	barrier.dstAccessMask = destinationAccess;
	VkDependencyInfo dependency;
	memset( &dependency, 0, sizeof( dependency ) );
	dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependency.memoryBarrierCount = 1;
	dependency.pMemoryBarriers = &barrier;
	state.CmdPipelineBarrier2( commandBuffer, &dependency );
}

struct sdRayTracingInstanceBuildInput {
	const sdRayTracingGeometry* geometry;
	int cacheIndex;
	bool water;
	unsigned int waterIndex;
};

bool BlasGeometryMatches( const sdRayTracingBlasCacheEntry& entry,
	const sdRayTracingGeometry& geometry ) {
	return entry.vertexBuffer == geometry.vertexBuffer &&
		entry.vertexOffset == geometry.vertexOffset &&
		entry.vertexCount == geometry.vertexCount &&
		entry.indexBuffer == geometry.indexBuffer &&
		entry.indexOffset == geometry.indexOffset &&
		entry.indexCount == geometry.indexCount;
}

void SetBlasGeometry( sdRayTracingBlasCacheEntry& entry,
	const sdRayTracingGeometry& geometry ) {
	entry.vertexBuffer = geometry.vertexBuffer;
	entry.vertexOffset = geometry.vertexOffset;
	entry.vertexCount = geometry.vertexCount;
	entry.indexBuffer = geometry.indexBuffer;
	entry.indexOffset = geometry.indexOffset;
	entry.indexCount = geometry.indexCount;
}

int AcquireBlasCacheEntry( sdRayTracingState& state,
	const sdRayTracingGeometry& geometry, unsigned int frameIndex ) {
	idList< sdRayTracingBlasCacheEntry >& cache = *state.blasCache;
	int reusableEntry = -1;
	for ( int cacheIndex = 0; cacheIndex < cache.Num(); ++cacheIndex ) {
		sdRayTracingBlasCacheEntry& entry = cache[ cacheIndex ];
		if ( entry.geometryOwner != geometry.geometryOwner ||
			entry.surfaceId != geometry.surfaceId ||
			entry.deforming != geometry.deforming ) {
			continue;
		}
		if ( !geometry.deforming ) {
			if ( BlasGeometryMatches( entry, geometry ) ) {
				return cacheIndex;
			}
			continue;
		}
		if ( entry.frameSlot != frameIndex ) {
			continue;
		}
		if ( BlasGeometryMatches( entry, geometry ) ) {
			return cacheIndex;
		}
		// BeginFrame is called only after this frame slot's fence has completed.
		// It is therefore safe to recycle that slot unless it has already been
		// referenced by another view recorded in the current frame.
		if ( entry.buildSerial != state.buildSerial && reusableEntry < 0 ) {
			reusableEntry = cacheIndex;
		}
	}
	if ( reusableEntry >= 0 ) {
		sdRayTracingBlasCacheEntry& entry = cache[ reusableEntry ];
		SetBlasGeometry( entry, geometry );
		entry.built = false;
		entry.buildSerial = 0;
		return reusableEntry;
	}

	sdRayTracingBlasCacheEntry entry;
	entry.geometryOwner = geometry.geometryOwner;
	entry.surfaceId = geometry.surfaceId;
	entry.frameSlot = geometry.deforming ? frameIndex : 0;
	entry.deforming = geometry.deforming;
	SetBlasGeometry( entry, geometry );
	return cache.Append( entry );
}

// A BLAS's one geometry: the run-time builds and the prewarm describe theirs here, so
// they agree on everything a driver may compile against (vertex format and stride,
// index type, geometry flags).
void DescribeTriangles( VkAccelerationStructureGeometryKHR& geometry,
	VkDeviceAddress vertices, unsigned int vertexCount, VkDeviceAddress indices ) {
	memset( &geometry, 0, sizeof( geometry ) );
	geometry.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR;
	geometry.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
	// Non-opaque candidates let the compute shader reject the raster source
	// triangle without relying on a large self-shadow bias.
	geometry.flags = 0;
	geometry.geometry.triangles.sType =
		VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR;
	geometry.geometry.triangles.vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
	geometry.geometry.triangles.vertexData.deviceAddress = vertices;
	geometry.geometry.triangles.vertexStride = sizeof( idDrawVert );
	geometry.geometry.triangles.maxVertex = vertexCount - 1;
	geometry.geometry.triangles.indexType = sizeof( glIndex_t ) == 2 ?
		VK_INDEX_TYPE_UINT16 : VK_INDEX_TYPE_UINT32;
	geometry.geometry.triangles.indexData.deviceAddress = indices;
}

// A TLAS's one geometry, the instance array.
void DescribeInstances( VkAccelerationStructureGeometryKHR& geometry,
	VkDeviceAddress instances ) {
	memset( &geometry, 0, sizeof( geometry ) );
	geometry.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR;
	geometry.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
	geometry.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
	geometry.geometry.instances.sType =
		VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR;
	geometry.geometry.instances.data.deviceAddress = instances;
}

void DescribeBuild( VkAccelerationStructureBuildGeometryInfoKHR& build,
	rayTracingBuildKind_t kind, const VkAccelerationStructureGeometryKHR* geometry ) {
	const sdRayTracingBuildConfig& config = RAY_TRACING_BUILD_CONFIGS[ kind ];
	memset( &build, 0, sizeof( build ) );
	build.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR;
	build.type = config.type;
	build.flags = config.flags;
	build.mode = config.mode;
	build.geometryCount = 1;
	build.pGeometries = geometry;
}

// The rung of the prewarm ladder a build of this many primitives counts as warmed by: the
// smallest at or above it. A build past the ladder's top gets a bucket nothing prewarms.
int PrewarmSizeBucket( VkAccelerationStructureTypeKHR type, unsigned int primitiveCount ) {
	const bool topLevel = type == VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR;
	const unsigned int* ladder = topLevel ? RAY_TRACING_PREWARM_INSTANCES : RAY_TRACING_PREWARM_TRIANGLES;
	const int rungs = topLevel ? RAY_TRACING_PREWARM_INSTANCE_RUNGS : RAY_TRACING_PREWARM_TRIANGLE_RUNGS;
	for ( int rung = 0; rung < rungs; ++rung ) {
		if ( primitiveCount <= ladder[ rung ] ) {
			return rung;
		}
	}
	return rungs;
}

sdRayTracingBuildKey MakeBuildKey( const VkAccelerationStructureBuildGeometryInfoKHR& build,
	unsigned int primitiveCount ) {
	sdRayTracingBuildKey key;
	memset( &key, 0, sizeof( key ) );
	key.type = build.type;
	key.flags = build.flags;
	key.mode = build.mode;
	key.geometryCount = build.geometryCount;
	const VkAccelerationStructureGeometryKHR* geometry = build.pGeometries != NULL ? build.pGeometries :
		( build.ppGeometries != NULL ? build.ppGeometries[ 0 ] : NULL );
	if ( geometry != NULL && build.geometryCount != 0 ) {
		key.geometryType = geometry->geometryType;
		key.geometryFlags = geometry->flags;
		if ( geometry->geometryType == VK_GEOMETRY_TYPE_TRIANGLES_KHR ) {
			const VkAccelerationStructureGeometryTrianglesDataKHR& triangles = geometry->geometry.triangles;
			key.vertexFormat = triangles.vertexFormat;
			key.vertexStride = triangles.vertexStride;
			key.indexType = triangles.indexType;
			key.transform = triangles.transformData.deviceAddress != 0;
		} else if ( geometry->geometryType == VK_GEOMETRY_TYPE_INSTANCES_KHR ) {
			key.arrayOfPointers = geometry->geometry.instances.arrayOfPointers;
		}
	}
	key.sizeBucket = PrewarmSizeBucket( build.type, primitiveCount );
	return key;
}

bool BuildKeysEqual( const sdRayTracingBuildKey& a, const sdRayTracingBuildKey& b ) {
	return a.type == b.type && a.flags == b.flags && a.mode == b.mode &&
		a.geometryCount == b.geometryCount && a.geometryType == b.geometryType &&
		a.geometryFlags == b.geometryFlags && a.vertexFormat == b.vertexFormat &&
		a.vertexStride == b.vertexStride && a.indexType == b.indexType &&
		a.transform == b.transform && a.arrayOfPointers == b.arrayOfPointers &&
		a.sizeBucket == b.sizeBucket;
}

// Adds a key unless it is known; returns whether it was.
bool KnowBuildKey( sdRayTracingState& state, const sdRayTracingBuildKey& key ) {
	for ( int i = 0; i < state.buildKeyCount; ++i ) {
		if ( BuildKeysEqual( state.buildKeys[ i ], key ) ) {
			return true;
		}
	}
	if ( state.buildKeyCount < MAX_RAY_TRACING_BUILD_KEYS ) {
		state.buildKeys[ state.buildKeyCount++ ] = key;
	}
	return false;
}

// Called for every build recorded in play. A build whose key the prewarm did not build is
// the first-use compile #338 is about: say so once per key, for the rt-build-prewarm test.
void NoteBuild( sdRayTracingState& state, const VkAccelerationStructureBuildGeometryInfoKHR& build,
	unsigned int primitiveCount ) {
	const sdRayTracingBuildKey key = MakeBuildKey( build, primitiveCount );
	if ( KnowBuildKey( state, key ) ) {
		return;
	}
	const char* name = "unlisted";
	for ( int kind = 0; kind < RAY_TRACING_BUILD_KINDS; ++kind ) {
		const sdRayTracingBuildConfig& config = RAY_TRACING_BUILD_CONFIGS[ kind ];
		if ( config.type == key.type && config.flags == key.flags && config.mode == key.mode ) {
			name = config.name;
			break;
		}
	}
	const bool topLevel = key.type == VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR;
	const int rungs = topLevel ? RAY_TRACING_PREWARM_INSTANCE_RUNGS : RAY_TRACING_PREWARM_TRIANGLE_RUNGS;
	common->Printf( "RAY_TRACING_UNWARMED_BUILD: %s build, %u primitives (size bucket %d of %d), was recorded in "
		"play and not prewarmed at init: type %d flags 0x%x mode %d, %u geometries, geometry type %d flags 0x%x, "
		"vertex format %d stride %llu, index type %d, transform %d, array of pointers %d\n",
		name, primitiveCount, key.sizeBucket, rungs, static_cast< int >( key.type ),
		static_cast< unsigned int >( key.flags ), static_cast< int >( key.mode ), key.geometryCount,
		static_cast< int >( key.geometryType ), static_cast< unsigned int >( key.geometryFlags ),
		static_cast< int >( key.vertexFormat ), static_cast< unsigned long long >( key.vertexStride ),
		static_cast< int >( key.indexType ), key.transform ? 1 : 0, static_cast< int >( key.arrayOfPointers ) );
}

void DescribeBlasGeometry( const sdRayTracingState& state,
	const sdRayTracingBlasCacheEntry& entry,
	VkAccelerationStructureGeometryKHR& geometry,
	VkAccelerationStructureBuildRangeInfoKHR& range ) {
	DescribeTriangles( geometry,
		BufferAddress( state, entry.vertexBuffer ) + entry.vertexOffset, entry.vertexCount,
		BufferAddress( state, entry.indexBuffer ) + entry.indexOffset );
	memset( &range, 0, sizeof( range ) );
	range.primitiveCount = entry.indexCount / 3;
}

// Builds every row of RAY_TRACING_BUILD_CONFIGS at each prewarm size, in one command
// buffer on the idle queue, then frees it all (#338).
bool PrewarmAccelerationStructureBuilds( sdRayTracingState& state ) {
	if ( state.queue == VK_NULL_HANDLE ) {
		return false;
	}
	const double startTicks = Sys_GetClockTicks();
	const unsigned int columns = RAY_TRACING_PREWARM_GRID_COLUMNS;
	const unsigned int rows = RAY_TRACING_PREWARM_GRID_ROWS;
	const unsigned int vertexCount = ( columns + 1 ) * ( rows + 1 );
	const unsigned int triangleCount = columns * rows * 2;
	const unsigned int maxInstances = RAY_TRACING_PREWARM_INSTANCES[ RAY_TRACING_PREWARM_INSTANCE_RUNGS - 1 ];
	assert( RAY_TRACING_PREWARM_TRIANGLES[ RAY_TRACING_PREWARM_TRIANGLE_RUNGS - 1 ] <= triangleCount );
	assert( sizeof( glIndex_t ) == 4 || vertexCount <= 65536 );

	const VkBufferUsageFlags inputUsage = VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT |
		VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR;
	const VkMemoryPropertyFlags hostMemory = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
		VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
	sdRayTracingBuffer vertices;
	sdRayTracingBuffer indices;
	sdRayTracingBuffer instances;
	sdRayTracingBuffer scratch;
	if ( !CreateBuffer( state, vertexCount * sizeof( idDrawVert ), inputUsage, hostMemory, true, vertices ) ||
		!CreateBuffer( state, triangleCount * 3 * sizeof( glIndex_t ), inputUsage, hostMemory, true, indices ) ||
		!CreateBuffer( state, maxInstances * sizeof( VkAccelerationStructureInstanceKHR ), inputUsage,
			hostMemory, true, instances ) ) {
		DestroyBuffer( state, vertices );
		DestroyBuffer( state, indices );
		DestroyBuffer( state, instances );
		return false;
	}
	// a flat grid; a prefix of its triangles is itself a valid mesh
	idDrawVert* vertex = static_cast< idDrawVert* >( vertices.mapped );
	memset( vertex, 0, vertexCount * sizeof( idDrawVert ) );
	for ( unsigned int y = 0; y <= rows; ++y ) {
		for ( unsigned int x = 0; x <= columns; ++x ) {
			vertex[ y * ( columns + 1 ) + x ].xyz.Set( static_cast< float >( x ), static_cast< float >( y ), 0.0f );
		}
	}
	glIndex_t* index = static_cast< glIndex_t* >( indices.mapped );
	for ( unsigned int y = 0; y < rows; ++y ) {
		for ( unsigned int x = 0; x < columns; ++x ) {
			const unsigned int corner = y * ( columns + 1 ) + x;
			const unsigned int quad[ 6 ] = { corner, corner + 1, corner + columns + 1,
				corner + 1, corner + columns + 2, corner + columns + 1 };
			for ( int i = 0; i < 6; ++i ) {
				*index++ = static_cast< glIndex_t >( quad[ i ] );
			}
		}
	}

	// one build per (configuration, size); the BLASes first, so the TLASes can use one
	struct prewarmBuild_t {
		rayTracingBuildKind_t kind;
		unsigned int primitiveCount;
	};
	idList< prewarmBuild_t > builds;
	for ( int pass = 0; pass < 2; ++pass ) {
		const bool topLevel = pass == 1;
		const unsigned int* sizes = topLevel ? RAY_TRACING_PREWARM_INSTANCES : RAY_TRACING_PREWARM_TRIANGLES;
		for ( int kind = 0; kind < RAY_TRACING_BUILD_KINDS; ++kind ) {
			if ( ( RAY_TRACING_BUILD_CONFIGS[ kind ].type == VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR ) !=
				topLevel ) {
				continue;
			}
			const int rungs = topLevel ? RAY_TRACING_PREWARM_INSTANCE_RUNGS : RAY_TRACING_PREWARM_TRIANGLE_RUNGS;
			for ( int size = 0; size < rungs; ++size ) {
				const prewarmBuild_t build = { static_cast< rayTracingBuildKind_t >( kind ), sizes[ size ] };
				builds.Append( build );
			}
		}
	}
	idList< VkAccelerationStructureGeometryKHR > geometries;
	idList< VkAccelerationStructureBuildGeometryInfoKHR > buildInfos;
	idList< sdRayTracingAccelerationStructure > structures;
	idList< sdRayTracingBuildKey > keys;	// recorded as the play builds will be (NoteBuild)
	geometries.SetNum( builds.Num() );
	buildInfos.SetNum( builds.Num() );
	structures.SetNum( builds.Num() );
	keys.SetNum( builds.Num() );
	VkDeviceSize scratchSize = 0;
	bool succeeded = true;
	int firstBlas = -1;
	for ( int i = 0; i < builds.Num() && succeeded; ++i ) {
		const sdRayTracingBuildConfig& config = RAY_TRACING_BUILD_CONFIGS[ builds[ i ].kind ];
		if ( config.type == VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR ) {
			DescribeInstances( geometries[ i ], BufferAddress( state, instances.buffer ) );
		} else {
			DescribeTriangles( geometries[ i ], BufferAddress( state, vertices.buffer ), vertexCount,
				BufferAddress( state, indices.buffer ) );
		}
		DescribeBuild( buildInfos[ i ], builds[ i ].kind, &geometries[ i ] );
		keys[ i ] = MakeBuildKey( buildInfos[ i ], builds[ i ].primitiveCount );
		VkAccelerationStructureBuildSizesInfoKHR sizes;
		memset( &sizes, 0, sizeof( sizes ) );
		sizes.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR;
		state.GetAccelerationStructureBuildSizesKHR( state.device,
			VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &buildInfos[ i ],
			&builds[ i ].primitiveCount, &sizes );
		succeeded = EnsureAccelerationStructure( state, structures[ i ], config.type,
			sizes.accelerationStructureSize, true );
		scratchSize = Max( scratchSize, sizes.buildScratchSize );
		if ( firstBlas < 0 && config.type == VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR ) {
			firstBlas = i;
		}
	}
	succeeded = succeeded && firstBlas >= 0 && CreateBuffer( state, scratchSize,
		VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, false, scratch );
	if ( succeeded ) {
		// every instance points at one of the BLASes built first in the same submit
		VkAccelerationStructureDeviceAddressInfoKHR addressInfo;
		memset( &addressInfo, 0, sizeof( addressInfo ) );
		addressInfo.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR;
		addressInfo.accelerationStructure = structures[ firstBlas ].handle;
		const VkDeviceAddress blasAddress = state.GetAccelerationStructureDeviceAddressKHR( state.device,
			&addressInfo );
		VkAccelerationStructureInstanceKHR* instance =
			static_cast< VkAccelerationStructureInstanceKHR* >( instances.mapped );
		memset( instance, 0, maxInstances * sizeof( VkAccelerationStructureInstanceKHR ) );
		for ( unsigned int i = 0; i < maxInstances; ++i ) {
			instance[ i ].transform.matrix[ 0 ][ 0 ] = 1.0f;
			instance[ i ].transform.matrix[ 1 ][ 1 ] = 1.0f;
			instance[ i ].transform.matrix[ 2 ][ 2 ] = 1.0f;
			instance[ i ].transform.matrix[ 2 ][ 3 ] = static_cast< float >( i );
			instance[ i ].mask = RAY_TRACING_SOLID_INSTANCE_MASK;
			instance[ i ].flags = VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
			instance[ i ].accelerationStructureReference = blasAddress;
		}
	}

	VkCommandPool commandPool = VK_NULL_HANDLE;
	VkCommandBuffer commandBuffer = VK_NULL_HANDLE;
	if ( succeeded ) {
		VkCommandPoolCreateInfo poolInfo;
		memset( &poolInfo, 0, sizeof( poolInfo ) );
		poolInfo.sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO;
		poolInfo.flags = VK_COMMAND_POOL_CREATE_TRANSIENT_BIT;
		poolInfo.queueFamilyIndex = state.queueFamilyIndex;
		succeeded = CheckRayTracingResult( state.CreateCommandPool( state.device, &poolInfo, NULL,
			&commandPool ), "vkCreateCommandPool(ray-tracing prewarm)" );
	}
	if ( succeeded ) {
		VkCommandBufferAllocateInfo allocateInfo;
		memset( &allocateInfo, 0, sizeof( allocateInfo ) );
		allocateInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO;
		allocateInfo.commandPool = commandPool;
		allocateInfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
		allocateInfo.commandBufferCount = 1;
		VkCommandBufferBeginInfo beginInfo;
		memset( &beginInfo, 0, sizeof( beginInfo ) );
		beginInfo.sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO;
		beginInfo.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
		succeeded = CheckRayTracingResult( state.AllocateCommandBuffers( state.device, &allocateInfo,
			&commandBuffer ), "vkAllocateCommandBuffers(ray-tracing prewarm)" ) &&
			CheckRayTracingResult( state.BeginCommandBuffer( commandBuffer, &beginInfo ),
				"vkBeginCommandBuffer(ray-tracing prewarm)" );
	}
	if ( succeeded ) {
		const VkDeviceAddress scratchAddress = BufferAddress( state, scratch.buffer );
		for ( int i = 0; i < builds.Num(); ++i ) {
			buildInfos[ i ].dstAccelerationStructure = structures[ i ].handle;
			buildInfos[ i ].scratchData.deviceAddress = scratchAddress;
			VkAccelerationStructureBuildRangeInfoKHR range;
			memset( &range, 0, sizeof( range ) );
			range.primitiveCount = builds[ i ].primitiveCount;
			const VkAccelerationStructureBuildRangeInfoKHR* rangePointer = &range;
			state.CmdBuildAccelerationStructuresKHR( commandBuffer, 1, &buildInfos[ i ], &rangePointer );
			// the builds share the scratch buffer, and the TLASes read the BLAS
			AccelerationStructureBarrier( state, commandBuffer,
				VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
				VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR,
				VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
				VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR |
				VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR );
		}
		VkSubmitInfo submitInfo;
		memset( &submitInfo, 0, sizeof( submitInfo ) );
		submitInfo.sType = VK_STRUCTURE_TYPE_SUBMIT_INFO;
		submitInfo.commandBufferCount = 1;
		submitInfo.pCommandBuffers = &commandBuffer;
		succeeded = CheckRayTracingResult( state.EndCommandBuffer( commandBuffer ),
			"vkEndCommandBuffer(ray-tracing prewarm)" ) &&
			CheckRayTracingResult( state.QueueSubmit( state.queue, 1, &submitInfo, VK_NULL_HANDLE ),
				"vkQueueSubmit(ray-tracing prewarm)" ) &&
			CheckRayTracingResult( state.QueueWaitIdle( state.queue ),
				"vkQueueWaitIdle(ray-tracing prewarm)" );
	}

	if ( commandPool != VK_NULL_HANDLE ) {
		state.DestroyCommandPool( state.device, commandPool, NULL );
	}
	for ( int i = 0; i < structures.Num(); ++i ) {
		DestroyAccelerationStructure( state, structures[ i ] );
	}
	DestroyBuffer( state, scratch );
	DestroyBuffer( state, instances );
	DestroyBuffer( state, indices );
	DestroyBuffer( state, vertices );
	if ( !succeeded ) {
		return false;
	}
	for ( int i = 0; i < keys.Num(); ++i ) {
		KnowBuildKey( state, keys[ i ] );
	}
	common->Printf( "Ray tracing: prewarmed %d acceleration-structure builds (%d configurations) in %.1f ms\n",
		builds.Num(), static_cast< int >( RAY_TRACING_BUILD_KINDS ),
		( Sys_GetClockTicks() - startTicks ) * 1000.0 / Sys_ClockTicksPerSecond() );
	return true;
}

bool BuildAccelerationStructures( sdRayTracingState& state,
	sdRayTracingFrame& frame, unsigned int frameIndex,
	VkCommandBuffer commandBuffer,
	const sdRayTracingGeometry* inputGeometries, int inputGeometryCount,
	const sdRayTracingGeometry* waterGeometries, int waterGeometryCount,
	unsigned int& waterGeometryStart, unsigned int& validWaterGeometryCount ) {
	idList< sdRayTracingInstanceBuildInput > instanceInputs;
	idList< int > pendingCacheEntries;
	instanceInputs.SetGranularity( 256 );
	pendingCacheEntries.SetGranularity( 256 );
	waterGeometryStart = 0;
	validWaterGeometryCount = 0;
	for ( int geometryClass = 0; geometryClass < 2; ++geometryClass ) {
		const sdRayTracingGeometry* classGeometries = geometryClass == 0 ?
			inputGeometries : waterGeometries;
		const int classGeometryCount = geometryClass == 0 ?
			inputGeometryCount : Min( waterGeometryCount,
				static_cast< int >( MAX_RAY_TRACING_WATER_GEOMETRIES ) );
		for ( int geometryIndex = 0; geometryIndex < classGeometryCount;
			++geometryIndex ) {
			const sdRayTracingGeometry& input = classGeometries[ geometryIndex ];
			if ( input.vertexBuffer == VK_NULL_HANDLE ||
				input.indexBuffer == VK_NULL_HANDLE || input.vertexCount < 3 ||
				input.indexCount < 3 ) {
				continue;
			}
			sdRayTracingInstanceBuildInput instanceInput;
			instanceInput.geometry = &input;
			instanceInput.cacheIndex = AcquireBlasCacheEntry( state, input,
				frameIndex );
			instanceInput.water = geometryClass == 1;
			instanceInput.waterIndex = instanceInput.water ?
				validWaterGeometryCount++ : 0;
			instanceInputs.Append( instanceInput );
			sdRayTracingBlasCacheEntry& entry =
				( *state.blasCache )[ instanceInput.cacheIndex ];
			const bool needsBuild = !entry.built ||
				( entry.deforming && entry.buildSerial != state.buildSerial );
			if ( needsBuild && pendingCacheEntries.FindIndex(
				instanceInput.cacheIndex ) < 0 ) {
				pendingCacheEntries.Append( instanceInput.cacheIndex );
			}
		}
	}
	if ( instanceInputs.Num() == 0 ) {
		return false;
	}

	idList< VkAccelerationStructureGeometryKHR > blasGeometries;
	idList< VkAccelerationStructureBuildRangeInfoKHR > blasRanges;
	idList< VkAccelerationStructureBuildGeometryInfoKHR > blasBuilds;
	idList< VkAccelerationStructureBuildSizesInfoKHR > blasSizes;
	blasGeometries.SetNum( pendingCacheEntries.Num() );
	blasRanges.SetNum( pendingCacheEntries.Num() );
	blasBuilds.SetNum( pendingCacheEntries.Num() );
	blasSizes.SetNum( pendingCacheEntries.Num() );
	VkDeviceSize scratchSize = 0;
	for ( int buildIndex = 0; buildIndex < pendingCacheEntries.Num();
		++buildIndex ) {
		sdRayTracingBlasCacheEntry& entry =
			( *state.blasCache )[ pendingCacheEntries[ buildIndex ] ];
		DescribeBlasGeometry( state, entry, blasGeometries[ buildIndex ],
			blasRanges[ buildIndex ] );
		VkAccelerationStructureBuildGeometryInfoKHR& build =
			blasBuilds[ buildIndex ];
		DescribeBuild( build, entry.deforming ? RAY_TRACING_BUILD_DEFORMING_BLAS :
			RAY_TRACING_BUILD_STATIC_BLAS, &blasGeometries[ buildIndex ] );
		VkAccelerationStructureBuildSizesInfoKHR& sizes = blasSizes[ buildIndex ];
		memset( &sizes, 0, sizeof( sizes ) );
		sizes.sType =
			VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR;
		const unsigned int primitiveCount = blasRanges[ buildIndex ].primitiveCount;
		state.GetAccelerationStructureBuildSizesKHR( state.device,
			VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &build,
			&primitiveCount, &sizes );
		if ( !EnsureAccelerationStructure( state, entry.blas,
			VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR,
			sizes.accelerationStructureSize, true ) ) {
			return false;
		}
		scratchSize = Max( scratchSize, sizes.buildScratchSize );
	}

	const bool mayReallocateFrameResources = !frame.usedAccelerationStructures;
	const VkDeviceSize instanceBytes = instanceInputs.Num() *
		sizeof( VkAccelerationStructureInstanceKHR );
	if ( !EnsureBuffer( state, frame.instances, instanceBytes,
		VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT |
		VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR,
		VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
		VK_MEMORY_PROPERTY_HOST_COHERENT_BIT,
		true, mayReallocateFrameResources ) ) {
		return false;
	}
	VkAccelerationStructureInstanceKHR* instances =
		static_cast< VkAccelerationStructureInstanceKHR* >( frame.instances.mapped );
	unsigned int solidInstanceIndex = 0;
	for ( int instanceIndex = 0; instanceIndex < instanceInputs.Num();
		++instanceIndex ) {
		const sdRayTracingInstanceBuildInput& input = instanceInputs[ instanceIndex ];
		const sdRayTracingBlasCacheEntry& entry =
			( *state.blasCache )[ input.cacheIndex ];
		VkAccelerationStructureInstanceKHR& instance = instances[ instanceIndex ];
		memset( &instance, 0, sizeof( instance ) );
		memcpy( instance.transform.matrix, input.geometry->transform,
			sizeof( input.geometry->transform ) );
		instance.instanceCustomIndex = input.water ?
			( RAY_TRACING_WATER_INSTANCE_BIT | input.waterIndex ) :
			solidInstanceIndex++;
		instance.mask = input.water ? RAY_TRACING_WATER_INSTANCE_MASK :
			RAY_TRACING_SOLID_INSTANCE_MASK;
		instance.flags =
			VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
		VkAccelerationStructureDeviceAddressInfoKHR addressInfo;
		memset( &addressInfo, 0, sizeof( addressInfo ) );
		addressInfo.sType =
			VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR;
		addressInfo.accelerationStructure = entry.blas.handle;
		instance.accelerationStructureReference =
			state.GetAccelerationStructureDeviceAddressKHR( state.device,
				&addressInfo );
	}

	VkAccelerationStructureGeometryKHR tlasGeometry;
	DescribeInstances( tlasGeometry, BufferAddress( state, frame.instances.buffer ) );
	VkAccelerationStructureBuildGeometryInfoKHR tlasBuild;
	DescribeBuild( tlasBuild, RAY_TRACING_BUILD_TLAS, &tlasGeometry );
	const unsigned int instanceCount = instanceInputs.Num();
	VkAccelerationStructureBuildSizesInfoKHR tlasSizes;
	memset( &tlasSizes, 0, sizeof( tlasSizes ) );
	tlasSizes.sType =
		VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR;
	state.GetAccelerationStructureBuildSizesKHR( state.device,
		VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &tlasBuild,
		&instanceCount, &tlasSizes );
	if ( !EnsureAccelerationStructure( state, frame.tlas,
		VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR,
		tlasSizes.accelerationStructureSize, mayReallocateFrameResources ) ) {
		return false;
	}
	scratchSize = Max( scratchSize, tlasSizes.buildScratchSize );
	if ( !EnsureBuffer( state, frame.scratch, scratchSize,
		VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
		VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
		VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, false,
		mayReallocateFrameResources ) ) {
		return false;
	}
	const VkDeviceAddress scratchAddress =
		BufferAddress( state, frame.scratch.buffer );
	if ( frame.usedAccelerationStructures ) {
		AccelerationStructureBarrier( state, commandBuffer,
			VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR,
			VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR |
			VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR );
	}
	for ( int buildIndex = 0; buildIndex < pendingCacheEntries.Num();
		++buildIndex ) {
		sdRayTracingBlasCacheEntry& entry =
			( *state.blasCache )[ pendingCacheEntries[ buildIndex ] ];
		VkAccelerationStructureBuildGeometryInfoKHR& build =
			blasBuilds[ buildIndex ];
		build.dstAccelerationStructure = entry.blas.handle;
		build.scratchData.deviceAddress = scratchAddress;
		const VkAccelerationStructureBuildRangeInfoKHR* range =
			&blasRanges[ buildIndex ];
		sdVulkanSlowCallScope slowBlas( "BLAS build" );
		if ( slowBlas.Active() ) {
			slowBlas.detail = va( " %u triangles, %s, index type %d",
				range->primitiveCount, entry.deforming ? "deforming" : "static",
				static_cast< int >( blasGeometries[ buildIndex ].geometry.triangles.indexType ) );
		}
		NoteBuild( state, build, range->primitiveCount );
		state.CmdBuildAccelerationStructuresKHR( commandBuffer, 1, &build,
			&range );
		entry.built = true;
		entry.buildSerial = state.buildSerial;
		// Every BLAS shares this frame slot's scratch buffer.  The barrier both
		// makes the completed BLAS visible and prevents the next build from
		// reusing scratch storage while it is still live.
		AccelerationStructureBarrier( state, commandBuffer,
			VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR,
			VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR |
			VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR );
	}
	if ( pendingCacheEntries.Num() == 0 ) {
		AccelerationStructureBarrier( state, commandBuffer,
			VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR,
			VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
			VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR );
	}
	tlasBuild.dstAccelerationStructure = frame.tlas.handle;
	tlasBuild.scratchData.deviceAddress = scratchAddress;
	VkAccelerationStructureBuildRangeInfoKHR tlasRange;
	memset( &tlasRange, 0, sizeof( tlasRange ) );
	tlasRange.primitiveCount = instanceCount;
	const VkAccelerationStructureBuildRangeInfoKHR* tlasRangePointer = &tlasRange;
	NoteBuild( state, tlasBuild, instanceCount );
	state.CmdBuildAccelerationStructuresKHR( commandBuffer, 1, &tlasBuild,
		&tlasRangePointer );
	frame.usedAccelerationStructures = true;
	return true;
}

void UpdateRayTracingDescriptors( sdRayTracingState& state,
	sdRayTracingFrame& frame, const sdRayTracingViewContext& context ) {
	VkWriteDescriptorSetAccelerationStructureKHR accelerationWrite;
	memset( &accelerationWrite, 0, sizeof( accelerationWrite ) );
	accelerationWrite.sType =
		VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR;
	accelerationWrite.accelerationStructureCount = 1;
	accelerationWrite.pAccelerationStructures = &frame.tlas.handle;
	VkDescriptorImageInfo colorInfo;
	memset( &colorInfo, 0, sizeof( colorInfo ) );
	colorInfo.imageView = context.colorImageView;
	colorInfo.imageLayout = VK_IMAGE_LAYOUT_GENERAL;
	VkDescriptorImageInfo depthInfo;
	memset( &depthInfo, 0, sizeof( depthInfo ) );
	depthInfo.sampler = state.depthSampler;
	depthInfo.imageView = context.depthSampleView != VK_NULL_HANDLE ?
		context.depthSampleView : context.depthImageView;
	depthInfo.imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	VkDescriptorImageInfo reflectionInfo;
	memset( &reflectionInfo, 0, sizeof( reflectionInfo ) );
	reflectionInfo.sampler = context.reflectionSampler != VK_NULL_HANDLE ?
		context.reflectionSampler : state.depthSampler;
	reflectionInfo.imageView = context.reflectionImageView != VK_NULL_HANDLE ?
		context.reflectionImageView : ( context.depthSampleView != VK_NULL_HANDLE ?
			context.depthSampleView : context.depthImageView );
	reflectionInfo.imageLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	VkDescriptorBufferInfo lightingInfo;
	memset( &lightingInfo, 0, sizeof( lightingInfo ) );
	lightingInfo.buffer = frame.viewLighting.buffer;
	lightingInfo.offset = 0;
	lightingInfo.range = RAY_TRACING_VIEW_STRIDE;
	VkWriteDescriptorSet writes[ 5 ];
	memset( writes, 0, sizeof( writes ) );
	for ( int writeIndex = 0; writeIndex < 5; ++writeIndex ) {
		writes[ writeIndex ].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
		writes[ writeIndex ].dstSet = frame.descriptorSet;
		writes[ writeIndex ].dstBinding = writeIndex;
		writes[ writeIndex ].descriptorCount = 1;
	}
	writes[ 0 ].pNext = &accelerationWrite;
	writes[ 0 ].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
	writes[ 1 ].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
	writes[ 1 ].pImageInfo = &colorInfo;
	writes[ 2 ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	writes[ 2 ].pImageInfo = &depthInfo;
	writes[ 3 ].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
	writes[ 3 ].pBufferInfo = &lightingInfo;
	writes[ 4 ].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
	writes[ 4 ].pImageInfo = &reflectionInfo;
	state.UpdateDescriptorSets( state.device, 5, writes, 0, NULL );
}

void TransitionForRayTracing( sdRayTracingState& state,
	const sdRayTracingViewContext& context, bool toRayTracing ) {
	VkImageMemoryBarrier2 barriers[ 2 ];
	memset( barriers, 0, sizeof( barriers ) );
	barriers[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barriers[ 0 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 0 ].image = context.colorImage;
	barriers[ 0 ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	barriers[ 0 ].subresourceRange.levelCount = 1;
	barriers[ 0 ].subresourceRange.layerCount = 1;
	barriers[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	barriers[ 1 ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
	barriers[ 1 ].image = context.depthImage;
	barriers[ 1 ].subresourceRange.aspectMask =
		VK_IMAGE_ASPECT_DEPTH_BIT | VK_IMAGE_ASPECT_STENCIL_BIT;
	barriers[ 1 ].subresourceRange.levelCount = 1;
	barriers[ 1 ].subresourceRange.layerCount = 1;
	if ( toRayTracing ) {
		barriers[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
		barriers[ 0 ].srcAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
		barriers[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
		barriers[ 0 ].dstAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
			VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
		barriers[ 0 ].oldLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
		barriers[ 0 ].newLayout = VK_IMAGE_LAYOUT_GENERAL;
		barriers[ 1 ].srcStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
			VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
		barriers[ 1 ].srcAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
		barriers[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
		barriers[ 1 ].dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		barriers[ 1 ].oldLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
		barriers[ 1 ].newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	} else {
		barriers[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
		barriers[ 0 ].srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
		barriers[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COLOR_ATTACHMENT_OUTPUT_BIT;
		barriers[ 0 ].dstAccessMask = VK_ACCESS_2_COLOR_ATTACHMENT_READ_BIT |
			VK_ACCESS_2_COLOR_ATTACHMENT_WRITE_BIT;
		barriers[ 0 ].oldLayout = VK_IMAGE_LAYOUT_GENERAL;
		barriers[ 0 ].newLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
		barriers[ 1 ].srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
		barriers[ 1 ].srcAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
		barriers[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_EARLY_FRAGMENT_TESTS_BIT |
			VK_PIPELINE_STAGE_2_LATE_FRAGMENT_TESTS_BIT;
		barriers[ 1 ].dstAccessMask = VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_READ_BIT |
			VK_ACCESS_2_DEPTH_STENCIL_ATTACHMENT_WRITE_BIT;
		barriers[ 1 ].oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
		barriers[ 1 ].newLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	}
	VkDependencyInfo dependency;
	memset( &dependency, 0, sizeof( dependency ) );
	dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependency.imageMemoryBarrierCount = 2;
	dependency.pImageMemoryBarriers = barriers;
	state.CmdPipelineBarrier2( context.commandBuffer, &dependency );
}

void CopyLitSceneForReflections( sdRayTracingState& state,
	const sdRayTracingViewContext& context ) {
	VkImageMemoryBarrier2 toTransfer[ 2 ];
	memset( toTransfer, 0, sizeof( toTransfer ) );
	toTransfer[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toTransfer[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
	toTransfer[ 0 ].srcAccessMask = VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
	toTransfer[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	toTransfer[ 0 ].dstAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
	toTransfer[ 0 ].oldLayout = VK_IMAGE_LAYOUT_GENERAL;
	toTransfer[ 0 ].newLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
	toTransfer[ 0 ].image = context.colorImage;
	toTransfer[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	toTransfer[ 1 ].srcStageMask = VK_PIPELINE_STAGE_2_FRAGMENT_SHADER_BIT;
	toTransfer[ 1 ].srcAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	toTransfer[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	toTransfer[ 1 ].dstAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	toTransfer[ 1 ].oldLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	toTransfer[ 1 ].newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	toTransfer[ 1 ].image = context.reflectionImage;
	for ( int barrierIndex = 0; barrierIndex < 2; ++barrierIndex ) {
		toTransfer[ barrierIndex ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer[ barrierIndex ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		toTransfer[ barrierIndex ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		toTransfer[ barrierIndex ].subresourceRange.levelCount = 1;
		toTransfer[ barrierIndex ].subresourceRange.layerCount = 1;
	}
	VkDependencyInfo dependency;
	memset( &dependency, 0, sizeof( dependency ) );
	dependency.sType = VK_STRUCTURE_TYPE_DEPENDENCY_INFO;
	dependency.imageMemoryBarrierCount = 2;
	dependency.pImageMemoryBarriers = toTransfer;
	state.CmdPipelineBarrier2( context.commandBuffer, &dependency );

	VkImageCopy copyRegion;
	memset( &copyRegion, 0, sizeof( copyRegion ) );
	copyRegion.srcSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	copyRegion.srcSubresource.layerCount = 1;
	copyRegion.dstSubresource.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
	copyRegion.dstSubresource.layerCount = 1;
	copyRegion.extent.width = context.framebufferExtent.width;
	copyRegion.extent.height = context.framebufferExtent.height;
	copyRegion.extent.depth = 1;
	state.CmdCopyImage( context.commandBuffer, context.colorImage,
		VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, context.reflectionImage,
		VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copyRegion );

	VkImageMemoryBarrier2 fromTransfer[ 2 ];
	memset( fromTransfer, 0, sizeof( fromTransfer ) );
	fromTransfer[ 0 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	fromTransfer[ 0 ].srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	fromTransfer[ 0 ].srcAccessMask = VK_ACCESS_2_TRANSFER_READ_BIT;
	fromTransfer[ 0 ].dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
	fromTransfer[ 0 ].dstAccessMask = VK_ACCESS_2_SHADER_STORAGE_READ_BIT |
		VK_ACCESS_2_SHADER_STORAGE_WRITE_BIT;
	fromTransfer[ 0 ].oldLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL;
	fromTransfer[ 0 ].newLayout = VK_IMAGE_LAYOUT_GENERAL;
	fromTransfer[ 0 ].image = context.colorImage;
	fromTransfer[ 1 ].sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER_2;
	fromTransfer[ 1 ].srcStageMask = VK_PIPELINE_STAGE_2_TRANSFER_BIT;
	fromTransfer[ 1 ].srcAccessMask = VK_ACCESS_2_TRANSFER_WRITE_BIT;
	fromTransfer[ 1 ].dstStageMask = VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT;
	fromTransfer[ 1 ].dstAccessMask = VK_ACCESS_2_SHADER_SAMPLED_READ_BIT;
	fromTransfer[ 1 ].oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
	fromTransfer[ 1 ].newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
	fromTransfer[ 1 ].image = context.reflectionImage;
	for ( int barrierIndex = 0; barrierIndex < 2; ++barrierIndex ) {
		fromTransfer[ barrierIndex ].srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromTransfer[ barrierIndex ].dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
		fromTransfer[ barrierIndex ].subresourceRange.aspectMask = VK_IMAGE_ASPECT_COLOR_BIT;
		fromTransfer[ barrierIndex ].subresourceRange.levelCount = 1;
		fromTransfer[ barrierIndex ].subresourceRange.layerCount = 1;
	}
	dependency.pImageMemoryBarriers = fromTransfer;
	state.CmdPipelineBarrier2( context.commandBuffer, &dependency );
}

void ResumeDynamicRendering( sdRayTracingState& state,
	const sdRayTracingViewContext& context ) {
	VkRenderingAttachmentInfo colorAttachment;
	memset( &colorAttachment, 0, sizeof( colorAttachment ) );
	colorAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	colorAttachment.imageView = context.colorImageView;
	colorAttachment.imageLayout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
	colorAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	colorAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	// The same depth and stencil attachments as the backend's own rendering
	// instances (VulkanBackend.cpp): every pipeline drawn after this was created
	// with the stencil format, and TransitionForRayTracing returns the image to
	// DEPTH_STENCIL_ATTACHMENT_OPTIMAL.
	VkRenderingAttachmentInfo depthAttachment;
	memset( &depthAttachment, 0, sizeof( depthAttachment ) );
	depthAttachment.sType = VK_STRUCTURE_TYPE_RENDERING_ATTACHMENT_INFO;
	depthAttachment.imageView = context.depthImageView;
	depthAttachment.imageLayout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
	depthAttachment.loadOp = VK_ATTACHMENT_LOAD_OP_LOAD;
	depthAttachment.storeOp = VK_ATTACHMENT_STORE_OP_STORE;
	VkRenderingAttachmentInfo stencilAttachment = depthAttachment;
	VkRenderingInfo renderingInfo;
	memset( &renderingInfo, 0, sizeof( renderingInfo ) );
	renderingInfo.sType = VK_STRUCTURE_TYPE_RENDERING_INFO;
	renderingInfo.renderArea.extent = context.framebufferExtent;
	renderingInfo.layerCount = 1;
	renderingInfo.colorAttachmentCount = 1;
	renderingInfo.pColorAttachments = &colorAttachment;
	renderingInfo.pDepthAttachment = &depthAttachment;
	renderingInfo.pStencilAttachment = &stencilAttachment;
	state.CmdBeginRendering( context.commandBuffer, &renderingInfo );
}

} // namespace

bool R_RayTracingDrawView( const viewDef_s* view,
	const sdRayTracingGeometry* geometries, int geometryCount,
	const sdRayTracingGeometry* waterGeometries, int waterGeometryCount,
	const sdRayTracingViewContext& context ) {
	if ( rayTracingState == NULL || view == NULL || geometries == NULL ||
		geometryCount <= 0 || context.commandBuffer == VK_NULL_HANDLE ||
		context.frameIndex >= rayTracingState->frameCount ||
		context.colorImage == VK_NULL_HANDLE ||
		context.depthImage == VK_NULL_HANDLE ||
		context.viewport.extent.width == 0 || context.viewport.extent.height == 0 ) {
		return false;
	}
	sdRayTracingState& state = *rayTracingState;
	sdRayTracingFrame& frame = state.frames[ context.frameIndex ];
	if ( frame.viewCount >= MAX_RAY_TRACING_VIEWS ) {
		common->Warning( "Ray tracing skipped: more than %u views in one frame",
			MAX_RAY_TRACING_VIEWS );
		return false;
	}
	const VkDeviceSize lightingOffset =
		static_cast< VkDeviceSize >( frame.viewCount ) * RAY_TRACING_VIEW_STRIDE;
	if ( !WriteViewLighting( frame, *view, context, lightingOffset ) ) {
		return false;
	}

	state.CmdEndRendering( context.commandBuffer );
	unsigned int waterGeometryStart = 0;
	unsigned int validWaterGeometryCount = 0;
	{
		sdVulkanSlowCallScope slowBuild( "BuildAccelerationStructures" );
		if ( !BuildAccelerationStructures( state, frame, context.frameIndex,
			context.commandBuffer,
			geometries, geometryCount, waterGeometries, waterGeometryCount,
			waterGeometryStart, validWaterGeometryCount ) ) {
			ResumeDynamicRendering( state, context );
			return false;
		}
	}
	byte* lightingDestination = static_cast< byte* >(
		frame.viewLighting.mapped ) + lightingOffset;
	sdRayTracingViewData* viewData =
		reinterpret_cast< sdRayTracingViewData* >( lightingDestination );
	viewData->lightCounts[ 1 ] = waterGeometryStart;
	viewData->lightCounts[ 2 ] = validWaterGeometryCount;
	float* waterNormals = reinterpret_cast< float* >(
		lightingDestination + sizeof( sdRayTracingViewData ) +
		MAX_RAY_TRACING_LIGHTS * sizeof( sdRayTracingLightData ) );
	unsigned int validWaterIndex = 0;
	for ( int waterIndex = 0; waterIndex < waterGeometryCount &&
		validWaterIndex < validWaterGeometryCount; ++waterIndex ) {
		const sdRayTracingGeometry& water = waterGeometries[ waterIndex ];
		if ( water.vertexBuffer == VK_NULL_HANDLE ||
			water.indexBuffer == VK_NULL_HANDLE || water.vertexCount < 3 ||
			water.indexCount < 3 ) {
			continue;
		}
		memcpy( waterNormals + validWaterIndex * 4, water.normal,
			4 * sizeof( float ) );
		validWaterIndex++;
	}
	UpdateRayTracingDescriptors( state, frame, context );
	AccelerationStructureBarrier( state, context.commandBuffer,
		VK_PIPELINE_STAGE_2_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
		VK_ACCESS_2_ACCELERATION_STRUCTURE_WRITE_BIT_KHR,
		VK_PIPELINE_STAGE_2_COMPUTE_SHADER_BIT,
		VK_ACCESS_2_ACCELERATION_STRUCTURE_READ_BIT_KHR );
	TransitionForRayTracing( state, context, true );
	state.CmdBindPipeline( context.commandBuffer,
		VK_PIPELINE_BIND_POINT_COMPUTE, state.lightingPipeline );
	const unsigned int dynamicOffset = static_cast< unsigned int >( lightingOffset );
	state.CmdBindDescriptorSets( context.commandBuffer,
		VK_PIPELINE_BIND_POINT_COMPUTE, state.pipelineLayout, 0, 1,
		&frame.descriptorSet, 1, &dynamicOffset );
	unsigned int passIndex = 0;
	state.CmdPushConstants( context.commandBuffer, state.pipelineLayout,
		VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof( passIndex ), &passIndex );
	{
		sdVulkanSlowCallScope slowDispatch( "ray-query lighting dispatch" );
		state.CmdDispatch( context.commandBuffer,
			( context.viewport.extent.width + 7 ) / 8,
			( context.viewport.extent.height + 7 ) / 8, 1 );
	}
	if ( validWaterGeometryCount != 0 &&
		viewData->waterParameters[ 1 ] > 0.0f &&
		context.reflectionImage != VK_NULL_HANDLE &&
		context.reflectionImageView != VK_NULL_HANDLE &&
		context.reflectionSampler != VK_NULL_HANDLE ) {
		{
			sdVulkanSlowCallScope slowCopy( "CopyLitSceneForReflections" );
			CopyLitSceneForReflections( state, context );
		}
		passIndex = 1;
		state.CmdPushConstants( context.commandBuffer, state.pipelineLayout,
			VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof( passIndex ), &passIndex );
		state.CmdDispatch( context.commandBuffer,
			( context.viewport.extent.width + 7 ) / 8,
			( context.viewport.extent.height + 7 ) / 8, 1 );
	}
	TransitionForRayTracing( state, context, false );
	ResumeDynamicRendering( state, context );
	frame.viewCount++;
	return true;
}

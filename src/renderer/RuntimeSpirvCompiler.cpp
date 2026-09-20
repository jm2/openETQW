// Copyright (C) 2007 Id Software, Inc.

#include "../framework/precompiled.h"
#pragma hdrstop

#include "RuntimeSpirvCompiler.h"
#include "../idlib/hashing/MD5.h"

#if defined( _WIN32 )
#include <windows.h>
typedef DWORD ProcessExitCode_t;
#else
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <spawn.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <pthread.h>
extern char **environ;
typedef unsigned int ProcessExitCode_t;
#endif

namespace {

const char* SPIRV_CACHE_VERSION = "etqw-runtime-spirv-cache-v1";
const char* SPIRV_TARGET_ENV = "vulkan1.3";
const char* SPIRV_CACHE_DIRECTORY = "vkprogs/cache";

#if defined( _WIN32 )
LONG temporaryFileSerial = 0;
#else
static int temporaryFileSerial = 0;
#endif
bool commandsRegistered = false;

static unsigned int GetTimestampMs() {
#if defined( _WIN32 )
	return GetTickCount();
#else
	return Sys_Milliseconds();
#endif
}

idCVar r_vkShaderCompiler(
	"r_vkShaderCompiler",
	"",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_NOCHEAT,
	"full path to glslangValidator.exe; empty searches VULKAN_SDK, the executable directory, and PATH"
);

idCVar r_vkSpirvValidator(
	"r_vkSpirvValidator",
	"",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_NOCHEAT,
	"full path to spirv-val.exe; empty searches beside the shader compiler, VULKAN_SDK, and PATH"
);

idCVar r_vkValidateSpirv(
	"r_vkValidateSpirv",
	"1",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_BOOL | CVAR_NOCHEAT,
	"run spirv-val on newly compiled Vulkan shaders when the validator is available"
);

idCVar r_vkShaderCompileTimeout(
	"r_vkShaderCompileTimeout",
	"60000",
	CVAR_RENDERER | CVAR_ARCHIVE | CVAR_INTEGER | CVAR_NOCHEAT,
	"maximum runtime shader compiler duration in milliseconds",
	1000.0f,
	300000.0f
);

bool IsRegularFile( const char* path ) {
	if ( path == NULL || path[ 0 ] == '\0' ) {
		return false;
	}
#if defined( _WIN32 )
	const DWORD attributes = GetFileAttributesA( path );
	return attributes != INVALID_FILE_ATTRIBUTES &&
		( attributes & FILE_ATTRIBUTE_DIRECTORY ) == 0;
#else
	struct stat st;
	return ( stat( path, &st ) == 0 ) && S_ISREG( st.st_mode );
#endif
}

#if defined( _WIN32 )
void AppendWin32Error( idStr& text, const char* prefix, DWORD error ) {
	char message[ 512 ];
	message[ 0 ] = '\0';
	FormatMessageA( FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
		NULL, error, 0, message, sizeof( message ), NULL );
	idStr messageText = message;
	messageText.StripTrailingWhiteSpace();
	text += va( "%s (error %u%s%s)", prefix, static_cast< unsigned int >( error ),
		messageText.IsEmpty() ? "" : ": ", messageText.c_str() );
}
#endif

bool ResolveExecutable( const char* configuredPath, const char* fileName,
	idStr& resolvedPath, idStr& reason ) {
	resolvedPath.Clear();
	reason.Clear();

	idList< idStr > candidateNames;
	candidateNames.Append( fileName );
	idStr cleanName = fileName;
	if ( cleanName.Length() > 4 && idStr::Icmp( cleanName.c_str() + cleanName.Length() - 4, ".exe" ) == 0 ) {
		cleanName.CapLength( cleanName.Length() - 4 );
		candidateNames.Append( cleanName );
	}

	if ( configuredPath != NULL && configuredPath[ 0 ] != '\0' ) {
		if ( IsRegularFile( configuredPath ) ) {
			resolvedPath = configuredPath;
			return true;
		}

#if defined( _WIN32 )
		char found[ 4096 ];
		const DWORD foundLength = SearchPathA( NULL, configuredPath, NULL,
			sizeof( found ), found, NULL );
		if ( foundLength > 0 && foundLength < sizeof( found ) && IsRegularFile( found ) ) {
			resolvedPath = found;
			return true;
		}
#endif

		reason = va( "configured executable was not found: %s", configuredPath );
		return false;
	}

	idStr executableDirectory = Sys_EXEPath();
	executableDirectory.StripFilename();
	if ( !executableDirectory.IsEmpty() ) {
		for ( int n = 0; n < candidateNames.Num(); ++n ) {
			idStr candidate = executableDirectory;
			candidate.AppendPath( "vkcompiler" );
			candidate.AppendPath( candidateNames[ n ] );
			if ( IsRegularFile( candidate ) ) {
				resolvedPath = candidate;
				return true;
			}

			candidate = executableDirectory;
			candidate.AppendPath( candidateNames[ n ] );
			if ( IsRegularFile( candidate ) ) {
				resolvedPath = candidate;
				return true;
			}
		}
	}

	const char* vulkanSDK = Sys_GetEnv( "VULKAN_SDK" );
	if ( vulkanSDK != NULL && vulkanSDK[ 0 ] != '\0' ) {
		for ( int n = 0; n < candidateNames.Num(); ++n ) {
			idStr candidate = vulkanSDK;
			candidate.AppendPath( "Bin" );
			candidate.AppendPath( candidateNames[ n ] );
			if ( IsRegularFile( candidate ) ) {
				resolvedPath = candidate;
				return true;
			}
			candidate = vulkanSDK;
			candidate.AppendPath( "bin" );
			candidate.AppendPath( candidateNames[ n ] );
			if ( IsRegularFile( candidate ) ) {
				resolvedPath = candidate;
				return true;
			}
		}
	}

#if defined( _WIN32 )
	for ( int n = 0; n < candidateNames.Num(); ++n ) {
		char found[ 4096 ];
		const DWORD foundLength = SearchPathA( NULL, candidateNames[ n ], NULL,
			sizeof( found ), found, NULL );
		if ( foundLength > 0 && foundLength < sizeof( found ) && IsRegularFile( found ) ) {
			resolvedPath = found;
			return true;
		}
	}
#else
	const char* pathEnv = Sys_GetEnv( "PATH" );
	if ( pathEnv != NULL && pathEnv[ 0 ] != '\0' ) {
		idStr pathStr = pathEnv;
		int start = 0;
		while ( start < pathStr.Length() ) {
			int colon = pathStr.Find( ':', start );
			idStr dir;
			if ( colon >= 0 ) {
				dir = pathStr.Mid( start, colon - start );
				start = colon + 1;
			} else {
				dir = pathStr.Mid( start, pathStr.Length() - start );
				start = pathStr.Length();
			}
			for ( int n = 0; n < candidateNames.Num(); ++n ) {
				idStr candidate = dir;
				candidate.AppendPath( candidateNames[ n ] );
				if ( IsRegularFile( candidate ) && access( candidate.c_str(), X_OK ) == 0 ) {
					resolvedPath = candidate;
					return true;
				}
			}
		}
	}
	const char* fallbackDirs[] = { "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin" };
	for ( size_t i = 0; i < sizeof( fallbackDirs ) / sizeof( fallbackDirs[ 0 ] ); ++i ) {
		for ( int n = 0; n < candidateNames.Num(); ++n ) {
			idStr candidate = fallbackDirs[ i ];
			candidate.AppendPath( candidateNames[ n ] );
			if ( IsRegularFile( candidate ) && access( candidate.c_str(), X_OK ) == 0 ) {
				resolvedPath = candidate;
				return true;
			}
		}
	}
#endif

	reason = va( "%s was not found in vkcompiler, beside executable, VULKAN_SDK, or PATH", fileName );
	return false;
}

bool ResolveValidator( const idStr& compilerPath, idStr& validatorPath,
	idStr& reason ) {
	if ( r_vkSpirvValidator.GetString()[ 0 ] != '\0' ) {
		return ResolveExecutable( r_vkSpirvValidator.GetString(),
			"spirv-val.exe", validatorPath, reason );
	}

	idStr companion = compilerPath;
	companion.StripFilename();
	companion.AppendPath( "spirv-val.exe" );
	if ( IsRegularFile( companion ) ) {
		validatorPath = companion;
		reason.Clear();
		return true;
	}
	companion = compilerPath;
	companion.StripFilename();
	companion.AppendPath( "spirv-val" );
	if ( IsRegularFile( companion ) ) {
		validatorPath = companion;
		reason.Clear();
		return true;
	}

	return ResolveExecutable( "", "spirv-val.exe", validatorPath, reason );
}

bool BuildRuntimeConfig( sdSpirvCompilerConfig& config, idStr& reason ) {
	if ( !ResolveExecutable( r_vkShaderCompiler.GetString(),
		"glslangValidator.exe", config.compilerPath, reason ) ) {
		return false;
	}
	if ( fileSystem == NULL ) {
		reason = "the engine file system is not initialized";
		return false;
	}

	config.cacheDirectory = SPIRV_CACHE_DIRECTORY;
	config.timeoutMilliseconds = r_vkShaderCompileTimeout.GetInteger();
	config.validate = r_vkValidateSpirv.GetBool();
	if ( config.validate ) {
		idStr validatorReason;
		if ( !ResolveValidator( config.compilerPath, config.validatorPath,
			validatorReason ) ) {
			if ( r_vkSpirvValidator.GetString()[ 0 ] != '\0' ) {
				reason = validatorReason;
				return false;
			}
			// Structural validation remains active when spirv-val is unavailable.
			config.validatorPath.Clear();
		}
	}
	return true;
}

idStr ToDevelopmentOSPath( const char* virtualPath ) {
	return idStr( fileSystem->RelativePathToOSPath( virtualPath, "fs_devpath" ) );
}

bool WriteVirtualFile( const char* virtualPath, const void* data, int length,
	idStr& diagnostics ) {
	if ( fileSystem->WriteFile( virtualPath, data, length, "fs_devpath" ) != length ) {
		diagnostics += va( "could not write generated shader file '%s' through the engine file system",
			virtualPath );
		return false;
	}
	return true;
}

bool ReadVirtualTextFile( const char* virtualPath, idStr& text ) {
	text.Clear();
	void* buffer = NULL;
	const int length = fileSystem->ReadFile( virtualPath, &buffer, NULL, false );
	if ( length < 0 || buffer == NULL ) {
		return false;
	}
	text = reinterpret_cast< const char* >( buffer );
	fileSystem->FreeFile( buffer );
	return true;
}

void DeleteVirtualFile( const idStr& virtualPath ) {
	if ( !virtualPath.IsEmpty() ) {
		fileSystem->RemoveFile( virtualPath );
	}
}

bool ValidateSpirvWords( const idList< unsigned int >& words,
	idStr& diagnostics ) {
	if ( words.Num() < 5 ) {
		diagnostics += "SPIR-V output is shorter than its five-word header";
		return false;
	}
	if ( words[ 0 ] != 0x07230203U ) {
		diagnostics += "SPIR-V output has an invalid magic number";
		return false;
	}
	if ( words[ 4 ] != 0 ) {
		diagnostics += "SPIR-V output has a non-zero reserved header word";
		return false;
	}
	return true;
}

bool ReadVirtualSpirvFile( const char* virtualPath,
	idList< unsigned int >& words, idStr& diagnostics ) {
	words.Clear();
	void* buffer = NULL;
	const int length = fileSystem->ReadFile( virtualPath, &buffer, NULL, false );
	if ( length < 20 || buffer == NULL || ( length & 3 ) != 0 ) {
		if ( buffer != NULL ) {
			fileSystem->FreeFile( buffer );
		}
		return false;
	}

	words.SetNum( length / static_cast< int >( sizeof( unsigned int ) ) );
	memcpy( words.Begin(), buffer, length );
	fileSystem->FreeFile( buffer );
	if ( !ValidateSpirvWords( words, diagnostics ) ) {
		words.Clear();
		return false;
	}
	return true;
}

void AppendCommandLineArgument( idStr& commandLine, const char* argument ) {
	if ( !commandLine.IsEmpty() ) {
		commandLine += " ";
	}
	if ( argument == NULL ) {
		argument = "";
	}

	const bool quote = argument[ 0 ] == '\0' || strpbrk( argument, " \t\"") != NULL;
	if ( !quote ) {
		commandLine += argument;
		return;
	}

	commandLine += '"';
	int backslashes = 0;
	for ( const char* scan = argument; ; ++scan ) {
		if ( *scan == '\\' ) {
			++backslashes;
			continue;
		}
		if ( *scan == '"' ) {
			for ( int i = 0; i < backslashes * 2 + 1; ++i ) {
				commandLine += '\\';
			}
			commandLine += '"';
			backslashes = 0;
			continue;
		}
		if ( *scan == '\0' ) {
			for ( int i = 0; i < backslashes * 2; ++i ) {
				commandLine += '\\';
			}
			break;
		}
		for ( int i = 0; i < backslashes; ++i ) {
			commandLine += '\\';
		}
		backslashes = 0;
		commandLine += *scan;
	}
	commandLine += '"';
}

bool RunProcess( const char* executable, const idList< idStr >& arguments,
	const char* logOSPath, int timeoutMilliseconds, ProcessExitCode_t& exitCode,
	idStr& diagnostics ) {
	exitCode = 0;

#if defined( _WIN32 )
	SECURITY_ATTRIBUTES security;
	memset( &security, 0, sizeof( security ) );
	security.nLength = sizeof( security );
	security.bInheritHandle = TRUE;
	HANDLE log = CreateFileA( logOSPath, GENERIC_WRITE, FILE_SHARE_READ, &security,
		CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL );
	if ( log == INVALID_HANDLE_VALUE ) {
		AppendWin32Error( diagnostics, "could not create shader compiler log",
			GetLastError() );
		return false;
	}

	idStr commandLine;
	AppendCommandLineArgument( commandLine, executable );
	for ( int i = 0; i < arguments.Num(); ++i ) {
		AppendCommandLineArgument( commandLine, arguments[ i ] );
	}
	idList< char > mutableCommandLine;
	mutableCommandLine.SetNum( commandLine.Length() + 1 );
	memcpy( mutableCommandLine.Begin(), commandLine.c_str(), commandLine.Length() + 1 );

	STARTUPINFOA startup;
	PROCESS_INFORMATION process;
	memset( &startup, 0, sizeof( startup ) );
	memset( &process, 0, sizeof( process ) );
	startup.cb = sizeof( startup );
	startup.dwFlags = STARTF_USESTDHANDLES;
	startup.hStdInput = GetStdHandle( STD_INPUT_HANDLE );
	startup.hStdOutput = log;
	startup.hStdError = log;

	const BOOL launched = CreateProcessA( executable, mutableCommandLine.Begin(),
		NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &startup, &process );
	if ( !launched ) {
		AppendWin32Error( diagnostics, "could not launch runtime shader compiler",
			GetLastError() );
		CloseHandle( log );
		return false;
	}
	CloseHandle( process.hThread );

	const DWORD waitResult = WaitForSingleObject( process.hProcess,
		static_cast< DWORD >( timeoutMilliseconds ) );
	if ( waitResult == WAIT_TIMEOUT ) {
		TerminateProcess( process.hProcess, ERROR_TIMEOUT );
		WaitForSingleObject( process.hProcess, 5000 );
		diagnostics += va( "runtime shader compiler exceeded its %d ms timeout",
			timeoutMilliseconds );
		CloseHandle( process.hProcess );
		CloseHandle( log );
		return false;
	}
	if ( waitResult != WAIT_OBJECT_0 ||
		!GetExitCodeProcess( process.hProcess, &exitCode ) ) {
		const DWORD error = GetLastError();
		AppendWin32Error( diagnostics, "could not wait for runtime shader compiler", error );
		CloseHandle( process.hProcess );
		CloseHandle( log );
		return false;
	}
	CloseHandle( process.hProcess );
	CloseHandle( log );

	if ( exitCode != 0 ) {
		diagnostics += va( "runtime shader compiler returned exit code %u",
			static_cast< unsigned int >( exitCode ) );
		return false;
	}
	return true;
#else
	int logFd = open( logOSPath, O_WRONLY | O_CREAT | O_TRUNC, 0644 );
	if ( logFd < 0 ) {
		diagnostics += va( "could not create shader compiler log '%s': %s", logOSPath, strerror( errno ) );
		return false;
	}

	idList< const char* > argvList;
	argvList.Append( executable );
	for ( int i = 0; i < arguments.Num(); ++i ) {
		argvList.Append( arguments[ i ].c_str() );
	}
	argvList.Append( NULL );

	posix_spawn_file_actions_t actions;
	posix_spawn_file_actions_init( &actions );
	posix_spawn_file_actions_adddup2( &actions, logFd, STDOUT_FILENO );
	posix_spawn_file_actions_adddup2( &actions, logFd, STDERR_FILENO );

	pid_t pid = 0;
	int spawnErr = posix_spawnp( &pid, executable, &actions, NULL,
		const_cast< char* const* >( argvList.Begin() ), environ );
	posix_spawn_file_actions_destroy( &actions );
	close( logFd );

	if ( spawnErr != 0 ) {
		diagnostics += va( "could not spawn '%s': %s", executable, strerror( spawnErr ) );
		return false;
	}

	int status = 0;
	int waitedMs = 0;
	const int pollIntervalMs = 10;
	bool finished = false;

	while ( waitedMs < timeoutMilliseconds ) {
		pid_t res = waitpid( pid, &status, WNOHANG );
		if ( res == pid ) {
			finished = true;
			break;
		} else if ( res < 0 ) {
			diagnostics += va( "waitpid error: %s", strerror( errno ) );
			return false;
		}
		usleep( pollIntervalMs * 1000 );
		waitedMs += pollIntervalMs;
	}

	if ( !finished ) {
		kill( pid, SIGKILL );
		waitpid( pid, &status, 0 );
		diagnostics += va( "runtime shader compiler exceeded its %d ms timeout", timeoutMilliseconds );
		return false;
	}

	if ( WIFEXITED( status ) ) {
		exitCode = static_cast< unsigned int >( WEXITSTATUS( status ) );
	} else if ( WIFSIGNALED( status ) ) {
		exitCode = static_cast< unsigned int >( 128 + WTERMSIG( status ) );
	} else {
		exitCode = 1;
	}

	if ( exitCode != 0 ) {
		diagnostics += va( "runtime shader compiler returned exit code %u", exitCode );
		return false;
	}
	return true;
#endif
}

void HashBytes( md5Context_t& context, const void* data, int length ) {
	MD5_UpdateChecksum( context, &length, sizeof( length ) );
	if ( length > 0 ) {
		MD5_UpdateChecksum( context, data, length );
	}
}

void HashString( md5Context_t& context, const char* text ) {
	if ( text == NULL ) {
		HashBytes( context, "", 0 );
		return;
	}
	HashBytes( context, text, idStr::Length( text ) );
}

idStr BuildCacheKey( const sdSpirvCompilerConfig& config,
	const sdSpirvCompileRequest& request ) {
	md5Context_t context;
	MD5_StartChecksum( context );
	HashString( context, SPIRV_CACHE_VERSION );
	HashString( context, SPIRV_TARGET_ENV );
	HashString( context, config.compilerPath );
	HashBytes( context, &request.stage, sizeof( request.stage ) );
	HashBytes( context, request.source, request.sourceLength );
	HashString( context, request.cacheSalt );

#if defined( _WIN32 )
	WIN32_FILE_ATTRIBUTE_DATA compilerAttributes;
	memset( &compilerAttributes, 0, sizeof( compilerAttributes ) );
	if ( GetFileAttributesExA( config.compilerPath, GetFileExInfoStandard,
		&compilerAttributes ) ) {
		HashBytes( context, &compilerAttributes.ftLastWriteTime,
			sizeof( compilerAttributes.ftLastWriteTime ) );
		HashBytes( context, &compilerAttributes.nFileSizeHigh,
			sizeof( compilerAttributes.nFileSizeHigh ) );
		HashBytes( context, &compilerAttributes.nFileSizeLow,
			sizeof( compilerAttributes.nFileSizeLow ) );
	}
#else
	struct stat st;
	if ( stat( config.compilerPath, &st ) == 0 ) {
		HashBytes( context, &st.st_mtime, sizeof( st.st_mtime ) );
		HashBytes( context, &st.st_size, sizeof( st.st_size ) );
	}
#endif

	unsigned char digest[ 16 ];
	MD5_FinishChecksum( context, digest );
	char key[ 33 ];
	for ( int i = 0; i < 16; ++i ) {
		idStr::snPrintf( key + i * 2, sizeof( key ) - i * 2, "%02x", digest[ i ] );
	}
	key[ 32 ] = '\0';
	return idStr( key );
}

void AppendToolOutput( idStr& diagnostics, const char* heading,
	const idStr& output ) {
	if ( output.IsEmpty() ) {
		return;
	}
	if ( !diagnostics.IsEmpty() ) {
		diagnostics += "\n";
	}
	diagnostics += heading;
	diagnostics += ":\n";
	diagnostics += output;
	diagnostics.StripTrailingWhiteSpace();
}

void PrintCompileResult( const char* sourceName,
	const sdSpirvCompileResult& result ) {
	common->Printf( "%s: %d SPIR-V bytes, %s, %d ms\n",
		sourceName != NULL ? sourceName : "<shader>",
		result.words.Num() * static_cast< int >( sizeof( unsigned int ) ),
		result.cacheHit ? "filesystem cache hit" : "compiled",
		result.elapsedMilliseconds );
	if ( !result.diagnostics.IsEmpty() ) {
		common->Printf( "%s\n", result.diagnostics.c_str() );
	}
}

bool ParseStage( const char* name, sdSpirvShaderStage& stage ) {
	if ( idStr::Icmp( name, "vertex" ) == 0 || idStr::Icmp( name, "vert" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_VERTEX;
	} else if ( idStr::Icmp( name, "fragment" ) == 0 || idStr::Icmp( name, "frag" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_FRAGMENT;
	} else if ( idStr::Icmp( name, "geometry" ) == 0 || idStr::Icmp( name, "geom" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_GEOMETRY;
	} else if ( idStr::Icmp( name, "tesscontrol" ) == 0 || idStr::Icmp( name, "tesc" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_TESS_CONTROL;
	} else if ( idStr::Icmp( name, "tesseval" ) == 0 || idStr::Icmp( name, "tese" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_TESS_EVALUATION;
	} else if ( idStr::Icmp( name, "compute" ) == 0 || idStr::Icmp( name, "comp" ) == 0 ) {
		stage = SPIRV_SHADER_STAGE_COMPUTE;
	} else {
		return false;
	}
	return true;
}

void R_CompileVulkanShader_f( const idCmdArgs& args ) {
	if ( args.Argc() < 3 ) {
		common->Printf( "usage: vkCompileShader <vertex|fragment|geometry|tesc|tese|compute> <vkprogs/file> [force]\n" );
		return;
	}

	sdSpirvShaderStage stage;
	if ( !ParseStage( args.Argv( 1 ), stage ) ) {
		common->Warning( "vkCompileShader: unknown stage '%s'", args.Argv( 1 ) );
		return;
	}
	const idStr shaderPath = args.Argv( 2 );
	if ( shaderPath.IcmpPrefixPath( "vkprogs/" ) != 0 ) {
		common->Warning( "vkCompileShader: source must be below vkprogs/" );
		return;
	}

	void* sourceBuffer = NULL;
	const int sourceLength = fileSystem->ReadFile( args.Argv( 2 ),
		&sourceBuffer, NULL, false );
	if ( sourceLength < 0 || sourceBuffer == NULL ) {
		common->Warning( "vkCompileShader: could not read '%s' through the engine file system",
			args.Argv( 2 ) );
		return;
	}

	sdSpirvCompileResult result;
	const bool force = args.Argc() > 3 && atoi( args.Argv( 3 ) ) != 0;
	const bool success = R_CompileVulkanGLSL( args.Argv( 2 ),
		reinterpret_cast< const char* >( sourceBuffer ), sourceLength, stage,
		"console-v1", force, result );
	fileSystem->FreeFile( sourceBuffer );

	if ( success ) {
		PrintCompileResult( args.Argv( 2 ), result );
	} else {
		common->Warning( "vkCompileShader failed for '%s':\n%s",
			args.Argv( 2 ), result.diagnostics.c_str() );
	}
}

void R_TestVulkanShaderCompiler_f( const idCmdArgs& ) {
	const char* names[ 2 ] = {
		"vkprogs/smoke/runtime_compiler.vert",
		"vkprogs/smoke/runtime_compiler.frag"
	};
	const sdSpirvShaderStage stages[ 2 ] = {
		SPIRV_SHADER_STAGE_VERTEX, SPIRV_SHADER_STAGE_FRAGMENT
	};

	for ( int i = 0; i < 2; ++i ) {
		void* sourceBuffer = NULL;
		const int sourceLength = fileSystem->ReadFile( names[ i ],
			&sourceBuffer, NULL, false );
		if ( sourceLength < 0 || sourceBuffer == NULL ) {
			common->Warning( "vkShaderCompilerTest could not load %s through the engine file system",
				names[ i ] );
			return;
		}

		sdSpirvCompileResult compiled;
		if ( !R_CompileVulkanGLSL( names[ i ],
			reinterpret_cast< const char* >( sourceBuffer ), sourceLength,
			stages[ i ], "smoke-v1", true, compiled ) ) {
			fileSystem->FreeFile( sourceBuffer );
			common->Warning( "vkShaderCompilerTest failed for %s:\n%s",
				names[ i ], compiled.diagnostics.c_str() );
			return;
		}
		PrintCompileResult( names[ i ], compiled );

		sdSpirvCompileResult cached;
		const bool cacheSuccess = R_CompileVulkanGLSL( names[ i ],
			reinterpret_cast< const char* >( sourceBuffer ), sourceLength,
			stages[ i ], "smoke-v1", false, cached );
		fileSystem->FreeFile( sourceBuffer );
		if ( !cacheSuccess || !cached.cacheHit ||
			cached.words.Num() != compiled.words.Num() ||
			memcmp( cached.words.Begin(), compiled.words.Begin(),
				compiled.words.Num() * sizeof( unsigned int ) ) != 0 ) {
			common->Warning( "vkShaderCompilerTest filesystem cache verification failed for %s",
				names[ i ] );
			return;
		}
		PrintCompileResult( names[ i ], cached );
	}
	common->Printf( "vkShaderCompilerTest: passed\n" );
}

} // namespace

sdSpirvCompilerConfig::sdSpirvCompilerConfig() :
	timeoutMilliseconds( 60000 ),
	validate( true ) {
}

sdSpirvCompileRequest::sdSpirvCompileRequest() :
	sourceName( "<shader>" ),
	source( NULL ),
	sourceLength( 0 ),
	stage( SPIRV_SHADER_STAGE_VERTEX ),
	cacheSalt( "" ),
	forceRecompile( false ) {
}

sdSpirvCompileResult::sdSpirvCompileResult() {
	Clear();
}

void sdSpirvCompileResult::Clear() {
	words.Clear();
	diagnostics.Clear();
	cachePath.Clear();
	cacheHit = false;
	elapsedMilliseconds = 0;
}

const char* sdRuntimeSpirvCompiler::StageName( sdSpirvShaderStage stage ) {
	switch ( stage ) {
		case SPIRV_SHADER_STAGE_VERTEX: return "vertex";
		case SPIRV_SHADER_STAGE_FRAGMENT: return "fragment";
		case SPIRV_SHADER_STAGE_GEOMETRY: return "geometry";
		case SPIRV_SHADER_STAGE_TESS_CONTROL: return "tessellation control";
		case SPIRV_SHADER_STAGE_TESS_EVALUATION: return "tessellation evaluation";
		case SPIRV_SHADER_STAGE_COMPUTE: return "compute";
		default: return "unknown";
	}
}

const char* sdRuntimeSpirvCompiler::StageExtension( sdSpirvShaderStage stage ) {
	switch ( stage ) {
		case SPIRV_SHADER_STAGE_VERTEX: return "vert";
		case SPIRV_SHADER_STAGE_FRAGMENT: return "frag";
		case SPIRV_SHADER_STAGE_GEOMETRY: return "geom";
		case SPIRV_SHADER_STAGE_TESS_CONTROL: return "tesc";
		case SPIRV_SHADER_STAGE_TESS_EVALUATION: return "tese";
		case SPIRV_SHADER_STAGE_COMPUTE: return "comp";
		default: return NULL;
	}
}

bool sdRuntimeSpirvCompiler::Compile( const sdSpirvCompilerConfig& config,
	const sdSpirvCompileRequest& request, sdSpirvCompileResult& result ) const {
	result.Clear();
	const unsigned int startTime = GetTimestampMs();

	if ( request.source == NULL || request.sourceLength <= 0 ) {
		result.diagnostics = "runtime shader compiler received empty GLSL source";
		return false;
	}
	const char* extension = StageExtension( request.stage );
	if ( extension == NULL ) {
		result.diagnostics = "runtime shader compiler received an invalid stage";
		return false;
	}
	if ( !IsRegularFile( config.compilerPath ) ) {
		result.diagnostics = va( "runtime shader compiler was not found: %s",
			config.compilerPath.c_str() );
		return false;
	}
	if ( config.timeoutMilliseconds <= 0 ) {
		result.diagnostics = "runtime shader compiler timeout must be positive";
		return false;
	}
	if ( config.cacheDirectory.IcmpPrefixPath( "vkprogs/" ) != 0 ) {
		result.diagnostics = "runtime shader cache must be below vkprogs/";
		return false;
	}

	const idStr cacheKey = BuildCacheKey( config, request );
	result.cachePath = config.cacheDirectory;
	result.cachePath.AppendPath( va( "%s.%s.spv", cacheKey.c_str(), extension ) );

	if ( !request.forceRecompile &&
		ReadVirtualSpirvFile( result.cachePath, result.words, result.diagnostics ) ) {
		result.cacheHit = true;
		result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
		return true;
	}
	result.words.Clear();
	result.diagnostics.Clear();

	char temporaryStem[ 160 ];
#if defined( _WIN32 )
	idStr::snPrintf( temporaryStem, sizeof( temporaryStem ),
		"%s.%s.%08x.%08x.%08x", cacheKey.c_str(), extension,
		static_cast< unsigned int >( GetCurrentProcessId() ),
		static_cast< unsigned int >( GetCurrentThreadId() ),
		static_cast< unsigned int >( InterlockedIncrement( &temporaryFileSerial ) ) );
#else
	idStr::snPrintf( temporaryStem, sizeof( temporaryStem ),
		"%s.%s.%08x.%08x.%08x", cacheKey.c_str(), extension,
		static_cast< unsigned int >( getpid() ),
		static_cast< unsigned int >( (uintptr_t)pthread_self() ),
		static_cast< unsigned int >( ++temporaryFileSerial ) );
#endif
	idStr sourcePath = config.cacheDirectory;
	sourcePath.AppendPath( va( "%s.%s", temporaryStem, extension ) );
	idStr outputPath = config.cacheDirectory;
	outputPath.AppendPath( va( "%s.tmp.spv", temporaryStem ) );
	idStr compileLogPath = config.cacheDirectory;
	compileLogPath.AppendPath( va( "%s.compile.log", temporaryStem ) );
	idStr validateLogPath = config.cacheDirectory;
	validateLogPath.AppendPath( va( "%s.validate.log", temporaryStem ) );

	if ( !WriteVirtualFile( sourcePath, request.source, request.sourceLength,
		result.diagnostics ) ) {
		return false;
	}

	const idStr sourceOSPath = ToDevelopmentOSPath( sourcePath );
	const idStr outputOSPath = ToDevelopmentOSPath( outputPath );
	const idStr compileLogOSPath = ToDevelopmentOSPath( compileLogPath );
	const idStr validateLogOSPath = ToDevelopmentOSPath( validateLogPath );
	const idStr cacheOSPath = ToDevelopmentOSPath( result.cachePath );

	idList< idStr > compilerArguments;
	compilerArguments.Append( "-V" );
	compilerArguments.Append( "--target-env" );
	compilerArguments.Append( SPIRV_TARGET_ENV );
	compilerArguments.Append( "-S" );
	compilerArguments.Append( extension );
	compilerArguments.Append( "-e" );
	compilerArguments.Append( "main" );
	compilerArguments.Append( "-o" );
	compilerArguments.Append( outputOSPath );
	compilerArguments.Append( sourceOSPath );

	ProcessExitCode_t compilerExitCode = 0;
	const bool compiled = RunProcess( config.compilerPath, compilerArguments,
		compileLogOSPath, config.timeoutMilliseconds, compilerExitCode,
		result.diagnostics );
	fileSystem->ClearDirCache();
	idStr compilerOutput;
	ReadVirtualTextFile( compileLogPath, compilerOutput );
	AppendToolOutput( result.diagnostics, "glslang output", compilerOutput );
	if ( !compiled ) {
		DeleteVirtualFile( outputPath );
		DeleteVirtualFile( compileLogPath );
		result.diagnostics += va( "\nGenerated source retained at %s", sourcePath.c_str() );
		result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
		return false;
	}

	fileSystem->ClearDirCache();
	if ( !ReadVirtualSpirvFile( outputPath, result.words, result.diagnostics ) ) {
		DeleteVirtualFile( outputPath );
		DeleteVirtualFile( compileLogPath );
		result.diagnostics += va( "\nGenerated source retained at %s", sourcePath.c_str() );
		result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
		return false;
	}

	if ( config.validate && !config.validatorPath.IsEmpty() ) {
		idList< idStr > validatorArguments;
		validatorArguments.Append( "--target-env" );
		validatorArguments.Append( SPIRV_TARGET_ENV );
		validatorArguments.Append( outputOSPath );
		ProcessExitCode_t validatorExitCode = 0;
		idStr validatorDiagnostics;
		const bool validated = RunProcess( config.validatorPath, validatorArguments,
			validateLogOSPath, config.timeoutMilliseconds, validatorExitCode,
			validatorDiagnostics );
		fileSystem->ClearDirCache();
		idStr validatorOutput;
		ReadVirtualTextFile( validateLogPath, validatorOutput );
		if ( !validated ) {
			if ( !result.diagnostics.IsEmpty() ) {
				result.diagnostics += "\n";
			}
			result.diagnostics += validatorDiagnostics;
			AppendToolOutput( result.diagnostics, "spirv-val output", validatorOutput );
			result.words.Clear();
			DeleteVirtualFile( outputPath );
			DeleteVirtualFile( compileLogPath );
			DeleteVirtualFile( validateLogPath );
			result.diagnostics += va( "\nGenerated source retained at %s", sourcePath.c_str() );
			result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
			return false;
		}
		AppendToolOutput( result.diagnostics, "spirv-val output", validatorOutput );
	}

#if defined( _WIN32 )
	if ( !MoveFileExA( outputOSPath, cacheOSPath,
		MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH ) ) {
		const DWORD error = GetLastError();
		AppendWin32Error( result.diagnostics, "could not publish SPIR-V cache entry", error );
		result.words.Clear();
		DeleteVirtualFile( outputPath );
		DeleteVirtualFile( sourcePath );
		DeleteVirtualFile( compileLogPath );
		DeleteVirtualFile( validateLogPath );
		result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
		return false;
	}
#else
	if ( rename( outputOSPath, cacheOSPath ) != 0 ) {
		result.diagnostics += va( "could not publish SPIR-V cache entry: %s", strerror( errno ) );
		result.words.Clear();
		DeleteVirtualFile( outputPath );
		DeleteVirtualFile( sourcePath );
		DeleteVirtualFile( compileLogPath );
		DeleteVirtualFile( validateLogPath );
		result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
		return false;
	}
#endif

	fileSystem->ClearDirCache();
	DeleteVirtualFile( sourcePath );
	DeleteVirtualFile( compileLogPath );
	DeleteVirtualFile( validateLogPath );
	result.cacheHit = false;
	result.elapsedMilliseconds = static_cast< int >( GetTimestampMs() - startTime );
	return true;
}

bool R_CompileVulkanGLSL( const char* sourceName, const char* source,
	int sourceLength, sdSpirvShaderStage stage, const char* cacheSalt,
	bool forceRecompile, sdSpirvCompileResult& result ) {
	sdSpirvCompilerConfig config;
	idStr reason;
	if ( !BuildRuntimeConfig( config, reason ) ) {
		result.Clear();
		result.diagnostics = reason;
		return false;
	}

	sdSpirvCompileRequest request;
	request.sourceName = sourceName;
	request.source = source;
	request.sourceLength = sourceLength;
	request.stage = stage;
	request.cacheSalt = cacheSalt != NULL ? cacheSalt : "";
	request.forceRecompile = forceRecompile;

	sdRuntimeSpirvCompiler compiler;
	return compiler.Compile( config, request, result );
}

void R_InitRuntimeSpirvCompiler() {
	if ( !commandsRegistered ) {
		cmdSystem->AddCommand( "vkCompileShader", R_CompileVulkanShader_f,
			CMD_FL_RENDERER, "compile a vkprogs GLSL shader to cached SPIR-V" );
		cmdSystem->AddCommand( "vkShaderCompilerTest", R_TestVulkanShaderCompiler_f,
			CMD_FL_RENDERER, "compile and validate the vkprogs shader smoke tests" );
		commandsRegistered = true;
	}

	sdSpirvCompilerConfig config;
	idStr reason;
	if ( BuildRuntimeConfig( config, reason ) ) {
		common->Printf( "runtime SPIR-V compiler: %s\n", config.compilerPath.c_str() );
		common->Printf( "runtime SPIR-V filesystem cache: %s\n",
			config.cacheDirectory.c_str() );
		if ( config.validate ) {
			common->Printf( "SPIR-V validator: %s\n",
				config.validatorPath.IsEmpty() ? "unavailable (structural validation only)" :
				config.validatorPath.c_str() );
		}
	} else {
		common->Printf( "runtime SPIR-V compiler unavailable: %s\n", reason.c_str() );
	}
}

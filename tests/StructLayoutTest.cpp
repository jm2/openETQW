// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: StructLayoutTest.cpp
Compile-time static assertions verifying exact field offsets, widths,
and alignments for wire and network structs.
===========================================================================
*/

#include "src/idlib/precompiled.h"
#include "src/framework/UsercmdGen.h"
#include "src/idlib/geometry/DrawVert.h"
#include "src/game/Game.h"

#include <cstddef>
#include <cstdio>

class TestCommonMock : public idCommon {
public:
    void Init( int argc, const char **argv, const char *cmdline ) override {}
    void Shutdown( void ) override {}
    void Quit( void ) override {}
    bool IsInitialized( void ) const override { return true; }
    void Frame( void ) override {}
    void Async( void ) override {}
    bool StartupVariable( const char *match ) override { return false; }
    void ClearStartupVariable( const char* match ) override {}
    void WriteConfigToFile( const char *filename, bool writeBindings = true, bool writeCVars = true ) override {}
    void WriteFlaggedCVarsToFile( const char *filename, int flags, const char *setCmd ) override {}
    void BeginRedirect( char *buffer, int buffersize, void *user, void (*flush)( void *, const char * ) ) override {}
    void EndRedirect( void ) override {}
    void SetRefreshOnPrint( bool set ) override {}
    void Printf( const char *fmt, ... ) override {}
    void TPrintf( const char *fmt, ... ) override {}
    void VPrintf( const char *fmt, va_list arg ) override {}
    void DPrintf( const char *fmt, ... ) override {}
    void Warning( const char *fmt, ... ) override {}
    void TWarning( const char *fmt, ... ) override {}
    void DWarning( const char *fmt, ... ) override {}
    void PrintWarnings( void ) override {}
    void ClearWarnings( const char *reason ) override {}
    void Error( const char *fmt, ... ) override {}
    void FatalError( const char *fmt, ... ) override {}
    void PrintLoadingMessage( const char *msg ) override {}
    void EnableWarnings( void ) override {}
    void DisableWarnings( void ) override {}
    void PacifierUpdate( void ) override {}
    void UpdateLevelLoadScreen( const wchar_t* status ) override {}
    const idLangDict* GetLanguageDict( void ) override { return NULL; }
    idWStr LocalizeText( const char* declName, const idWStrList& arguments = idWStrList() ) override { return idWStr(); }
    idWStr LocalizeText( const sdDeclLocStr* loc, const idWStrList& arguments = idWStrList() ) override { return idWStr(); }
    int GetNumVideoModes( void ) const override { return 0; }
    vidmode_t& GetVideoMode( int index ) const override { static vidmode_t vm; return vm; }
    idSoundWorld* GetGameSoundWorld( void ) override { return NULL; }
    idSoundWorld* GetMenuSoundWorld( void ) override { return NULL; }
    void WriteConfigs( void ) override {}
};

static TestCommonMock s_testCommon;
idCommon * common = &s_testCommon;
idSys * sys = NULL;
idDeclManager * declManager = NULL;

// ----------------------------------------------------------------------------
// 1. idDrawVert Layout Assertions (SD_USE_DRAWVERT_SIZE_32)
// ----------------------------------------------------------------------------
static_assert( sizeof( idDrawVert ) == 32, "idDrawVert size must be exactly 32 bytes" );
static_assert( offsetof( idDrawVert, xyz ) == 0, "idDrawVert::xyz offset must be 0" );
static_assert( offsetof( idDrawVert, color ) == 12, "idDrawVert::color offset must be 12" );
static_assert( offsetof( idDrawVert, _normal ) == 16, "idDrawVert::_normal offset must be 16" );
static_assert( offsetof( idDrawVert, _tangent ) == 20, "idDrawVert::_tangent offset must be 20" );
static_assert( offsetof( idDrawVert, _signs ) == 24, "idDrawVert::_signs offset must be 24" );
static_assert( offsetof( idDrawVert, _st ) == 28, "idDrawVert::_st offset must be 28" );

// ----------------------------------------------------------------------------
// 2. usercmd_t Layout Assertions
// ----------------------------------------------------------------------------
static_assert( sizeof( userButtonsUnion_t ) == 2, "userButtonsUnion_t must be 2 bytes" );
static_assert( sizeof( usercmd_t ) == 28, "usercmd_t must be exactly 28 bytes" );
static_assert( offsetof( usercmd_t, gameFrame ) == 0, "usercmd_t::gameFrame offset" );
static_assert( offsetof( usercmd_t, gameTime ) == 4, "usercmd_t::gameTime offset" );
static_assert( offsetof( usercmd_t, duplicateCount ) == 8, "usercmd_t::duplicateCount offset" );
static_assert( offsetof( usercmd_t, buttons ) == 12, "usercmd_t::buttons offset" );
static_assert( offsetof( usercmd_t, forwardmove ) == 14, "usercmd_t::forwardmove offset" );
static_assert( offsetof( usercmd_t, rightmove ) == 15, "usercmd_t::rightmove offset" );
static_assert( offsetof( usercmd_t, upmove ) == 16, "usercmd_t::upmove offset" );
static_assert( offsetof( usercmd_t, angles ) == 18, "usercmd_t::angles offset" );
static_assert( offsetof( usercmd_t, impulse ) == 24, "usercmd_t::impulse offset" );
static_assert( offsetof( usercmd_t, flags ) == 25, "usercmd_t::flags offset" );
static_assert( offsetof( usercmd_t, clientButtons ) == 26, "usercmd_t::clientButtons offset" );

// ----------------------------------------------------------------------------
// 3. Network Endpoint & Math Assertions
// ----------------------------------------------------------------------------
static_assert( sizeof( netadr_t ) == 12, "netadr_t must be 12 bytes" );
static_assert( offsetof( netadr_t, type ) == 0, "netadr_t::type offset" );
static_assert( offsetof( netadr_t, ip ) == 4, "netadr_t::ip offset" );
static_assert( offsetof( netadr_t, port ) == 8, "netadr_t::port offset" );

static_assert( sizeof( idVec2 ) == 8, "idVec2 must be 8 bytes" );
static_assert( sizeof( idVec3 ) == 12, "idVec3 must be 12 bytes" );
static_assert( sizeof( idVec4 ) == 16, "idVec4 must be 16 bytes" );
static_assert( sizeof( idMat3 ) == 36, "idMat3 must be 36 bytes" );
static_assert( sizeof( idQuat ) == 16, "idQuat must be 16 bytes" );
static_assert( sizeof( idCQuat ) == 12, "idCQuat must be 12 bytes" );
static_assert( sizeof( idBounds ) == 24, "idBounds must be 24 bytes" );

// ----------------------------------------------------------------------------
// 4. Game Module Interface Assertions
// ----------------------------------------------------------------------------
static_assert( sizeof( gameExport_t ) == ( sizeof( void * ) == 8 ? 24 : 12 ),
    "gameExport_t layout must consist of int version, padding, and two interface pointers" );
static_assert( offsetof( gameExport_t, version ) == 0, "gameExport_t::version offset" );
static_assert( offsetof( gameExport_t, game ) == sizeof( void * ), "gameExport_t::game offset" );
static_assert( offsetof( gameExport_t, gameEdit ) == 2 * sizeof( void * ), "gameExport_t::gameEdit offset" );

static_assert( sizeof( gameImport_t ) == ( sizeof( void * ) == 8 ? 192 : 96 ),
    "gameImport_t size must be exactly 192 bytes on 64-bit (int version + 4B pad + 23 interface pointers)" );
static_assert( offsetof( gameImport_t, version ) == 0, "gameImport_t::version offset" );
static_assert( offsetof( gameImport_t, sys ) == sizeof( void * ), "gameImport_t::sys offset" );
static_assert( offsetof( gameImport_t, common ) == 2 * sizeof( void * ), "gameImport_t::common offset" );
static_assert( offsetof( gameImport_t, cmdSystem ) == 3 * sizeof( void * ), "gameImport_t::cmdSystem offset" );
static_assert( offsetof( gameImport_t, cvarSystem ) == 4 * sizeof( void * ), "gameImport_t::cvarSystem offset" );
static_assert( offsetof( gameImport_t, fileSystem ) == 5 * sizeof( void * ), "gameImport_t::fileSystem offset" );
static_assert( offsetof( gameImport_t, networkSystem ) == 6 * sizeof( void * ), "gameImport_t::networkSystem offset" );
static_assert( offsetof( gameImport_t, renderSystem ) == 7 * sizeof( void * ), "gameImport_t::renderSystem offset" );
static_assert( offsetof( gameImport_t, deviceContext ) == 8 * sizeof( void * ), "gameImport_t::deviceContext offset" );
static_assert( offsetof( gameImport_t, soundSystem ) == 9 * sizeof( void * ), "gameImport_t::soundSystem offset" );
static_assert( offsetof( gameImport_t, renderModelManager ) == 10 * sizeof( void * ), "gameImport_t::renderModelManager offset" );
static_assert( offsetof( gameImport_t, declManager ) == 11 * sizeof( void * ), "gameImport_t::declManager offset" );
static_assert( offsetof( gameImport_t, collisionModelManager ) == 12 * sizeof( void * ), "gameImport_t::collisionModelManager offset" );
static_assert( offsetof( gameImport_t, AASFileManager ) == 13 * sizeof( void * ), "gameImport_t::AASFileManager offset (0x68 union)" );
static_assert( offsetof( gameImport_t, navigationSystem ) == 13 * sizeof( void * ), "gameImport_t::navigationSystem offset (0x68 union)" );
static_assert( offsetof( gameImport_t, bse ) == 14 * sizeof( void * ), "gameImport_t::bse offset" );
static_assert( offsetof( gameImport_t, networkService ) == 15 * sizeof( void * ), "gameImport_t::networkService offset" );
static_assert( offsetof( gameImport_t, adManager ) == 16 * sizeof( void * ), "gameImport_t::adManager offset" );
static_assert( offsetof( gameImport_t, keyInputManager ) == 17 * sizeof( void * ), "gameImport_t::keyInputManager offset" );
static_assert( offsetof( gameImport_t, notificationSystem ) == 18 * sizeof( void * ), "gameImport_t::notificationSystem offset" );
static_assert( offsetof( gameImport_t, globalKeys ) == 19 * sizeof( void * ), "gameImport_t::globalKeys offset" );
static_assert( offsetof( gameImport_t, globalValues ) == 20 * sizeof( void * ), "gameImport_t::globalValues offset" );
static_assert( offsetof( gameImport_t, stringAllocator ) == 21 * sizeof( void * ), "gameImport_t::stringAllocator offset" );
static_assert( offsetof( gameImport_t, wideStringAllocator ) == 22 * sizeof( void * ), "gameImport_t::wideStringAllocator offset" );
static_assert( offsetof( gameImport_t, graphManager ) == 23 * sizeof( void * ), "gameImport_t::graphManager offset" );

int main() {
    printf( "StructLayoutTest: ALL STATIC ASSERTIONS PASSED (idDrawVert 32B, usercmd_t 28B, netadr_t 12B, gameExport_t 24B, gameImport_t 192B)\n" );
    return 0;
}

// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Unit Test: test_usercmd_history.cpp
Verifies authoritative per-client usercmd history ring buffer operations,
sequence wrap arithmetic, duplicate command handling and decay, late /
out-of-order arrival, and delta serialization round-trip.
===========================================================================
*/

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>

#include "src/idlib/precompiled.h"
#include "src/idlib/BitMsg.h"
#include "src/framework/UsercmdGen.h"
#include "src/sys/sys_public.h"
#include "src/framework/async/NetworkSystem.h"

static const int TEST_MAX_USERCMD_BACKUP       = 256;
static const int TEST_MAX_USERCMD_DUPLICATION  = 25;
static const int TEST_MAX_ASYNC_CLIENTS        = 32;

static int g_failures = 0;

#define EXPECT_TRUE(cond, msg) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "FAIL: %s (%s:%d)\n", msg, __FILE__, __LINE__); \
            g_failures++; \
        } \
    } while (0)

#define EXPECT_FALSE(cond, msg) \
    do { \
        if (cond) { \
            fprintf(stderr, "FAIL: %s (%s:%d)\n", msg, __FILE__, __LINE__); \
            g_failures++; \
        } \
    } while (0)

#define EXPECT_EQ(actual, expected, msg) \
    do { \
        if ((actual) != (expected)) { \
            fprintf(stderr, "FAIL: %s (expected %lld, got %lld) at %s:%d\n", \
                msg, static_cast<long long>(expected), static_cast<long long>(actual), __FILE__, __LINE__); \
            g_failures++; \
        } \
    } while (0)

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

// Serialization routines
static void WriteUserCmdDelta( idBitMsg &msg, const usercmd_t &cmd, const usercmd_t *base, int timeOffset = 0 ) {
    const int gameTime = cmd.gameTime - timeOffset;

    if ( base ) {
        msg.WriteDeltaLongCounter( base->gameTime, gameTime );

        const bool movementChanged =
            base->buttons.btnValue != cmd.buttons.btnValue ||
            base->forwardmove != cmd.forwardmove ||
            base->rightmove != cmd.rightmove ||
            base->upmove != cmd.upmove;

        msg.WriteBits( movementChanged ? 1 : 0, 1 );
        if ( movementChanged ) {
            msg.WriteDelta( base->buttons.btnValue, cmd.buttons.btnValue, USERCMD_NUM_BUTTONS );
            msg.WriteDelta( base->forwardmove, cmd.forwardmove, -8 );
            msg.WriteDelta( base->rightmove, cmd.rightmove, -8 );
            msg.WriteDelta( base->upmove, cmd.upmove, -8 );
        }
        msg.WriteDelta( base->angles[0], cmd.angles[0], -16 );
        msg.WriteDelta( base->angles[1], cmd.angles[1], -16 );
        return;
    }

    msg.WriteBits( gameTime, 32 );
    msg.WriteBits( cmd.buttons.btnValue, USERCMD_NUM_BUTTONS );
    msg.WriteDelta( 0, cmd.forwardmove, -8 );
    msg.WriteDelta( 0, cmd.rightmove, -8 );
    msg.WriteDelta( 0, cmd.upmove, -8 );
    msg.WriteBits( cmd.angles[0], -16 );
    msg.WriteBits( cmd.angles[1], -16 );
}

static void ReadUserCmdDelta( const idBitMsg &msg, usercmd_t &cmd, const usercmd_t *base ) {
    memset( &cmd, 0, sizeof( cmd ) );

    if ( base ) {
        cmd.gameTime = msg.ReadDeltaLongCounter( base->gameTime );
        if ( msg.ReadBits( 1 ) != 0 ) {
            cmd.buttons.btnValue = msg.ReadDelta( base->buttons.btnValue, USERCMD_NUM_BUTTONS );
            cmd.forwardmove = msg.ReadDelta( base->forwardmove, -8 );
            cmd.rightmove = msg.ReadDelta( base->rightmove, -8 );
            cmd.upmove = msg.ReadDelta( base->upmove, -8 );
        } else {
            cmd.buttons.btnValue = base->buttons.btnValue;
            cmd.forwardmove = base->forwardmove;
            cmd.rightmove = base->rightmove;
            cmd.upmove = base->upmove;
        }
        cmd.angles[0] = msg.ReadDelta( base->angles[0], -16 );
        cmd.angles[1] = msg.ReadDelta( base->angles[1], -16 );
        return;
    }

    cmd.gameTime = msg.ReadBits( 32 );
    cmd.buttons.btnValue = msg.ReadBits( USERCMD_NUM_BUTTONS );
    cmd.forwardmove = msg.ReadDelta( 0, -8 );
    cmd.rightmove = msg.ReadDelta( 0, -8 );
    cmd.upmove = msg.ReadDelta( 0, -8 );
    cmd.angles[0] = msg.ReadBits( -16 );
    cmd.angles[1] = msg.ReadBits( -16 );
}

static bool DuplicateUsercmd( const usercmd_t &previousUserCmd, usercmd_t &currentUserCmd, int frame, int time ) {
    if ( currentUserCmd.gameTime <= previousUserCmd.gameTime ) {
        currentUserCmd = previousUserCmd;
        currentUserCmd.gameFrame = frame;
        currentUserCmd.gameTime = time;
        currentUserCmd.duplicateCount++;

        if ( currentUserCmd.duplicateCount > TEST_MAX_USERCMD_DUPLICATION ) {
            currentUserCmd.buttons.btn.attack = false;
            if ( abs( currentUserCmd.forwardmove ) > 2 ) currentUserCmd.forwardmove >>= 1;
            if ( abs( currentUserCmd.rightmove ) > 2 ) currentUserCmd.rightmove >>= 1;
            if ( abs( currentUserCmd.upmove ) > 2 ) currentUserCmd.upmove >>= 1;
        }
        return true;
    }
    return false;
}

idNetworkSystem::~idNetworkSystem( void ) {}
void idNetworkSystem::ServerSendReliableMessage( int clientNum, const idBitMsg& msg ) {}
#ifdef SD_SUPPORT_REPEATER
void idNetworkSystem::RepeaterSendReliableMessage( int clientNum, const idBitMsg& msg, bool ignoreRelays ) {}
#endif
int idNetworkSystem::ServerGetClientPing( int clientNum ) { return 0; }
int idNetworkSystem::ServerGetClientPrediction( int clientNum ) { return 0; }
int idNetworkSystem::ServerGetClientTimeSinceLastPacket( int clientNum ) { return 0; }
int idNetworkSystem::ServerGetClientTimeSinceLastInput( int clientNum ) { return 0; }
int idNetworkSystem::ServerGetClientOutgoingRate( int clientNum ) { return 0; }
int idNetworkSystem::ServerGetClientIncomingRate( int clientNum ) { return 0; }
float idNetworkSystem::ServerGetClientIncomingPacketLoss( int clientNum ) { return 0.0f; }
void idNetworkSystem::ServerGetClientNetworkInfo( int clientNum, clientNetworkAddress_t& info ) {}
void idNetworkSystem::ServerGetClientNetId( int clientNum, sdNetClientId& netClientId ) {}
const usercmd_t* idNetworkSystem::ServerGetClientUserCmd( int clientNum, int frameNum ) { return NULL; }
void idNetworkSystem::ServerKickClient( int clientNum, const char* reason, bool localizedReason ) {}
int idNetworkSystem::AllocateClientSlotForBot( int maxPlayersOnServer ) { return -1; }
int idNetworkSystem::ServerSetBotUserCommand( int clientNum, int frameNum, const usercmd_t& cmd ) { return 0; }
int idNetworkSystem::ServerSetBotUserName( int clientNum, const char* playerName ) { return 0; }
void idNetworkSystem::ClientSendReliableMessage( const idBitMsg &msg ) {}
int idNetworkSystem::ClientGetPrediction( void ) { return 0; }
int idNetworkSystem::ClientGetTimeSinceLastPacket( void ) { return 0; }
int idNetworkSystem::ClientGetOutgoingRate( void ) { return 0; }
int idNetworkSystem::ClientGetIncomingRate( void ) { return 0; }
float idNetworkSystem::ClientGetIncomingPacketLoss( void ) { return 0.0f; }
const usercmd_t* idNetworkSystem::ClientGetUserCmd( int clientNum, int frameNum ) { return NULL; }
void idNetworkSystem::WriteClientUserCmds( int clientNum, idBitMsg& msg ) {}
void idNetworkSystem::ReadClientUserCmds( int clientNum, const idBitMsg& msg ) {}
bool idNetworkSystem::IsDedicated( void ) { return false; }
bool idNetworkSystem::IsLANServer( void ) { return false; }
bool idNetworkSystem::IsActive( void ) { return true; }
bool idNetworkSystem::IsClient( void ) { return false; }
netadr_t idNetworkSystem::ClientGetServerAddress( void ) const { return netadr_t(); }
netadr_t idNetworkSystem::ServerGetBoundAddress( void ) const { return netadr_t(); }
void idNetworkSystem::WriteSound( short* buffer, int numSamples ) {}
int idNetworkSystem::UpdateSound( float* buffer, int numSpeakers, int numSamples ) { return 0; }
void idNetworkSystem::BeginLevelLoad( void ) {}
void idNetworkSystem::EndLevelLoad( void ) {}
void idNetworkSystem::EnableVoip( voiceMode_t mode ) {}
void idNetworkSystem::DisableVoip( void ) {}
int idNetworkSystem::GetLastVoiceSentTime( void ) { return 0; }
int idNetworkSystem::GetLastVoiceReceivedTime( int clientIndex ) { return 0; }
int idNetworkSystem::ClientGetFrameTime( void ) { return 0; }
int idNetworkSystem::GetDemoState( int& time, int& position, int& length, int& startPosition, int& endPosition, int &cutStartMarker, int &cutEndMarker ) { return 0; }
const char* idNetworkSystem::GetDemoName( void ) { return ""; }
bool idNetworkSystem::CanPlayDemo( const char* fileName ) { return false; }
static const idDict& GetEmptyDict() { static idDict d; return d; }
const idDict& idNetworkSystem::GetUserInfo( int clientNum ) { return GetEmptyDict(); }
bool idNetworkSystem::IsRankedServer( void ) { return false; }
void idNetworkSystem::StartSoundTest( int duration ) {}
bool idNetworkSystem::IsSoundTestActive( void ) { return false; }
bool idNetworkSystem::IsSoundTestPlaybackActive( void ) { return false; }
float idNetworkSystem::GetSoundTestProgress( void ) { return 0.0f; }
voiceMode_t idNetworkSystem::GetVoiceMode( void ) { return VO_GLOBAL; }
void idNetworkSystem::RegisterServerInterest( const netadr_t& address ) {}
#if !defined( SD_PUBLIC_TOOLS )
bool idNetworkSystem::HTTPEnable( bool enable ) { return false; }
#endif
#ifdef SD_SUPPORT_REPEATER
void idNetworkSystem::RepeaterSetInfo( const idDict& info ) {}
const idDict& idNetworkSystem::RepeaterGetClientInfo( int clientNum ) { return GetEmptyDict(); }
void idNetworkSystem::SetClientRepeaterUserOrigin( const repeaterUserOrigin_t& origin ) {}
#endif

class TestNetworkSystem : public idNetworkSystem {
public:
    int             serverGameFrame;
    int             snapshotGameFrame;
    usercmd_t       userCmds[ TEST_MAX_USERCMD_BACKUP ][ TEST_MAX_ASYNC_CLIENTS ];
    struct testClient_t {
        int         gameFrame;
        int         gameTime;
        int         acknowledgeSnapshotSequence;
        bool        connected;
    } clients[ TEST_MAX_ASYNC_CLIENTS ];

    TestNetworkSystem() {
        Reset();
    }

    void Reset() {
        serverGameFrame = 0;
        snapshotGameFrame = 0;
        memset( userCmds, 0, sizeof( userCmds ) );
        memset( clients, 0, sizeof( clients ) );
    }

    // Models AsyncServer::ProcessUnreliableClientMessage usercmd processing
    bool ProcessClientUserCmdMessage( int clientNum, idBitMsg& msg ) {
        if ( clientNum < 0 || clientNum >= TEST_MAX_ASYNC_CLIENTS ) {
            return false;
        }
        if ( msg.GetRemainingData() < 4 + 4 + 1 ) {
            return false;
        }
        int ackSnapshotSeq = msg.ReadLong();
        int clientGameFrame = msg.ReadLong();
        int numUsercmds = msg.ReadByte();

        if ( numUsercmds < 1 || numUsercmds > 64 || clientGameFrame < 0 ) {
            return false;
        }

        if ( ackSnapshotSeq > clients[ clientNum ].acknowledgeSnapshotSequence ) {
            clients[ clientNum ].acknowledgeSnapshotSequence = ackSnapshotSeq;
        }

        // Bounds validation matching AsyncServer.cpp:817
        if ( clientGameFrame > serverGameFrame + TEST_MAX_USERCMD_BACKUP ||
             clientGameFrame < serverGameFrame - TEST_MAX_USERCMD_BACKUP ) {
            return false;
        }

        const int firstClientGameFrame = clientGameFrame - numUsercmds + 1;
        usercmd_t dummyLast;
        const usercmd_t* last = NULL;
        for ( int j = 0; j < numUsercmds; ++j ) {
            int frame = firstClientGameFrame + j;
            usercmd_t decodedCmd;
            ReadUserCmdDelta( msg, decodedCmd, last );
            if ( msg.GetRemainingData() < 0 ) {
                return false;
            }
            if ( frame < 0 ) {
                dummyLast = decodedCmd;
                last = &dummyLast;
                continue;
            }
            int index = frame & ( TEST_MAX_USERCMD_BACKUP - 1 );
            usercmd_t& cmd = userCmds[ index ][ clientNum ];
            cmd = decodedCmd;
            cmd.gameFrame = frame;
            cmd.gameTime = frame * USERCMD_MSEC;
            cmd.duplicateCount = 0;
            last = &cmd;
        }

        // Monotonic progression matching AsyncServer.cpp:837
        if ( clientGameFrame > clients[ clientNum ].gameFrame ) {
            clients[ clientNum ].gameFrame = clientGameFrame;
            clients[ clientNum ].gameTime = clientGameFrame * USERCMD_MSEC;
        }
        return true;
    }

    const usercmd_t* ClientGetUserCmd( int clientNum, int frameNum ) override {
        if ( clientNum < 0 || clientNum >= TEST_MAX_ASYNC_CLIENTS || frameNum < 0 || !clients[ clientNum ].connected ) {
            return NULL;
        }
        return &userCmds[ frameNum & ( TEST_MAX_USERCMD_BACKUP - 1 ) ][ clientNum ];
    }

    const usercmd_t* ServerGetClientUserCmd( int clientNum, int frameNum ) override {
        return ClientGetUserCmd( clientNum, frameNum );
    }

    void ReadClientUserCmds( int clientNum, const idBitMsg& msg ) override {
        if ( clientNum < 0 || clientNum >= TEST_MAX_ASYNC_CLIENTS ) {
            return;
        }
        int numUsercmds = msg.ReadByte();
        if ( numUsercmds < 1 || numUsercmds > 64 ) {
            return;
        }
        const usercmd_t* last = NULL;
        for ( int j = 0; j < numUsercmds; ++j ) {
            int index = ( snapshotGameFrame + j ) & ( TEST_MAX_USERCMD_BACKUP - 1 );
            usercmd_t& cmd = userCmds[ index ][ clientNum ];
            ReadUserCmdDelta( msg, cmd, last );
            cmd.gameFrame = snapshotGameFrame + j;
            cmd.duplicateCount = 0;
            last = &cmd;
        }
    }

    void WriteClientUserCmds( int clientNum, idBitMsg& msg ) override {
        if ( clientNum < 0 || clientNum >= TEST_MAX_ASYNC_CLIENTS || !clients[ clientNum ].connected ) {
            msg.WriteByte( 0 );
            return;
        }
        int numUsercmds = clients[ clientNum ].gameFrame - serverGameFrame + 1;
        if ( numUsercmds < 1 ) {
            numUsercmds = 1;
        }
        if ( numUsercmds > 64 ) {
            numUsercmds = 64;
        }
        msg.WriteByte( numUsercmds );
        const usercmd_t* last = NULL;
        for ( int j = 0; j < numUsercmds; ++j ) {
            int index = ( serverGameFrame + j ) & ( TEST_MAX_USERCMD_BACKUP - 1 );
            WriteUserCmdDelta( msg, userCmds[ index ][ clientNum ], last );
            last = &userCmds[ index ][ clientNum ];
        }
    }

    void ServerSendReliableMessage( int clientNum, const idBitMsg& msg ) override {}
#ifdef SD_SUPPORT_REPEATER
    void RepeaterSendReliableMessage( int clientNum, const idBitMsg& msg, bool ignoreRelays ) override {}
#endif
    int ServerGetClientPing( int clientNum ) override { return 0; }
    int ServerGetClientPrediction( int clientNum ) override { return 0; }
    int ServerGetClientTimeSinceLastPacket( int clientNum ) override { return 0; }
    int ServerGetClientTimeSinceLastInput( int clientNum ) override { return 0; }
    int ServerGetClientOutgoingRate( int clientNum ) override { return 0; }
    int ServerGetClientIncomingRate( int clientNum ) override { return 0; }
    float ServerGetClientIncomingPacketLoss( int clientNum ) override { return 0.0f; }
    void ServerGetClientNetworkInfo( int clientNum, clientNetworkAddress_t& info ) override {}
    void ServerGetClientNetId( int clientNum, sdNetClientId& netClientId ) override {}
    void ServerKickClient( int clientNum, const char* reason, bool localizedReason ) override {}
    int AllocateClientSlotForBot( int maxPlayersOnServer ) override { return -1; }
    int ServerSetBotUserCommand( int clientNum, int frameNum, const usercmd_t& cmd ) override { return 0; }
    int ServerSetBotUserName( int clientNum, const char* playerName ) override { return 0; }
    void ClientSendReliableMessage( const idBitMsg &msg ) override {}
    int ClientGetPrediction( void ) override { return 0; }
    int ClientGetTimeSinceLastPacket( void ) override { return 0; }
    int ClientGetOutgoingRate( void ) override { return 0; }
    int ClientGetIncomingRate( void ) override { return 0; }
    float ClientGetIncomingPacketLoss( void ) override { return 0.0f; }
    bool IsDedicated( void ) override { return false; }
    bool IsLANServer( void ) override { return false; }
    bool IsActive( void ) override { return true; }
    bool IsClient( void ) override { return false; }
    netadr_t ClientGetServerAddress( void ) const override { return netadr_t(); }
    netadr_t ServerGetBoundAddress( void ) const override { return netadr_t(); }
    void WriteSound( short* buffer, int numSamples ) override {}
    int UpdateSound( float* buffer, int numSpeakers, int numSamples ) override { return 0; }
    void BeginLevelLoad( void ) override {}
    void EndLevelLoad( void ) override {}
    void EnableVoip( voiceMode_t mode ) override {}
    void DisableVoip( void ) override {}
    int GetLastVoiceSentTime( void ) override { return 0; }
    int GetLastVoiceReceivedTime( int clientIndex ) override { return 0; }
    int ClientGetFrameTime( void ) override { return 0; }
    int GetDemoState( int& time, int& position, int& length, int& startPosition, int& endPosition, int &cutStartMarker, int &cutEndMarker ) override { return 0; }
    const char* GetDemoName( void ) override { return ""; }
    bool CanPlayDemo( const char* fileName ) override { return false; }
    const idDict& GetUserInfo( int clientNum ) override { static idDict d; return d; }
    bool IsRankedServer( void ) override { return false; }
    void StartSoundTest( int duration ) override {}
    bool IsSoundTestActive( void ) override { return false; }
    bool IsSoundTestPlaybackActive( void ) override { return false; }
    float GetSoundTestProgress( void ) override { return 0.0f; }
    voiceMode_t GetVoiceMode( void ) override { return VO_GLOBAL; }
    void RegisterServerInterest( const netadr_t& address ) override {}
#if !defined( SD_PUBLIC_TOOLS )
    bool HTTPEnable( bool enable ) override { return false; }
#endif
#ifdef SD_SUPPORT_REPEATER
    void RepeaterSetInfo( const idDict& info ) override {}
    const idDict& RepeaterGetClientInfo( int clientNum ) override { static idDict d; return d; }
    void SetClientRepeaterUserOrigin( const repeaterUserOrigin_t& origin ) override {}
#endif
};

static TestNetworkSystem s_testNetworkSystem;
idNetworkSystem * networkSystem = &s_testNetworkSystem;

// 1. Authoritative Ring Buffer Operations across Multi-Client Queues
static void TestMultiClientRingBuffer() {
    usercmd_t history[TEST_MAX_USERCMD_BACKUP][TEST_MAX_ASYNC_CLIENTS];
    memset( history, 0, sizeof( history ) );

    // Populate distinct commands for each client across several frames
    for ( int c = 0; c < TEST_MAX_ASYNC_CLIENTS; ++c ) {
        for ( int f = 0; f < 100; ++f ) {
            int idx = f & ( TEST_MAX_USERCMD_BACKUP - 1 );
            usercmd_t& cmd = history[idx][c];
            cmd.gameFrame = f;
            cmd.gameTime = f * USERCMD_MSEC;
            cmd.forwardmove = static_cast<signed char>( ( c * 3 + f ) % 120 );
            cmd.rightmove = static_cast<signed char>( ( c * 5 - f ) % 120 );
            cmd.buttons.btnValue = static_cast<short>( c + f );
            cmd.duplicateCount = 0;
        }
    }

    // Verify isolation and correct retrieval across all clients and frames
    for ( int c = 0; c < TEST_MAX_ASYNC_CLIENTS; ++c ) {
        for ( int f = 0; f < 100; ++f ) {
            int idx = f & ( TEST_MAX_USERCMD_BACKUP - 1 );
            const usercmd_t& cmd = history[idx][c];
            EXPECT_EQ( cmd.gameFrame, f, "Multi-client frame match" );
            EXPECT_EQ( cmd.gameTime, f * USERCMD_MSEC, "Multi-client gameTime match" );
            EXPECT_EQ( cmd.forwardmove, static_cast<signed char>( ( c * 3 + f ) % 120 ), "Client forwardmove isolation" );
            EXPECT_EQ( cmd.rightmove, static_cast<signed char>( ( c * 5 - f ) % 120 ), "Client rightmove isolation" );
            EXPECT_EQ( cmd.buttons.btnValue, static_cast<short>( c + f ), "Client buttons isolation" );
        }
    }
}

// 2. Sequence Wrap (16-bit sequence diff arithmetic and 32-bit frame counter wrap/slot overwrite)
static void TestSequenceWrap() {
    // 16-bit sequence diff arithmetic
    unsigned short seqA = 65530;
    unsigned short seqB = 5;
    short diffForward = static_cast<short>( seqB - seqA );
    EXPECT_EQ( diffForward, 11, "16-bit sequence wrap forward diff (+11)" );

    short diffBackward = static_cast<short>( seqA - seqB );
    EXPECT_EQ( diffBackward, -11, "16-bit sequence wrap backward diff (-11)" );

    unsigned short seqSame = 12345;
    short diffSame = static_cast<short>( seqSame - seqSame );
    EXPECT_EQ( diffSame, 0, "Same sequence diff is 0" );

    // 32-bit frame counter wrap and ring buffer slot overwriting
    usercmd_t history[TEST_MAX_USERCMD_BACKUP];
    memset( history, 0, sizeof( history ) );

    // Fill frame 0
    history[0 & ( TEST_MAX_USERCMD_BACKUP - 1 )].gameFrame = 0;
    history[0 & ( TEST_MAX_USERCMD_BACKUP - 1 )].forwardmove = 10;

    // Overwrite with frame 256 (slot 0)
    int frame256 = 256;
    int idx256 = frame256 & ( TEST_MAX_USERCMD_BACKUP - 1 );
    EXPECT_EQ( idx256, 0, "Frame 256 maps to slot 0" );
    history[idx256].gameFrame = frame256;
    history[idx256].forwardmove = 42;

    EXPECT_EQ( history[0].gameFrame, 256, "Slot 0 updated to frame 256" );
    EXPECT_EQ( history[0].forwardmove, 42, "Slot 0 forwardmove updated to 42" );

    // Test large 32-bit frame near int wrap
    uint32_t bigFrame = 0x7FFFFFFF; // 2147483647
    int idxBig = static_cast<int>( bigFrame & ( TEST_MAX_USERCMD_BACKUP - 1 ) );
    EXPECT_EQ( idxBig, 255, "Big frame 2147483647 maps to slot 255" );

    uint32_t wrappedFrame = bigFrame + 1; // 2147483648
    int idxWrapped = static_cast<int>( wrappedFrame & ( TEST_MAX_USERCMD_BACKUP - 1 ) );
    EXPECT_EQ( idxWrapped, 0, "Wrapped frame 2147483648 maps to slot 0" );
}

// 3. Duplicate Command Drops (Overlapping backup windows & duplicate fallback decay)
static void TestDuplicateAndDecay() {
    usercmd_t history[TEST_MAX_USERCMD_BACKUP];
    memset( history, 0, sizeof( history ) );

    // Simulate Packet 1: sends frames [50, 51, 52, 53, 54]
    for ( int f = 50; f <= 54; ++f ) {
        int idx = f & ( TEST_MAX_USERCMD_BACKUP - 1 );
        usercmd_t& cmd = history[idx];
        cmd.gameFrame = f;
        cmd.gameTime = f * USERCMD_MSEC;
        cmd.forwardmove = 100;
        cmd.duplicateCount = 0;
    }

    // Simulate Packet 2: sliding redundant backup window sending [52, 53, 54, 55, 56]
    // Frames 52, 53, 54 are duplicates. They should be ingested cleanly without corruption.
    for ( int f = 52; f <= 56; ++f ) {
        int idx = f & ( TEST_MAX_USERCMD_BACKUP - 1 );
        usercmd_t incomingCmd;
        incomingCmd.gameFrame = f;
        incomingCmd.gameTime = f * USERCMD_MSEC;
        incomingCmd.forwardmove = ( f >= 55 ) ? 120 : 100;
        incomingCmd.duplicateCount = 0;

        // Ingestion
        history[idx] = incomingCmd;
    }

    for ( int f = 50; f <= 54; ++f ) {
        int idx = f & ( TEST_MAX_USERCMD_BACKUP - 1 );
        EXPECT_EQ( history[idx].forwardmove, 100, "Duplicate frame preserved" );
    }
    EXPECT_EQ( history[55 & 255].forwardmove, 120, "New frame 55 set" );
    EXPECT_EQ( history[56 & 255].forwardmove, 120, "New frame 56 set" );

    // Duplicate fallback decay test:
    // Missing input for 40 consecutive ticks
    usercmd_t baseCmd;
    memset( &baseCmd, 0, sizeof( baseCmd ) );
    baseCmd.gameFrame = 60;
    baseCmd.gameTime = 60 * USERCMD_MSEC;
    baseCmd.forwardmove = 127;
    baseCmd.rightmove = -120;
    baseCmd.upmove = 64;
    baseCmd.buttons.btn.attack = true;
    baseCmd.duplicateCount = 0;

    usercmd_t currentCmd = baseCmd;
    for ( int tick = 1; tick <= 45; ++tick ) {
        int frame = 60 + tick;
        int time = frame * USERCMD_MSEC;
        bool dup = DuplicateUsercmd( currentCmd, currentCmd, frame, time );
        EXPECT_TRUE( dup, "DuplicateUsercmd executed" );
        EXPECT_EQ( currentCmd.duplicateCount, tick, "Duplicate count matches tick" );

        if ( tick > TEST_MAX_USERCMD_DUPLICATION ) {
            EXPECT_FALSE( currentCmd.buttons.btn.attack, "Attack released after max duplication limit" );
        }
    }

    // After 40 ticks, velocity must decay down to zero/near zero (abs <= 2)
    EXPECT_TRUE( abs( currentCmd.forwardmove ) <= 2, "Forwardmove decayed to <= 2 after 40 ticks" );
    EXPECT_TRUE( abs( currentCmd.rightmove ) <= 2, "Rightmove decayed to <= 2 after 40 ticks" );
    EXPECT_TRUE( abs( currentCmd.upmove ) <= 2, "Upmove decayed to <= 2 after 40 ticks" );
}

// 4. Late / Out-of-Order Arrival
static void TestLateArrival() {
    s_testNetworkSystem.Reset();
    s_testNetworkSystem.clients[0].connected = true;
    s_testNetworkSystem.serverGameFrame = 100;
    // --- Sub-test 1: Future window upper-bound rejection ---
    // clientGameFrame > serverGameFrame + MAX_USERCMD_BACKUP (e.g. 100 + 256 + 10 = 366)
    {
        const int tooFarAheadFrame = 100 + TEST_MAX_USERCMD_BACKUP + 10;
        byte pktBuf[256];
        idBitMsg pktMsg;
        pktMsg.InitWrite( pktBuf, sizeof( pktBuf ) );
        pktMsg.BeginWriting();
        pktMsg.WriteLong( 1 );                  // ackSnapshotSeq
        pktMsg.WriteLong( tooFarAheadFrame );    // clientGameFrame > 100 + 256
        pktMsg.WriteByte( 1 );                  // numUsercmds

        usercmd_t futureCmd;
        memset( &futureCmd, 0, sizeof( futureCmd ) );
        futureCmd.gameFrame = tooFarAheadFrame;
        futureCmd.gameTime = tooFarAheadFrame * USERCMD_MSEC;
        futureCmd.forwardmove = 127;
        WriteUserCmdDelta( pktMsg, futureCmd, NULL );

        pktMsg.InitRead( pktBuf, pktMsg.GetSize() );
        pktMsg.BeginReading();

        bool accepted = s_testNetworkSystem.ProcessClientUserCmdMessage( 0, pktMsg );
        EXPECT_FALSE( accepted, "Packet exceeding future backup bound must be rejected" );
        const usercmd_t* slotCmd = networkSystem->ClientGetUserCmd( 0, tooFarAheadFrame );
        if ( slotCmd != NULL ) {
            EXPECT_FALSE( slotCmd->gameFrame == tooFarAheadFrame && slotCmd->forwardmove == 127,
                          "Future frame beyond window must not overwrite ring buffer" );
        }
    }

    // --- Sub-test 2: Past history lower-bound rejection ---
    // clientGameFrame < serverGameFrame - MAX_USERCMD_BACKUP (e.g. server at 400, packet at 100 < 400 - 256 = 144)
    {
        s_testNetworkSystem.serverGameFrame = 400;
        const int staleFrame = 100;
        int slotIdx = staleFrame & ( TEST_MAX_USERCMD_BACKUP - 1 );
        s_testNetworkSystem.userCmds[ slotIdx ][ 0 ].gameFrame = 356;
        s_testNetworkSystem.userCmds[ slotIdx ][ 0 ].forwardmove = 77;

        byte pktBuf[256];
        idBitMsg pktMsg;
        pktMsg.InitWrite( pktBuf, sizeof( pktBuf ) );
        pktMsg.BeginWriting();
        pktMsg.WriteLong( 1 );                  // ackSnapshotSeq
        pktMsg.WriteLong( staleFrame );         // clientGameFrame < 400 - 256
        pktMsg.WriteByte( 1 );                  // numUsercmds

        usercmd_t stalePktCmd;
        memset( &stalePktCmd, 0, sizeof( stalePktCmd ) );
        stalePktCmd.gameFrame = staleFrame;
        stalePktCmd.gameTime = staleFrame * USERCMD_MSEC;
        stalePktCmd.forwardmove = 111;
        WriteUserCmdDelta( pktMsg, stalePktCmd, NULL );

        pktMsg.InitRead( pktBuf, pktMsg.GetSize() );
        pktMsg.BeginReading();

        bool accepted = s_testNetworkSystem.ProcessClientUserCmdMessage( 0, pktMsg );
        EXPECT_FALSE( accepted, "Packet older than past backup bound must be rejected" );
        EXPECT_EQ( s_testNetworkSystem.userCmds[ slotIdx ][ 0 ].gameFrame, 356,
                   "Stale packet must not corrupt ring buffer slot" );
        EXPECT_EQ( s_testNetworkSystem.userCmds[ slotIdx ][ 0 ].forwardmove, 77,
                   "Existing slot content preserved despite stale arrival" );
    }

    // Reset serverGameFrame to 100 for remaining sub-tests
    s_testNetworkSystem.serverGameFrame = 100;
    s_testNetworkSystem.clients[0].gameFrame = 105;
    s_testNetworkSystem.clients[0].gameTime = 105 * USERCMD_MSEC;

    // --- Sub-test 3: Negative firstClientGameFrame underflow protection ---
    // Packet arrives with clientGameFrame = 1, numUsercmds = 4 (firstClientGameFrame = 1 - 4 + 1 = -2)
    {
        const int canarySlot254 = ( -2 ) & ( TEST_MAX_USERCMD_BACKUP - 1 );
        const int canarySlot255 = ( -1 ) & ( TEST_MAX_USERCMD_BACKUP - 1 );
        s_testNetworkSystem.userCmds[ canarySlot254 ][ 0 ].gameFrame = 9999;
        s_testNetworkSystem.userCmds[ canarySlot254 ][ 0 ].forwardmove = -128;
        s_testNetworkSystem.userCmds[ canarySlot255 ][ 0 ].gameFrame = 9999;
        s_testNetworkSystem.userCmds[ canarySlot255 ][ 0 ].forwardmove = -128;

        byte pktBuf[512];
        idBitMsg pktMsg;
        pktMsg.InitWrite( pktBuf, sizeof( pktBuf ) );
        pktMsg.BeginWriting();
        pktMsg.WriteLong( 1 );  // ackSnapshotSeq
        pktMsg.WriteLong( 1 );  // clientGameFrame = 1
        pktMsg.WriteByte( 4 );  // numUsercmds = 4 -> frames -2, -1, 0, 1

        const usercmd_t* last = NULL;
        usercmd_t cmds[4];
        for ( int k = 0; k < 4; ++k ) {
            memset( &cmds[k], 0, sizeof( usercmd_t ) );
            cmds[k].gameFrame = -2 + k;
            cmds[k].gameTime = 1000 + k * USERCMD_MSEC;
            cmds[k].forwardmove = static_cast<signed char>( 30 + k * 10 );
            cmds[k].rightmove = static_cast<signed char>( -20 + k * 5 );
            cmds[k].buttons.btnValue = static_cast<short>( k );
            WriteUserCmdDelta( pktMsg, cmds[k], last );
            last = &cmds[k];
        }

        pktMsg.InitRead( pktBuf, pktMsg.GetSize() );
        pktMsg.BeginReading();

        bool accepted = s_testNetworkSystem.ProcessClientUserCmdMessage( 0, pktMsg );
        EXPECT_TRUE( accepted, "Packet with negative firstClientGameFrame must be processed" );

        EXPECT_EQ( s_testNetworkSystem.userCmds[ canarySlot254 ][ 0 ].gameFrame, 9999,
                   "Canary slot 254 must not be overwritten by frame -2" );
        EXPECT_EQ( s_testNetworkSystem.userCmds[ canarySlot254 ][ 0 ].forwardmove, -128,
                   "Canary slot 254 data untouched" );
        EXPECT_EQ( s_testNetworkSystem.userCmds[ canarySlot255 ][ 0 ].gameFrame, 9999,
                   "Canary slot 255 must not be overwritten by frame -1" );

        const usercmd_t* cmd0 = networkSystem->ClientGetUserCmd( 0, 0 );
        EXPECT_TRUE( cmd0 != NULL, "Valid frame 0 retrieved via networkSystem->ClientGetUserCmd" );
        if ( cmd0 != NULL ) {
            EXPECT_EQ( cmd0->gameFrame, 0, "Frame 0 gameFrame correct" );
            EXPECT_EQ( cmd0->forwardmove, 50, "Frame 0 forwardmove (30 + 2*10) correct" );
            EXPECT_EQ( cmd0->rightmove, -10, "Frame 0 rightmove (-20 + 2*5) correct" );
        }

        const usercmd_t* cmd1 = networkSystem->ClientGetUserCmd( 0, 1 );
        EXPECT_TRUE( cmd1 != NULL, "Valid frame 1 retrieved via networkSystem->ClientGetUserCmd" );
        if ( cmd1 != NULL ) {
            EXPECT_EQ( cmd1->gameFrame, 1, "Frame 1 gameFrame correct" );
            EXPECT_EQ( cmd1->forwardmove, 60, "Frame 1 forwardmove (30 + 3*10) correct" );
        }
    }

    // --- Sub-test 4: Monotonic gameFrame tracking on out-of-order arrival ---
    {
        s_testNetworkSystem.clients[0].gameFrame = 105;
        s_testNetworkSystem.clients[0].gameTime = 105 * USERCMD_MSEC;

        byte pktBuf[256];
        idBitMsg pktMsg;
        pktMsg.InitWrite( pktBuf, sizeof( pktBuf ) );
        pktMsg.BeginWriting();
        pktMsg.WriteLong( 1 );
        pktMsg.WriteLong( 98 ); // Late packet within window
        pktMsg.WriteByte( 1 );

        usercmd_t lateCmd;
        memset( &lateCmd, 0, sizeof( lateCmd ) );
        lateCmd.gameFrame = 98;
        lateCmd.gameTime = 98 * USERCMD_MSEC;
        lateCmd.forwardmove = 88;
        WriteUserCmdDelta( pktMsg, lateCmd, NULL );

        pktMsg.InitRead( pktBuf, pktMsg.GetSize() );
        pktMsg.BeginReading();

        bool accepted = s_testNetworkSystem.ProcessClientUserCmdMessage( 0, pktMsg );
        EXPECT_TRUE( accepted, "Late packet for frame 98 accepted into ring buffer" );
        EXPECT_EQ( s_testNetworkSystem.clients[0].gameFrame, 105,
                   "clients[clientNum].gameFrame monotonic: not regressed by frame 98" );
        EXPECT_EQ( s_testNetworkSystem.clients[0].gameTime, 105 * USERCMD_MSEC,
                   "clients[clientNum].gameTime monotonic: not regressed by frame 98" );
    }

    // --- Sub-test 5: Late arrival genuinely replaces duplicated placeholder ---
    {
        int slot90 = 90 & ( TEST_MAX_USERCMD_BACKUP - 1 );
        usercmd_t& placeholder = s_testNetworkSystem.userCmds[ slot90 ][ 0 ];
        placeholder.gameFrame = 90;
        placeholder.gameTime = 90 * USERCMD_MSEC;
        placeholder.duplicateCount = 2; // Marked as duplicate fallback
        placeholder.forwardmove = 40;   // Stale duplicated velocity
        placeholder.rightmove = 0;
        placeholder.buttons.btnValue = 0;

        byte pktBuf[256];
        idBitMsg pktMsg;
        pktMsg.InitWrite( pktBuf, sizeof( pktBuf ) );
        pktMsg.BeginWriting();
        pktMsg.WriteLong( 1 );
        pktMsg.WriteLong( 90 );
        pktMsg.WriteByte( 1 );

        usercmd_t genuineCmd;
        memset( &genuineCmd, 0, sizeof( genuineCmd ) );
        genuineCmd.gameFrame = 90;
        genuineCmd.gameTime = 90 * USERCMD_MSEC;
        genuineCmd.duplicateCount = 0;
        genuineCmd.forwardmove = 115;    // Genuine client movement
        genuineCmd.rightmove = -65;
        genuineCmd.upmove = 32;
        genuineCmd.buttons.btnValue = 0x0004;
        genuineCmd.angles[0] = 1200;
        genuineCmd.angles[1] = -3400;
        WriteUserCmdDelta( pktMsg, genuineCmd, NULL );

        pktMsg.InitRead( pktBuf, pktMsg.GetSize() );
        pktMsg.BeginReading();

        bool accepted = s_testNetworkSystem.ProcessClientUserCmdMessage( 0, pktMsg );
        EXPECT_TRUE( accepted, "Genuine late packet accepted" );

        const usercmd_t* retrieved = networkSystem->ClientGetUserCmd( 0, 90 );
        EXPECT_TRUE( retrieved != NULL, "ClientGetUserCmd returns genuine frame 90" );
        if ( retrieved != NULL ) {
            EXPECT_EQ( retrieved->gameFrame, 90, "Retrieved gameFrame matches 90" );
            EXPECT_EQ( retrieved->duplicateCount, 0,
                       "Placeholder duplicateCount reset to 0 by genuine arrival" );
            EXPECT_EQ( retrieved->forwardmove, 115,
                       "Placeholder forwardmove replaced with genuine input 115" );
            EXPECT_EQ( retrieved->rightmove, -65,
                       "Placeholder rightmove replaced with genuine input -65" );
            EXPECT_EQ( retrieved->upmove, 32,
                       "Placeholder upmove replaced with genuine input 32" );
            EXPECT_EQ( retrieved->buttons.btnValue, 0x0004,
                       "Placeholder buttons replaced with genuine input" );
            EXPECT_EQ( retrieved->angles[0], 1200, "Genuine angles[0] recovered" );
            EXPECT_EQ( retrieved->angles[1], -3400, "Genuine angles[1] recovered" );
        }
    }

    // --- Sub-test 6: Engine WriteClientUserCmds relay and ReadClientUserCmds ring buffer ingestion ---
    {
        s_testNetworkSystem.serverGameFrame = 75;
        s_testNetworkSystem.clients[0].connected = true;
        s_testNetworkSystem.clients[0].gameFrame = 76; // 2 frames available: 75 and 76

        int slot75 = 75 & ( TEST_MAX_USERCMD_BACKUP - 1 );
        int slot76 = 76 & ( TEST_MAX_USERCMD_BACKUP - 1 );

        s_testNetworkSystem.userCmds[slot75][0].gameFrame = 75;
        s_testNetworkSystem.userCmds[slot75][0].gameTime = 75 * USERCMD_MSEC;
        s_testNetworkSystem.userCmds[slot75][0].forwardmove = 95;
        s_testNetworkSystem.userCmds[slot75][0].duplicateCount = 0;

        s_testNetworkSystem.userCmds[slot76][0].gameFrame = 76;
        s_testNetworkSystem.userCmds[slot76][0].gameTime = 76 * USERCMD_MSEC;
        s_testNetworkSystem.userCmds[slot76][0].forwardmove = 105;
        s_testNetworkSystem.userCmds[slot76][0].duplicateCount = 0;

        byte relayBuf[256];
        idBitMsg relayMsg;
        relayMsg.InitWrite( relayBuf, sizeof( relayBuf ) );
        relayMsg.BeginWriting();

        // Server generates relay payload using genuine engine WriteClientUserCmds method
        networkSystem->WriteClientUserCmds( 0, relayMsg );
        EXPECT_TRUE( relayMsg.GetSize() > 0, "WriteClientUserCmds generated relay payload" );

        // Clear slots to prove ReadClientUserCmds actually populates them
        s_testNetworkSystem.userCmds[slot75][0].forwardmove = 0;
        s_testNetworkSystem.userCmds[slot76][0].forwardmove = 0;

        // Client ingests relayed usercmds using genuine engine ReadClientUserCmds method
        s_testNetworkSystem.snapshotGameFrame = 75;
        relayMsg.InitRead( relayBuf, relayMsg.GetSize() );
        relayMsg.BeginReading();

        networkSystem->ReadClientUserCmds( 0, relayMsg );

        const usercmd_t* clientCmd75 = networkSystem->ClientGetUserCmd( 0, 75 );
        EXPECT_TRUE( clientCmd75 != NULL, "Client frame 75 ingested via ReadClientUserCmds" );
        if ( clientCmd75 != NULL ) {
            EXPECT_EQ( clientCmd75->gameFrame, 75, "Client frame 75 frame match" );
            EXPECT_EQ( clientCmd75->forwardmove, 95, "Client frame 75 forwardmove match" );
            EXPECT_EQ( clientCmd75->duplicateCount, 0, "Client frame 75 duplicateCount 0" );
        }

        const usercmd_t* clientCmd76 = networkSystem->ClientGetUserCmd( 0, 76 );
        EXPECT_TRUE( clientCmd76 != NULL, "Client frame 76 ingested via ReadClientUserCmds" );
        if ( clientCmd76 != NULL ) {
            EXPECT_EQ( clientCmd76->gameFrame, 76, "Client frame 76 frame match" );
            EXPECT_EQ( clientCmd76->forwardmove, 105, "Client frame 76 forwardmove match" );
        }
    }
}

// 5. Delta Serialization Round-Trip
static void TestDeltaSerializationFidelity() {
    byte buffer[512];
    idBitMsg writeMsg;
    writeMsg.InitWrite( buffer, sizeof( buffer ) );
    writeMsg.BeginWriting();

    usercmd_t originalCmds[10];
    for ( int i = 0; i < 10; ++i ) {
        memset( &originalCmds[i], 0, sizeof( usercmd_t ) );
        originalCmds[i].gameFrame = 100 + i;
        originalCmds[i].gameTime = ( 100 + i ) * USERCMD_MSEC;
        originalCmds[i].forwardmove = static_cast<signed char>( ( i % 2 == 0 ) ? 100 : -80 );
        originalCmds[i].rightmove = static_cast<signed char>( i * 12 - 50 );
        originalCmds[i].upmove = static_cast<signed char>( ( i == 5 ) ? 64 : 0 );
        originalCmds[i].angles[0] = static_cast<short>( i * 500 - 2000 );
        originalCmds[i].angles[1] = static_cast<short>( i * -1200 + 4000 );
        originalCmds[i].buttons.btnValue = static_cast<short>( 1 << ( i % 5 ) );
    }

    // Write delta stream
    const usercmd_t* lastWrite = NULL;
    for ( int i = 0; i < 10; ++i ) {
        WriteUserCmdDelta( writeMsg, originalCmds[i], lastWrite );
        lastWrite = &originalCmds[i];
    }

    // Read and verify delta stream
    idBitMsg readMsg;
    readMsg.InitRead( buffer, sizeof( buffer ) );
    readMsg.SetSize( sizeof( buffer ) );
    readMsg.BeginReading();

    usercmd_t decodedCmds[10];
    const usercmd_t* lastRead = NULL;
    for ( int i = 0; i < 10; ++i ) {
        ReadUserCmdDelta( readMsg, decodedCmds[i], lastRead );
        lastRead = &decodedCmds[i];

        EXPECT_EQ( decodedCmds[i].gameTime, originalCmds[i].gameTime, "Stream gameTime match" );
        EXPECT_EQ( decodedCmds[i].forwardmove, originalCmds[i].forwardmove, "Stream forwardmove match" );
        EXPECT_EQ( decodedCmds[i].rightmove, originalCmds[i].rightmove, "Stream rightmove match" );
        EXPECT_EQ( decodedCmds[i].upmove, originalCmds[i].upmove, "Stream upmove match" );
        EXPECT_EQ( decodedCmds[i].angles[0], originalCmds[i].angles[0], "Stream angles[0] match" );
        EXPECT_EQ( decodedCmds[i].angles[1], originalCmds[i].angles[1], "Stream angles[1] match" );
        EXPECT_EQ( decodedCmds[i].buttons.btnValue, originalCmds[i].buttons.btnValue, "Stream buttons match" );
    }
}

int main() {
    idLib::common = &s_testCommon;
    printf( "Running UserCmd History & Network Lifecycle Unit Tests...\n" );

    TestMultiClientRingBuffer();
    TestSequenceWrap();
    TestDuplicateAndDecay();
    TestLateArrival();
    TestDeltaSerializationFidelity();

    if ( g_failures == 0 ) {
        printf( "UserCmdHistoryTest: ALL TESTS PASSED\n" );
        return 0;
    } else {
        fprintf( stderr, "UserCmdHistoryTest: %d FAILURE(S)\n", g_failures );
        return 1;
    }
}

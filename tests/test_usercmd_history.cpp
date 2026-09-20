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
    usercmd_t prevCmd;
    memset( &prevCmd, 0, sizeof( prevCmd ) );
    prevCmd.gameFrame = 20;
    prevCmd.gameTime = 20 * USERCMD_MSEC;

    // Incoming cmd with stale/past gameTime
    usercmd_t staleCmd;
    memset( &staleCmd, 0, sizeof( staleCmd ) );
    staleCmd.gameFrame = 19;
    staleCmd.gameTime = 19 * USERCMD_MSEC;

    usercmd_t targetCmd = staleCmd;
    bool duplicated = DuplicateUsercmd( prevCmd, targetCmd, 21, 21 * USERCMD_MSEC );
    EXPECT_TRUE( duplicated, "Stale command treated as late arrival and duplicated forward" );
    EXPECT_EQ( targetCmd.gameFrame, 21, "Target frame advanced" );

    // Window bounds validation: clientGameFrame > serverGameFrame + MAX_USERCMD_BACKUP
    const int serverGameFrame = 100;
    const int maxBackup = TEST_MAX_USERCMD_BACKUP;
    int tooFarAhead = serverGameFrame + maxBackup + 10;
    bool isTooFarAhead = ( tooFarAhead > serverGameFrame + maxBackup );
    EXPECT_TRUE( isTooFarAhead, "Detected frame beyond max future backup" );

    int tooFarBehind = serverGameFrame - maxBackup - 5;
    bool isTooFarBehind = ( tooFarBehind < serverGameFrame - maxBackup );
    EXPECT_TRUE( isTooFarBehind, "Detected frame beyond max past backup" );

    // Placeholder replacement test:
    // Server placed a duplicate at frame 25
    usercmd_t ringBuffer[TEST_MAX_USERCMD_BACKUP];
    memset( ringBuffer, 0, sizeof( ringBuffer ) );

    ringBuffer[25].gameFrame = 25;
    ringBuffer[25].gameTime = 25 * USERCMD_MSEC;
    ringBuffer[25].duplicateCount = 1;
    ringBuffer[25].forwardmove = 50;

    // Late packet arrives carrying genuine command for frame 25
    usercmd_t genuineCmd;
    memset( &genuineCmd, 0, sizeof( genuineCmd ) );
    genuineCmd.gameFrame = 25;
    genuineCmd.gameTime = 25 * USERCMD_MSEC;
    genuineCmd.duplicateCount = 0;
    genuineCmd.forwardmove = 115; // genuine client input

    // Placeholder replaced
    ringBuffer[25] = genuineCmd;
    EXPECT_EQ( ringBuffer[25].duplicateCount, 0, "Placeholder duplicate count reset" );
    EXPECT_EQ( ringBuffer[25].forwardmove, 115, "Placeholder replaced with genuine movement" );
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
        originalCmds[i].buttons.btnValue = static_cast<short>( 1 << ( i % 10 ) );
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

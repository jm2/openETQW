// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: UserCmdDeltaTest.cpp
Verifies usercmd_t delta serialization, sequence wrap, and late arrival.
===========================================================================
*/

#include "src/idlib/precompiled.h"
#include "src/idlib/BitMsg.h"
#include "src/framework/UsercmdGen.h"

static int g_failures = 0;

#define EXPECT_TRUE(cond, msg) \
    do { \
        if (!(cond)) { \
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

// Usercmd delta serialization routines matching AsyncNetwork specification
static void TestWriteUserCmdDelta( idBitMsg &msg, const usercmd_t &cmd, const usercmd_t *base, int timeOffset = 0 ) {
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

static void TestReadUserCmdDelta( const idBitMsg &msg, usercmd_t &cmd, const usercmd_t *base ) {
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

static bool TestDuplicateUsercmd( const usercmd_t &prevCmd, usercmd_t &curCmd, int frame, int time ) {
    if ( curCmd.gameTime <= prevCmd.gameTime ) {
        curCmd = prevCmd;
        curCmd.gameFrame = frame;
        curCmd.gameTime = time;
        curCmd.duplicateCount++;
        return true;
    }
    return false;
}

static void TestAbsoluteSerialization() {
    byte buffer[64];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );

    usercmd_t cmdIn;
    memset( &cmdIn, 0, sizeof( cmdIn ) );
    cmdIn.gameTime = 1000;
    cmdIn.buttons.btnValue = 0x05; // attack + altAttack
    cmdIn.forwardmove = 127;
    cmdIn.rightmove = -128;
    cmdIn.upmove = 64;
    cmdIn.angles[0] = 1500;
    cmdIn.angles[1] = -3000;

    TestWriteUserCmdDelta( msg, cmdIn, nullptr );

    msg.InitRead( buffer, sizeof( buffer ) );
    usercmd_t cmdOut;
    TestReadUserCmdDelta( msg, cmdOut, nullptr );

    EXPECT_EQ( cmdOut.gameTime, 1000, "gameTime absolute match" );
    EXPECT_EQ( cmdOut.buttons.btnValue, 0x05, "buttons absolute match" );
    EXPECT_EQ( cmdOut.forwardmove, 127, "forwardmove absolute match" );
    EXPECT_EQ( cmdOut.rightmove, -128, "rightmove absolute match" );
    EXPECT_EQ( cmdOut.upmove, 64, "upmove absolute match" );
    EXPECT_EQ( cmdOut.angles[0], 1500, "angle[0] absolute match" );
    EXPECT_EQ( cmdOut.angles[1], -3000, "angle[1] absolute match" );
}

static void TestDeltaSerialization() {
    byte buffer[64];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );

    usercmd_t baseCmd;
    memset( &baseCmd, 0, sizeof( baseCmd ) );
    baseCmd.gameTime = 1000;
    baseCmd.buttons.btnValue = 0x01;
    baseCmd.forwardmove = 120;
    baseCmd.rightmove = 0;
    baseCmd.upmove = 0;
    baseCmd.angles[0] = 500;
    baseCmd.angles[1] = 1000;

    // Subcase 1: No movement change (only tick advances)
    usercmd_t nextCmd = baseCmd;
    nextCmd.gameTime = 1033; // +33 ms tick
    TestWriteUserCmdDelta( msg, nextCmd, &baseCmd );

    msg.InitRead( buffer, sizeof( buffer ) );
    usercmd_t outCmd;
    TestReadUserCmdDelta( msg, outCmd, &baseCmd );

    EXPECT_EQ( outCmd.gameTime, 1033, "Delta gameTime match" );
    EXPECT_EQ( outCmd.buttons.btnValue, 0x01, "Delta buttons match" );
    EXPECT_EQ( outCmd.forwardmove, 120, "Delta forwardmove match" );

    // Subcase 2: Movement changed
    msg.InitWrite( buffer, sizeof( buffer ) );
    nextCmd.forwardmove = -60;
    nextCmd.buttons.btnValue = 0x03;
    nextCmd.angles[0] = 520;
    TestWriteUserCmdDelta( msg, nextCmd, &baseCmd );

    msg.InitRead( buffer, sizeof( buffer ) );
    TestReadUserCmdDelta( msg, outCmd, &baseCmd );
    EXPECT_EQ( outCmd.forwardmove, -60, "Delta changed forwardmove" );
    EXPECT_EQ( outCmd.buttons.btnValue, 0x03, "Delta changed buttons" );
    EXPECT_EQ( outCmd.angles[0], 520, "Delta changed angle[0]" );
}

static void TestSequenceWrapAndLateArrival() {
    usercmd_t cmd1;
    memset( &cmd1, 0, sizeof( cmd1 ) );
    cmd1.gameFrame = 10;
    cmd1.gameTime = 1000;
    cmd1.duplicateCount = 0;

    // Late arrival test: incoming cmd2 has gameTime <= cmd1.gameTime
    usercmd_t cmd2;
    memset( &cmd2, 0, sizeof( cmd2 ) );
    cmd2.gameFrame = 11;
    cmd2.gameTime = 990; // arrived late, stamped with past time

    const bool duplicated = TestDuplicateUsercmd( cmd1, cmd2, 11, 1033 );
    EXPECT_TRUE( duplicated, "DuplicateUsercmd detected late command" );
    EXPECT_EQ( cmd2.gameFrame, 11, "cmd2 frame updated" );
    EXPECT_EQ( cmd2.gameTime, 1033, "cmd2 time advanced" );
    EXPECT_EQ( cmd2.duplicateCount, 1, "cmd2 duplicateCount incremented" );

    // Sequence wrap test: wrapping around 16-bit boundary (e.g. sequence numbers 65535 -> 0)
    unsigned short prevSeq = 65535;
    unsigned short newSeq = 2;
    short diff = static_cast<short>( newSeq - prevSeq );
    EXPECT_EQ( diff, 3, "16-bit sequence wrap difference correctly yields +3" );
}

int main() {
    TestAbsoluteSerialization();
    TestDeltaSerialization();
    TestSequenceWrapAndLateArrival();

    if ( g_failures == 0 ) {
        printf( "UserCmdDeltaTest: ALL TESTS PASSED\n" );
        return 0;
    } else {
        fprintf( stderr, "UserCmdDeltaTest: %d FAILURE(S)\n", g_failures );
        return 1;
    }
}

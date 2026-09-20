/*
===========================================================================
openETQW Asset-Free Test Harness: BitMsgTest.cpp
Verifies idBitMsg bit/byte level operations, bounds safety, and overflow.
===========================================================================
*/

#include "src/idlib/precompiled.h"
#include "src/idlib/BitMsg.h"
#include "src/framework/Common_public.h"

#include <cmath>

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

// Minimal mock idCommon to intercept Printf and prevent null dereference on overflow
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

static void TestBitLevel() {
    byte buffer[64];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );

    // Write alternating bits and arbitrary bit-width fields
    msg.WriteBits( 1, 1 );
    msg.WriteBits( 0, 1 );
    msg.WriteBits( 5, 3 );          // 101 in binary
    msg.WriteBits( 0x1A, 5 );       // 11010 in binary
    msg.WriteBits( 0x55AA, 16 );
    msg.WriteBits( -1, -8 );        // signed 8-bit -1
    msg.WriteBits( -120, -8 );      // signed 8-bit -120

    EXPECT_EQ( msg.GetNumBitsWritten(), 1 + 1 + 3 + 5 + 16 + 8 + 8, "Bits written calculation" );

    msg.InitRead( buffer, sizeof( buffer ) );
    EXPECT_EQ( msg.ReadBits( 1 ), 1, "Read 1 bit" );
    EXPECT_EQ( msg.ReadBits( 1 ), 0, "Read 1 bit" );
    EXPECT_EQ( msg.ReadBits( 3 ), 5, "Read 3 bits" );
    EXPECT_EQ( msg.ReadBits( 5 ), 0x1A, "Read 5 bits" );
    EXPECT_EQ( msg.ReadBits( 16 ), 0x55AA, "Read 16 bits" );
    EXPECT_EQ( msg.ReadBits( -8 ), -1, "Read signed 8-bit -1" );
    EXPECT_EQ( msg.ReadBits( -8 ), -120, "Read signed 8-bit -120" );
}

static void TestByteLevel() {
    byte buffer[128];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );

    msg.WriteChar( 'X' );
    msg.WriteByte( 0xFF );
    msg.WriteShort( -12345 );
    msg.WriteUShort( 54321 );
    msg.WriteLong( 0x12345678 );
    msg.WriteFloat( 3.14159265f );

    msg.InitRead( buffer, sizeof( buffer ) );
    EXPECT_EQ( msg.ReadChar(), 'X', "ReadChar" );
    EXPECT_EQ( msg.ReadByte(), 0xFF, "ReadByte" );
    EXPECT_EQ( msg.ReadShort(), -12345, "ReadShort" );
    EXPECT_EQ( msg.ReadUShort(), 54321, "ReadUShort" );
    EXPECT_EQ( msg.ReadLong(), 0x12345678, "ReadLong" );
    
    const float f = msg.ReadFloat();
    EXPECT_TRUE( fabsf( f - 3.14159265f ) < 0.0001f, "ReadFloat close match" );
}

static void TestByteAlign() {
    byte buffer[32];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );

    msg.WriteBits( 1, 1 );
    msg.WriteBits( 1, 1 );
    msg.WriteBits( 1, 1 );
    EXPECT_EQ( msg.GetWriteBit(), 3, "WriteBit at 3" );

    msg.WriteByteAlign();
    EXPECT_EQ( msg.GetWriteBit(), 0, "WriteBit at 0 after align" );
    EXPECT_EQ( msg.GetSize(), 1, "Message size 1 byte" );

    msg.WriteByte( 0xAB );
    EXPECT_EQ( msg.GetSize(), 2, "Message size 2 bytes" );

    msg.InitRead( buffer, sizeof( buffer ) );
    EXPECT_EQ( msg.ReadBits( 1 ), 1, "Read bit 0" );
    EXPECT_EQ( msg.ReadBits( 1 ), 1, "Read bit 1" );
    EXPECT_EQ( msg.ReadBits( 1 ), 1, "Read bit 2" );
    msg.ReadByteAlign();
    EXPECT_EQ( msg.ReadByte(), 0xAB, "Read aligned byte" );
}

static void TestBoundsAndOverflow() {
    byte buffer[8];
    idBitMsg msg;
    msg.InitWrite( buffer, sizeof( buffer ) );
    msg.SetAllowOverflow( true );

    EXPECT_EQ( msg.GetMaxSize(), 8, "Max size is 8 bytes" );
    EXPECT_TRUE( !msg.IsOverflowed(), "Not overflowed initially" );

    // Write 7 bytes
    for ( int i = 0; i < 7; ++i ) {
        msg.WriteByte( i );
    }
    EXPECT_TRUE( !msg.IsOverflowed(), "Not overflowed after 7 bytes" );
    EXPECT_EQ( msg.GetRemainingSpace(), 1, "1 byte remaining" );

    // Write 2 more bytes (overflows 8 bytes capacity)
    msg.WriteByte( 0x11 );
    msg.WriteByte( 0x22 );

    EXPECT_TRUE( msg.IsOverflowed(), "IsOverflowed true after write past end" );
}

int main() {
    idLib::common = &s_testCommon;

    TestBitLevel();
    TestByteLevel();
    TestByteAlign();
    TestBoundsAndOverflow();

    if ( g_failures == 0 ) {
        printf( "BitMsgTest: ALL TESTS PASSED\n" );
        return 0;
    } else {
        fprintf( stderr, "BitMsgTest: %d FAILURE(S)\n", g_failures );
        return 1;
    }
}

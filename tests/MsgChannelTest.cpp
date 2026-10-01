// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: MsgChannelTest.cpp
idMsgChannel fragments a message of FRAGMENT_SIZE or more. A message whose
length is an exact multiple of FRAGMENT_SIZE ends with an empty fragment, so
the receiver can tell no more follow. idLib's idBitMsg::WriteData is fatal on a
zero length, and SendNextFragment used to write the empty fragment's data
anyway: the dedicated server died with "idBitMsg::GetByteSpace: length must be
greater than zero ( was 0 )" mid-session (reconnect-restart on Windows,
2026-10-01). WriteMessageData did the same for an empty message. This test
sends an empty message and payloads of one to four fragments through a real
channel (the real compressor), checks that at least one payload ends with an
empty fragment, and that a second channel receives every message intact.
Without the fix the send reaches the fatal error, which crashes the test
(idLib::common is not set here, so the error call is through a null pointer).
===========================================================================
*/

// Compiled as the engine compiles its sources: src/framework/precompiled.h is
// force-included, so idLib, the framework (idCVar, idCompressor, idFile) and the
// sys declarations (idPort, netadr_t) are already here.
#include "async/MsgChannel.h"

// Globals the channel's sources refer to; nothing here calls through them on
// the paths this test takes (the channel prints only when net_channelShowPackets
// is set or a packet is bad)
idCommon * common = NULL;
idSys * sys = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

// MAX_PACKETLEN and FRAGMENT_SIZE are local to MsgChannel.cpp
static const int PACKETLEN = 1400;
static const int FRAGMENT = PACKETLEN - 100;

// The UDP port: packets are captured, not sent
static const int MAX_TEST_PACKETS = 64;
static byte g_packets[ MAX_TEST_PACKETS ][ PACKETLEN ];
static int g_packetSizes[ MAX_TEST_PACKETS ];
static int g_numPackets = 0;

idPort::idPort() {
	memset( &bound_to, 0, sizeof( bound_to ) );
	netSocket = 0;
	silent = false;
	packetsRead = bytesRead = packetsWritten = bytesWritten = 0;
}

idPort::~idPort() {
}

void idPort::SendPacket( const netadr_t to, const void *data, int size ) {
	(void)to;
	if ( g_numPackets < MAX_TEST_PACKETS && size >= 0 && size <= PACKETLEN ) {
		memcpy( g_packets[ g_numPackets ], data, size );
		g_packetSizes[ g_numPackets ] = size;
	}
	g_numPackets++;
}

const char *Sys_NetAdrToString( const netadr_t &a ) {
	(void)a;
	return "test";
}

// File.cpp's idFile_Permanent refers to it; idFile_BitMsg, the one used here, does not
long Sys_FileTimeStamp( FILE *file ) {
	(void)file;
	return 0;
}

static int g_failures = 0;
#define CHECK( cond, ... ) do { if ( !( cond ) ) { printf( "FAIL: " __VA_ARGS__ ); printf( "\n" ); g_failures++; } } while ( 0 )

// little-endian short at an offset, as idBitMsg::WriteShort writes it
static int PacketShort( const byte *data, int offset ) {
	return static_cast<short>( data[ offset ] | ( data[ offset + 1 ] << 8 ) );
}

// Feeds the captured packets to the receiver, which must complete the message on
// the last one with exactly the n bytes that were sent
static void Deliver( idMsgChannel &receiver, const netadr_t &adr, int time, const byte *sent, int n ) {
	bool done = false;
	for ( int p = 0; p < g_numPackets && p < MAX_TEST_PACKETS && !done; p++ ) {
		static byte inBuf[ MAX_MESSAGE_SIZE ];
		memcpy( inBuf, g_packets[ p ], g_packetSizes[ p ] );
		idBitMsg in;
		in.InitWrite( inBuf, sizeof( inBuf ) );
		in.SetSize( g_packetSizes[ p ] );
		in.BeginReading();
		in.ReadShort();	// the channel id, which the engine reads to pick the channel
		int sequence = 0;
		if ( receiver.Process( adr, time, in, sequence ) ) {
			done = true;
			CHECK( p == g_numPackets - 1, "size %d: complete after packet %d of %d", n, p + 1, g_numPackets );
			CHECK( in.GetRemainingData() == n, "size %d: received %d bytes", n, in.GetRemainingData() );
			if ( n > 0 && in.GetRemainingData() == n ) {
				static byte got[ MAX_MESSAGE_SIZE ];
				in.ReadData( got, n );
				CHECK( memcmp( got, sent, n ) == 0, "size %d: received data differs", n );
			}
		}
	}
	CHECK( done, "size %d: the receiver never completed the message", n );
}

int main() {
	netadr_t adr;
	memset( &adr, 0, sizeof( adr ) );
	adr.type = NA_LOOPBACK;

	static byte payload[ MAX_MESSAGE_SIZE ];
	unsigned int seed = 12345u;
	for ( int i = 0; i < MAX_MESSAGE_SIZE; i++ ) {
		// incompressible: the compressed message grows with the payload
		seed = seed * 1103515245u + 12345u;
		payload[ i ] = static_cast<byte>( seed >> 16 );
	}

	idPort port;

	// an empty message: one packet, the channel's header and no data
	{
		idMsgChannel *sender = new idMsgChannel;
		idMsgChannel *receiver = new idMsgChannel;
		sender->Init( adr, 7 );
		receiver->Init( adr, 7 );

		static byte emptyBuf[ 16 ];
		idBitMsg empty;
		empty.InitWrite( emptyBuf, sizeof( emptyBuf ) );

		g_numPackets = 0;
		sender->SendMessage( port, 1000, empty );
		CHECK( g_numPackets == 1, "size 0: %d packets", g_numPackets );
		Deliver( *receiver, adr, 1000, payload, 0 );

		delete sender;
		delete receiver;
	}

	int exactMultiples = 0;
	int tried = 0;
	// the compressed message grows with the payload but not one for one: scan
	// payloads from one to four fragments (all of them fragmented), which crosses
	// several exact multiples
	for ( int n = FRAGMENT; n <= 4 * FRAGMENT; n++, tried++ ) {
		idMsgChannel *sender = new idMsgChannel;
		idMsgChannel *receiver = new idMsgChannel;
		sender->Init( adr, 7 );
		receiver->Init( adr, 7 );

		static byte msgBuf[ MAX_MESSAGE_SIZE ];
		idBitMsg msg;
		msg.InitWrite( msgBuf, sizeof( msgBuf ) );
		msg.WriteData( payload, n );

		g_numPackets = 0;
		int time = 1000;
		sender->SendMessage( port, time, msg );
		while ( sender->UnsentFragmentsLeft() && g_numPackets < MAX_TEST_PACKETS ) {
			sender->SendNextFragment( port, ++time );
		}
		CHECK( !sender->UnsentFragmentsLeft(), "size %d: fragments left after %d packets", n, g_numPackets );
		CHECK( g_numPackets >= 2 && g_numPackets <= MAX_TEST_PACKETS, "size %d: %d packets", n, g_numPackets );

		if ( g_numPackets >= 1 && g_numPackets <= MAX_TEST_PACKETS ) {
			// a fragment packet: short id, long sequence | FRAGMENT_BIT, short start, short length, data
			const int lastLength = PacketShort( g_packets[ g_numPackets - 1 ], 8 );
			if ( lastLength == 0 ) {
				exactMultiples++;
				CHECK( g_packetSizes[ g_numPackets - 1 ] == 10, "size %d: the empty fragment is %d bytes", n, g_packetSizes[ g_numPackets - 1 ] );
			}
		}

		Deliver( *receiver, adr, time, payload, n );

		delete sender;
		delete receiver;
	}

	CHECK( exactMultiples > 0, "no payload size in the scan made a message of an exact multiple of FRAGMENT_SIZE (%d)", FRAGMENT );
	printf( "msgchannel: an empty message and %d payload sizes, %d ending in an empty fragment, %d failure(s)\n", tried, exactMultiples, g_failures );
	return g_failures == 0 ? 0 : 1;
}

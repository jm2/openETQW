// Copyright (C) 2007 Id Software, Inc.
// SPDX-License-Identifier: GPL-3.0-or-later

#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>

#include "idlib/precompiled.h"
#include "posix_public.h"

idCVar net_ip( "net_ip", "localhost", CVAR_SYSTEM, "local IP address" );
idCVar net_port( "net_port", "0", CVAR_SYSTEM | CVAR_INTEGER, "local IP port number" );
idCVar net_forceDrop( "net_forceDrop", "0", CVAR_SYSTEM | CVAR_INTEGER, "percentage packet loss" );	// as win_net.cpp: outgoing packets
idCVar net_forceLatency( "net_forceLatency", "0", CVAR_SYSTEM | CVAR_INTEGER, "milliseconds latency added to received packets" );
idCVar net_forceJitter( "net_forceJitter", "0", CVAR_SYSTEM | CVAR_INTEGER, "up to this many more milliseconds of random latency per received packet (order kept)" );

// Received packets held back by net_forceLatency / net_forceJitter (network testing).
// Keyed by socket, so idPort (shared with the game module) is unchanged.
struct delayedPacket_t {
	int				socket;
	int				releaseTime;
	netadr_t		from;
	idList< byte >	data;
};
static idList< delayedPacket_t* > delayedPackets;

// last release time per socket: jitter varies the delay but keeps the arrival
// order, like queueing on a real path (independent per-packet delays reordered
// the fragments of every large message, which the channel then dropped)
struct socketRelease_t {
	int		socket;
	int		releaseTime;
};
static idList< socketRelease_t > socketReleases;

static int Net_ReleaseTime( int netSocket, int wanted ) {
	for ( int i = 0; i < socketReleases.Num(); i++ ) {
		if ( socketReleases[ i ].socket == netSocket ) {
			socketReleases[ i ].releaseTime = Max( wanted, socketReleases[ i ].releaseTime );
			return socketReleases[ i ].releaseTime;
		}
	}
	socketRelease_t release = { netSocket, wanted };
	socketReleases.Append( release );
	return wanted;
}

static bool Net_ReadSocket( int netSocket, netadr_t &from, void *data, int &size, int maxSize ) {
	struct sockaddr_in from_addr;
	socklen_t fromlen = sizeof( from_addr );
	int ret = recvfrom( netSocket, static_cast< char* >( data ), maxSize, 0, reinterpret_cast< struct sockaddr* >( &from_addr ), &fromlen );
	if ( ret <= 0 ) {
		return false;
	}
	from.type = NA_IP;
	from.port = ntohs( from_addr.sin_port );
	memcpy( from.ip, &from_addr.sin_addr.s_addr, 4 );
	size = ret;
	return true;
}

typedef struct {
	uint32_t ip;
	uint32_t mask;
} net_interface;

#define MAX_INTERFACES 16
static int num_interfaces = 0;
static net_interface netint[ MAX_INTERFACES ];

idPort::idPort() {
	netSocket = 0;
	memset( &bound_to, 0, sizeof( bound_to ) );
	packetsRead = 0;
	bytesRead = 0;
	packetsWritten = 0;
	bytesWritten = 0;
	silent = false;
}

idPort::~idPort() {
	Close();
}

void idPort::Close() {
	if ( netSocket != 0 ) {
		close( netSocket );
		netSocket = 0;
	}
	memset( &bound_to, 0, sizeof( bound_to ) );
}

bool idPort::InitForPort( int portNumber ) {
	Close();

	int s = socket( AF_INET, SOCK_DGRAM, IPPROTO_UDP );
	if ( s < 0 ) {
		common->Warning( "idPort::InitForPort: socket() failed: %s", strerror( errno ) );
		return false;
	}

	int flags = fcntl( s, F_GETFL, 0 );
	if ( flags != -1 ) {
		fcntl( s, F_SETFL, flags | O_NONBLOCK );
	}

	int opt = 1;
	setsockopt( s, SOL_SOCKET, SO_BROADCAST, &opt, sizeof( opt ) );
	setsockopt( s, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof( opt ) );

	struct sockaddr_in address;
	memset( &address, 0, sizeof( address ) );
	address.sin_family = AF_INET;
	address.sin_addr.s_addr = INADDR_ANY;
	address.sin_port = htons( ( portNumber == PORT_ANY || portNumber < 0 ) ? 0 : static_cast< unsigned short >( portNumber ) );

	if ( bind( s, reinterpret_cast< struct sockaddr* >( &address ), sizeof( address ) ) < 0 ) {
		common->Warning( "idPort::InitForPort: bind() failed on port %d: %s", portNumber, strerror( errno ) );
		close( s );
		return false;
	}

	socklen_t len = sizeof( address );
	if ( getsockname( s, reinterpret_cast< struct sockaddr* >( &address ), &len ) == 0 ) {
		bound_to.type = NA_IP;
		bound_to.port = ntohs( address.sin_port );
	} else {
		bound_to.type = NA_IP;
		bound_to.port = static_cast< unsigned short >( portNumber );
	}

	netSocket = s;
	return true;
}

bool idPort::GetPacket( netadr_t &from, void *data, int &size, int maxSize ) {
	if ( netSocket == 0 ) {
		return false;
	}

	const int latency = Max( 0, net_forceLatency.GetInteger() );
	const int jitter = Max( 0, net_forceJitter.GetInteger() );
	if ( latency == 0 && jitter == 0 && delayedPackets.Num() == 0 ) {
		if ( !Net_ReadSocket( netSocket, from, data, size, maxSize ) ) {
			return false;
		}
		packetsRead++;
		bytesRead += size;
		return true;
	}

	// queue everything that has arrived, each with its own release time
	const int now = Sys_Milliseconds();
	netadr_t packetFrom;
	int packetSize = 0;
	while ( Net_ReadSocket( netSocket, packetFrom, data, packetSize, maxSize ) ) {
		delayedPacket_t* packet = new delayedPacket_t;
		packet->socket = netSocket;
		packet->releaseTime = Net_ReleaseTime( netSocket, now + latency + ( jitter > 0 ? rand() % ( jitter + 1 ) : 0 ) );
		packet->from = packetFrom;
		packet->data.SetNum( packetSize );
		memcpy( &packet->data[ 0 ], data, packetSize );
		delayedPackets.Append( packet );
	}

	// hand out the due packet of this socket that is released first
	int best = -1;
	for ( int i = 0; i < delayedPackets.Num(); i++ ) {
		const delayedPacket_t* packet = delayedPackets[ i ];
		if ( packet->socket == netSocket && packet->releaseTime <= now && ( best < 0 || packet->releaseTime < delayedPackets[ best ]->releaseTime ) ) {
			best = i;
		}
	}
	if ( best < 0 ) {
		return false;
	}
	delayedPacket_t* packet = delayedPackets[ best ];
	delayedPackets.RemoveIndex( best );
	from = packet->from;
	size = Min( packet->data.Num(), maxSize );
	memcpy( data, &packet->data[ 0 ], size );
	delete packet;
	packetsRead++;
	bytesRead += size;
	return true;
}

bool idPort::GetPacketBlocking( netadr_t &from, void *data, int &size, int maxSize, int timeout ) {
	if ( netSocket == 0 ) {
		return false;
	}

	fd_set readfds;
	FD_ZERO( &readfds );
	FD_SET( netSocket, &readfds );

	struct timeval tv;
	tv.tv_sec = timeout / 1000;
	tv.tv_usec = ( timeout % 1000 ) * 1000;

	int selectRet = select( netSocket + 1, &readfds, NULL, NULL, &tv );
	if ( selectRet <= 0 ) {
		return false;
	}

	return GetPacket( from, data, size, maxSize );
}

void idPort::SendPacket( const netadr_t to, const void *data, int size ) {
	if ( netSocket == 0 || size <= 0 || data == NULL ) {
		return;
	}

	struct sockaddr_in addr;
	memset( &addr, 0, sizeof( addr ) );
	addr.sin_family = AF_INET;
	addr.sin_port = htons( to.port );

	if ( to.type == NA_BROADCAST ) {
		addr.sin_addr.s_addr = INADDR_BROADCAST;
	} else if ( to.type == NA_IP ) {
		memcpy( &addr.sin_addr.s_addr, to.ip, 4 );
	} else if ( to.type == NA_LOOPBACK ) {
		addr.sin_addr.s_addr = htonl( INADDR_LOOPBACK );
	} else {
		return;
	}

	const int dropPercent = idMath::ClampInt( 0, 100, net_forceDrop.GetInteger() );
	if ( dropPercent > 0 && rand() < dropPercent * ( RAND_MAX / 100 ) ) {
		packetsWritten++;	// counted as sent, as a lost packet would be
		bytesWritten += size;
		return;
	}

	int ret = sendto( netSocket, static_cast< const char* >( data ), size, 0, reinterpret_cast< struct sockaddr* >( &addr ), sizeof( addr ) );
	if ( ret > 0 ) {
		packetsWritten++;
		bytesWritten += ret;
	}
}

bool Sys_StringToNetAdr( const char *s, netadr_t *a, bool doDNSResolve ) {
	if ( s == NULL || a == NULL || s[0] == '\0' ) {
		return false;
	}

	memset( a, 0, sizeof( *a ) );

	if ( idStr::Icmp( s, "localhost" ) == 0 || idStr::Icmp( s, "127.0.0.1" ) == 0 ) {
		a->type = NA_LOOPBACK;
		return true;
	}

	char host[ 256 ];
	idStr::Copynz( host, s, sizeof( host ) );
	int port = 0;
	char *colon = strchr( host, ':' );
	if ( colon != NULL ) {
		*colon = '\0';
		port = atoi( colon + 1 );
	}

	struct in_addr inaddr;
	if ( inet_aton( host, &inaddr ) != 0 ) {
		a->type = NA_IP;
		memcpy( a->ip, &inaddr.s_addr, 4 );
		a->port = static_cast< unsigned short >( port );
		return true;
	}

	if ( doDNSResolve ) {
		struct hostent *he = gethostbyname( host );
		if ( he != NULL && he->h_addr_list[0] != NULL ) {
			a->type = NA_IP;
			memcpy( a->ip, he->h_addr_list[0], 4 );
			a->port = static_cast< unsigned short >( port );
			return true;
		}
	}

	return false;
}

const char *Sys_NetAdrToString( const netadr_t& a ) {
	static char s[ 4 ][ 64 ];
	static int index = 0;
	index = ( index + 1 ) & 3;

	if ( a.type == NA_LOOPBACK ) {
		idStr::snPrintf( s[ index ], sizeof( s[ index ] ), "localhost:%i", a.port );
	} else if ( a.type == NA_IP || a.type == NA_BROADCAST ) {
		idStr::snPrintf( s[ index ], sizeof( s[ index ] ), "%i.%i.%i.%i:%i", a.ip[0], a.ip[1], a.ip[2], a.ip[3], a.port );
	} else {
		idStr::snPrintf( s[ index ], sizeof( s[ index ] ), "bad_address" );
	}
	return s[ index ];
}

bool Sys_NetAdrToHostName( const netadr_t& a, char** s ) {
	return false;
}

bool Sys_IsLANAddress( const netadr_t& a ) {
	if ( a.type == NA_LOOPBACK ) {
		return true;
	}
	if ( a.type != NA_IP ) {
		return false;
	}
	if ( a.ip[0] == 10 ) return true;
	if ( a.ip[0] == 172 && ( a.ip[1] >= 16 && a.ip[1] <= 31 ) ) return true;
	if ( a.ip[0] == 192 && a.ip[1] == 168 ) return true;
	if ( a.ip[0] == 127 ) return true;
	return false;
}

bool Sys_CompareNetAdrBase( const netadr_t& a, const netadr_t& b ) {
	if ( a.type != b.type ) {
		return false;
	}
	if ( a.type == NA_LOOPBACK ) {
		return true;
	}
	return ( memcmp( a.ip, b.ip, 4 ) == 0 );
}

int Sys_GetLocalIPCount( void ) {
	return num_interfaces > 0 ? num_interfaces : 1;
}

const char *Sys_GetLocalIP( int i ) {
	static char s[ 64 ];
	if ( i >= 0 && i < num_interfaces ) {
		const uint8_t *ip = reinterpret_cast< const uint8_t* >( &netint[ i ].ip );
		idStr::snPrintf( s, sizeof( s ), "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3] );
		return s;
	}
	return "127.0.0.1";
}

void Sys_InitNetworking( void ) {
	num_interfaces = 0;
	netint[ 0 ].ip = htonl( INADDR_LOOPBACK );
	netint[ 0 ].mask = htonl( 0xFF000000 );
	num_interfaces = 1;
}

void Sys_ShutdownNetworking( void ) {
	num_interfaces = 0;
}

int Sys_MessageBox( const char* title, const char* buffer, messageBoxType_t type ) {
	fprintf( stderr, "\n[%s] %s\n", title != NULL ? title : "Message", buffer != NULL ? buffer : "" );
	return 0;
}

sdLogitechLCDSystem* Sys_GetLogitechLCDSystem( void ) {
	return NULL;
}

void Sys_SetConsoleName( const char* name ) {
}

bool Sys_GetHTTPProxyAddress( char proxy[ MAX_PROXY_LENGTH ] ) {
	return false;
}

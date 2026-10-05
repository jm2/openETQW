// Copyright (C) 2007 Id Software, Inc.
//
// ETQW asynchronous client bootstrap.  The Doom 3 networking source that
// previously occupied this file used incompatible UI, authentication, bit
// message, and game interfaces.  This implementation restores the ETQW
// control surface first; protocol details are filled from the retail PDB and
// disassembly as the lower networking layers are reconstructed.

#include "../../idlib/precompiled.h"
#pragma hdrstop

#include "../precompiled.h"
#include "AsyncNetwork.h"
#include <chrono>
#include "../Session_local.h"

static const int CLIENT_CONNECT_RESEND_MSEC = 1000;
static const int CLIENT_EMPTY_RESEND_MSEC = 500;
static const int PREDICTION_FAST_ADJUST = 4;	// Doom 3: fraction of the prediction error corrected per snapshot

static idCVar net_clientShowSnapshots( "net_clientShowSnapshots", "0", CVAR_SYSTEM | CVAR_BOOL | CVAR_NOCHEAT, "print a line for every snapshot the client reads (network tests)" );

idAsyncClient::idAsyncClient() :
	active( false ),
	realTime( 0 ),
	clientTime( 0 ),
	clientId( 0 ),
	clientDataChecksum( 0 ),
	clientNum( 0 ),
	clientState( CS_DISCONNECTED ),
	clientPrediction( 0 ),
	clientPredictTime( 0 ),
	serverId( 0 ),
	serverChallenge( 0 ),
	serverMessageSequence( 0 ),
	lastRconTime( 0 ),
	lastConnectTime( -9999 ),
	lastEmptyTime( -9999 ),
	lastPacketTime( -9999 ),
	lastSnapshotTime( -9999 ),
	snapshotSequence( 0 ),
	snapshotGameFrame( 0 ),
	snapshotGameTime( 0 ),
	predictedGameFrame( 0 ),
	gameInitId( GAME_INIT_ID_INVALID ),
	gameFrame( 0 ),
	gameTime( 0 ),
	gameTimeResidual( 0 ),
	guiNetMenu( NULL ),
	updateState( UPDATE_NONE ),
	updateSentTime( 0 ),
	updateDirectDownload( false ),
	updateMime( FILE_OPEN ),
	showUpdateMessage( false ),
	dltotal( 0 ),
	dlnow( 0 ),
	lastFrameDelta( 0 ),
	dlRequest( -1 ),
	dlCount( -1 ),
	currentDlSize( 0 ),
	totalDlSize( 0 ) {
	memset( &serverAddress, 0, sizeof( serverAddress ) );
	memset( &lastRconAddress, 0, sizeof( lastRconAddress ) );
	memset( userCmds, 0, sizeof( userCmds ) );
	memset( dlChecksums, 0, sizeof( dlChecksums ) );
	backgroundDownload.completed = true;
}

void idAsyncClient::Clear() {
	active = false;
	realTime = 0;
	clientTime = 0;
	clientId = 0;
	clientDataChecksum = 0;
	clientNum = 0;
	clientState = CS_DISCONNECTED;
	clientPrediction = 0;
	clientPredictTime = 0;
	serverId = 0;
	serverChallenge = 0;
	serverMessageSequence = 0;
	lastConnectTime = -9999;
	lastEmptyTime = -9999;
	lastPacketTime = -9999;
	lastSnapshotTime = -9999;
	snapshotSequence = 0;
	snapshotGameFrame = 0;
	snapshotGameTime = 0;
	predictedGameFrame = 0;
	gameInitId = GAME_INIT_ID_INVALID;
	gameFrame = 0;
	gameTime = 0;
	gameTimeResidual = 0;
	lastFrameDelta = 0;
	dlRequest = -1;
	dlCount = -1;
	currentDlSize = 0;
	totalDlSize = 0;
	memset( userCmds, 0, sizeof( userCmds ) );
	memset( dlChecksums, 0, sizeof( dlChecksums ) );
	backgroundDownload.completed = true;
}

void idAsyncClient::Shutdown() {
	DisconnectFromServer();
	serverList.Shutdown();
	updateMSG.Clear();
	updateURL.Clear();
	updateFile.Clear();
	updateFallback.Clear();
	backgroundDownload.url.url.Clear();
	dlList.Clear();
	guiNetMenu = NULL;
}

bool idAsyncClient::InitPort() {
	if ( clientPort.GetPort() != 0 ) {
		return true;
	}
	if ( !clientPort.InitForPort( PORT_ANY ) ) {
		common->Printf( "Couldn't open client network port.\n" );
		return false;
	}
	return true;
}

void idAsyncClient::ClosePort() {
	clientPort.Close();
}

void idAsyncClient::ClearPendingPackets() {
	byte buffer[ MAX_MESSAGE_SIZE ];
	netadr_t from;
	int size = 0;
	while ( clientPort.GetPacket( from, buffer, size, sizeof( buffer ) ) ) {
	}
}

void idAsyncClient::ConnectToServer( const netadr_t adr ) {
	session->Stop();
	// leave a server we are still connected to (Doom 3): tell it, and shut the old
	// channel so nothing of that session reaches the new one
	DisconnectFromServer();
	if ( !InitPort() ) {
		return;
	}

	ClearPendingPackets();
	Clear();
	serverAddress = adr;
	// random: the server tells clients behind one address apart by it, and the
	// time since start (Doom 3) is the same for clients started together
	// (a nanosecond clock and a stack address, which differs per process with ASLR,
	// mixed so both reach the low bits)
	const int stackMarker = 0;
	unsigned long long seed = static_cast< unsigned long long >( std::chrono::high_resolution_clock::now().time_since_epoch().count() );
	seed ^= static_cast< unsigned long long >( reinterpret_cast< uintptr_t >( &stackMarker ) );
	seed *= 0x9E3779B97F4A7C15ull;
	clientId = static_cast< int >( ( seed >> 40 ) & CONNECTIONLESS_MESSAGE_ID_MASK );
	clientDataChecksum = declManager->GetChecksum();
	clientState = CS_CHALLENGING;
	active = true;
	lastConnectTime = -9999;
	common->Printf( "Connecting to %s\n", Sys_NetAdrToString( serverAddress ) );
}

void idAsyncClient::ConnectToServer( const char* address ) {
	netadr_t adr;
	if ( address == NULL || !Sys_StringToNetAdr( address, &adr, true ) ) {
		common->Printf( "Couldn't resolve server '%s'\n", address != NULL ? address : "" );
		return;
	}
	if ( adr.port == 0 ) {
		adr.port = PORT_SERVER;
	}
	ConnectToServer( adr );
}

void idAsyncClient::Reconnect() {
	if ( serverAddress.type != NA_BAD ) {
		ConnectToServer( serverAddress );
	}
}

void idAsyncClient::DisconnectFromServer() {
	if ( active ) {
		if ( clientState >= CS_CONNECTED ) {
			byte buffer[ 16 ];
			idBitMsg msg;
			msg.InitWrite( buffer, sizeof( buffer ) );
			msg.BeginWriting();
			msg.WriteByte( CLIENT_RELIABLE_MESSAGE_DISCONNECT );
			channel.SendReliableMessage( msg );

			SendEmptyToServer( true );
			SendEmptyToServer( true );
			SendEmptyToServer( true );
		}
		channel.Shutdown();
	}
	Clear();
}

void idAsyncClient::GetServerInfo( const netadr_t adr ) {
	if ( !InitPort() ) {
		return;
	}
	serverAddress = adr;
	common->DPrintf( "requesting server info from %s\n", Sys_NetAdrToString( adr ) );
}

void idAsyncClient::GetServerInfo( const char* address ) {
	netadr_t adr;
	if ( address != NULL && Sys_StringToNetAdr( address, &adr, true ) ) {
		if ( adr.port == 0 ) {
			adr.port = PORT_SERVER;
		}
		GetServerInfo( adr );
	}
}

void idAsyncClient::GetLANServers() {
	if ( InitPort() ) {
		serverList.SetupLANScan();
	}
}

void idAsyncClient::GetNETServers() {
	if ( InitPort() ) {
		serverList.StartServers( true );
		idAsyncNetwork::GetNETServers();
	}
}

void idAsyncClient::ListServers() {
	for ( int i = 0; i < serverList.Num(); ++i ) {
		common->Printf(
			"%3d: %s %dms (%s)\n",
			i,
			serverList[ i ].serverInfo.GetString( "si_name" ),
			serverList[ i ].ping,
			Sys_NetAdrToString( serverList[ i ].adr )
		);
	}
}

void idAsyncClient::ClearServers() {
	serverList.Clear();
}

void idAsyncClient::RemoteConsole( const char* command ) {
	if ( command == NULL || command[ 0 ] == '\0' ) {
		return;
	}
	netadr_t adr;
	if ( !Sys_StringToNetAdr( idAsyncNetwork::clientRemoteConsoleAddress.GetString(), &adr, true ) ) {
		return;
	}
	if ( adr.port == 0 ) {
		adr.port = PORT_SERVER;
	}
	lastRconAddress = adr;
	lastRconTime = Sys_Milliseconds();
	common->DPrintf( "rcon to %s: %s\n", Sys_NetAdrToString( adr ), command );
}

int idAsyncClient::GetPrediction() const {
	return clientPrediction;
}

int idAsyncClient::GetTimeSinceLastPacket() const {
	return active ? realTime - lastPacketTime : 0;
}

int idAsyncClient::GetOutgoingRate() const {
	return active ? channel.GetOutgoingRate() : 0;
}

int idAsyncClient::GetIncomingRate() const {
	return active ? channel.GetIncomingRate() : 0;
}

float idAsyncClient::GetOutgoingCompression() const {
	return active ? channel.GetOutgoingCompression() : 0.0f;
}

float idAsyncClient::GetIncomingCompression() const {
	return active ? channel.GetIncomingCompression() : 0.0f;
}

float idAsyncClient::GetIncomingPacketLoss() const {
	return active ? channel.GetIncomingPacketLoss() : 0.0f;
}

int idAsyncClient::GetGameTimeLeft() const {
	int elapsedInTic = gameTimeResidual + clientPredictTime;	// the tic boundary RunFrame uses
	if ( elapsedInTic < 1 ) {
		elapsedInTic = 1;
	} else if ( elapsedInTic >= USERCMD_MSEC ) {
		elapsedInTic = USERCMD_MSEC - 1;
	}
	return USERCMD_MSEC - elapsedInTic;
}

void idAsyncClient::RunFrame() {
	if ( !active ) {
		serverList.RunFrame();
		return;
	}

	UpdateTime( 100 );
	byte buffer[ MAX_MESSAGE_SIZE ];
	netadr_t from;
	int size = 0;
	while ( clientPort.GetPacket( from, buffer, size, sizeof( buffer ) ) ) {
		idBitMsg msg;
		msg.InitWrite( buffer, sizeof( buffer ) );
		msg.SetSize( size );
		ProcessMessage( from, msg );
	}

	if ( CheckTimeout() ) {
		DisconnectFromServer();
		session->StartMenu();
		return;
	}

	SetupConnection();
	if ( clientState >= CS_CONNECTED && !sessLocal.insideExecuteMapChange && ( cvarSystem->GetModifiedFlags() & CVAR_USERINFO ) != 0 ) {
		SendUserInfoToServer();	// a ui_* cvar changed
	}
	if ( clientState == CS_INGAME && !sessLocal.insideExecuteMapChange ) {
		com_frameInGame = true;
		const int startFrame = gameFrame;
		while ( gameTimeResidual + clientPredictTime >= USERCMD_MSEC ) {
			SendUsercmdsToServer();
			++gameFrame;
			gameTime += USERCMD_MSEC;
			gameTimeResidual -= USERCMD_MSEC;
			++com_frameTicks;
		}
		if ( predictedGameFrame < gameFrame - MAX_USERCMD_BACKUP + 1 ) {
			predictedGameFrame = gameFrame - MAX_USERCMD_BACKUP + 1;
		}
		while ( predictedGameFrame < gameFrame ) {
			DuplicateUsercmds( predictedGameFrame, predictedGameFrame * USERCMD_MSEC );
			game->ClientPrediction( userCmds[ predictedGameFrame & ( MAX_USERCMD_BACKUP - 1 ) ], NULL );
			predictedGameFrame++;
		}
		ProcessReliableServerMessages();

		// com_unlockFPS 0 draws no render-only frame (idCommonLocal::Frame), so none is prepared.
		// M5 step 10 (D7): a frame that ran a tick is prepared too, with the last tick's
		// command (as in single player, no new sample)
		if ( game != NULL && com_unlockFPS.GetBool() ) {
			if ( gameFrame == startFrame ) {
				const usercmd_t cmd = usercmdGen->GetDirectUsercmd();
				const int timeLeft = GetGameTimeLeft();
				game->ClientUpdateView( cmd, timeLeft );
				++com_frameUpdateViews;
			} else if ( com_unlock_tickFrames.GetBool() ) {
				game->ClientUpdateView( userCmds[ ( gameFrame - 1 ) & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ], GetGameTimeLeft() );
				++com_frameTickUpdateViews;
			}
		}
	} else if ( clientState >= CS_CONNECTED && !sessLocal.insideExecuteMapChange ) {
		SendUsercmdsToServer();
		ProcessReliableServerMessages();
	}
}

void idAsyncClient::SendReliableGameMessage( const idBitMsg& msg ) {
	if ( active && clientState >= CS_CONNECTED ) {
		byte buffer[ MAX_MESSAGE_SIZE ];
		idBitMsg out;
		out.InitWrite( buffer, sizeof( buffer ) );
		out.BeginWriting();
		out.WriteByte( CLIENT_RELIABLE_MESSAGE_GAME );
		out.WriteData( msg.GetData(), msg.GetSize() );
		channel.SendReliableMessage( out );
	}
}

const usercmd_t* idAsyncClient::GetClientUserCmd( int clientNum, int frameNum ) const {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || frameNum < 0 ) {
		return NULL;
	}
	return &userCmds[ frameNum & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ];
}

void idAsyncClient::ReadClientUserCmds( int clientNum, const idBitMsg& msg ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS ) {
		return;
	}
	int numUsercmds = msg.ReadByte();
	if ( numUsercmds < 1 || numUsercmds > MAX_USERCMD_RELAY ) {
		return;
	}
	const usercmd_t* last = NULL;
	for ( int j = 0; j < numUsercmds; ++j ) {
		int index = ( snapshotGameFrame + j ) & ( MAX_USERCMD_BACKUP - 1 );
		usercmd_t& cmd = userCmds[ index ][ clientNum ];
		idAsyncNetwork::ReadUserCmdDelta( msg, cmd, last );
		cmd.gameFrame = snapshotGameFrame + j;
		cmd.duplicateCount = 0;
		last = &cmd;
	}
	// clear the frames after the relayed ones, so prediction duplicates the last
	// relayed command instead of running old ones (Doom 3)
	for ( int j = numUsercmds; j < MAX_USERCMD_BACKUP; ++j ) {
		usercmd_t& cmd = userCmds[ ( snapshotGameFrame + j ) & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ];
		cmd.gameFrame = 0;
		cmd.gameTime = 0;
	}
}

void idAsyncClient::SendVersionCheck( bool fromMenu ) {
	common->DPrintf( "version check requested%s\n", fromMenu ? " from menu" : "" );
}

bool idAsyncClient::SendAuthCheck( const char* cdkey, const char* xpkey ) {
	return InitPort();
}

void idAsyncClient::PacifierUpdate() {
	if ( !active ) {
		return;
	}
	UpdateTime( 100 );
	SendEmptyToServer( false, true );
}

void idAsyncClient::DuplicateUsercmds( int frame, int time ) {
	if ( frame <= 0 ) {
		return;
	}
	for ( int client = 0; client < MAX_ASYNC_CLIENTS; ++client ) {
		idAsyncNetwork::DuplicateUsercmd( userCmds[ ( frame - 1 ) & ( MAX_USERCMD_BACKUP - 1 ) ][ client ],
			userCmds[ frame & ( MAX_USERCMD_BACKUP - 1 ) ][ client ], frame, time );
	}
}

/*
==================
idAsyncClient::SendUserInfoToServer

The ui_* user info cvars, in full (the server validates them and sends every
player's info back through SERVER_RELIABLE_MESSAGE_CLIENTINFO).  Sent at
connect and whenever one of them changes.
==================
*/
void idAsyncClient::SendUserInfoToServer() {
	if ( !active || clientState < CS_CONNECTED ) {
		return;
	}
	const idDict* cvars = cvarSystem->MoveCVarsToDict( CVAR_USERINFO );
	idDict info;
	for ( int i = 0; cvars != NULL && i < cvars->GetNumKeyVals() && info.GetNumKeyVals() < 32; ++i ) {
		const idKeyValue* kv = cvars->GetKeyVal( i );
		if ( idStr::Icmpn( kv->GetKey().c_str(), "ui_", 3 ) != 0 ) {
			continue;
		}
		idStr value = kv->GetValue();
		value.CapLength( 64 );
		info.Set( kv->GetKey(), value );
	}
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( CLIENT_RELIABLE_MESSAGE_CLIENTINFO );
	msg.WriteDeltaDict( info, NULL );
	if ( channel.SendReliableMessage( msg ) ) {
		cvarSystem->ClearModifiedFlags( CVAR_USERINFO );	// otherwise retried next frame
	}
}

void idAsyncClient::SendEmptyToServer( bool force, bool mapLoad ) {
	if ( !active || clientState < CS_CONNECTED ) {
		return;
	}
	if ( !force && realTime - lastEmptyTime < CLIENT_EMPTY_RESEND_MSEC ) {
		return;
	}
	byte buffer[ 16 ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( CLIENT_UNRELIABLE_MESSAGE_EMPTY );
	msg.WriteLong( gameInitId );	// the server ignores the load state of another game
	msg.WriteBool( mapLoad );
	channel.SendMessage( clientPort, realTime, msg );
	while ( channel.UnsentFragmentsLeft() ) {	// a large reliable backlog fragments even a small message
		channel.SendNextFragment( clientPort, realTime );
	}
	lastEmptyTime = realTime;
}

void idAsyncClient::SendPingResponseToServer( int time ) {
	byte buffer[ 16 ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( CLIENT_UNRELIABLE_MESSAGE_PINGRESPONSE );
	msg.WriteLong( time );
	channel.SendMessage( clientPort, realTime, msg );
	while ( channel.UnsentFragmentsLeft() ) {
		channel.SendNextFragment( clientPort, realTime );
	}
}

void idAsyncClient::SendUsercmdsToServer() {
	const int index = gameFrame & ( MAX_USERCMD_BACKUP - 1 );
	userCmds[ index ][ clientNum ] = usercmdGen->GetDirectUsercmd();
	userCmds[ index ][ clientNum ].gameFrame = gameFrame;
	userCmds[ index ][ clientNum ].gameTime = gameTime;

	if ( !channel.ReadyToSend( realTime ) ) {
		return;
	}

	int requestedUsercmds = idMath::ClampInt( 1, MAX_USERCMD_RELAY, idAsyncNetwork::clientUsercmdBackup.GetInteger() );
	int numUsercmds = gameFrame >= requestedUsercmds - 1 ? requestedUsercmds : gameFrame + 1;
	if ( numUsercmds < 1 ) {
		numUsercmds = 1;
	}

	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( CLIENT_UNRELIABLE_MESSAGE_USERCMD );
	msg.WriteLong( gameInitId );	// the server ignores acks and usercmds of another game
	msg.WriteLong( snapshotSequence );
	msg.WriteLong( gameFrame );
	msg.WriteShort( clientPrediction );	// the server's anti-lag rewinds by it
	msg.WriteByte( numUsercmds );

	const usercmd_t* last = NULL;
	for ( int i = gameFrame - numUsercmds + 1; i <= gameFrame; ++i ) {
		int cmdIndex = i & ( MAX_USERCMD_BACKUP - 1 );
		idAsyncNetwork::WriteUserCmdDelta( msg, userCmds[ cmdIndex ][ clientNum ], last );
		last = &userCmds[ cmdIndex ][ clientNum ];
	}

	channel.SendMessage( clientPort, realTime, msg );
	while ( channel.UnsentFragmentsLeft() ) {
		channel.SendNextFragment( clientPort, realTime );
	}
}

void idAsyncClient::InitGame( int serverGameInitId, int serverGameFrame, int serverGameTime, const idDict& serverSI ) {
	gameInitId = serverGameInitId;
	gameFrame = serverGameFrame;
	gameTime = serverGameTime;
	snapshotGameFrame = serverGameFrame;
	snapshotGameTime = serverGameTime;
	predictedGameFrame = serverGameFrame;
	memset( userCmds, 0, sizeof( userCmds ) );	// the old game's usercmds would be predicted on this map (Doom 3)
	// every player's user info for this game follows the game init (SERVER_RELIABLE_MESSAGE_CLIENTINFO)
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		sessLocal.mapSpawnData.userInfo[ i ].Clear();
	}
	sessLocal.mapSpawnData.serverInfo = serverSI;
	game->SetServerInfo( serverSI );

	idStr mapName = serverSI.GetString( "si_map" );
	if ( mapName.Length() > 0 ) {
		common->Printf( "Client initializing map %s\n", mapName.c_str() );
		sessLocal.ExecuteMapChange( mapName.c_str(), 0, true, true, true );
	}
	game->SetClientNum( clientNum, false );
	// In the game once the first snapshot of this game arrives (as in Doom 3):
	// the reliable state that follows the game init creates the networked
	// entities but carries none of their current state, and predicting them
	// before a snapshot ran client physics on garbage.
	clientState = CS_CONNECTED;
}

void idAsyncClient::ProcessUnreliableServerMessage( const idBitMsg& msg ) {
	lastPacketTime = realTime;
	const int type = msg.ReadByte();
	if ( type == SERVER_UNRELIABLE_MESSAGE_PING ) {
		SendPingResponseToServer( msg.ReadLong() );
	} else if ( type == SERVER_UNRELIABLE_MESSAGE_EMPTY ) {
		lastPacketTime = realTime;
	} else if ( type == SERVER_UNRELIABLE_MESSAGE_SNAPSHOT ) {
		if ( clientState < CS_CONNECTED || sessLocal.insideExecuteMapChange ) {
			return;
		}
		int sGameInitId = msg.ReadLong();
		if ( sGameInitId != gameInitId ) {
			return;
		}
		int sequence = msg.ReadLong();
		if ( sequence <= snapshotSequence ) {
			return;
		}
		int sGameFrame = msg.ReadLong();
		int sGameTime = msg.ReadLong();
		int numDuplicatedUsercmds = msg.ReadByte();
		int aheadOfServer = msg.ReadShort();

		int ucmdBytes = msg.ReadShort();
		byte ucmdBuffer[ MAX_MESSAGE_SIZE ];
		if ( ucmdBytes > 0 && ucmdBytes <= (int)sizeof( ucmdBuffer ) ) {
			msg.ReadData( ucmdBuffer, ucmdBytes );
		}
		idBitMsg ucmdMsg;
		ucmdMsg.InitRead( ucmdBuffer, ucmdBytes );
		ucmdMsg.SetSize( ucmdBytes );

		int gameBytes = msg.ReadShort();
		byte gameBuffer[ MAX_MESSAGE_SIZE ];
		if ( gameBytes > 0 && gameBytes <= (int)sizeof( gameBuffer ) ) {
			msg.ReadData( gameBuffer, gameBytes );
		}
		idBitMsg gameMsg;
		gameMsg.InitRead( gameBuffer, gameBytes );
		gameMsg.SetSize( gameBytes );

		snapshotGameFrame = sGameFrame;
		snapshotGameTime = sGameTime;

		// The client runs ahead of the server by about the latency, so its usercmds
		// arrive before the server runs their frames (Doom 3).  Behind the snapshot,
		// or further ahead than net_clientMaxPrediction: restart from the snapshot.
		const int maxPrediction = idAsyncNetwork::clientMaxPrediction.GetInteger();
		if ( gameTime < snapshotGameTime || gameTime > snapshotGameTime + maxPrediction ) {
			for ( int f = gameFrame; f <= snapshotGameFrame; ++f ) {
				memset( &userCmds[ f & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ], 0, sizeof( usercmd_t ) );
				userCmds[ f & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ].gameFrame = f;
				userCmds[ f & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ].gameTime = f * USERCMD_MSEC;
			}
			gameFrame = snapshotGameFrame;
			gameTime = snapshotGameTime;
			gameTimeResidual = idMath::ClampInt( -maxPrediction, maxPrediction, gameTimeResidual );
			clientPredictTime = idMath::ClampInt( -maxPrediction, maxPrediction, clientPredictTime );
		}

		// read only: the snapshot becomes the delta base when the server says so
		// (SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT); snapshotSequence is the ack
		if ( game->ClientReadSnapshot( sequence, sGameFrame, sGameTime, numDuplicatedUsercmds, aheadOfServer, gameMsg, ucmdMsg ) ) {
			snapshotSequence = sequence;
			lastSnapshotTime = realTime;
			predictedGameFrame = snapshotGameFrame;
			if ( clientState == CS_CONNECTED ) {
				clientState = CS_INGAME;	// first snapshot of this game
				gameTimeResidual = 0;
			}

			// aheadOfServer: how far ahead of the server's time our newest usercmd was
			// when the server wrote this snapshot.  Move the prediction a millisecond at
			// a time towards net_clientPrediction ahead, and the clock towards the
			// snapshot time plus that prediction (Doom 3).
			clientPrediction -= ( 1 - ( INTSIGNBITSET( aheadOfServer - idAsyncNetwork::clientPrediction.GetInteger() ) << 1 ) );
			clientPrediction = idMath::ClampInt( idAsyncNetwork::clientPrediction.GetInteger(), maxPrediction, clientPrediction );
			const int delta = gameTime - ( snapshotGameTime + clientPrediction );
			clientPredictTime -= ( delta / PREDICTION_FAST_ADJUST ) + ( 1 - ( INTSIGNBITSET( delta ) << 1 ) );

			if ( net_clientShowSnapshots.GetBool() ) {
				common->Printf( "Client received snapshot %d (frame %d)\n", sequence, sGameFrame );
			}
		}
	}
}

void idAsyncClient::ProcessReliableServerMessages() {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	while ( channel.GetReliableMessage( msg ) ) {
		const int type = msg.ReadByte();
		switch ( type ) {
			case SERVER_RELIABLE_MESSAGE_DISCONNECT:
				DisconnectFromServer();
				session->StartMenu();
				return;
			case SERVER_RELIABLE_MESSAGE_GAMEINIT: {
				// Loads the map here, so the reliable messages after it (the new
				// game's state) are handled on the new map.
				const int sGameInitId = msg.ReadLong();
				const int sGameFrame = msg.ReadLong();
				const int sGameTime = msg.ReadLong();
				idDict sServerInfo;
				msg.ReadDeltaDict( sServerInfo, NULL );
				InitGame( sGameInitId, sGameFrame, sGameTime, sServerInfo );
				break;
			}
			case SERVER_RELIABLE_MESSAGE_CLIENTINFO: {
				// The server's validated info for one player.  Not written back into
				// our ui_name cvar: that is archived, and would keep a server's '_'.
				const int infoNum = msg.ReadByte();
				idDict info;
				game->ReadUserInfo( msg, info );
				if ( infoNum >= 0 && infoNum < MAX_ASYNC_CLIENTS ) {
					sessLocal.mapSpawnData.userInfo[ infoNum ] = info;
					common->Printf( "Client user info %d: '%s'\n", infoNum, info.GetString( "ui_realname" ) );
					game->UserInfoChanged( infoNum );
				}
				break;
			}
			case SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT: {
				// the server made this snapshot its base for us; it is one we read (the
				// ack), and applying it frees the older ones
				const int sequence = msg.ReadLong();
				if ( !game->ClientApplySnapshot( sequence ) ) {
					common->Error( "idAsyncClient: couldn't apply snapshot %d", sequence );
				}
				break;
			}
			case SERVER_RELIABLE_MESSAGE_GAME:
				game->ClientProcessReliableMessage( msg );
				break;
			case SERVER_RELIABLE_MESSAGE_ENTERGAME:
				// the first snapshot of the game puts the client in it (InitGame)
				break;
			case SERVER_RELIABLE_MESSAGE_SYNCEDCVARS: {
				idDict& info = sessLocal.mapSpawnData.syncedCVars;
				msg.ReadDeltaDict( info, &info );
				cvarSystem->SetCVarsFromDict( info );
				break;
			}
			default:
				break;
		}
	}
}

void idAsyncClient::ProcessChallengeResponseMessage( const netadr_t from, const idBitMsg& msg ) {
	if ( !Sys_CompareNetAdrBase( from, serverAddress ) ) {
		return;
	}
	serverChallenge = msg.ReadLong();
	clientState = CS_CONNECTING;
	lastPacketTime = realTime;
}

void idAsyncClient::ProcessConnectResponseMessage( const netadr_t from, const idBitMsg& msg ) {
	if ( !Sys_CompareNetAdrBase( from, serverAddress ) || clientState != CS_CONNECTING ) {
		return;	// a repeated response must not reset the channel (Doom 3)
	}
	serverId = msg.ReadLong();
	clientNum = msg.ReadLong();
	channel.Init( from, clientId );
	clientState = CS_CONNECTED;
	lastPacketTime = realTime;
	common->Printf( "Client connected to server (slot %d, serverId %d)\n", clientNum, serverId );
	// first estimate of how far ahead to run: the connect round trip (Doom 3)
	clientPredictTime = clientPrediction = idMath::ClampInt( 0, idAsyncNetwork::clientMaxPrediction.GetInteger(), realTime - lastConnectTime );
	SendUserInfoToServer();
}

void idAsyncClient::ProcessDisconnectMessage( const netadr_t from, const idBitMsg& msg ) {
	if ( Sys_CompareNetAdrBase( from, serverAddress ) ) {
		DisconnectFromServer();
		session->StartMenu();
	}
}

void idAsyncClient::ProcessInfoResponseMessage( const netadr_t from, const idBitMsg& msg ) {
	networkServer_t server;
	memset( (void*)&server, 0, sizeof( server ) );
	server.adr = from;
	server.ping = 0;
	serverList.InfoResponse( server );
}

void idAsyncClient::ProcessPrintMessage( const netadr_t from, const idBitMsg& msg ) {
	int opcode = msg.ReadByte();
	(void)opcode;
	char text[ 4096 ];
	msg.ReadString( text, sizeof( text ) );
	common->Printf( "%s\n", text );
}

void idAsyncClient::ProcessServersListMessage( const netadr_t from, const idBitMsg& msg ) {
}

void idAsyncClient::ProcessAuthKeyMessage( const netadr_t from, const idBitMsg& msg ) {
}

void idAsyncClient::ProcessVersionMessage( const netadr_t from, const idBitMsg& msg ) {
}

void idAsyncClient::ConnectionlessMessage( const netadr_t from, const idBitMsg& msg ) {
	char command[ 1024 ];
	msg.ReadString( command, sizeof( command ) );
	if ( idStr::Icmp( command, "challengeResponse" ) == 0 ) {
		ProcessChallengeResponseMessage( from, msg );
	} else if ( idStr::Icmp( command, "connectResponse" ) == 0 ) {
		ProcessConnectResponseMessage( from, msg );
	} else if ( idStr::Icmp( command, "disconnect" ) == 0 ) {
		ProcessDisconnectMessage( from, msg );
	} else if ( idStr::Icmp( command, "infoResponse" ) == 0 ) {
		ProcessInfoResponseMessage( from, msg );
	} else if ( idStr::Icmp( command, "print" ) == 0 ) {
		ProcessPrintMessage( from, msg );
	}
}

void idAsyncClient::ProcessMessage( const netadr_t from, idBitMsg& msg ) {
	msg.BeginReading();
	const short id = msg.ReadShort();
	if ( id == CONNECTIONLESS_MESSAGE_ID ) {
		ConnectionlessMessage( from, msg );
		return;
	}
	// no channel yet, or a packet of another server on this address (the id is
	// the server's; a stale server's packet would move the channel to its port)
	if ( !active || clientState < CS_CONNECTED || id != serverId || !Sys_CompareNetAdrBase( from, channel.GetRemoteAddress() ) ) {
		return;
	}
	if ( sessLocal.insideExecuteMapChange ) {
		return;
	}
	int sequence = 0;
	if ( channel.Process( from, realTime, msg, sequence ) ) {
		serverMessageSequence = sequence;
		lastPacketTime = realTime;
		ProcessReliableServerMessages();
		ProcessUnreliableServerMessage( msg );
	}
}

void idAsyncClient::SetupConnection() {
	if ( realTime - lastConnectTime < CLIENT_CONNECT_RESEND_MSEC ) {
		return;
	}
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();

	if ( clientState == CS_CHALLENGING ) {
		common->Printf( "Sending challenge request to %s (clientId: %d)\n", Sys_NetAdrToString( serverAddress ), clientId );
		msg.WriteShort( CONNECTIONLESS_MESSAGE_ID );
		msg.WriteString( "getChallenge" );
		msg.WriteLong( clientId );
		clientPort.SendPacket( serverAddress, msg.GetData(), msg.GetSize() );
		lastConnectTime = realTime;
	} else if ( clientState == CS_CONNECTING ) {
		common->Printf( "Sending connect request to %s (challenge: %d)\n", Sys_NetAdrToString( serverAddress ), serverChallenge );
		msg.WriteShort( CONNECTIONLESS_MESSAGE_ID );
		msg.WriteString( "connect" );
		msg.WriteLong( ASYNC_PROTOCOL_VERSION );
		msg.WriteShort( 0 ); // OS
		msg.WriteLong( clientDataChecksum );
		msg.WriteLong( serverChallenge );
		msg.WriteShort( clientId );
		msg.WriteLong( idAsyncNetwork::clientMaxRate.GetInteger() );
		msg.WriteString( cvarSystem->GetCVarString( "com_guid" ) );
		msg.WriteString( cvarSystem->GetCVarString( "password" ) );
		clientPort.SendPacket( serverAddress, msg.GetData(), msg.GetSize() );
		lastConnectTime = realTime;
	}
}

void idAsyncClient::ProcessPureMessage( const netadr_t from, const idBitMsg& msg ) {
}

bool idAsyncClient::ValidatePureServerChecksums( const netadr_t from, const idBitMsg& msg ) {
	return true;
}

void idAsyncClient::ProcessReliableMessagePure( const idBitMsg& msg ) {
}

const char* idAsyncClient::HandleGuiCommand( const char* cmd ) {
	return idAsyncNetwork::client.HandleGuiCommandInternal( cmd );
}

const char* idAsyncClient::HandleGuiCommandInternal( const char* cmd ) {
	if ( cmd != NULL && ( idStr::Icmp( cmd, "abort" ) == 0 || idStr::Icmp( cmd, "pure_abort" ) == 0 ) ) {
		DisconnectFromServer();
		return "";
	}
	return NULL;
}

void idAsyncClient::SendVersionDLUpdate( int state ) {
	updateState = static_cast< clientUpdateState_t >( state );
}

void idAsyncClient::HandleDownloads() {
}

void idAsyncClient::Idle() {
	SendEmptyToServer();
}

int idAsyncClient::UpdateTime( int clamp ) {
	const int now = Sys_Milliseconds();
	int delta = now - realTime;
	if ( delta < 0 ) {
		delta = 0;
	}
	if ( clamp > 0 && delta > clamp ) {
		delta = clamp;
	}
	realTime = now;
	clientTime += delta;
	gameTimeResidual += delta;
	return delta;
}

void idAsyncClient::ReadLocalizedServerString( const idBitMsg& msg, char* out, int maxLen ) {
	if ( out == NULL || maxLen <= 0 ) {
		return;
	}
	msg.ReadString( out, maxLen );
}

bool idAsyncClient::CheckTimeout() {
	if ( !active || lastPacketTime < 0 ) {
		return false;
	}
	return realTime - lastPacketTime > idAsyncNetwork::clientServerTimeout.GetInteger() * 1000;
}

void idAsyncClient::ProcessDownloadInfoMessage( const netadr_t from, const idBitMsg& msg ) {
}

int idAsyncClient::GetDownloadRequest( const int checksums[ MAX_PURE_PAKS ], int count, int gamePakChecksum ) {
	dlCount = idMath::ClampInt( 0, MAX_PURE_PAKS - 1, count );
	for ( int i = 0; i < dlCount; ++i ) {
		dlChecksums[ i ] = checksums[ i ];
	}
	if ( dlCount < MAX_PURE_PAKS ) {
		dlChecksums[ dlCount ] = 0;
	}
	dlRequest = Sys_Milliseconds();
	return dlRequest;
}

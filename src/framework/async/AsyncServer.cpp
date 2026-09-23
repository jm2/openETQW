// Copyright (C) 2007 Id Software, Inc.
//
// ETQW asynchronous server bootstrap.  This replaces the incompatible Doom 3
// implementation while the larger AsyncServerBase/Repeater split present in
// the Microsoft PDB is reconstructed.

#include "../../idlib/precompiled.h"
#pragma hdrstop

#include "../precompiled.h"
#include "AsyncNetwork.h"
#include "../Session_local.h"
#include "sdnet/SDNet.h"
#include "AllowFailureReason.h"
#include <cstdint>

static const int SERVER_EMPTY_RESEND_MSEC = 500;
static const int SERVER_SNAPSHOT_RESEND_MSEC = 250;	// resend an unacknowledged snapshot after this long
static const int SERVER_PING_RESEND_MSEC = 500;

/*
==================
Server_NormalizeMapRequest

si_map as typed ("quarry", "maps/quarry", "maps/quarry.entities") reduced to
one comparable form.  A campaign name passes through unchanged.
==================
*/
static idStr Server_NormalizeMapRequest( const char* request ) {
	idStr name = request != NULL ? request : "";
	name.BackSlashesToSlashes();
	name.StripFileExtension();
	if ( name.Icmpn( "maps/", 5 ) == 0 ) {
		name = name.Right( name.Length() - 5 );
	}
	return name;
}

idAsyncServer::idAsyncServer() :
	active( false ),
	realTime( 0 ),
	serverTime( 0 ),
	serverId( 0 ),
	serverDataChecksum( 0 ),
	localClientNum( -1 ),
	gameInitId( 0 ),
	gameFrame( 0 ),
	gameTime( 0 ),
	gameTimeResidual( 0 ),
	nextHeartbeatTime( 0 ),
	nextAsyncStatsTime( 0 ),
	serverReloadingEngine( false ),
	noRconOutput( true ),
	lastAuthTime( 0 ),
	stats_current( 0 ),
	stats_average_sum( 0 ),
	stats_max( 0 ),
	stats_max_index( 0 ) {
	memset( challenges, 0, sizeof( challenges ) );
	memset( userCmds, 0, sizeof( userCmds ) );
	memset( &rconAddress, 0, sizeof( rconAddress ) );
	memset( stats_outrate, 0, sizeof( stats_outrate ) );
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		ClearClient( i );
	}
}

bool idAsyncServer::InitPort() {
	if ( serverPort.GetPort() != 0 ) {
		return true;
	}

	const int configuredPort = cvarSystem->GetCVarInteger( "net_port" );
	if ( configuredPort != 0 ) {
		return serverPort.InitForPort( configuredPort );
	}
	for ( int i = 0; i < MAX_SERVER_PORTS; ++i ) {
		if ( serverPort.InitForPort( PORT_SERVER + i ) ) {
			return true;
		}
	}
	common->Printf( "Unable to open server network port.\n" );
	return false;
}

void idAsyncServer::ClosePort() {
	serverPort.Close();
	for ( int i = 0; i < MAX_CHALLENGES; ++i ) {
		challenges[ i ].authReplyPrint.Clear();
	}
}

void idAsyncServer::Spawn() {
	if ( active ) {
		return;
	}

	// Retail stops the frontend/map before it initializes the new server.  In
	// particular, no game client callbacks are made until the requested map has
	// created its rules object.
	session->Stop();
	if ( !InitPort() ) {
		return;
	}

	byte buffer[ MAX_MESSAGE_SIZE ];
	netadr_t from;
	int size = 0;
	while ( serverPort.GetPacket( from, buffer, size, sizeof( buffer ) ) ) {
	}

	memset( challenges, 0, sizeof( challenges ) );
	memset( userCmds, 0, sizeof( userCmds ) );
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		ClearClient( i );
	}

	active = true;
	realTime = Sys_Milliseconds();
	serverTime = realTime;
	serverId = realTime & CONNECTIONLESS_MESSAGE_ID_MASK;
	serverDataChecksum = declManager->GetChecksum();
	localClientNum = -1;
	gameInitId = 1;
	gameFrame = 0;
	gameTime = 0;
	gameTimeResidual = 0;
	serverReloadingEngine = false;

	common->Printf( "Server spawned on %s\n", Sys_NetAdrToString( serverPort.GetAdr() ) );
	ExecuteMapChange();
}

void idAsyncServer::Kill() {
	if ( !active ) {
		return;
	}
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState != SCS_FREE ) {
			ClearClient( i );
		}
	}
	if ( game != NULL ) {
		game->OnServerShutdown();
	}
	active = false;
	localClientNum = -1;
	loadedMapRequest.Clear();
	loadedRules.Clear();
}

/*
==================
idAsyncServer::IsMapRequestLoaded

True when mapRequest names the running map and si_rules still names the rules
it was started with, so spawnServer can use the cheaper serverMapRestart.  The
game's map restart keeps the current rules object; a rules change (an admin
stopwatch/objective changemap to the current map) must go through
ExecuteMapChange, where OnUserStartMap makes the new rules.
==================
*/
bool idAsyncServer::IsMapRequestLoaded( const char* mapRequest ) const {
	if ( !active || !sessLocal.MapSpawned() || loadedMapRequest.IsEmpty() ) {
		return false;
	}
	return Server_NormalizeMapRequest( mapRequest ).Icmp( loadedMapRequest ) == 0 &&
		idStr::Icmp( cvarSystem->GetCVarString( "si_rules" ), loadedRules ) == 0;
}

void idAsyncServer::ExecuteMapChange() {
	if ( !active ) {
		return;
	}

	const idStr requestedMap = cvarSystem->GetCVarString( "si_map" );
	if ( requestedMap.IsEmpty() || game == NULL ) {
		return;
	}

	// OnUserStartMap selects and constructs the sdGameRules instance.  This
	// must precede ServerClientConnect: GUID authentication writes the local
	// client's user group through gameLocal.rules.
	idStr reason;
	idStr mapName;
	const userMapChangeResult_e result = game->OnUserStartMap( requestedMap.c_str(), reason, mapName );
	if ( result == UMCR_ERROR ) {
		common->Printf( "User Map Start Denied '%s'\n", reason.c_str() );
		return;
	}
	if ( result == UMCR_STOP ) {
		return;
	}

	serverTime = Sys_Milliseconds();
	gameFrame = 0;
	gameTime = 0;
	gameTimeResidual = 0;
	memset( userCmds, 0, sizeof( userCmds ) );
	++gameInitId;

	localClientNum = idAsyncNetwork::serverDedicated.GetBool() ? -1 : 0;
	if ( localClientNum >= 0 ) {
		InitLocalClient( localClientNum );
	}

	sessLocal.mapSpawnData.serverInfo = *cvarSystem->MoveCVarsToDict( CVAR_SERVERINFO );
	sessLocal.mapSpawnData.syncedCVars = *cvarSystem->MoveCVarsToDict( CVAR_NETWORKSYNC );
	if ( localClientNum >= 0 ) {
		sessLocal.mapSpawnData.userInfo[ localClientNum ] = *cvarSystem->MoveCVarsToDict( CVAR_USERINFO );
		game->SetClientNum( localClientNum, true );
	}

	ReinitRemoteClients();

	loadedMapRequest.Clear();
	loadedRules.Clear();
	sessLocal.ExecuteMapChange( mapName.c_str(), 0, true, true, true );
	if ( !sessLocal.MapSpawned() ) {
		return;
	}
	loadedMapRequest = Server_NormalizeMapRequest( requestedMap.c_str() );
	loadedRules = cvarSystem->GetCVarString( "si_rules" );	// after OnUserStartMap, which may force the rules
	if ( localClientNum >= 0 ) {
		BeginLocalClient();
	}
	BeginBotClients();

	// the load can take longer than net_serverClientTimeout
	const int now = Sys_Milliseconds();
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( i != localClientNum && !clients[ i ].isBot && clients[ i ].clientState == SCS_CONNECTED ) {
			clients[ i ].lastPacketTime = now;
			clients[ i ].lastInputTime = now;
		}
	}
}

int idAsyncServer::GetPort() const {
	return serverPort.GetPort();
}

netadr_t idAsyncServer::GetBoundAdr() const {
	return serverPort.GetAdr();
}

int idAsyncServer::GetOutgoingRate() const {
	int rate = 0;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED ) {
			rate += clients[ i ].channel.GetOutgoingRate();
		}
	}
	return rate;
}

int idAsyncServer::GetIncomingRate() const {
	int rate = 0;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED ) {
			rate += clients[ i ].channel.GetIncomingRate();
		}
	}
	return rate;
}

bool idAsyncServer::IsClientInGame( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS && clients[ clientNum ].clientState == SCS_INGAME;
}

int idAsyncServer::GetClientPing( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].clientPing : 0;
}

int idAsyncServer::GetClientPrediction( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].clientPrediction : 0;
}

int idAsyncServer::GetClientTimeSinceLastPacket( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? realTime - clients[ clientNum ].lastPacketTime : 0;
}

int idAsyncServer::GetClientTimeSinceLastInput( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? realTime - clients[ clientNum ].lastInputTime : 0;
}

int idAsyncServer::GetClientOutgoingRate( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].channel.GetOutgoingRate() : 0;
}

int idAsyncServer::GetClientIncomingRate( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].channel.GetIncomingRate() : 0;
}

float idAsyncServer::GetClientOutgoingCompression( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].channel.GetOutgoingCompression() : 0.0f;
}

float idAsyncServer::GetClientIncomingCompression( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].channel.GetIncomingCompression() : 0.0f;
}

float idAsyncServer::GetClientIncomingPacketLoss( int clientNum ) const {
	return clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ? clients[ clientNum ].channel.GetIncomingPacketLoss() : 0.0f;
}

int idAsyncServer::GetNumClients() const {
	int count = 0;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED ) {
			++count;
		}
	}
	return count;
}

int idAsyncServer::GetNumIdleClients() const {
	int count = 0;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED && realTime - clients[ i ].lastInputTime > 30000 ) {
			++count;
		}
	}
	return count;
}

const usercmd_t* idAsyncServer::GetClientUserCmd( int clientNum, int frameNum ) const {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || frameNum < 0 || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return NULL;
	}
	return &userCmds[ frameNum & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ];
}

const idDict& idAsyncServer::GetUserInfo( int clientNum ) const {
	static idDict emptyUserInfo;
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS ) {
		return emptyUserInfo;
	}
	return sessLocal.mapSpawnData.userInfo[ clientNum ];
}

int idAsyncServer::AllocateClientSlotForBot( int maxPlayersOnServer ) {
	if ( !active || game == NULL || !sessLocal.MapSpawned() ) {
		return -1;
	}
	const int slotLimit = idMath::ClampInt( 0, MAX_ASYNC_CLIENTS, maxPlayersOnServer );
	int clientNum = -1;
	for ( int i = 0; i < slotLimit; ++i ) {
		if ( clients[ i ].clientState == SCS_FREE ) {
			clientNum = i;
			break;
		}
	}
	if ( clientNum < 0 ) {
		return -1;
	}

	InitClient( clientNum, serverId ^ ( 0x40000000 | clientNum ), 0 );
	clients[ clientNum ].isBot = true;
	clients[ clientNum ].clientState = SCS_INGAME;

	idDict& userInfo = sessLocal.mapSpawnData.userInfo[ clientNum ];
	userInfo.Clear();
	const idStr botName = va( "Bot %d", clientNum );
	userInfo.Set( "ui_name", botName );
	userInfo.Set( "ui_realname", botName );
	userInfo.SetBool( "ui_bot", true );
	game->ValidateUserInfo( clientNum, userInfo );
	game->ServerClientConnect( clientNum );
	game->ServerClientBegin( clientNum, true );
	game->UserInfoChanged( clientNum );
	return clientNum;
}

int idAsyncServer::SetBotUserCommand( int clientNum, int frameNum, const usercmd_t& cmd ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || frameNum < 0 || !clients[ clientNum ].isBot || clients[ clientNum ].clientState != SCS_INGAME ) {
		return 0;
	}
	userCmds[ frameNum & ( MAX_USERCMD_BACKUP - 1 ) ][ clientNum ] = cmd;
	clients[ clientNum ].gameFrame = frameNum;
	clients[ clientNum ].gameTime = cmd.gameTime;
	clients[ clientNum ].lastInputTime = realTime;
	return 1;
}

int idAsyncServer::SetBotUserName( int clientNum, const char* playerName ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || playerName == NULL || playerName[ 0 ] == '\0' || !clients[ clientNum ].isBot || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return 0;
	}
	idDict& userInfo = sessLocal.mapSpawnData.userInfo[ clientNum ];
	userInfo.Set( "ui_name", playerName );
	userInfo.Set( "ui_realname", playerName );
	userInfo.SetBool( "ui_bot", true );
	game->ValidateUserInfo( clientNum, userInfo );
	game->UserInfoChanged( clientNum );
	SendUserInfoBroadcast( clientNum, userInfo, true );
	return 1;
}

void idAsyncServer::RunFrame() {
	if ( !active ) {
		return;
	}

	const int delta = UpdateTime( 100 );
	ProcessConnectionLessMessages();
	CheckClientTimeouts();

	// The requested map was denied or failed to load: the server stays up so
	// an admin can start another map, but there is no game to tick.
	if ( !sessLocal.MapSpawned() ) {
		gameTimeResidual = 0;
		return;
	}
	gameTimeResidual += delta;

	while ( gameTimeResidual >= USERCMD_MSEC ) {
		DuplicateUsercmds( gameFrame, gameTime );
		LocalClientInput();
		game->RunFrame( userCmds[ gameFrame & ( MAX_USERCMD_BACKUP - 1 ) ], USERCMD_MSEC );
		++gameFrame;
		gameTime += USERCMD_MSEC;
		gameTimeResidual -= USERCMD_MSEC;
	}

	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		// the local client shares the server's game: no snapshots, no channel traffic
		if ( i == localClientNum || clients[ i ].clientState < SCS_CONNECTED || clients[ i ].isBot ) {
			continue;
		}
		if ( !SendSnapshotToClient( i ) ) {
			// Keep the channel moving while no snapshot goes out (the client is
			// loading, or a snapshot is waiting for its ack): reliable messages and
			// their retransmissions only travel inside unreliable ones.
			SendEmptyToClient( i, false );
		}
		ProcessReliableClientMessages( i );
	}
	MasterHeartbeat();
}

void idAsyncServer::ProcessConnectionLessMessages() {
	byte buffer[ MAX_MESSAGE_SIZE ];
	netadr_t from;
	int size = 0;
	while ( serverPort.GetPacket( from, buffer, size, sizeof( buffer ) ) ) {
		idBitMsg msg;
		msg.InitWrite( buffer, sizeof( buffer ) );
		msg.SetSize( size );
		ProcessMessage( from, msg );
	}
}

void idAsyncServer::RemoteConsoleOutput( const char* string ) {
	if ( noRconOutput || string == NULL ) {
		return;
	}
	PrintOOB( rconAddress, SERVER_PRINT_RCON, string );
}

void idAsyncServer::SendReliableGameMessage( int clientNum, const idBitMsg& msg ) {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg out;
	out.InitWrite( buffer, sizeof( buffer ) );
	out.BeginWriting();
	out.WriteByte( SERVER_RELIABLE_MESSAGE_GAME );
	out.WriteData( msg.GetData(), msg.GetSize() );

	// only clients in the game (Doom 3 rule): a client waiting for its game init
	// gets the full state from ServerWriteInitialReliableMessages instead
	if ( clientNum == -1 ) {
		for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
			if ( clients[ i ].clientState == SCS_INGAME ) {
				SendReliableMessage( i, out );
			}
		}
	} else if ( clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS && clients[ clientNum ].clientState == SCS_INGAME ) {
		SendReliableMessage( clientNum, out );
	}
}

void idAsyncServer::SendReliableGameMessageExcluding( int clientNum, const idBitMsg& msg ) {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg out;
	out.InitWrite( buffer, sizeof( buffer ) );
	out.BeginWriting();
	out.WriteByte( SERVER_RELIABLE_MESSAGE_GAME );
	out.WriteData( msg.GetData(), msg.GetSize() );
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( i != clientNum && clients[ i ].clientState == SCS_INGAME ) {
			SendReliableMessage( i, out );
		}
	}
}

void idAsyncServer::LocalClientSendReliableMessage( const idBitMsg& msg ) {
	if ( localClientNum >= 0 ) {
		game->ServerProcessReliableMessage( localClientNum, msg );
	}
}

void idAsyncServer::MasterHeartbeat( bool force ) {
	if ( !force && realTime < nextHeartbeatTime ) {
		return;
	}
	nextHeartbeatTime = realTime + 5 * 60 * 1000;
}

void idAsyncServer::DropClient( int clientNum, const char* reason ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState == SCS_FREE ) {
		return;
	}
	common->Printf( "client %d dropped: %s\n", clientNum, reason != NULL ? reason : "" );
	if ( game != NULL ) {
		game->ServerClientDisconnect( clientNum );
	}
	if ( clients[ clientNum ].isBot ) {
		sessLocal.mapSpawnData.userInfo[ clientNum ].Clear();
		ClearClient( clientNum );
		return;
	}
	clients[ clientNum ].clientState = SCS_ZOMBIE;
	clients[ clientNum ].lastPacketTime = realTime;
}

void idAsyncServer::PacifierUpdate() {
	if ( active ) {
		ProcessConnectionLessMessages();
	}
}

void idAsyncServer::UpdateUI( int clientNum ) {
}

void idAsyncServer::UpdateAsyncStatsAvg() {
	const int value = GetOutgoingRate();
	stats_average_sum -= stats_outrate[ stats_current ];
	stats_outrate[ stats_current ] = value;
	stats_average_sum += value;
	if ( value > stats_max ) {
		stats_max = value;
		stats_max_index = stats_current;
	}
	stats_current = ( stats_current + 1 ) % stats_numsamples;
}

void idAsyncServer::GetAsyncStatsAvgMsg( idStr& msg ) {
	msg = va( "out: avg %d B/s, max %d B/s", stats_average_sum / stats_numsamples, stats_max );
}

void idAsyncServer::PrintLocalServerInfo() {
	common->Printf( "server %s: %d clients\n", Sys_NetAdrToString( serverPort.GetAdr() ), GetNumClients() );
}

void idAsyncServer::PrintClientSlots() const {
	if ( !active ) {
		common->Printf( "server is not running\n" );
		return;
	}
	static const char* stateNames[] = { "free", "zombie", "purewait", "connected", "ingame" };
	common->Printf( "map: %s\n", loadedMapRequest.IsEmpty() ? "<none>" : loadedMapRequest.c_str() );
	int used = 0;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		const serverClient_t& client = clients[ i ];
		if ( client.clientState == SCS_FREE ) {
			continue;
		}
		const int state = client.clientState;
		common->Printf( "slot %2d: %-9s %s '%s'\n", i,
			state >= 0 && state < static_cast< int >( sizeof( stateNames ) / sizeof( stateNames[ 0 ] ) ) ? stateNames[ state ] : "?",
			client.isBot ? "bot  " : "human",
			sessLocal.mapSpawnData.userInfo[ i ].GetString( "ui_name" ) );
		++used;
	}
	common->Printf( "%d client slot%s in use\n", used, used == 1 ? "" : "s" );
}

void idAsyncServer::PrintOOB( const netadr_t to, int opcode, const char* string ) {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteShort( CONNECTIONLESS_MESSAGE_ID );
	msg.WriteString( "print" );
	msg.WriteByte( opcode );
	msg.WriteString( string != NULL ? string : "" );
	serverPort.SendPacket( to, msg.GetData(), msg.GetSize() );
}

void idAsyncServer::DuplicateUsercmds( int frame, int time ) {
	if ( frame <= 0 ) {
		return;
	}
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		idAsyncNetwork::DuplicateUsercmd(
			userCmds[ ( frame - 1 ) & ( MAX_USERCMD_BACKUP - 1 ) ][ i ],
			userCmds[ frame & ( MAX_USERCMD_BACKUP - 1 ) ][ i ],
			frame,
			time
		);
	}
}

void idAsyncServer::ClearClient( int clientNum ) {
	serverClient_t& client = clients[ clientNum ];
	client.OS = 0;
	client.clientId = 0;
	client.clientState = SCS_FREE;
	client.clientPrediction = 0;
	client.clientAheadTime = 0;
	client.clientRate = 0;
	client.clientPing = 0;
	client.gameInitSequence = 0;
	client.gameFrame = 0;
	client.gameTime = 0;
	client.lastConnectTime = 0;
	client.lastEmptyTime = 0;
	client.lastPingTime = 0;
	client.lastSnapshotTime = 0;
	client.lastPacketTime = 0;
	client.lastInputTime = 0;
	client.snapshotSequence = 0;
	client.acknowledgeSnapshotSequence = 0;
	client.numDuplicatedUsercmds = 0;
	client.isBot = false;
	client.isMapLoading = false;
	client.lastSnapshotSize = 0;
	client.guid[ 0 ] = '\0';
	client.channel.Shutdown();
}

void idAsyncServer::InitClient( int clientNum, int clientId, int clientRate ) {
	ClearClient( clientNum );
	serverClient_t& client = clients[ clientNum ];
	client.clientId = clientId;
	client.clientRate = clientRate;
	client.clientState = SCS_CONNECTED;
	client.lastConnectTime = realTime;
	client.lastPacketTime = realTime;
	client.channel.SetMaxOutgoingRate( clientRate );
}

void idAsyncServer::InitLocalClient( int clientNum ) {
	InitClient( clientNum, serverId, 0 );
	clients[ clientNum ].channel.Init( serverPort.GetAdr(), serverId );
}

void idAsyncServer::BeginLocalClient() {
	if ( localClientNum >= 0 && clients[ localClientNum ].clientState == SCS_CONNECTED ) {
		idDict& userInfo = sessLocal.mapSpawnData.userInfo[ localClientNum ];
		if ( userInfo.GetString( "ui_name" )[ 0 ] == '\0' ) {
			userInfo.Set( "ui_name", "Player" );
		}
		userInfo.Set( "ui_realname", userInfo.GetString( "ui_name" ) );
		game->ValidateUserInfo( localClientNum, userInfo );
		clients[ localClientNum ].clientState = SCS_INGAME;
		game->ServerClientConnect( localClientNum );
		game->ServerClientBegin( localClientNum, false );
		game->UserInfoChanged( localClientNum );
	}
}

/*
==================
idAsyncServer::BeginBotClients

Bot clients stay connected across a map change, as they do in retail, but the
game's MapShutdown removed their player entities with the rest of the map.
Re-run the game side of AllocateClientSlotForBot for each one so the bot
spawns on the new map in the same client slot.  Without this every map change
leaked the bot slots (SCS_INGAME here, no player in the game) and addbot kept
taking fresh slots until none were left.
==================
*/
void idAsyncServer::BeginBotClients() {
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		serverClient_t& client = clients[ i ];
		if ( !client.isBot || client.clientState != SCS_INGAME ) {
			continue;
		}
		client.gameFrame = 0;
		client.gameTime = 0;
		client.lastInputTime = realTime;

		idDict& userInfo = sessLocal.mapSpawnData.userInfo[ i ];
		game->ValidateUserInfo( i, userInfo );
		game->ServerClientConnect( i );
		game->ServerClientBegin( i, true );
		game->UserInfoChanged( i );
	}
}

/*
==================
idAsyncServer::ReinitRemoteClients

Called before the server loads a new map.  Remote clients stay connected, but
their player entities go with the old map and their frames and snapshots
belong to the old game.  Put each one back in SCS_CONNECTED, as Doom 3's
ExecuteMapChange re-initialises every connected client before the load:
SendReliableGameMessage skips it, so none of the game's reliable messages from
the map shutdown and the new map's spawn reach a client that still has the
old map loaded, and the first SendSnapshotToClient after the load sends it the
game init (the client loads the map; the game spawns its player and writes
the initial reliable state).  The channel is kept (InitClient would shut it
down), and the snapshot sequence keeps counting up: the client drops a
snapshot whose sequence is not above the last one it applied, and the game
frees its per-client snapshot state in MapShutdown.
==================
*/
void idAsyncServer::ReinitRemoteClients() {
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		serverClient_t& client = clients[ i ];
		if ( i == localClientNum || client.isBot || client.clientState < SCS_CONNECTED ) {
			continue;
		}
		client.clientState = SCS_CONNECTED;
		client.isMapLoading = false;
		client.gameFrame = 0;
		client.gameTime = 0;
		client.clientAheadTime = 0;
		client.numDuplicatedUsercmds = 0;
		client.acknowledgeSnapshotSequence = client.snapshotSequence;
		client.lastSnapshotTime = 0;
		client.lastSnapshotSize = 0;
	}
}

void idAsyncServer::LocalClientInput() {
	if ( localClientNum < 0 || localClientNum >= MAX_ASYNC_CLIENTS ) {
		return;
	}
	usercmd_t& cmd = userCmds[ gameFrame & ( MAX_USERCMD_BACKUP - 1 ) ][ localClientNum ];
	cmd = usercmdGen->GetDirectUsercmd();
	cmd.gameFrame = gameFrame;
	cmd.gameTime = gameTime;
	cmd.duplicateCount = 0;
	clients[ localClientNum ].gameFrame = gameFrame;
	clients[ localClientNum ].gameTime = gameTime;
	clients[ localClientNum ].lastInputTime = realTime;
}

void idAsyncServer::CheckClientTimeouts() {
	const int timeout = idAsyncNetwork::serverClientTimeout.GetInteger() * 1000;
	const int zombieTimeout = idAsyncNetwork::serverZombieTimeout.GetInteger() * 1000;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState == SCS_ZOMBIE && realTime - clients[ i ].lastPacketTime > zombieTimeout ) {
			ClearClient( i );
		} else if ( clients[ i ].clientState >= SCS_CONNECTED && i != localClientNum && !clients[ i ].isBot &&
			realTime - clients[ i ].lastPacketTime > timeout ) {
			DropClient( i, "timed out" );
		}
	}
}

void idAsyncServer::SendPrintBroadcast( const char* string ) {
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED ) {
			SendPrintToClient( i, string );
		}
	}
}

void idAsyncServer::SendPrintToClient( int clientNum, const char* string ) {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_RELIABLE_MESSAGE_PRINT );
	msg.WriteString( string != NULL ? string : "" );
	SendReliableMessage( clientNum, msg );
}

void idAsyncServer::SendUserInfoBroadcast( int userInfoNum, const idDict& info, bool sendToAll ) {
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED && ( sendToAll || i != userInfoNum ) ) {
			SendUserInfoToClient( i, userInfoNum, info );
		}
	}
}

void idAsyncServer::SendUserInfoToClient( int clientNum, int userInfoNum, const idDict& info ) {
}

void idAsyncServer::SendSyncedCvarsBroadcast( const idDict& cvars ) {
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState >= SCS_CONNECTED ) {
			SendSyncedCvarsToClient( i, cvars );
		}
	}
}

void idAsyncServer::SendSyncedCvarsToClient( int clientNum, const idDict& cvars ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return;
	}
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_RELIABLE_MESSAGE_SYNCEDCVARS );
	msg.WriteDeltaDict( cvars, NULL );
	SendReliableMessage( clientNum, msg );
}

void idAsyncServer::SendApplySnapshotToClient( int clientNum, int sequence ) {
}

/*
==================
idAsyncServer::SendMessageToClient

Sends an unreliable message and every fragment it needs.  The channel packs
the queued reliable messages into each unreliable send, so after a map load
even a small message (the game init) can exceed FRAGMENT_SIZE; fragments left
unsent make the channel refuse every later message and SendSnapshotToClient
wait forever, so the client never saw the game init.  Returns the channel's
result: -1 when it refused the message (queued reliable messages plus the
message exceed MAX_MESSAGE_SIZE).
==================
*/
int idAsyncServer::SendMessageToClient( serverClient_t& client, const idBitMsg& msg ) {
	const int result = client.channel.SendMessage( serverPort, realTime, msg );
	while ( client.channel.UnsentFragmentsLeft() ) {
		client.channel.SendNextFragment( serverPort, realTime );
	}
	return result;
}

bool idAsyncServer::SendEmptyToClient( int clientNum, bool force ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return false;
	}
	serverClient_t& client = clients[ clientNum ];
	if ( !force && realTime - Max( client.lastEmptyTime, client.lastSnapshotTime ) < SERVER_EMPTY_RESEND_MSEC ) {
		return false;
	}
	byte buffer[ 16 ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_UNRELIABLE_MESSAGE_EMPTY );
	SendMessageToClient( client, msg );
	client.lastEmptyTime = realTime;
	return true;
}

bool idAsyncServer::SendPingToClient( int clientNum ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS ) {
		return false;
	}
	serverClient_t& client = clients[ clientNum ];
	if ( realTime - client.lastPingTime < SERVER_PING_RESEND_MSEC ) {
		return false;
	}
	byte buffer[ 16 ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_UNRELIABLE_MESSAGE_PING );
	msg.WriteLong( realTime );
	SendMessageToClient( client, msg );
	client.lastPingTime = realTime;
	return true;
}

/*
==================
idAsyncServer::SendGameInitToClient

The game init is a reliable message (protocol minor 2), queued ahead of the
new game's reliable state: the channel retransmits it until it is
acknowledged, and the client loads the map when it handles it, before the
state that follows.  As an unreliable message it could be lost, or overtaken
by that state, which the client then applied to the old map.  The state goes
out now rather than when the client has loaded: the game's client-side map
scripts need the networked entities before the client's first frame.
==================
*/
void idAsyncServer::SendGameInitToClient( int clientNum ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return;
	}
	serverClient_t& client = clients[ clientNum ];

	common->Printf( "Sending game init to client %d\n", clientNum );

	// 1. The game init
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_RELIABLE_MESSAGE_GAMEINIT );
	msg.WriteLong( gameInitId );
	msg.WriteLong( gameFrame );
	msg.WriteLong( gameTime );
	msg.WriteDeltaDict( sessLocal.mapSpawnData.serverInfo, NULL );
	SendReliableMessage( clientNum, msg );
	if ( client.clientState < SCS_CONNECTED ) {
		return;	// dropped (reliable overflow)
	}

	// 2. Transmit synced cvars
	SendSyncedCvarsToClient( clientNum, sessLocal.mapSpawnData.syncedCVars );

	// 3. Transmit reliable enter game notification (sets SCS_INGAME, so the
	// game's reliable messages below reach this client)
	SendEnterGameToClient( clientNum );

	// 4. Initialize client player entity in game module
	idDict& userInfo = sessLocal.mapSpawnData.userInfo[ clientNum ];
	if ( userInfo.GetString( "ui_name" )[ 0 ] == '\0' ) {
		userInfo.Set( "ui_name", va( "Player %d", clientNum ) );
	}
	if ( userInfo.GetString( "ui_realname" )[ 0 ] == '\0' ) {
		userInfo.Set( "ui_realname", userInfo.GetString( "ui_name" ) );
	}
	game->ValidateUserInfo( clientNum, userInfo );

	game->ServerClientConnect( clientNum );
	game->ServerClientBegin( clientNum, false );
	game->UserInfoChanged( clientNum );

	// 5. Send initial reliable game state burst
	game->ServerWriteInitialReliableMessages( clientNum );

	// 6. Transition server client state to SCS_INGAME, wait for client map load
	// (its first usercmd of this game clears isMapLoading)
	client.clientState = SCS_INGAME;
	client.isMapLoading = true;

	// carry the reliable messages now; later empties retransmit them
	SendEmptyToClient( clientNum, true );
}

bool idAsyncServer::SendSnapshotToClient( int clientNum ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return false;
	}
	serverClient_t& client = clients[ clientNum ];
	if ( client.clientState == SCS_CONNECTED ) {
		SendGameInitToClient( clientNum );
		return true;
	}
	if ( client.clientState != SCS_INGAME || client.isMapLoading ) {
		return false;
	}
	if ( realTime - client.lastSnapshotTime < idAsyncNetwork::serverSnapshotDelay.GetInteger() ) {
		return false;
	}
	if ( !client.channel.ReadyToSend( realTime ) || client.channel.UnsentFragmentsLeft() ) {
		return false;
	}

	// Snapshots are stop-and-wait: the client makes each snapshot its delta base
	// as soon as it reads it, so the next one is written only after this one is
	// acknowledged.  Acks ride on every usercmd message, so a missing ack means
	// the snapshot (or one of its fragments) was lost: resend the same message.
	// Without the resend one lost packet stopped the client's snapshots for good.
	// The client drops a duplicate (its sequence is not newer).
	if ( client.snapshotSequence > 0 && client.acknowledgeSnapshotSequence < client.snapshotSequence ) {
		if ( client.lastSnapshotSize <= 0 || realTime - client.lastSnapshotTime < SERVER_SNAPSHOT_RESEND_MSEC ) {
			return false;
		}
		idBitMsg resendMsg;
		resendMsg.InitWrite( client.lastSnapshot, sizeof( client.lastSnapshot ) );
		resendMsg.SetSize( client.lastSnapshotSize );
		if ( SendMessageToClient( client, resendMsg ) < 0 ) {
			SendEmptyToClient( clientNum, true );	// let the reliable backlog drain first
		}
		client.lastSnapshotTime = realTime;
		return true;
	}

	const int sequence = client.snapshotSequence + 1;

	byte gameBuffer[ MAX_MESSAGE_SIZE ];
	byte ucmdBuffer[ MAX_MESSAGE_SIZE ];
	idBitMsg gameMsg;
	idBitMsg ucmdMsg;
	gameMsg.InitWrite( gameBuffer, sizeof( gameBuffer ) );
	gameMsg.BeginWriting();
	ucmdMsg.InitWrite( ucmdBuffer, sizeof( ucmdBuffer ) );
	ucmdMsg.BeginWriting();

	game->ServerWriteSnapshot( clientNum, sequence, gameMsg, ucmdMsg );

	if ( gameMsg.GetNumBitsWritten() == 0 ) {
		// nothing written (no player yet): the sequence stays unused, since an
		// unsent sequence would never be acknowledged
		return SendEmptyToClient( clientNum, true );
	}
	client.snapshotSequence = sequence;

	const int64_t clientAheadTime = static_cast<int64_t>( client.gameTime ) - static_cast<int64_t>( gameTime ) - static_cast<int64_t>( gameTimeResidual );
	int aheadOfServer = clientAheadTime < -32768 ? -32768 : ( clientAheadTime > 32767 ? 32767 : static_cast<int>( clientAheadTime ) );
	int numDuplicatedUsercmds = idMath::ClampInt( 0, 255, client.numDuplicatedUsercmds );

	idBitMsg snapshotMsg;
	snapshotMsg.InitWrite( client.lastSnapshot, sizeof( client.lastSnapshot ) );	// kept for resends
	snapshotMsg.BeginWriting();
	snapshotMsg.WriteByte( SERVER_UNRELIABLE_MESSAGE_SNAPSHOT );
	snapshotMsg.WriteLong( gameInitId );
	snapshotMsg.WriteLong( sequence );
	snapshotMsg.WriteLong( gameFrame );
	snapshotMsg.WriteLong( gameTime );
	snapshotMsg.WriteByte( numDuplicatedUsercmds );
	snapshotMsg.WriteShort( aheadOfServer );

	snapshotMsg.WriteShort( ucmdMsg.GetSize() );
	snapshotMsg.WriteData( ucmdMsg.GetData(), ucmdMsg.GetSize() );

	snapshotMsg.WriteShort( gameMsg.GetSize() );
	snapshotMsg.WriteData( gameMsg.GetData(), gameMsg.GetSize() );
	client.lastSnapshotSize = snapshotMsg.GetSize();

	if ( SendMessageToClient( client, snapshotMsg ) < 0 ) {
		SendEmptyToClient( clientNum, true );	// resent once the reliable backlog has drained
	}

	client.lastSnapshotTime = realTime;
	return true;
}

void idAsyncServer::ProcessUnreliableClientMessage( int clientNum, const idBitMsg& msg ) {
	clients[ clientNum ].lastPacketTime = realTime;
	const int type = msg.ReadByte();
	if ( type == CLIENT_UNRELIABLE_MESSAGE_EMPTY || type == CLIENT_UNRELIABLE_MESSAGE_USERCMD ) {
		// A message written before the client loaded the current game (sent
		// before a map change, or while the game init was in flight) carries an
		// older id: its load state, snapshot ack and usercmds belong to that game.
		if ( msg.ReadLong() != gameInitId ) {
			return;
		}
	}
	if ( type == CLIENT_UNRELIABLE_MESSAGE_EMPTY ) {
		bool mapLoad = msg.ReadBool();
		clients[ clientNum ].lastPacketTime = realTime;
		clients[ clientNum ].isMapLoading = mapLoad;
		return;
	} else if ( type == CLIENT_UNRELIABLE_MESSAGE_PINGRESPONSE ) {
		clients[ clientNum ].clientPing = realTime - msg.ReadLong();
	} else if ( type == CLIENT_UNRELIABLE_MESSAGE_USERCMD ) {
		clients[ clientNum ].isMapLoading = false;
		if ( msg.GetRemainingData() < 4 + 4 + 1 ) {
			return;
		}
		int ackSnapshotSeq = msg.ReadLong();
		int clientGameFrame = msg.ReadLong();
		int numUsercmds = msg.ReadByte();

		if ( numUsercmds < 1 || numUsercmds > MAX_USERCMD_RELAY || clientGameFrame < 0 ) {
			return;
		}

		if ( ackSnapshotSeq > clients[ clientNum ].acknowledgeSnapshotSequence ) {
			clients[ clientNum ].acknowledgeSnapshotSequence = ackSnapshotSeq;
			game->ServerApplySnapshot( clientNum, ackSnapshotSeq );
		}

		if ( clientGameFrame > gameFrame + MAX_USERCMD_BACKUP || clientGameFrame < gameFrame - MAX_USERCMD_BACKUP ) {
			common->Printf( "WARNING: Server dropped client usercmds: clientGameFrame=%d, serverGameFrame=%d\n", clientGameFrame, gameFrame );
			return;
		}

		const int firstClientGameFrame = clientGameFrame - numUsercmds + 1;
		usercmd_t dummyLast;
		const usercmd_t* last = NULL;
		for ( int j = 0; j < numUsercmds; ++j ) {
			int frame = firstClientGameFrame + j;
			usercmd_t decodedCmd;
			idAsyncNetwork::ReadUserCmdDelta( msg, decodedCmd, last );
			if ( msg.GetRemainingData() < 0 ) {
				return;
			}
			if ( frame < 0 ) {
				dummyLast = decodedCmd;
				last = &dummyLast;
				continue;
			}
			int index = frame & ( MAX_USERCMD_BACKUP - 1 );
			usercmd_t& cmd = userCmds[ index ][ clientNum ];
			cmd = decodedCmd;
			cmd.gameFrame = frame;
			cmd.gameTime = frame * USERCMD_MSEC;
			cmd.duplicateCount = 0;
			last = &cmd;
		}

		if ( clientGameFrame > clients[ clientNum ].gameFrame ) {
			clients[ clientNum ].gameFrame = clientGameFrame;
			clients[ clientNum ].gameTime = clientGameFrame * USERCMD_MSEC;
		}
		clients[ clientNum ].lastInputTime = realTime;
	}
}

void idAsyncServer::ProcessReliableClientMessages( int clientNum ) {
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	while ( clients[ clientNum ].channel.GetReliableMessage( msg ) ) {
		const int type = msg.ReadByte();
		if ( type == CLIENT_RELIABLE_MESSAGE_DISCONNECT ) {
			DropClient( clientNum, "disconnected" );
			return;
		}
		if ( type == CLIENT_RELIABLE_MESSAGE_GAME ) {
			game->ServerProcessReliableMessage( clientNum, msg );
		}
	}
}

void idAsyncServer::ProcessChallengeMessage( const netadr_t from, const idBitMsg& msg ) {
	if ( msg.GetRemainingData() < 4 ) {
		return;
	}
	int clientId = msg.ReadLong();

	int slot = -1;
	int oldest = 0;
	int oldestTime = realTime;

	for ( int i = 0; i < MAX_CHALLENGES; ++i ) {
		if ( challenges[ i ].time != 0 && realTime - challenges[ i ].time > 10000 ) {
			challenges[ i ].clientId = 0;
			challenges[ i ].challenge = 0;
			challenges[ i ].time = 0;
			challenges[ i ].pingTime = 0;
			challenges[ i ].connected = false;
		}
		if ( challenges[ i ].time != 0 && challenges[ i ].clientId == clientId &&
			 Sys_CompareNetAdrBase( challenges[ i ].address, from ) ) {
			slot = i;
			break;
		}
		if ( challenges[ i ].time == 0 && slot == -1 ) {
			slot = i;
		}
		if ( challenges[ i ].time < oldestTime ) {
			oldestTime = challenges[ i ].time;
			oldest = i;
		}
	}

	if ( slot == -1 ) {
		slot = oldest;
	}

	int challengeVal = challenges[ slot ].challenge;
	if ( challengeVal == 0 ) {
		challengeVal = ( ( rand() << 16 ) ^ rand() ^ realTime ) & 0x7FFFFFFF;
		if ( challengeVal == 0 ) {
			challengeVal = 1;
		}
	}

	challenges[ slot ].address = from;
	challenges[ slot ].clientId = clientId;
	challenges[ slot ].challenge = challengeVal;
	challenges[ slot ].time = realTime;
	challenges[ slot ].pingTime = realTime;
	challenges[ slot ].connected = false;

	common->Printf( "Challenge generated for %s (id: %d, challenge: %d)\n", Sys_NetAdrToString( from ), clientId, challengeVal );

	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg outMsg;
	outMsg.InitWrite( buffer, sizeof( buffer ) );
	outMsg.BeginWriting();
	outMsg.WriteShort( CONNECTIONLESS_MESSAGE_ID );
	outMsg.WriteString( "challengeResponse" );
	outMsg.WriteLong( challengeVal );
	serverPort.SendPacket( from, outMsg.GetData(), outMsg.GetSize() );
}

void idAsyncServer::ProcessConnectMessage( const netadr_t from, const idBitMsg& msg ) {
	if ( msg.GetRemainingData() < 4 + 2 + 4 + 4 + 2 + 4 ) {
		return;
	}
	int protocol = msg.ReadLong();
	int OS = msg.ReadShort();
	int clientChecksum = msg.ReadLong();
	int challenge = msg.ReadLong();
	int clientId = msg.ReadShort();
	int clientRate = msg.ReadLong();

	int ichallenge = ValidateChallenge( from, challenge, clientId );
	if ( ichallenge == -1 ) {
		PrintOOB( from, SERVER_PRINT_BADCHALLENGE, "Bad challenge" );
		return;
	}

	if ( protocol != ASYNC_PROTOCOL_VERSION ) {
		PrintOOB( from, SERVER_PRINT_BADPROTOCOL, va( "Server uses protocol %d.%d", OPENETQW_PROTOCOL_MAJOR, ASYNC_PROTOCOL_MINOR ) );
		return;
	}

	if ( sessLocal.mapSpawnData.serverInfo.GetBool( "si_pure" ) && serverDataChecksum != 0 && clientChecksum != serverDataChecksum ) {
		PrintOOB( from, SERVER_PRINT_MISC, "Data checksum mismatch" );
		return;
	}

	char guid[ 12 ] = { 0 };
	char password[ 32 ] = { 0 };
	msg.ReadString( guid, sizeof( guid ) );
	msg.ReadString( password, sizeof( password ) );

	clientNetworkAddress_t netAddr;
	netAddr.ip[0] = from.ip[0];
	netAddr.ip[1] = from.ip[1];
	netAddr.ip[2] = from.ip[2];
	netAddr.ip[3] = from.ip[3];
	netAddr.port = from.port;

	sdNetClientId netClientId;
	netClientId.id[0] = clientId;
	netClientId.id[1] = 0;

	allowFailureReason_t reason = ALLOWFAIL_UNKNOWN;
	allowReply_t reply = ( game != NULL ) ? game->ServerAllowClient( GetNumClients(), 0, netAddr, netClientId, guid, password, reason ) : ALLOW_YES;
	if ( reply != ALLOW_YES ) {
		const char* reasonStr = "Connection rejected";
		switch ( reason ) {
			case ALLOWFAIL_PERMBAN: reasonStr = "Permanently banned"; break;
			case ALLOWFAIL_TEMPBAN: reasonStr = "Temporarily banned"; break;
			case ALLOWFAIL_SERVERSPAWNING: reasonStr = "Server is spawning"; break;
			case ALLOWFAIL_SERVERFULL: reasonStr = "Server is full"; break;
			case ALLOWFAIL_SERVERNOMAP: reasonStr = "Server has no map"; break;
			case ALLOWFAIL_SERVERMISCONFIGURED: reasonStr = "Server misconfigured"; break;
			case ALLOWFAIL_SERVERNOSYNC: reasonStr = "Server no sync"; break;
			case ALLOWFAIL_SERVERONLYLAN: reasonStr = "Server is LAN only"; break;
			case ALLOWFAIL_SERVERONLYAUTH: reasonStr = "Server requires authentication"; break;
			case ALLOWFAIL_SERVERNOTAUTHENTICATED: reasonStr = "Client not authenticated"; break;
			case ALLOWFAIL_SERVERWRONGVERSION: reasonStr = "Wrong version"; break;
			case ALLOWFAIL_INVALIDPASSWORD: reasonStr = "Invalid password"; break;
			case ALLOWFAIL_PUNKBUSTER: reasonStr = "PunkBuster error"; break;
			case ALLOWFAIL_NOREPEATERSALOWED: reasonStr = "No repeaters allowed"; break;
			default: break;
		}
		PrintOOB( from, SERVER_PRINT_GAMEDENY, reasonStr );
		return;
	}

	int clientNum = -1;
	// 3-pass slot allocation
	for ( int islot = 0; islot < 3 && clientNum == -1; ++islot ) {
		for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
			if ( islot == 0 ) {
				if ( Sys_CompareNetAdrBase( from, clients[ i ].channel.GetRemoteAddress() ) &&
					 ( clientId == clients[ i ].clientId || from.port == clients[ i ].channel.GetRemoteAddress().port ) ) {
					clientNum = i;
					break;
				}
			} else if ( islot == 1 ) {
				if ( clients[ i ].clientState < SCS_CONNECTED && Sys_CompareNetAdrBase( from, clients[ i ].channel.GetRemoteAddress() ) ) {
					clientNum = i;
					break;
				}
			} else if ( islot == 2 ) {
				if ( clients[ i ].clientState == SCS_FREE ) {
					clientNum = i;
					break;
				}
			}
		}
	}

	if ( clientNum == -1 ) {
		PrintOOB( from, SERVER_PRINT_MISC, "Server is full" );
		return;
	}

	InitClient( clientNum, clientId, clientRate );
	clients[ clientNum ].channel.Init( from, serverId );
	clients[ clientNum ].OS = OS;
	idStr::Copynz( clients[ clientNum ].guid, guid, sizeof( clients[ clientNum ].guid ) );
	challenges[ ichallenge ].connected = true;

	common->Printf( "Client %d connected from %s (clientId: %d)\n", clientNum, Sys_NetAdrToString( from ), clientId );

	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg outMsg;
	outMsg.InitWrite( buffer, sizeof( buffer ) );
	outMsg.BeginWriting();
	outMsg.WriteShort( CONNECTIONLESS_MESSAGE_ID );
	outMsg.WriteString( "connectResponse" );
	outMsg.WriteLong( serverId );
	outMsg.WriteLong( clientNum );
	serverPort.SendPacket( from, outMsg.GetData(), outMsg.GetSize() );
}

void idAsyncServer::ProcessRemoteConsoleMessage( const netadr_t from, const idBitMsg& msg ) {
	rconAddress = from;
	noRconOutput = false;
}

void idAsyncServer::ProcessGetInfoMessage( const netadr_t from, const idBitMsg& msg ) {
}

bool idAsyncServer::ConnectionlessMessage( const netadr_t from, const idBitMsg& msg ) {
	char command[ 1024 ];
	msg.ReadString( command, sizeof( command ) );
	if ( idStr::Icmp( command, "getChallenge" ) == 0 ) {
		ProcessChallengeMessage( from, msg );
		return true;
	}
	if ( idStr::Icmp( command, "connect" ) == 0 ) {
		ProcessConnectMessage( from, msg );
		return true;
	}
	if ( idStr::Icmp( command, "getInfo" ) == 0 ) {
		ProcessGetInfoMessage( from, msg );
		return true;
	}
	if ( idStr::Icmp( command, "rcon" ) == 0 ) {
		ProcessRemoteConsoleMessage( from, msg );
		return true;
	}
	return false;
}

bool idAsyncServer::ProcessMessage( const netadr_t from, idBitMsg& msg ) {
	msg.BeginReading();
	const short id = msg.ReadShort();
	if ( id == CONNECTIONLESS_MESSAGE_ID ) {
		return ConnectionlessMessage( from, msg );
	}
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		serverClient_t& client = clients[ i ];
		if ( client.clientState < SCS_CONNECTED || client.clientId != id ||
			!Sys_CompareNetAdrBase( from, client.channel.GetRemoteAddress() ) ) {
			continue;
		}
		int sequence = 0;
		if ( client.channel.Process( from, realTime, msg, sequence ) ) {
			client.lastPacketTime = realTime;
			ProcessReliableClientMessages( i );
			ProcessUnreliableClientMessage( i, msg );
		}
		return true;
	}
	return false;
}

void idAsyncServer::ProcessAuthMessage( const idBitMsg& msg ) {
}

bool idAsyncServer::SendPureServerMessage( const netadr_t to, int OS ) {
	return false;
}

void idAsyncServer::ProcessPureMessage( const netadr_t from, const idBitMsg& msg ) {
}

int idAsyncServer::ValidateChallenge( const netadr_t from, int challenge, int clientId ) {
	for ( int i = 0; i < MAX_CHALLENGES; ++i ) {
		if ( challenges[ i ].challenge == challenge && challenges[ i ].clientId == clientId &&
			Sys_CompareNetAdrBase( challenges[ i ].address, from ) ) {
			return i;
		}
	}
	return -1;
}

bool idAsyncServer::SendReliablePureToClient( int clientNum ) {
	return false;
}

void idAsyncServer::ProcessReliablePure( int clientNum, const idBitMsg& msg ) {
}

bool idAsyncServer::VerifyChecksumMessage( int clientNum, const netadr_t* from, const idBitMsg& msg, idStr& reply, int OS ) {
	reply.Clear();
	return true;
}

void idAsyncServer::SendReliableMessage( int clientNum, const idBitMsg& msg ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return;
	}
	// The local client shares the server's game, and nothing ever acknowledges
	// its channel: queued messages only piled up until 'reliable message
	// overflow' dropped the host (Doom 3 skips it too).
	if ( clientNum == localClientNum ) {
		return;
	}
	if ( clients[ clientNum ].isBot ) {
		return;
	}
	if ( !clients[ clientNum ].channel.SendReliableMessage( msg ) ) {
		DropClient( clientNum, "reliable message overflow" );
	}
}

int idAsyncServer::UpdateTime( int clamp ) {
	const int now = Sys_Milliseconds();
	int delta = now - realTime;
	if ( delta < 0 ) {
		delta = 0;
	}
	if ( clamp > 0 && delta > clamp ) {
		delta = clamp;
	}
	realTime = now;
	serverTime += delta;
	return delta;
}

void idAsyncServer::SendEnterGameToClient( int clientNum ) {
	if ( clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ) {
		clients[ clientNum ].clientState = SCS_INGAME;
		byte buffer[ 16 ];
		idBitMsg msg;
		msg.InitWrite( buffer, sizeof( buffer ) );
		msg.BeginWriting();
		msg.WriteByte( SERVER_RELIABLE_MESSAGE_ENTERGAME );
		SendReliableMessage( clientNum, msg );
	}
}

void idAsyncServer::WriteClientUserCmds( int clientNum, idBitMsg& msg ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		msg.WriteByte( 0 );
		return;
	}
	int maxRelay = idMath::ClampInt( 1, MAX_USERCMD_RELAY, idAsyncNetwork::serverMaxUsercmdRelay.GetInteger() );
	int numUsercmds = idMath::ClampInt( 1, maxRelay, clients[ clientNum ].gameFrame - gameFrame + 1 );
	if ( numUsercmds < 1 ) {
		numUsercmds = 1;
	}
	msg.WriteByte( numUsercmds );
	const usercmd_t* last = NULL;
	for ( int j = 0; j < numUsercmds; ++j ) {
		int index = ( gameFrame + j ) & ( MAX_USERCMD_BACKUP - 1 );
		idAsyncNetwork::WriteUserCmdDelta( msg, userCmds[ index ][ clientNum ], last );
		last = &userCmds[ index ][ clientNum ];
	}
}

netadr_t idAsyncServer::GetClientAddress( int clientNum ) const {
	if ( clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS ) {
		return clients[ clientNum ].channel.GetRemoteAddress();
	}
	netadr_t adr;
	memset( &adr, 0, sizeof( adr ) );
	return adr;
}

void idAsyncServer::ProcessDownloadRequestMessage( const netadr_t from, const idBitMsg& msg ) {
}

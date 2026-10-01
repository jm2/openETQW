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

// user info limits: client input is untrusted, and every client's info goes to every client
static const int USERINFO_MIN_INTERVAL_MSEC	= 1000;	// a client's user info goes out at most this often
static const int MAX_USERINFO_KEYS		= 32;
static const int MAX_USERINFO_VALUE_LEN	= 64;
static const int MAX_CLANTAG_LEN		= 16;

/*
==================
Server_TruncateColored

Shortens a string that may hold ^ colour escapes to at most maxLen characters
without leaving a dangling '^'.
==================
*/
static void Server_TruncateColored( idStr& s, int maxLen ) {
	if ( s.Length() <= maxLen ) {
		return;
	}
	int len = maxLen;
	if ( len > 0 && s[ len - 1 ] == '^' ) {
		--len;
	}
	s.CapLength( len );
}
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
		cvarSystem->ClearModifiedFlags( CVAR_USERINFO );
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

	// the game's user info cvars at their values here (defaults on a dedicated
	// server), so bots do not read every flag as false
	idDict& userInfo = sessLocal.mapSpawnData.userInfo[ clientNum ];
	userInfo = *cvarSystem->MoveCVarsToDict( CVAR_USERINFO );
	userInfo.Set( "ui_name", va( "Bot %d", clientNum ) );
	userInfo.Delete( "ui_clanTag" );
	UpdateClientUserInfo( clientNum );	// reaches the clients before the bot's player is created
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
	sessLocal.mapSpawnData.userInfo[ clientNum ].Set( "ui_name", playerName );
	UpdateClientUserInfo( clientNum );
	game->UserInfoChanged( clientNum );
	return 1;
}

// the time left in the current tic for a listen host's render-only frame, as
// idAsyncClient::GetGameTimeLeft and Com_GetGameTimeLeft give it; the clamp keeps it
// in 1 to USERCMD_MSEC - 1 (ClientUpdateView aborts on USERCMD_MSEC)
int idAsyncServer::GetGameTimeLeft() const {
	int elapsedInTic = gameTimeResidual;	// the tic boundary RunFrame uses
	if ( elapsedInTic < 1 ) {
		elapsedInTic = 1;
	} else if ( elapsedInTic >= USERCMD_MSEC ) {
		elapsedInTic = USERCMD_MSEC - 1;
	}
	return USERCMD_MSEC - elapsedInTic;
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
	if ( localClientNum >= 0 ) {
		// a listen host draws its game: with com_unlockFPS 0, only on frames that tick
		com_frameInGame = true;
	}

	// the listen server host changed a user info cvar (ui_name, ...)
	if ( localClientNum >= 0 && ( cvarSystem->GetModifiedFlags() & CVAR_USERINFO ) != 0 ) {
		cvarSystem->ClearModifiedFlags( CVAR_USERINFO );
		if ( clients[ localClientNum ].clientState == SCS_INGAME ) {
			sessLocal.mapSpawnData.userInfo[ localClientNum ] = *cvarSystem->MoveCVarsToDict( CVAR_USERINFO );
			UpdateClientUserInfo( localClientNum );
			game->UserInfoChanged( localClientNum );
		}
	}

	const int startFrame = gameFrame;
	while ( gameTimeResidual >= USERCMD_MSEC ) {
		DuplicateUsercmds( gameFrame, gameTime );
		LocalClientInput();
		game->RunFrame( userCmds[ gameFrame & ( MAX_USERCMD_BACKUP - 1 ) ], USERCMD_MSEC );
		++gameFrame;
		gameTime += USERCMD_MSEC;
		gameTimeResidual -= USERCMD_MSEC;
		++com_frameTicks;
	}

	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		// the local client shares the server's game: no snapshots, no channel traffic
		if ( i == localClientNum || clients[ i ].clientState < SCS_CONNECTED || clients[ i ].isBot ) {
			continue;
		}
		if ( !SendSnapshotToClient( i ) ) {
			// Keep the channel moving while no snapshot goes out (the client is
			// loading, or between snapshots): reliable messages and their
			// retransmissions only travel inside unreliable ones.
			SendEmptyToClient( i, false );
		}
		ProcessReliableClientMessages( i );
		ApplyPendingUserInfo( i );
	}

	// A listen host's render-only frame (M5 step 7), as in single player and on the async
	// client: the host's input is sampled for this draw's view only. The command is not
	// stored; the server simulates, and relays to remote clients, only the commands
	// LocalClientInput builds at tics. After the snapshots, so this frame's are written before
	// it presents; the frames after it, up to the next tic, write theirs inside the
	// unlockedDraw window it opens, as on a client (vehicle-view-net checks those AoR calls).
	// com_unlockFPS 0 draws no render-only frame (idCommonLocal::Frame), so none is prepared
	if ( localClientNum >= 0 && gameFrame == startFrame && game != NULL && com_unlockFPS.GetBool() ) {
		const usercmd_t cmd = usercmdGen->GetDirectUsercmd();
		game->ClientUpdateView( cmd, GetGameTimeLeft() );
		++com_frameUpdateViews;
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
		// a usercmd that had not arrived in time: the lagometer shows the count
		if ( idAsyncNetwork::DuplicateUsercmd(
				userCmds[ ( frame - 1 ) & ( MAX_USERCMD_BACKUP - 1 ) ][ i ],
				userCmds[ frame & ( MAX_USERCMD_BACKUP - 1 ) ][ i ],
				frame,
				time ) ) {
			clients[ i ].numDuplicatedUsercmds++;
		}
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
	client.gameInitSentId = GAME_INIT_ID_INVALID;
	client.lastUserInfoTime = 0;
	client.userInfoPending = false;
	client.pendingUserInfo.Clear();
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
	client.guid[ 0 ] = '\0';
	client.channel.Shutdown();
}

void idAsyncServer::InitClient( int clientNum, int clientId, int clientRate ) {
	ClearClient( clientNum );
	sessLocal.mapSpawnData.userInfo[ clientNum ].Clear();	// always start with a clean base
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
		UpdateClientUserInfo( localClientNum );
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

		UpdateClientUserInfo( i );
		game->ServerClientConnect( i );
		game->ServerClientBegin( i, true );
		game->UserInfoChanged( i );
	}
}

/*
==================
idAsyncServer::UpdateClientUserInfo

Validates a client's stored user info and sends it to every client in the
game.  The engine owns ui_bot and ui_realname (the displayed name: the clan
tag and ui_name); ValidateUserInfo may change ui_name (a duplicate gets '_'),
so the real name is composed after it.  Client input is untrusted: only ui_*
keys are kept, with bounded counts and lengths.
==================
*/
void idAsyncServer::UpdateClientUserInfo( int clientNum ) {
	idDict& info = sessLocal.mapSpawnData.userInfo[ clientNum ];

	idDict clean;
	for ( int i = 0; i < info.GetNumKeyVals() && clean.GetNumKeyVals() < MAX_USERINFO_KEYS; ++i ) {
		const idKeyValue* kv = info.GetKeyVal( i );
		if ( idStr::Icmpn( kv->GetKey().c_str(), "ui_", 3 ) != 0 || !kv->GetKey().Icmp( "ui_realname" ) || !kv->GetKey().Icmp( "ui_bot" ) ) {
			continue;
		}
		idStr value = kv->GetValue();
		Server_TruncateColored( value, MAX_USERINFO_VALUE_LEN );
		clean.Set( kv->GetKey(), value );
	}

	idStr name = clean.GetString( "ui_name" );
	name.StripLeading( ' ' );
	name.StripTrailing( ' ' );
	Server_TruncateColored( name, MAX_NICKLEN - 1 );
	if ( name.IsEmpty() ) {
		if ( clients[ clientNum ].isBot ) {
			name = va( "Bot %d", clientNum );
		} else if ( clientNum == localClientNum ) {
			name = "Player";
		} else {
			name = va( "Player %d", clientNum );
		}
	}
	clean.Set( "ui_name", name );
	idStr clanTag = clean.GetString( "ui_clanTag" );
	Server_TruncateColored( clanTag, MAX_CLANTAG_LEN );
	clean.Set( "ui_clanTag", clanTag );
	clean.SetBool( "ui_bot", clients[ clientNum ].isBot );

	info = clean;
	game->ValidateUserInfo( clientNum, info );

	// displayed name: "tag^0name", or "name^0tag" with ui_clanTagPosition 1 (a
	// reconstruction choice, ledger 049)
	idStr realName = info.GetString( "ui_name" );
	clanTag = info.GetString( "ui_clanTag" );
	if ( !clanTag.IsEmpty() ) {
		realName = info.GetInt( "ui_clanTagPosition" ) == 1 ? realName + "^0" + clanTag : clanTag + "^0" + realName;
	}
	info.Set( "ui_realname", realName );

	SendUserInfoBroadcast( clientNum, info, true );
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
		client.gameInitSentId = GAME_INIT_ID_INVALID;
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
	// clients in the game only: a loading client gets everyone's info when it
	// enters the game (BeginRemoteClient)
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		if ( clients[ i ].clientState == SCS_INGAME && ( sendToAll || i != userInfoNum ) ) {
			SendUserInfoToClient( i, userInfoNum, info );
		}
	}
}

void idAsyncServer::SendUserInfoToClient( int clientNum, int userInfoNum, const idDict& info ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || userInfoNum < 0 || userInfoNum >= MAX_ASYNC_CLIENTS || game == NULL ) {
		return;
	}
	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_RELIABLE_MESSAGE_CLIENTINFO );
	msg.WriteByte( userInfoNum );
	game->WriteUserInfo( msg, info );	// the game's codec: names and the flags it uses on clients
	SendReliableMessage( clientNum, msg );	// skips the local client and bots
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

/*
==================
idAsyncServer::SendApplySnapshotToClient

Tells the client to make snapshot 'sequence' its delta base, as the server
just did (game->ServerApplySnapshot).  Reliable and queued before every
snapshot written after that apply, so the client applies exactly the
snapshots the server applied, in the same order, between the same reads.
==================
*/
void idAsyncServer::SendApplySnapshotToClient( int clientNum, int sequence ) {
	byte buffer[ 16 ];
	idBitMsg msg;
	msg.InitWrite( buffer, sizeof( buffer ) );
	msg.BeginWriting();
	msg.WriteByte( SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT );
	msg.WriteLong( sequence );
	SendReliableMessage( clientNum, msg );
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

The game init is a reliable message (protocol minor 2): the channel
retransmits it until it is acknowledged, and it is ordered after everything
queued for the client's old game.  The client loads the map when it handles
it.  The client stays SCS_CONNECTED, so none of the game's broadcasts pile up
in its channel while it loads (it acknowledges nothing then), until its first
usercmd of this game says it has loaded: BeginRemoteClient then spawns its
player and sends the game's state, and the first snapshot follows.  The
client does not run the game (CS_CONNECTED) before that snapshot.
==================
*/
void idAsyncServer::SendGameInitToClient( int clientNum ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return;
	}
	serverClient_t& client = clients[ clientNum ];

	common->Printf( "Sending game init to client %d\n", clientNum );

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
	client.gameInitSentId = gameInitId;
	client.isMapLoading = true;

	// carry it now; later empties retransmit it until it is acknowledged
	SendEmptyToClient( clientNum, true );
}

/*
==================
idAsyncServer::BeginRemoteClient

The client has loaded the map of the current game init: enter it into the
game.  Any step can drop the client (reliable overflow); stop there.
==================
*/
void idAsyncServer::BeginRemoteClient( int clientNum ) {
	serverClient_t& client = clients[ clientNum ];

	// 1. Transmit synced cvars
	SendSyncedCvarsToClient( clientNum, sessLocal.mapSpawnData.syncedCVars );
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}

	// 2. Transmit reliable enter game notification (sets SCS_INGAME, so the
	// game's reliable messages below reach this client)
	SendEnterGameToClient( clientNum );
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}

	// 3. Everyone's user info, then this client's own (validated, and sent to
	// every client in the game), ahead of the players' entity creates.  The
	// client's CLIENTINFO arrived with or before the usercmd that got us here.
	for ( int i = 0; i < MAX_ASYNC_CLIENTS && client.clientState >= SCS_CONNECTED; ++i ) {
		if ( i != clientNum && clients[ i ].clientState == SCS_INGAME ) {
			SendUserInfoToClient( clientNum, i, sessLocal.mapSpawnData.userInfo[ i ] );
		}
	}
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}
	if ( client.userInfoPending ) {
		sessLocal.mapSpawnData.userInfo[ clientNum ] = client.pendingUserInfo;
		client.userInfoPending = false;
	}
	client.lastUserInfoTime = realTime;
	UpdateClientUserInfo( clientNum );
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}

	// 4. Initialize client player entity in game module
	game->ServerClientConnect( clientNum );
	game->ServerClientBegin( clientNum, false );
	game->UserInfoChanged( clientNum );
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}

	// 5. Send initial reliable game state burst
	game->ServerWriteInitialReliableMessages( clientNum );
	if ( client.clientState < SCS_CONNECTED ) {
		return;
	}

	// 6. In the game; the first snapshot goes out with the next SendSnapshotToClient
	client.clientState = SCS_INGAME;
	client.isMapLoading = false;
	client.lastInputTime = realTime;
}

bool idAsyncServer::SendSnapshotToClient( int clientNum ) {
	if ( clientNum < 0 || clientNum >= MAX_ASYNC_CLIENTS || clients[ clientNum ].clientState < SCS_CONNECTED ) {
		return false;
	}
	serverClient_t& client = clients[ clientNum ];
	if ( client.clientState == SCS_CONNECTED ) {
		// one game init per game; the client enters the game when it has loaded
		// the map (ProcessUnreliableClientMessage -> BeginRemoteClient)
		if ( client.gameInitSentId != gameInitId ) {
			SendGameInitToClient( clientNum );
			return true;
		}
		return false;
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

	// Snapshots stream (protocol minor 4, as in retail and Doom 3): each one is
	// written against the base the server has applied, which the client has
	// applied too (SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT), and carries every
	// change since, so a lost snapshot is simply superseded by the next one.
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
		// nothing written (no player yet): the game recorded nothing either
		return SendEmptyToClient( clientNum, true );
	}
	client.snapshotSequence = sequence;

	const int64_t clientAheadTime = static_cast<int64_t>( client.gameTime ) - static_cast<int64_t>( gameTime ) - static_cast<int64_t>( gameTimeResidual );
	int aheadOfServer = clientAheadTime < -32768 ? -32768 : ( clientAheadTime > 32767 ? 32767 : static_cast<int>( clientAheadTime ) );
	int numDuplicatedUsercmds = idMath::ClampInt( 0, 255, client.numDuplicatedUsercmds );

	byte buffer[ MAX_MESSAGE_SIZE ];
	idBitMsg snapshotMsg;
	snapshotMsg.InitWrite( buffer, sizeof( buffer ) );
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

	if ( SendMessageToClient( client, snapshotMsg ) < 0 ) {
		// refused (reliable backlog + snapshot too large): the game holds its record
		// until an apply or the 64-snapshot window frees it; drain the backlog
		SendEmptyToClient( clientNum, true );
	}
	client.numDuplicatedUsercmds = 0;

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
		if ( clients[ clientNum ].clientState == SCS_CONNECTED ) {
			// usercmds of the current game: the client has loaded the map of its game init
			if ( type == CLIENT_UNRELIABLE_MESSAGE_USERCMD && clients[ clientNum ].gameInitSentId == gameInitId ) {
				BeginRemoteClient( clientNum );
			}
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
		if ( msg.GetRemainingData() < 4 + 4 + 2 + 1 ) {
			return;
		}
		int ackSnapshotSeq = msg.ReadLong();
		int clientGameFrame = msg.ReadLong();
		// how far ahead the client runs: the game's anti-lag rewinds by it
		clients[ clientNum ].clientPrediction = idMath::ClampInt( 0, idAsyncNetwork::clientMaxPrediction.GetInteger(), msg.ReadShort() );
		int numUsercmds = msg.ReadByte();

		if ( numUsercmds < 1 || numUsercmds > MAX_USERCMD_RELAY || clientGameFrame < 0 ) {
			return;
		}

		// The client read snapshot ackSnapshotSeq: make it the base here and tell the
		// client to do the same.  Only sequences we sent are applied (a bogus one would
		// free every pending record).  ServerApplySnapshot fails for a snapshot already
		// applied or already freed by the 64-snapshot window: no apply then.
		if ( ackSnapshotSeq > clients[ clientNum ].acknowledgeSnapshotSequence && ackSnapshotSeq <= clients[ clientNum ].snapshotSequence ) {
			clients[ clientNum ].acknowledgeSnapshotSequence = ackSnapshotSeq;
			if ( game->ServerApplySnapshot( clientNum, ackSnapshotSeq ) ) {
				SendApplySnapshotToClient( clientNum, ackSnapshotSeq );
				if ( clients[ clientNum ].clientState < SCS_CONNECTED ) {
					return;	// dropped (reliable overflow)
				}
			}
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

/*
==================
idAsyncServer::ApplyPendingUserInfo
==================
*/
void idAsyncServer::ApplyPendingUserInfo( int clientNum ) {
	serverClient_t& client = clients[ clientNum ];
	if ( !client.userInfoPending || client.clientState != SCS_INGAME || realTime - client.lastUserInfoTime < USERINFO_MIN_INTERVAL_MSEC ) {
		return;
	}
	client.userInfoPending = false;
	client.lastUserInfoTime = realTime;
	sessLocal.mapSpawnData.userInfo[ clientNum ] = client.pendingUserInfo;
	UpdateClientUserInfo( clientNum );
	common->Printf( "Client %d user info: '%s'\n", clientNum, sessLocal.mapSpawnData.userInfo[ clientNum ].GetString( "ui_name" ) );
	if ( client.clientState >= SCS_CONNECTED ) {
		game->UserInfoChanged( clientNum );
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
		if ( type == CLIENT_RELIABLE_MESSAGE_CLIENTINFO ) {
			// Stored now, validated and sent to every client at most once a second
			// (ApplyPendingUserInfo; the latest wins): each update fans out to every
			// client, so an unthrottled client could overflow everyone's channel.  A
			// client still loading is validated when it enters the game.
			idDict& info = clients[ clientNum ].pendingUserInfo;
			info.Clear();
			msg.ReadDeltaDict( info, NULL );
			clients[ clientNum ].userInfoPending = true;
			continue;
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
	// The protocol comes first in every idTech connect message, and it is checked
	// first (as Doom 3 does), so a retail ET:QW peer or another OpenETQW version is
	// told which protocol this server speaks, whatever the rest of its message holds
	// and whether or not its challenge is one of ours.
	if ( msg.GetRemainingData() < 4 ) {
		return;
	}
	int protocol = msg.ReadLong();
	if ( protocol != ASYNC_PROTOCOL_VERSION ) {
		PrintOOB( from, SERVER_PRINT_BADPROTOCOL, va( "Server uses protocol %d.%d", OPENETQW_PROTOCOL_MAJOR, ASYNC_PROTOCOL_MINOR ) );
		return;
	}

	if ( msg.GetRemainingData() < 2 + 4 + 4 + 2 + 4 ) {
		return;
	}
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

	// The slot this client already has, before the allow check counts it.  Bots and
	// free slots keep the channel address of a previous human, so they never match.
	// The same clientId and port is the same client again (its connectResponse was
	// lost): answer again.  Another instance on the same endpoint replaces the old
	// one; a known clientId from another port (another host behind the same NAT
	// could guess it) only replaces a slot that has gone silent.
	int existingSlot = -1;
	bool resendResponse = false;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
		const serverClient_t& c = clients[ i ];
		if ( c.clientState == SCS_FREE || c.isBot || !Sys_CompareNetAdrBase( from, c.channel.GetRemoteAddress() ) ) {
			continue;
		}
		const bool samePort = from.port == c.channel.GetRemoteAddress().port;
		if ( samePort && c.clientId == clientId ) {
			existingSlot = i;
			resendResponse = c.clientState >= SCS_CONNECTED;
			break;
		}
		if ( samePort || ( c.clientId == clientId && realTime - c.lastPacketTime > 3000 ) ) {
			existingSlot = i;
			break;
		}
	}

	const int numClients = GetNumClients() - ( existingSlot >= 0 && clients[ existingSlot ].clientState >= SCS_CONNECTED ? 1 : 0 );
	allowFailureReason_t reason = ALLOWFAIL_UNKNOWN;
	allowReply_t reply = ( resendResponse || game == NULL ) ? ALLOW_YES : game->ServerAllowClient( numClients, 0, netAddr, netClientId, guid, password, reason );
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

	int clientNum = existingSlot;
	if ( existingSlot >= 0 && !resendResponse && clients[ existingSlot ].clientState >= SCS_CONNECTED ) {
		// a new client instance: the old one's player and snapshot state must go
		// (the game resets them on disconnect)
		DropClient( existingSlot, "reconnect" );
	}
	// then a slot this address left, then any free one
	for ( int islot = 1; islot < 3 && clientNum == -1; ++islot ) {
		for ( int i = 0; i < MAX_ASYNC_CLIENTS; ++i ) {
			if ( islot == 1 ) {
				if ( clients[ i ].clientState < SCS_CONNECTED && !clients[ i ].isBot && Sys_CompareNetAdrBase( from, clients[ i ].channel.GetRemoteAddress() ) ) {
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

	if ( !resendResponse ) {
		InitClient( clientNum, clientId, clientRate );
		clients[ clientNum ].channel.Init( from, serverId );
		clients[ clientNum ].OS = OS;
		idStr::Copynz( clients[ clientNum ].guid, guid, sizeof( clients[ clientNum ].guid ) );
		challenges[ ichallenge ].connected = true;

		common->Printf( "Client %d connected from %s (clientId: %d)\n", clientNum, Sys_NetAdrToString( from ), clientId );
	}

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
	// The client's exact address first.  Two clients behind one address can pick
	// the same clientId; matching on the base address alone let one client's
	// packets land in the other's slot, where the channel then took its port
	// ("fixing up a translated port") and sent it the other client's snapshots.
	// A NAT that changed the client's port is recognised only when the slot's
	// own port has gone quiet.
	int clientNum = -1;
	for ( int i = 0; i < MAX_ASYNC_CLIENTS && clientNum < 0; ++i ) {
		const serverClient_t& client = clients[ i ];
		if ( client.clientState >= SCS_CONNECTED && client.clientId == id && Sys_CompareNetAdrBase( from, client.channel.GetRemoteAddress() ) && from.port == client.channel.GetRemoteAddress().port ) {
			clientNum = i;
		}
	}
	for ( int i = 0; i < MAX_ASYNC_CLIENTS && clientNum < 0; ++i ) {
		const serverClient_t& client = clients[ i ];
		if ( client.clientState >= SCS_CONNECTED && client.clientId == id && Sys_CompareNetAdrBase( from, client.channel.GetRemoteAddress() ) &&
			realTime - client.lastPacketTime > 1000 ) {
			clientNum = i;
		}
	}
	if ( clientNum < 0 ) {
		return false;
	}
	serverClient_t& client = clients[ clientNum ];
	int sequence = 0;
	if ( client.channel.Process( from, realTime, msg, sequence ) ) {
		client.lastPacketTime = realTime;
		ProcessReliableClientMessages( clientNum );
		ProcessUnreliableClientMessage( clientNum, msg );
	}
	return true;
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
	if ( clientNum >= 0 && clientNum < MAX_ASYNC_CLIENTS && clients[ clientNum ].clientState >= SCS_CONNECTED ) {
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

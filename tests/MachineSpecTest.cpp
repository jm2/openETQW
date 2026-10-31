// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: MachineSpecTest.cpp
Sys_FindDeviceSpec and Sys_DeviceSpecLevel (src/framework/MachineSpec.h), which
read retail's device.info to pick a fresh profile's graphics preset (#387). The
table below is synthetic, in device.info's format; the retail file stays in its
pak.
===========================================================================
*/

// Compiled as the engine compiles its sources (the force-included precompiled.h)
#include "MachineSpec.h"

#include <cstdio>

// Globals idLib refers to; nothing here calls through them
idCommon * common = NULL;
idSys * sys = NULL;
idDeclManager * declManager = NULL;
idCVar * idCVar::staticVars = NULL;
idCVarSystem * cvarSystem = NULL;
idFileSystem * fileSystem = NULL;

static int failures = 0;

static void Expect( bool ok, const char* what ) {
	if ( !ok ) {
		std::printf( "FAIL: %s\n", what );
		failures++;
	}
}

static const char* const table =
	"// device and vendor settings\r\n"
	"\r\n"
	"\r\n"
	"// vendor one\r\n"
	"Vendor 10DE\r\n"
	"// a low card, listed under three IDs\r\n"
	"Device 014f 00f3 0221\r\n"
	"exec specs/minspec_gpu\r\n"
	"exec specs/minspec_gpudetail\r\n"
	"\r\n"
	"// a middle card\r\n"
	"Device 0141\r\n"
	"exec specs/recspec_gpu\r\n"
	"exec specs/recspec_gpudetail\r\n"
	"\r\n"
	"// listed twice: the first entry counts\r\n"
	"Device 0162\r\n"
	"exec specs/highspec_gpu\r\n"
	"exec specs/highspec_gpudetail\r\n"
	"\r\n"
	"Device 0162\r\n"
	"exec specs/minspec_gpu\r\n"
	"\r\n"
	"// vendor two\r\n"
	"Vendor 1002\r\n"
	"// a card with an extra command\r\n"
	"Device 4a4b\r\n"
	"exec specs/recspec_gpu\r\n"
	"exec specs/recspec_gpudetail\r\n"
	"lockcvar r_softParticles 0\r\n"
	"// the last entry, with no blank line after it\r\n"
	"Device 5d52\r\n"
	"\texec specs/highspec_gpu";

int main() {
	idStr::InitMemory();	// as idLib::Init: an idStr past its base buffer allocates from it
	idStr commands;

	Expect( Sys_FindDeviceSpec( table, 0x10DE, 0x00f3, commands ), "a card's second ID is listed" );
	Expect( commands == "exec specs/minspec_gpu\nexec specs/minspec_gpudetail\n",
		"its entry's commands, and only those" );
	Expect( Sys_DeviceSpecLevel( commands ) == 0, "minspec is level 0" );

	Expect( Sys_FindDeviceSpec( table, 0x10DE, 0x0221, commands ), "a card's last ID is listed" );
	Expect( Sys_FindDeviceSpec( table, 0x10DE, 0x0141, commands ), "a middle card is listed" );
	Expect( Sys_DeviceSpecLevel( commands ) == 1, "recspec is level 1" );

	Expect( Sys_FindDeviceSpec( table, 0x10DE, 0x0162, commands ), "a card listed twice is listed" );
	Expect( Sys_DeviceSpecLevel( commands ) == 2, "the first of two entries counts (highspec, level 2)" );
	Expect( commands.Find( "minspec" ) < 0, "the second entry's commands are not added" );

	Expect( Sys_FindDeviceSpec( table, 0x1002, 0x4a4b, commands ), "the second vendor's card is listed" );
	Expect( commands == "exec specs/recspec_gpu\nexec specs/recspec_gpudetail\nlockcvar r_softParticles 0\n",
		"an entry ends at the comment and Device line after it, extra commands kept" );

	Expect( Sys_FindDeviceSpec( table, 0x1002, 0x5d52, commands ), "the last entry, at the end of the text" );
	Expect( commands == "exec specs/highspec_gpu\n", "leading whitespace is stripped" );

	// a device ID under the other vendor, and GPUs the table doesn't list
	Expect( !Sys_FindDeviceSpec( table, 0x1002, 0x0141, commands ), "a device ID counts only under its vendor" );
	Expect( commands.IsEmpty(), "an unlisted GPU gets no commands" );
	Expect( !Sys_FindDeviceSpec( table, 0x5143, 0x3600, commands ), "an unlisted vendor (Qualcomm)" );
	Expect( !Sys_FindDeviceSpec( table, 0x106B, 0x0000, commands ), "an unlisted vendor (Apple)" );
	Expect( !Sys_FindDeviceSpec( table, 0x10DE, 0x2684, commands ), "a newer card of a listed vendor" );
	Expect( !Sys_FindDeviceSpec( NULL, 0x10DE, 0x0141, commands ), "no table" );
	Expect( !Sys_FindDeviceSpec( "", 0x10DE, 0x0141, commands ), "an empty table" );

	Expect( Sys_DeviceSpecLevel( idStr( "lockcvar r_softParticles 0\n" ) ) == -1, "no preset named: -1" );

	if ( failures ) {
		std::printf( "FAIL: %d check(s) failed\n", failures );
		return 1;
	}
	std::printf( "PASS: device.info entries are found by PCI ID and give their preset level\n" );
	return 0;
}

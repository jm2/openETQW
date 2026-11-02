// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __FRAMEWORK_MACHINESPEC_H__
#define __FRAMEWORK_MACHINESPEC_H__

/*
The graphics preset a fresh profile starts with (#387, docs/compat-ledger.md 082).

Retail 1.5 picks it from device.info (pak000), a table of PCI IDs:

	Vendor 10DE
	// GeForce 6600
	Device 0141 00f2
	exec specs/recspec_gpu
	exec specs/recspec_gpudetail

"Vendor" names the vendor of the entries after it, and "Device" lists device IDs.
The lines after a Device line, up to the next Device or Vendor line or a blank
line, are the console commands for those devices. Comments start with "//".
The table lists only NVIDIA and ATI GPUs up to 2007, so retail starts any newer GPU
at its lowest preset. openETQW keeps the table for the GPUs it lists and starts
every other GPU at the high preset, by owner decision.
*/

// the console commands device.info gives the GPU with these PCI IDs, one per line;
// false when the table doesn't list it
inline bool Sys_FindDeviceSpec( const char* deviceInfo, unsigned int vendorID,
	unsigned int deviceID, idStr& commands ) {
	commands.Clear();
	if ( deviceInfo == NULL ) {
		return false;
	}
	unsigned int vendor = 0;
	bool matched = false;
	const char* line = deviceInfo;
	while ( *line != '\0' ) {
		const char* end = line;
		while ( *end != '\0' && *end != '\n' ) {
			end++;
		}
		idStr text( line, 0, static_cast< int >( end - line ) );
		text.StripLeadingWhiteSpace();
		text.StripTrailingWhiteSpace();
		line = *end == '\n' ? end + 1 : end;

		if ( text.IsEmpty() ) {
			if ( matched ) {
				return true;
			}
			continue;
		}
		if ( text.Cmpn( "//", 2 ) == 0 ) {
			continue;
		}
		const bool isVendor = text.Icmpn( "Vendor", 6 ) == 0 && ( text[ 6 ] == ' ' || text[ 6 ] == '\t' );
		const bool isDevice = text.Icmpn( "Device", 6 ) == 0 && ( text[ 6 ] == ' ' || text[ 6 ] == '\t' );
		if ( isVendor || isDevice ) {
			if ( matched ) {
				return true;
			}
			// the hexadecimal IDs after the keyword
			const char* id = text.c_str() + 6;
			while ( *id != '\0' ) {
				char* idEnd = NULL;
				const unsigned long value = strtoul( id, &idEnd, 16 );
				if ( idEnd == id ) {
					break;
				}
				id = idEnd;
				if ( isVendor ) {
					vendor = static_cast< unsigned int >( value );
					break;
				}
				if ( vendor == vendorID && value == deviceID ) {
					matched = true;
				}
			}
			continue;
		}
		if ( matched ) {
			// the entries exec their presets without an extension; exec adds .cfg,
			// and the presets are specs/*.dat
			if ( text.Icmpn( "exec ", 5 ) == 0 ) {
				idStr file = text.c_str() + 5;
				file.StripLeadingWhiteSpace();
				file.DefaultFileExtension( ".dat" );
				text = "exec " + file;
			}
			commands += text;
			commands += "\n";
		}
	}
	return matched;
}

// the preset level device.info's commands apply: 0 for retail's minspec, 1 for
// recspec, 2 for highspec; -1 when they name none
inline int Sys_DeviceSpecLevel( const idStr& commands ) {
	if ( commands.Find( "highspec_", false ) >= 0 ) {
		return 2;
	}
	if ( commands.Find( "recspec_", false ) >= 0 ) {
		return 1;
	}
	if ( commands.Find( "minspec_", false ) >= 0 ) {
		return 0;
	}
	return -1;
}

// setMachineSpec, the command retail's settings menu runs for its overall preset
// (Low, Normal, High) and for Autodetect. Retail 1.5 sets com_machineSpec, runs the
// level's whole preset (specs/minspec.dat, recspec.dat or highspec.dat: all six parts,
// lighting included) and sets s_maxSoundsPerShader: 3 below High, 0 at High. A level
// outside 0-2, or none, detects the GPU as a fresh profile does.

// true when setMachineSpec's argument names a preset level rather than detection
inline bool Sys_IsMachineSpecLevel( int level ) {
	return level >= 0 && level <= 2;
}

// the preset file a level runs
inline const char* Sys_MachineSpecPreset( int level ) {
	return level <= 0 ? "specs/minspec.dat" : ( level == 1 ? "specs/recspec.dat" : "specs/highspec.dat" );
}

// the quality name retail's setMachineSpec prints for a level
inline const char* Sys_MachineSpecQuality( int level ) {
	return level <= 0 ? "Low" : ( level == 1 ? "Normal" : "High" );
}

// s_maxSoundsPerShader at a level, as retail's setMachineSpec sets it
inline int Sys_MachineSpecSoundsPerShader( int level ) {
	return level >= 2 ? 0 : 3;
}

#endif /* !__FRAMEWORK_MACHINESPEC_H__ */

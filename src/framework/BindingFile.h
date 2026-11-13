// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __FRAMEWORK_BINDINGFILE_H__
#define __FRAMEWORK_BINDINGFILE_H__

/*
====================
Com_BindingFileHasBindings

Whether a saved binding file's text holds anything besides comments and unbindall.
Drafts of v0.1.0 before #363 saved a binding file with only its header and
unbindall; run after the default bindings, it left every key unbound (#454), so
such a file is not run. Commands may share a line, separated by ';'.
====================
*/
inline bool Com_BindingFileHasBindings( const char* text ) {
	if ( text == NULL ) {
		return false;
	}
	const char* p = text;
	while ( *p != '\0' ) {
		// a comment runs to the end of its line
		if ( p[0] == '/' && p[1] == '/' ) {
			while ( *p != '\0' && *p != '\n' ) {
				p++;
			}
			continue;
		}
		if ( *p == ' ' || *p == '\t' || *p == '\r' || *p == '\n' || *p == ';' ) {
			p++;
			continue;
		}
		const char* start = p;
		while ( *p != '\0' && *p != ' ' && *p != '\t' && *p != '\r' && *p != '\n' && *p != ';' ) {
			p++;
		}
		static const char unbindall[] = "unbindall";
		const size_t length = static_cast< size_t >( p - start );
		if ( length != sizeof( unbindall ) - 1 || idStr::Icmpn( start, unbindall, static_cast< int >( length ) ) != 0 ) {
			return true;
		}
	}
	return false;
}

#endif // !__FRAMEWORK_BINDINGFILE_H__

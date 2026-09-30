// SPDX-License-Identifier: GPL-3.0-or-later
/*
===========================================================================
openETQW Asset-Free Test Harness: StrBuilderTest.cpp
Grows a stack sdStringBuilder_Heap past its 512-character base buffer, the way
idLexer::ParseBracedSectionExact does for every braced section longer than
that (renderprogs, decls).  MSVC 19.51 at /O2 compiled the inlined grow path's
strncpy with a 64-bit count (len together with the adjacent alloced), which
zero-padded far past the new buffer and crashed the optimized engine at
startup; the game repository's StrBuilder.h now grows with memcpy
(openETQW-game 464ea56).  In an optimized build this test crashes, or fails,
if that comes back.
===========================================================================
*/

// The C headers first: idLib's Str.h defines strcmp and strlen as idStr
// members, which breaks libc++'s <cstring> (macOS) when it comes after.
#include <cstdio>
#include <cstring>

#include "src/idlib/precompiled.h"

// idLib's objects refer to the engine's globals; nothing here calls through them
class idCommon;
class idSys;
idCommon * common = NULL;
idSys * sys = NULL;

static int g_failures = 0;

// The shape of idLexer::ParseBracedSectionExact: a builder on the stack, fed one
// character at a time, read out at the end.  Not inlined into main, so the
// builder stays a local of this frame as it is in the lexer.
#if defined( _MSC_VER )
__declspec( noinline )
#else
__attribute__(( noinline ))
#endif
static int BuildBracedSection( const char* text, char* out, int outSize ) {
	sdStringBuilder_Heap builder;
	builder = "{";
	int depth = 1;
	while ( depth > 0 && *text != '\0' ) {
		const char c = *text++;
		if ( c == '{' ) {
			depth++;
		} else if ( c == '}' ) {
			depth--;
		}
		builder += c;
	}
	const int length = builder.Length();
	if ( length < outSize ) {
		memcpy( out, builder.c_str(), static_cast< size_t >( length ) + 1 );
	}
	return length;
}

static void TestLength( int bodyLength ) {
	static char text[ 8192 ];
	static char out[ 8192 ];
	static char expected[ 8192 ];
	for ( int i = 0; i < bodyLength; i++ ) {
		text[ i ] = static_cast< char >( 'a' + i % 26 );
	}
	text[ bodyLength ] = '}';
	text[ bodyLength + 1 ] = '\0';
	expected[ 0 ] = '{';
	memcpy( expected + 1, text, static_cast< size_t >( bodyLength ) + 2 );

	const int length = BuildBracedSection( text, out, sizeof( out ) );
	if ( length != bodyLength + 2 ) {
		fprintf( stderr, "FAIL: body %d: built %d characters, expected %d\n", bodyLength, length, bodyLength + 2 );
		g_failures++;
	} else if ( memcmp( out, expected, static_cast< size_t >( length ) + 1 ) != 0 ) {
		fprintf( stderr, "FAIL: body %d: the built text differs from its input\n", bodyLength );
		g_failures++;
	}
}

int main() {
	// around the 512-character base buffer and each later 512-character step
	const int lengths[] = { 0, 100, 509, 510, 511, 512, 1021, 1022, 1023, 1500, 4000, 8000 };
	for ( int length : lengths ) {
		TestLength( length );
	}
	if ( g_failures == 0 ) {
		printf( "StrBuilderTest: ALL TESTS PASSED\n" );
		return 0;
	}
	fprintf( stderr, "StrBuilderTest: %d FAILURE(S)\n", g_failures );
	return 1;
}

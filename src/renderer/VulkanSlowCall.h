// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RENDERER_VULKANSLOWCALL_H__
#define __RENDERER_VULKANSLOWCALL_H__

/*
OpenETQW (#338): r_vkLogSlowCalls times one Vulkan backend call and prints a VK_SLOW_CALL
line when it runs over the threshold, with the time it began on the FRAME_POSE clock. A
first-use cost inside the driver shows up as one call far over the frame's budget: on a
cold per-executable AMD shader cache, about 650 ms in the middle of a game. A scope is
inert while the cvar is 0.
*/
extern idCVar r_vkLogSlowCalls;

class sdVulkanSlowCallScope {
public:
	explicit sdVulkanSlowCallScope( const char* label_ ) :
		label( label_ ),
		startTicks( r_vkLogSlowCalls.GetInteger() > 0 ? Sys_GetClockTicks() : 0.0 ) {
	}
	~sdVulkanSlowCallScope() {
		if ( startTicks == 0.0 ) {
			return;
		}
		const double ticksPerUsec = Sys_ClockTicksPerSecond() / 1000000.0;
		const double usec = ( Sys_GetClockTicks() - startTicks ) / ticksPerUsec;
		if ( usec >= r_vkLogSlowCalls.GetInteger() * 1000.0 ) {
			common->Printf( "VK_SLOW_CALL: us=%lld ms=%.1f %s%s\n",
				static_cast< long long >( startTicks / ticksPerUsec ), usec / 1000.0, label,
				detail.c_str() );
		}
	}
	bool Active() const { return startTicks != 0.0; }

	idStr detail;	// appended to the line; set it only while Active()

private:
	const char* label;	// a string literal
	double startTicks;
};

#endif /* !__RENDERER_VULKANSLOWCALL_H__ */

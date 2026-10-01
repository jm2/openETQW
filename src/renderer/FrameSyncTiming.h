// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RENDERER_FRAMESYNCTIMING_H__
#define __RENDERER_FRAMESYNCTIMING_H__

/*
OpenETQW (M5 step 4): how long the Vulkan backend's frames spent in the calls that can
wait for the GPU or the display: the frame fence and the swapchain image acquire
(BeginFrame), and the queue submit and the present (EndFrame). The backend adds up
every frame's times; com_logFrameTiming takes and clears the sums once per FRAME_TIMING
window (docs/frame-path.md section 7.5). The dedicated server has no backend and reports
no frames.
*/
struct frameSyncTiming_t {
	int			frames;			// frames that reached the present
	uint64_t	fenceUsec;
	uint64_t	acquireUsec;
	uint64_t	submitUsec;
	uint64_t	presentUsec;
};

void R_TakeFrameSyncTiming( frameSyncTiming_t& timing );

#endif /* !__RENDERER_FRAMESYNCTIMING_H__ */

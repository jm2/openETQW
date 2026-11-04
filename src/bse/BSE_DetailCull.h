// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __BSE_DETAILCULL_H__
#define __BSE_DETAILCULL_H__

/*
====================
BSE_SegmentDetailCulled

Whether an effect segment is left out at an effects detail level (bse_detailLevel,
"Detail setting for effects": 1 by default, 0.5 in the Low preset; #423). A segment
with a "detail" above the level is left out; one with no detail (0) always draws.
This is openQ4's rule for the same BSE and the same per-segment key
(rvSegmentTemplate::DetailCull, against bse_scale); retail 1.5's own cut-off was not
measured (docs/compat-ledger.md 084).
====================
*/
inline bool BSE_SegmentDetailCulled( float segmentDetail, float detailLevel ) {
	return segmentDetail > 0.0f && detailLevel < segmentDetail;
}

#endif // !__BSE_DETAILCULL_H__

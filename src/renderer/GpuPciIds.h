// SPDX-License-Identifier: GPL-3.0-or-later
#ifndef __RENDERER_GPUPCIIDS_H__
#define __RENDERER_GPUPCIIDS_H__

/*
The PCI vendor and device IDs of the GPU the renderer runs on, which retail's
device.info lists to pick a fresh profile's graphics preset (#387). Only the Vulkan
backend knows them; false under OpenGL and on the dedicated server.
*/
bool R_GetGpuPciIds( unsigned int& vendorID, unsigned int& deviceID );

#endif /* !__RENDERER_GPUPCIIDS_H__ */

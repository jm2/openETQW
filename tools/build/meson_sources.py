#!/usr/bin/env python3
"""Emit deterministic, static Meson source lists for openETQW engine targets."""

from __future__ import annotations

import argparse
import pathlib
import sys

COMMON_FRAMEWORK_SOURCES = (
    "src/framework/AdManager.cpp",
    "src/framework/BuildVersion.cpp",
    "src/framework/CVarSystem.cpp",
    "src/framework/CmdSystem.cpp",
    "src/framework/Common.cpp",
    "src/framework/Compressor.cpp",
    "src/framework/Compressor_FixedHuffman.cpp",
    "src/framework/Console.cpp",
    "src/framework/DeclAF.cpp",
    "src/framework/DeclEntityDef.cpp",
    "src/framework/DeclFX.cpp",
    "src/framework/DeclManager.cpp",
    "src/framework/DeclPDA.cpp",
    "src/framework/DeclParticle.cpp",
    "src/framework/DeclSkin.cpp",
    "src/framework/DeclTable.cpp",
    "src/framework/DemoFile.cpp",
    "src/framework/EditField.cpp",
    "src/framework/EventLoop.cpp",
    "src/framework/FileSystem.cpp",
    "src/framework/GraphManager.cpp",
    "src/framework/KeyInput.cpp",
    "src/framework/NotificationSystem.cpp",
    "src/framework/Session.cpp",
    "src/framework/Session_menu.cpp",
    "src/framework/Unzip.cpp",
    "src/framework/UsercmdGen.cpp",
    "src/framework/async/AsyncClient.cpp",
    "src/framework/async/AsyncNetwork.cpp",
    "src/framework/async/AsyncServer.cpp",
    "src/framework/async/MsgChannel.cpp",
    "src/framework/async/NetworkSystem.cpp",
    "src/framework/async/ServerScan.cpp",
    "src/framework/file_inzip.cpp",
    "src/framework/minizip/ioapi.cpp",
    "src/framework/minizip/unzip.cpp",
)

COLLISION_MODEL_SOURCES = (
    "src/cm/CollisionModel.cpp",
    "src/cm/CollisionModel_contacts.cpp",
    "src/cm/CollisionModel_contents.cpp",
    "src/cm/CollisionModel_debug.cpp",
    "src/cm/CollisionModel_files.cpp",
    "src/cm/CollisionModel_load.cpp",
    "src/cm/CollisionModel_rotate.cpp",
    "src/cm/CollisionModel_trace.cpp",
    "src/cm/CollisionModel_translate.cpp",
)

DECL_LIB_SOURCES = (
    "src/decllib/DeclRenderProgram_opengl.cpp",
    "src/decllib/DeclSurfaceTypeMap.cpp",
    "src/decllib/DeclType.cpp",
    "src/decllib/RenderProgramParser.cpp",
    "src/decllib/declRenderProgram.cpp",
    "src/decllib/declTemplate.cpp",
    "src/decllib/declaf.cpp",
    "src/decllib/declambientcubemap.cpp",
    "src/decllib/declatmosphere.cpp",
    "src/decllib/decldecal.cpp",
    "src/decllib/declentitydef.cpp",
    "src/decllib/declimposter.cpp",
    "src/decllib/decllocstr.cpp",
    "src/decllib/declmodelexport.cpp",
    "src/decllib/declrenderbinding.cpp",
    "src/decllib/declskin.cpp",
    "src/decllib/declstufftype.cpp",
    "src/decllib/declsurfacetype.cpp",
    "src/decllib/decltable.cpp",
)

NAVIGATION_RECAST_SOURCES = (
    "src/navigation/Navigation.cpp",
    "src/recast/Detour/Source/DetourAlloc.cpp",
    "src/recast/Detour/Source/DetourAssert.cpp",
    "src/recast/Detour/Source/DetourCommon.cpp",
    "src/recast/Detour/Source/DetourNavMesh.cpp",
    "src/recast/Detour/Source/DetourNavMeshBuilder.cpp",
    "src/recast/Detour/Source/DetourNavMeshQuery.cpp",
    "src/recast/Detour/Source/DetourNode.cpp",
    "src/recast/Recast/Source/Recast.cpp",
    "src/recast/Recast/Source/RecastAlloc.cpp",
    "src/recast/Recast/Source/RecastArea.cpp",
    "src/recast/Recast/Source/RecastAssert.cpp",
    "src/recast/Recast/Source/RecastContour.cpp",
    "src/recast/Recast/Source/RecastFilter.cpp",
    "src/recast/Recast/Source/RecastLayers.cpp",
    "src/recast/Recast/Source/RecastMesh.cpp",
    "src/recast/Recast/Source/RecastMeshDetail.cpp",
    "src/recast/Recast/Source/RecastRasterization.cpp",
    "src/recast/Recast/Source/RecastRegion.cpp",
)

SDNET_SOURCES = (
    "src/sdnet/SDNet.cpp",
)

BSE_PARTICLE_SOURCES = (
    "src/bse/BSE_Bounds.cpp",
    "src/bse/BSE_Effect.cpp",
    "src/bse/BSE_EffectTemplate.cpp",
    "src/bse/BSE_Electricity.cpp",
    "src/bse/BSE_Envelopes.cpp",
    "src/bse/BSE_Light.cpp",
    "src/bse/BSE_Manager.cpp",
    "src/bse/BSE_ParseParticle2.cpp",
    "src/bse/BSE_Particle.cpp",
    "src/bse/BSE_Render.cpp",
    "src/bse/BSE_Segment.cpp",
    "src/bse/BSE_SegmentTemplate.cpp",
    "src/bse/BSE_SpawnDomains.cpp",
)

CLIENT_SOUND_SOURCES = (
    "src/sound/SoundShader.cpp",
    "src/sound/SoundSystemBootstrap.cpp",
    "src/sound/snd_cache.cpp",
    "src/sound/snd_decoder.cpp",
    "src/sound/snd_efxfile.cpp",
    "src/sound/snd_emitter.cpp",
    "src/sound/snd_shader.cpp",
    "src/sound/snd_system.cpp",
    "src/sound/snd_wavefile.cpp",
    "src/sound/snd_world.cpp",
)

CLIENT_RENDERER_SOURCES = (
    "src/renderer/Cinematic.cpp",
    "src/renderer/DeviceContext.cpp",
    "src/renderer/GuiModel.cpp",
    "src/renderer/Image_files.cpp",
    "src/renderer/Image_init.cpp",
    "src/renderer/Image_load.cpp",
    "src/renderer/Image_process.cpp",
    "src/renderer/Image_program.cpp",
    "src/renderer/Interaction.cpp",
    "src/renderer/Material.cpp",
    "src/renderer/Model.cpp",
    "src/renderer/ModelDecal.cpp",
    "src/renderer/ModelManager.cpp",
    "src/renderer/ModelOverlay.cpp",
    "src/renderer/Model_Stuff.cpp",
    "src/renderer/Model_ase.cpp",
    "src/renderer/Model_beam.cpp",
    "src/renderer/Model_liquid.cpp",
    "src/renderer/Model_lwo.cpp",
    "src/renderer/Model_ma.cpp",
    "src/renderer/Model_md3.cpp",
    "src/renderer/Model_md5.cpp",
    "src/renderer/Model_prt.cpp",
    "src/renderer/Model_sprite.cpp",
    "src/renderer/RenderEntity.cpp",
    "src/renderer/RenderSystem.cpp",
    "src/renderer/RenderSystemBackend.cpp",
    "src/renderer/RenderSystem_init.cpp",
    "src/renderer/RenderWorld.cpp",
    "src/renderer/RenderWorld_demo.cpp",
    "src/renderer/RenderWorld_load.cpp",
    "src/renderer/RenderWorld_portals.cpp",
    "src/renderer/RendererJobs.cpp",
    "src/renderer/RendererMetrics.cpp",
    "src/renderer/RuntimeSpirvCompiler.cpp",
    "src/renderer/ScreenRect.cpp",
    "src/renderer/SurfaceTypeMap.cpp",
    "src/renderer/VertexCache.cpp",
    "src/renderer/VulkanBackend.cpp",
    "src/renderer/cg_explicit.cpp",
    "src/renderer/draw_arb2.cpp",
    "src/renderer/draw_atmosphere.cpp",
    "src/renderer/draw_common.cpp",
    "src/renderer/draw_foglights.cpp",
    "src/renderer/draw_new.cpp",
    "src/renderer/draw_ocq.cpp",
    "src/renderer/draw_raytracing.cpp",
    "src/renderer/draw_shadow.cpp",
    "src/renderer/draw_shadowmap.cpp",
    "src/renderer/dynamicmodelcache.cpp",
)

IMGUI_SOURCES = (
    "src/imgui/imgui.cpp",
    "src/imgui/imgui_draw.cpp",
    "src/imgui/imgui_tables.cpp",
    "src/imgui/imgui_widgets.cpp",
)

PLATFORM_POSIX_SOURCES = (
    "src/sys/posix/posix_main.cpp",
    "src/sys/posix/posix_net.cpp",
    "src/sys/posix/posix_signal.cpp",
    "src/sys/posix/posix_syscon.cpp",
    "src/sys/posix/posix_threads.cpp",
)

DARWIN_PLATFORM_SOURCES = (
    "src/sys/osx/macosx_compat.mm",
    "src/sys/osx/macosx_misc.mm",
    "src/sys/osx/macosx_sdl3.cpp",
)

LINUX_DEDICATED_PLATFORM_SOURCES = (
    "src/sys/linux/dedicated.cpp",
    "src/sys/linux/main.cpp",
    "src/sys/linux/stack.cpp",
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit Meson static source lists for openETQW")
    parser.add_argument("--host-system", choices=("windows", "linux", "darwin"), default="darwin")
    parser.add_argument("--platform-backend", choices=("sdl3", "native"), default="sdl3")
    parser.add_argument("--target-kind", choices=("client", "dedicated"), default="client")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    sources: list[str] = []

    # Common engine modules
    sources.extend(COMMON_FRAMEWORK_SOURCES)
    sources.extend(COLLISION_MODEL_SOURCES)
    sources.extend(DECL_LIB_SOURCES)
    sources.extend(NAVIGATION_RECAST_SOURCES)
    sources.extend(SDNET_SOURCES)
    sources.extend(BSE_PARTICLE_SOURCES)

    if args.target_kind == "client":
        sources.extend(CLIENT_SOUND_SOURCES)
        sources.extend(CLIENT_RENDERER_SOURCES)
        sources.extend(IMGUI_SOURCES)

    # Platform selection
    if args.host_system in ("darwin", "linux"):
        sources.extend(PLATFORM_POSIX_SOURCES)
        if args.host_system == "darwin" and args.target_kind == "client":
            sources.extend(DARWIN_PLATFORM_SOURCES)
        elif args.host_system == "linux" and args.target_kind == "dedicated":
            sources.extend(LINUX_DEDICATED_PLATFORM_SOURCES)

    # Deduplicate and print
    seen = set()
    for s in sources:
        if s not in seen:
            seen.add(s)
            print(s)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

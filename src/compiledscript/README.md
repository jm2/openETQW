# ETQW compiled scripts

This directory is the self-contained compiled-script project.

- `scripts/` is the canonical DoomScript input tree used for native script builds.
- `generated/` is recreated by the top-level `compiledscript_generate` target whenever an input script changes.
- The CMake project in this directory builds those generated sources and the runtime support code into `compiledscriptx64.dll` (or `compiledscriptx86.dll`) in the workspace root.

Build the top-level `compiledscript` target; it builds the game exporter, runs it headlessly, configures this project, and builds the DLL.

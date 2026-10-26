# glprogs

ARB assembly ports of retail render programs that retail 1.5 wrote in Cg. The
OpenGL backend has no Cg runtime on x64 or arm64 (`docs/compat-ledger.md` row 077),
so `sdDeclRenderProgram::ParseProgram` loads `glprogs/<decl name>.vp` (vertex) or
`.fp` (fragment), lower case, in place of a `program … cg` block when one exists
(#219). Each file is the body of an ARB program, written the way retail's own
`program … arb` blocks are: `$name` render bindings and `$if` precompiler lines,
without the `!!ARBvp1.0` header or `END`. Vertex programs are position-invariant,
as retail compiled its Cg vertex programs (`-posinv`). The lookup is skipped under
Vulkan and on the dedicated server (`SD_LoadCgReplacement`).

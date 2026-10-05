# Official pk4 checksums

The engine refuses to start unless `base/` holds the retail ET:QW data patched to 1.5, with each required pak unmodified (`src/framework/FileSystem.cpp`, `officialPk4s` and `ValidateRequiredOfficialPaks`). This is openQ4's check (engine commit `b37b1456`, `src/framework/FileSystem.cpp`), ported with ET:QW's paks. The owner chose it on 2026-09-25 ("exactly how openQ4 does"). It is one of the two safeguards the owner attached to accepting the SDK EULA risk the way openQ4 does (2026-09-25); the other is shipping the licence texts with every package.

## What is compared

The value is the engine's own pack checksum, computed when a pak loads (`idFileSystemLocal::LoadPackFile`) and printed as `Loaded pk4 ... with checksum 0x...`. It is not a hash of the whole file: it is MD4 over the zip central directory's CRC-32s (one little-endian word per entry with data), folded to 32 bits. `tools/build/pk4_checksum.py` computes it without the engine.

A pak counts only when it sits directly in a directory named `base` (the pak's parent directory, not any component of its path). The check runs each time the file system starts, in the client and the dedicated server, after the search paths are set up.

## The table

| Pak | Checksum | Required | Source |
|---|---|---|---|
| `pak000.pk4` | `0x442eb08b` | yes | base game (English) |
| `pak001.pk4` | `0x010e16e6` | yes | as above |
| `pak002.pk4` | `0x8dbe7353` | yes | as above |
| `pak003.pk4` | `0x99dfcabb` | yes | as above |
| `pak004.pk4` | `0x7e49f838` | yes | as above |
| `pak005.pk4` | `0x5ccc7213` | yes | official 1.5 patch |
| `pak006.pk4` | `0x9edf1b7d` | yes | as above |
| `pak007.pk4` | `0x074a1a2f` | yes | as above |
| `pak008.pk4` | `0x71a93b80` | yes | as above |
| `zpak_english000.pk4` | `0x977c7bd0` | no | base game (English) |
| `zpak_english001.pk4` | `0x06583cd8` | no | official 1.5 patch |
| `zpak_english002.pk4` | `0x8dc70e3d` | no | as above |
| `zpak_english003.pk4` | `0xc2d7ed49` | no | as above |

Evidence:
- The official 1.5 patch README (`README.txt` in Splash Damage's `ETQW-client-1.5-full.x86.run`) lists the client files in two groups: "from the DVD" (`pak000`–`pak004`, `zpak_english000` and the megatextures) and "patch content" (`pak005`–`pak008` and `zpak_<language>001`–`003`). The Source column follows that split, and the installer's `data/base/` ships exactly the patch-content paks.
- The table's checksums were computed from paks whose MD5s match those two lists.
- The engine's `Loaded pk4` lines give the same values as `tools/build/pk4_checksum.py` for all 13 files.

## Differences from openQ4

- **The 1.5 patch paks are required.** openQ4 requires only its base set (`pak001`–`pak022`) and leaves its patch paks optional. OpenETQW targets 1.5 (owner decision, 2026-09-25), so `pak005`–`pak008` are required. They were checked against one platform's 1.5 patch. That the other platforms' 1.5 patches ship identical paks is inferred, not checked: the patch README tells users they may already have the patch files from another platform's patch.
- **Other editions:** only one English set has been checked. Other editions and languages may ship different `pak000`–`pak004`. The table then needs a second accepted checksum per name, which openQ4's format does not have.
- **Retail dedicated-server data:** the retail dedicated-server download ships its own, server-only `pak000`–`pak005` (different MD5s in the 1.5 README). OpenETQW's dedicated server requires the client set, as openQ4 does; the server-only set is refused.
- **Language warning:** a client without a retail dialogue archive (`zpak_<language>000.pk4`: english, french, german, italian, polish, russian or spanish) prints a warning. The 1.5 patch's 2 KB `zpak_korean000.pk4` holds no dialogue, so it does not count.
- **Retail game modules:** `game000.pk4`–`game002.pk4` in `base/` (the 1.5 patch's Windows, macOS and Linux game modules) are skipped with "Ignoring unneeded game binary pk4". OpenETQW cannot load them.
- Not ported: openQ4's checks of its own runtime paks (`baseoq4`), which OpenETQW does not have.
- As in openQ4, the game module has no check of its own; the owner confirmed this on 2026-09-25, and CHARTER §11 names the engine's check.

## Turning it off

`+set fs_validateOfficialPaks 0` (an init-only CVar, so on the command line) turns the check off. It is for tests on synthetic data (`tools/ci/dedicated_server_smoke.py`). `tests/test_retail_install_check.py` runs the dedicated server on synthetic data: it checks the refusal, the messages and the switch, and needs no retail data. Like openQ4's, the check confirms that unmodified retail files are present, not that the user owns the game.

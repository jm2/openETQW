# 0002: SDK EULA risk, the retail-install check and the licence texts

- **Date:** 2026-09-25
- **Status:** decided by the owner (answers A3, A7 and C11)
- **Scope:** CHARTER §11 mitigations; release gate item 6

## Context

The game repository is id's ET:QW 1.5 SDK code, and the SDK EULA is the only permission to change and share it. EULA §2 allows modifications "that shall operate only with ENEMY TERRITORY: QUAKE WARS (but not any demo, test, or other version)". In 2007 that meant modules loaded by id's executable; OpenETQW loads them into a new engine, which still needs the owner's retail data. The EULA does not define the phrase, and §3(k) bans derivative works outright, which contradicts §2. openQ4's SDK licence has the same clause. It publishes anyway, checks for real Quake 4 data, and has not asked id.

## Decision

- **A3:** accept the risk as openQ4 does, with two safeguards: a retail-install check, and the licence texts in every package.
- **A7:** the check works "exactly how openQ4 does". It is engine-only: when the file system starts, each required base pak must be present with its retail engine checksum, or the engine stops with an error that lists what is missing or modified. The init-only CVar `fs_validateOfficialPaks 0` turns it off for tests on synthetic data.
- **C11:** the data target is retail 1.5, so the required set is `pak000`–`pak008`. Language paks are not required, as in openQ4: editions differ, and only the English set's checksums are known. A client without a base dialogue pak (`zpak_<language>000.pk4`) gets a warning. Docs that name `zpak_english000`–`003` describe the English edition, not an additional requirement.

## Consequences

- The check is built on the branch `feat/retail-install-check` (not merged). `docs/official-pk4-checksums.md` on that branch records the checksums, their sources and the differences from openQ4:
  - the 1.5 patch paks are required, where openQ4's patch paks are optional;
  - a pak counts only when its parent directory is `base`;
  - the retail dedicated-server download's server-only paks are refused;
  - installs without the 1.5 patch are refused until the patch paks are added.
- The game module has no check of its own, as in openQ4. The owner confirmed this on 2026-09-25 ("mirroring openQ4 here sounds reasonable"), and CHARTER §11 now names the engine's check instead of "the game module and engine".
- Licence texts: the engine's install set carries the GPL, the SDK EULA (text and RTF) and the notices of the compiled-in third-party code; the game's install set and CI artifact carry `LICENSE` and the EULA. They are built on `feat/retail-install-check` and game `feat/install-licenses`, neither merged yet.
- The check confirms that unmodified retail files are present, not that the user owns the game. That is the openQ4 precedent the owner accepted.

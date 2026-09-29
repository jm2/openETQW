#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# The 'push' validation profile (tools/validation/openetqw_validate.py); extra
# arguments pass through, e.g. --clean or --sanitize address,undefined.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="$(command -v python3 || command -v python)"
exec "${python}" "${here}/openetqw_validate.py" push "$@"

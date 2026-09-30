# SPDX-License-Identifier: GPL-3.0-or-later
# The 'push' validation profile (tools/validation/openetqw_validate.py); extra
# arguments pass through, e.g. --clean.
$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { $python = Get-Command py }
& $python.Source (Join-Path $PSScriptRoot "openetqw_validate.py") push @args
exit $LASTEXITCODE

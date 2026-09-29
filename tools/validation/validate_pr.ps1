# SPDX-License-Identifier: GPL-3.0-or-later
# The 'pr' validation profile (tools/validation/openetqw_validate.py); extra
# arguments pass through, e.g. --clean.
$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { $python = Get-Command py }
& $python.Source (Join-Path $PSScriptRoot "openetqw_validate.py") pr @args
exit $LASTEXITCODE

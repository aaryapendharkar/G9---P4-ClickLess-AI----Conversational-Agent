<#
.SYNOPSIS
    Evaluation run: preflight checks, then Configs A, B, C and C-noKG one at a time, then the combined report.

.DESCRIPTION
    Run it yourself in a plain PowerShell window (Windows PowerShell 5.1 or PowerShell 7), from any
    directory. It is not meant to be run from Claude Code.

    Preflight (aborts with a message if anything fails):
      - -Split / -Final are consistent; for test, benchmark/FROZEN.json exists and no earlier final
        test run is in results/
      - git: on main, working tree clean (untracked files count); prints the commit. With -Limit
        (a smoke run) another branch or a dirty tree only gives a warning: not reproducible.
      - CLICKLESS_DATA_SOURCE=snapshot
      - snapshot: verify_manifest() -> [] and every config's snapshot id is snapshot:96dfbabb4837 (frozen)
      - Ollama: reachable at VOYAGER_BASE_URL, qwen3:8b digest starts 500a1f067a9f, a warm-up call succeeds

    Then each config runs as its own process (python -m evaluation.harness), logging to
    results/<timestamp>/<config>.log. A crashed config is reported and the others still run.
    At the end, python -m evaluation.report --from results/<timestamp> builds report.md and the CSVs.

.PARAMETER Split
    dev or test.

.PARAMETER Final
    Required with -Split test. The test split is held out and runs once (evaluations/README.md).

.PARAMETER Limit
    Run only the first N items of the split (a quick smoke run; passed to the harness as --limit).
    Not allowed with -Final: the final test run always covers the whole split.

.PARAMETER Python
    Python executable to use (default: python on PATH).

.EXAMPLE
    $env:CLICKLESS_DATA_SOURCE = "snapshot"
    $env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
    .\scripts\final_run.ps1 -Split dev

.EXAMPLE
    .\scripts\final_run.ps1 -Split dev -Limit 5

.EXAMPLE
    .\scripts\final_run.ps1 -Split test -Final
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'test')]
    [string]$Split,

    [switch]$Final,

    [ValidateRange(1, [int]::MaxValue)]
    [int]$Limit = 0,

    [string]$Python = 'python'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Configs          = @('A', 'B', 'C', 'C-noKG')
$ExpectedSnapshot = 'snapshot:96dfbabb4837 (frozen'
$Model            = 'qwen3:8b'
$ExpectedDigest   = '500a1f067a9f'

$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Started = Get-Date

function Stop-Preflight([string]$Message) {
    Write-Host ""
    Write-Host "PREFLIGHT FAILED: $Message" -ForegroundColor Red
    Write-Host "Nothing was run."
    exit 1
}

function Write-Ok([string]$Message) {
    Write-Host "  ok  $Message" -ForegroundColor Green
}

function Format-Duration([TimeSpan]$Span) {
    '{0}h {1:00}m {2:00}s' -f [int][Math]::Floor($Span.TotalHours), $Span.Minutes, $Span.Seconds
}

# Writes UTF-8 without a BOM (Python's json.loads rejects a BOM; Set-Content in PS 5.1 writes one).
function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

$Scope = if ($Limit -gt 0) { "first $Limit items of the $Split split" } else { "$Split split" }
Write-Host "Preflight ($Scope)" -ForegroundColor Cyan

# --- split / -Final -----------------------------------------------------------------------------
if ($Split -eq 'test' -and -not $Final) {
    Stop-Preflight "the test split is held out and runs once. Pass -Final to run it on purpose."
}
if ($Final -and $Split -ne 'test') {
    Stop-Preflight "-Final is only for -Split test."
}
if ($Final -and $Limit -gt 0) {
    Stop-Preflight "-Limit cannot be used with -Final: the final test run covers the whole split."
}
if ($Split -eq 'test') {
    if (-not (Test-Path (Join-Path $Repo 'benchmark\FROZEN.json'))) {
        Stop-Preflight "benchmark\FROZEN.json is missing: freeze the benchmark before the test run."
    }
    $previous = @()
    if (Test-Path (Join-Path $Repo 'results')) {
        $previous = @(Get-ChildItem (Join-Path $Repo 'results') -Recurse -Filter 'report_inputs.json' -ErrorAction SilentlyContinue |
            Where-Object {
                $j = Get-Content $_.FullName -Raw | ConvertFrom-Json
                $names = @($j.PSObject.Properties.Name)
                ($names -contains 'split') -and ($names -contains 'final') -and $j.split -eq 'test' -and $j.final
            })
    }
    if ($previous.Count -gt 0) {
        Stop-Preflight ("a final test run already exists: $($previous[0].DirectoryName). The test split runs once. " +
            "If that run was aborted and must be redone, agree it with the team, move that folder out of results\, and rerun.")
    }
    Write-Ok "test split: -Final given, FROZEN.json present, no earlier final test run in results\"
}

# --- git ----------------------------------------------------------------------------------------
# A full run (no -Limit) and every -Final run need main + a clean tree. A -Limit smoke run may run
# from any branch or a dirty tree, with a warning (-Limit with -Final is already refused above).
$Smoke = ($Limit -gt 0) -and -not $Final
$branch = (& git rev-parse --abbrev-ref HEAD)
if ($LASTEXITCODE -ne 0) { Stop-Preflight "git failed: is $Repo a git checkout?" }
$Branch = $branch.Trim()
$dirty = @(& git status --porcelain)
$Commit = (& git rev-parse HEAD).Trim()
$GitProblems = @()
if ($Branch -ne 'main') { $GitProblems += "on branch '$Branch', not main" }
if ($dirty.Count -gt 0) { $GitProblems += "working tree not clean ($($dirty.Count) changed or untracked paths)" }
if ($GitProblems.Count -eq 0) {
    Write-Ok "git: main, clean, commit $Commit"
} elseif ($Smoke) {
    Write-Host "  WARN smoke run, not reproducible: $($GitProblems -join '; '), commit $Commit" -ForegroundColor Yellow
    $dirty | Select-Object -First 20 | ForEach-Object { Write-Host "         $_" -ForegroundColor Yellow }
    if ($dirty.Count -gt 20) { Write-Host "         ... and $($dirty.Count - 20) more" -ForegroundColor Yellow }
} else {
    $detail = if ($dirty.Count -gt 0) { "`n" + ($dirty -join "`n") } else { "" }
    Stop-Preflight ("$($GitProblems -join '; '). A full or -Final run needs a clean main " +
        "(commit, remove or stash changes first; -Limit allows a smoke run).$detail")
}

# --- data source --------------------------------------------------------------------------------
if ($env:CLICKLESS_DATA_SOURCE -ne 'snapshot') {
    Stop-Preflight "CLICKLESS_DATA_SOURCE is '$($env:CLICKLESS_DATA_SOURCE)', expected 'snapshot'. Run: `$env:CLICKLESS_DATA_SOURCE = `"snapshot`""
}
Write-Ok "CLICKLESS_DATA_SOURCE=snapshot"

# --- snapshot -----------------------------------------------------------------------------------
$verify = (& $Python -m clickless.data.snapshot_report --verify-only) -join "`n"
if ($LASTEXITCODE -ne 0 -or $verify -notmatch '-> \[\] \(intact\)') {
    Stop-Preflight "snapshot verify did not return []:`n$verify"
}
Write-Ok "snapshot: $($verify.Trim())"

# Single quotes only: Windows PowerShell 5.1 drops embedded double quotes when calling a native exe.
$pyList = "[" + (($Configs | ForEach-Object { "'$_'" }) -join ',') + "]"
$py = "import json; from evaluation.report import snapshot_id; " +
      "print(json.dumps({c: snapshot_id(c) for c in $pyList}))"
$snapshotJson = (& $Python -c $py) -join ''
if ($LASTEXITCODE -ne 0) { Stop-Preflight "could not read the snapshot id (see the error above)." }
$SnapshotIds = $snapshotJson | ConvertFrom-Json
foreach ($c in $Configs) {
    $sid = $SnapshotIds.$c
    if (-not $sid.StartsWith($ExpectedSnapshot)) {
        Stop-Preflight "config $c snapshot id is '$sid', expected '$ExpectedSnapshot, ...)'."
    }
}
Write-Ok "snapshot id: $($SnapshotIds.A)"

# --- Ollama -------------------------------------------------------------------------------------
if (-not $env:VOYAGER_BASE_URL) {
    Stop-Preflight "VOYAGER_BASE_URL is not set. Run: `$env:VOYAGER_BASE_URL = `"http://localhost:11434/v1`""
}
$OllamaRoot = ($env:VOYAGER_BASE_URL.TrimEnd('/')) -replace '/v1$', ''
try {
    $tags = Invoke-RestMethod -Uri "$OllamaRoot/api/tags" -TimeoutSec 15
} catch {
    Stop-Preflight "Ollama not reachable at $OllamaRoot ($($_.Exception.Message)). Is 'ollama serve' running?"
}
$entry = @($tags.models | Where-Object { $_.name -eq $Model -or $_.model -eq $Model }) | Select-Object -First 1
if (-not $entry) { Stop-Preflight "$Model is not pulled on $OllamaRoot. Run: ollama pull $Model" }
if (-not $entry.digest.StartsWith($ExpectedDigest)) {
    Stop-Preflight "$Model digest is $($entry.digest.Substring(0, 12)), expected $ExpectedDigest (docs/VOYAGER_LOCAL.md)."
}
Write-Ok "Ollama at $OllamaRoot, $Model digest $($entry.digest.Substring(0, 12))"

$warmBody = @{
    model      = $Model
    prompt     = 'OK'
    stream     = $false
    think      = $false
    keep_alive = '2h'
    options    = @{ num_predict = 1 }
} | ConvertTo-Json -Compress
try {
    $warm = Invoke-RestMethod -Uri "$OllamaRoot/api/generate" -Method Post -Body $warmBody `
        -ContentType 'application/json' -TimeoutSec 300
} catch {
    Stop-Preflight "warm-up call to $Model failed: $($_.Exception.Message)"
}
if (-not $warm.done) { Stop-Preflight "warm-up call to $Model did not finish (done=false)." }
Write-Ok "warm-up call succeeded (model loaded, keep_alive 2h)"

# --- run the configs ----------------------------------------------------------------------------
$Stamp  = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$OutRel = "results/$Stamp"
$OutDir = Join-Path $Repo "results\$Stamp"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$env:PYTHONUNBUFFERED = '1'

Write-Host ""
Write-Host "Running $($Configs -join ', ') on the $Scope -> $OutRel" -ForegroundColor Cyan
if ($Final) { Write-Host "FINAL run on the held-out test split." -ForegroundColor Yellow }

$Failed    = [ordered]@{}
$Outputs   = [ordered]@{}
$Durations = [ordered]@{}
$LimitArg = if ($Limit -gt 0) { " --limit $Limit" } else { "" }
$i = 0
foreach ($c in $Configs) {
    $i++
    $log = "results\$Stamp\$c.log"
    Write-Host ""
    Write-Host "[$i/$($Configs.Count)] Config $c  (log: $log; follow it with: Get-Content $log -Wait)"
    $t0 = Get-Date
    # Each config is its own process. cmd.exe merges stdout and stderr into the one log file.
    $cmdLine = "/d /s /c `"`"$Python`" -m evaluation.harness --config $c --split $Split$LimitArg --out $OutRel > `"$log`" 2>&1`""
    $proc = Start-Process -FilePath $env:ComSpec -ArgumentList $cmdLine -NoNewWindow -Wait -PassThru
    $Durations[$c] = (Get-Date) - $t0
    $json = @(Get-ChildItem -Path $OutDir -Filter "$($c)_$($Split)_*.json" -ErrorAction SilentlyContinue)
    if ($proc.ExitCode -ne 0 -or $json.Count -eq 0) {
        $why = if ($proc.ExitCode -ne 0) { "exit code $($proc.ExitCode)" } else { "no result JSON written" }
        $last = @(Get-Content $log -Tail 1 -ErrorAction SilentlyContinue) -join ''
        $Failed[$c] = "process failed ($why): $last"
        Write-Host "  FAILED: Config $c ($why) after $(Format-Duration $Durations[$c]). Last lines of the log:" -ForegroundColor Red
        Get-Content $log -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
        Write-Host "  Continuing with the next config." -ForegroundColor Yellow
    } else {
        $Outputs[$c] = $json[-1].Name
        Write-Host "  done in $(Format-Duration $Durations[$c]) -> $($json[-1].Name)" -ForegroundColor Green
    }
}

# What evaluation.report's run_all would have written, so the report shows the split, the FINAL
# label, the snapshot each config ran on, and why a config is missing.
$inputs = [ordered]@{
    split       = $Split
    items       = $null
    final       = [bool]$Final
    limit       = $(if ($Limit -gt 0) { $Limit } else { $null })
    configs     = $Configs
    snapshot_id = $SnapshotIds
    failed      = $Failed
    outputs     = $Outputs
    commit      = $Commit
    git_branch  = $Branch
    git_dirty   = ($dirty.Count -gt 0)
}
Write-Utf8NoBom (Join-Path $OutDir 'report_inputs.json') ($inputs | ConvertTo-Json -Depth 5)

# --- combined report ----------------------------------------------------------------------------
Write-Host ""
Write-Host "Building the combined report" -ForegroundColor Cyan
& $Python -m evaluation.report --from $OutRel
$reportOk = ($LASTEXITCODE -eq 0)
$reportMd = Join-Path $OutDir 'report.md'

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
if ($reportOk -and (Test-Path $reportMd)) {
    Write-Host "Report: $reportMd"
    Write-Host ""
    $inHeadline = $false
    foreach ($line in Get-Content $reportMd -Encoding UTF8) {
        if ($line -match '^## ') {
            if ($inHeadline) { break }
            $inHeadline = ($line -eq '## Headline')
        }
        if ($inHeadline) { Write-Host $line }
    }
} else {
    Write-Host "Report build FAILED (see the error above). Rebuild with: $Python -m evaluation.report --from $OutRel" -ForegroundColor Red
}

Write-Host ""
foreach ($c in $Configs) {
    $status = if ($Failed.Contains($c)) { "FAILED" } else { "ok" }
    $colour = if ($Failed.Contains($c)) { 'Red' } else { 'Green' }
    Write-Host ("  {0,-7} {1,-6} {2}" -f $c, $status, (Format-Duration $Durations[$c])) -ForegroundColor $colour
}
if ($Failed.Count -gt 0) {
    Write-Host "Failed configs: $($Failed.Keys -join ', ') (logs in results\$Stamp\)" -ForegroundColor Red
}
Write-Host "Commit $Commit, $Scope$(if ($Final) { ' (FINAL)' })"
if ($GitProblems.Count -gt 0) {
    Write-Host "WARNING: smoke run, not reproducible ($($GitProblems -join '; '))." -ForegroundColor Yellow
}
Write-Host "Total time: $(Format-Duration ((Get-Date) - $Started))"

if ($Failed.Count -gt 0 -or -not $reportOk) { exit 1 }
exit 0

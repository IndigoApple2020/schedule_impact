# Keyword discovery + TF-IDF classification - batch runner over many datasets.
#
# Reads a list of jobs from scripts\keyword-batch.config.ps1 (gitignored).
# Each job runs:
#   1. text-classify discover-keywords   (always)
#   2. text-classify classify-tfidf      (only if Taxonomy is set)
#
# Usage:
#   .\scripts\keyword-batch.ps1                              # all jobs, sequential
#   .\scripts\keyword-batch.ps1 -Parallel                    # all jobs at once
#   .\scripts\keyword-batch.ps1 -Only root_cause,activity    # selected jobs only
#                                                           # (-Jobs also works as alias)
#   .\scripts\keyword-batch.ps1 -DiscoverOnly                # skip classify step
#   .\scripts\keyword-batch.ps1 -DryRun                      # print commands, do nothing
#   .\scripts\keyword-batch.ps1 -ShowCommands                # echo each command, then run

[CmdletBinding()]
param(
    [Alias("Jobs")]                # backward compat - older docs/examples used -Jobs
    [string]$Only = "",            # comma-separated job names to run (empty = all)
    [switch]$Parallel,             # run jobs concurrently via Start-Job
    [switch]$DiscoverOnly,         # skip classify-tfidf even if Taxonomy is set
    [switch]$DryRun,               # print every CLI invocation, execute none
    [switch]$ShowCommands          # echo every CLI invocation, then execute
)

$ErrorActionPreference = "Stop"

# --- Load gitignored job config -----------------------------------------
$ConfigPath = Join-Path $PSScriptRoot "keyword-batch.config.ps1"
if (-not (Test-Path $ConfigPath)) {
    Write-Host ""
    Write-Host "ERROR: $ConfigPath not found." -ForegroundColor Red
    Write-Host ""
    Write-Host "First-time setup:" -ForegroundColor Yellow
    Write-Host "  copy `"$PSScriptRoot\keyword-batch.config.example.ps1`" \`" -ForegroundColor Yellow
    Write-Host "       `"$ConfigPath`"" -ForegroundColor Yellow
    Write-Host "  notepad `"$ConfigPath`"        # edit the `$Jobs array" -ForegroundColor Yellow
    Write-Host ""
    throw "Config file missing"
}
. $ConfigPath

# Validate that the config defined a populated $Jobs array of hashtables
if (-not (Get-Variable -Name "Jobs" -Scope Script -ValueOnly -ErrorAction SilentlyContinue)) {
    throw "Config did not define `$Jobs (expected an array of job hashtables)."
}
if ($Jobs.Count -eq 0) {
    throw "Config `$Jobs array is empty. Add at least one job hashtable."
}

# --- Helpers ------------------------------------------------------------
function Section($title) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host $title -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
}

function Get-JobField($job, $name, $default) {
    if ($job.ContainsKey($name) -and ($null -ne $job[$name]) -and ($job[$name] -ne "")) {
        return $job[$name]
    }
    return $default
}

function Invoke-OrEcho($command, $argList) {
    if ($ShowCommands -or $DryRun) {
        Write-Host ""
        Write-Host "$command $($argList -join ' ')" -ForegroundColor Magenta
    }
    if ($DryRun) { return $true }
    & $command @argList
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $command exit $LASTEXITCODE" -ForegroundColor Red
        return $false
    }
    return $true
}

function Run-Job($job) {
    $name = $job.Name
    if ([string]::IsNullOrWhiteSpace($name)) {
        Write-Host "[SKIP] Job with no Name field" -ForegroundColor Yellow
        return
    }
    Section "Job: $name"

    # Validate required fields
    if ([string]::IsNullOrWhiteSpace($job.Input)) {
        Write-Host "[FAIL] $name : Input not set" -ForegroundColor Red
        return
    }
    if (-not (Test-Path $job.Input)) {
        Write-Host "[FAIL] $name : Input file does not exist: $($job.Input)" -ForegroundColor Red
        return
    }
    if ([string]::IsNullOrWhiteSpace($job.Output)) {
        Write-Host "[FAIL] $name : Output not set" -ForegroundColor Red
        return
    }
    New-Item -ItemType Directory -Path $job.Output -Force | Out-Null

    $idColumn    = Get-JobField $job "IdColumn"    "row_id"
    $textColumn  = Get-JobField $job "TextColumn"  "root_cause"
    $minDocCount = Get-JobField $job "MinDocCount" 5
    $maxDocCount = Get-JobField $job "MaxDocCount" 5000
    $ngramMin    = Get-JobField $job "NgramMin"    1
    $ngramMax    = Get-JobField $job "NgramMax"    3
    # SplitNgrams (default $true): run a separate keyword pass for each ngram
    # size in [NgramMin..NgramMax], producing one keywords CSV per size.
    # Set to $false for a single combined pass over the whole range.
    $splitNgrams = Get-JobField $job "SplitNgrams" $true
    $threshold   = Get-JobField $job "Threshold"   $null
    $taxonomy    = Get-JobField $job "Taxonomy"    $null

    # --- 1. discover-keywords
    # When SplitNgrams is true, run a separate pass per n-gram size so each
    # gets its own keywords CSV (1-grams in keywords\ngram_1\,
    # 2-grams in keywords\ngram_2\, etc.). Easier to scan than a mixed CSV.
    $sizes = @()
    if ($splitNgrams) {
        for ($n = $ngramMin; $n -le $ngramMax; $n++) { $sizes += $n }
    } else {
        $sizes = @(@($ngramMin, $ngramMax))   # single combined pass
    }

    foreach ($size in $sizes) {
        if ($splitNgrams) {
            $thisMin = $size
            $thisMax = $size
            $label   = "ngram_$size"
        } else {
            $thisMin = $size[0]
            $thisMax = $size[1]
            $label   = "ngram_${thisMin}_${thisMax}"
        }
        Write-Host "[$name] Stage 1: discover-keywords ($label)" -ForegroundColor Green
        $kwOut = Join-Path $job.Output (Join-Path "keywords" $label)
        $kwArgs = @(
            "discover-keywords",
            "--input",       $job.Input,
            "--out",         $kwOut,
            "--id-column",   $idColumn,
            "--text-column", $textColumn,
            "--min-doc-count", ([string]$minDocCount),
            "--max-doc-count", ([string]$maxDocCount),
            "--ngram-min",     ([string]$thisMin),
            "--ngram-max",     ([string]$thisMax)
        )
        if ($taxonomy -and (Test-Path $taxonomy)) {
            $kwArgs += @("--taxonomy", $taxonomy)
        }
        $ok = Invoke-OrEcho "text-classify" $kwArgs
        if (-not $ok) { return }
    }

    # --- 2. classify-tfidf (only if a taxonomy is set AND -DiscoverOnly not given)
    if ($DiscoverOnly) {
        Write-Host "[$name] Stage 2 skipped (-DiscoverOnly)" -ForegroundColor Yellow
        return
    }
    if (-not $taxonomy) {
        Write-Host "[$name] Stage 2 skipped (no taxonomy set - keyword-only run)" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path $taxonomy)) {
        Write-Host "[$name] Stage 2 skipped (taxonomy file not found: $taxonomy)" -ForegroundColor Yellow
        return
    }

    Write-Host "[$name] Stage 2: classify-tfidf" -ForegroundColor Green
    $clOut = Join-Path $job.Output "classify"
    $clArgs = @(
        "classify-tfidf",
        "--input",       $job.Input,
        "--taxonomy",    $taxonomy,
        "--out",         $clOut,
        "--id-column",   $idColumn,
        "--text-column", $textColumn
    )
    if ($null -ne $threshold) { $clArgs += @("--threshold", ([string]$threshold)) }
    Invoke-OrEcho "text-classify" $clArgs | Out-Null
}

# --- Pick the subset of jobs to run -------------------------------------
$selectedNames = @()
if (-not [string]::IsNullOrWhiteSpace($Only)) {
    $selectedNames = $Only.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ }
}

$toRun = @()
foreach ($job in $Jobs) {
    if ($selectedNames.Count -gt 0 -and -not ($selectedNames -contains $job.Name)) {
        continue
    }
    $toRun += $job
}

if ($toRun.Count -eq 0) {
    Write-Host "No jobs selected." -ForegroundColor Yellow
    Write-Host "  -Only filter:  '$Only'" -ForegroundColor Yellow
    Write-Host "  Available job names in config:" -ForegroundColor Yellow
    foreach ($job in $Jobs) { Write-Host "    $($job.Name)" }
    exit 1
}

Section "Plan"
Write-Host "Jobs to run: $($toRun.Name -join ', ')"
if ($Parallel) { Write-Host "Mode: parallel (PowerShell Start-Job)" } else { Write-Host "Mode: sequential" }
if ($DiscoverOnly) { Write-Host "Stage 2 (classify-tfidf): disabled" }
if ($DryRun)      { Write-Host "DryRun: commands will be printed, not executed" }

# --- Execute ------------------------------------------------------------
if ($Parallel -and -not $DryRun) {
    # Each job runs in its own PowerShell job. Output streams are merged at the end.
    $bgJobs = @()
    foreach ($job in $toRun) {
        $scriptPath = $PSCommandPath
        $bgJobs += Start-Job -ScriptBlock {
            param($scriptPath, $jobName, $discoverOnly, $showCmds)
            $args = @($scriptPath, "-Only", $jobName)
            if ($discoverOnly) { $args += "-DiscoverOnly" }
            if ($showCmds)     { $args += "-ShowCommands" }
            & powershell -NoProfile -ExecutionPolicy Bypass -File $args
        } -ArgumentList $scriptPath, $job.Name, $DiscoverOnly.IsPresent, $ShowCommands.IsPresent
    }
    Write-Host ""
    Write-Host "Started $($bgJobs.Count) background job(s). Waiting..." -ForegroundColor Cyan
    $bgJobs | Wait-Job | Out-Null
    foreach ($bj in $bgJobs) {
        Write-Host ""
        Write-Host ("=" * 72) -ForegroundColor Cyan
        Write-Host "Background job output ($($bj.Name)):" -ForegroundColor Cyan
        Write-Host ("=" * 72) -ForegroundColor Cyan
        Receive-Job -Job $bj
    }
    $bgJobs | Remove-Job
} else {
    foreach ($job in $toRun) {
        Run-Job $job
    }
}

Section "Done"
Write-Host "Completed $($toRun.Count) job(s)." -ForegroundColor Green

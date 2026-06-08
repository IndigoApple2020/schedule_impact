# Multi-period theme analysis — reusable across datasets.
#
# Chains: run-batch -> aggregate-memos -> discover-keywords -> classify-llm-prompt.
# Edit the variables at the top, then run the whole script (or sections of it).
#
# Usage:
#   .\scripts\multi-period-analysis.ps1
#   .\scripts\multi-period-analysis.ps1 -SkipBatch    # if pipeline outputs already exist
#   .\scripts\multi-period-analysis.ps1 -KeywordsOnly # stop after discover-keywords
#   .\scripts\multi-period-analysis.ps1 -SkipClassify # skip the slow LLM stage
#   .\scripts\multi-period-analysis.ps1 -Verbose      # echo every CLI invocation with all args
#   .\scripts\multi-period-analysis.ps1 -DryRun       # print commands without executing
#
# Outputs (all under $AnalysisDir):
#   all_memos.csv                    aggregated memos from every monthly run
#   keywords\<run>\keywords.csv      recurring phrases across all memos
#   keywords\<run>\keywords_by_category.csv
#   classify\<run>\matches.csv       LLM classification against your taxonomy
#   classify\<run>\all_scores_sub_long.csv

[CmdletBinding()]
param(
    [switch]$SkipBatch,
    [switch]$KeywordsOnly,
    [switch]$SkipClassify,
    [switch]$Verbose,           # echo every variable + every CLI invocation
    [switch]$DryRun             # print commands without executing
)

$ErrorActionPreference = "Stop"

# ─── Edit these for your dataset ─────────────────────────────────────────────
$Programme    = "HS2"
$XerRoot      = "C:\Users\admin\data\raw\xer\HS2"          # contains {YYYY-MM}\*.xer
$PdfRoot      = "C:\Users\admin\data\raw\pdf\HS2"          # optional; $null to skip
$Taxonomy     = "C:\Users\admin\Documents\git\schedule_impact\config\taxonomies\construction_root_cause.yaml"
$OutputsRoot  = "C:\Users\admin\outputs"                   # schedule_impact run-monthly outputs land here
$AnalysisDir  = "C:\Users\admin\outputs\HS2_analysis"      # this script's outputs land here
$LlmModel     = "llama3.1:8b"                              # Ollama model for classify-llm-prompt
$LlmThreshold = 0.4                                        # match threshold for matches.csv (full scores always saved)
# ──────────────────────────────────────────────────────────────────────────────

# Derived paths
$AllMemos    = Join-Path $AnalysisDir "all_memos.csv"
$KeywordsDir = Join-Path $AnalysisDir "keywords"
$ClassifyDir = Join-Path $AnalysisDir "classify_run"

function Section($title) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host $title -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
}

function Assert-NonEmpty($name, $value) {
    if ([string]::IsNullOrWhiteSpace([string]$value)) {
        throw "Required variable '$name' is empty or null. Edit the variables at the top of the script."
    }
}

function Show-Vars() {
    Write-Host "  Programme    = $Programme"
    Write-Host "  XerRoot      = $XerRoot"
    Write-Host "  PdfRoot      = $PdfRoot"
    Write-Host "  Taxonomy     = $Taxonomy"
    Write-Host "  OutputsRoot  = $OutputsRoot"
    Write-Host "  AnalysisDir  = $AnalysisDir"
    Write-Host "  AllMemos     = $AllMemos"
    Write-Host "  KeywordsDir  = $KeywordsDir"
    Write-Host "  ClassifyDir  = $ClassifyDir"
    Write-Host "  LlmModel     = $LlmModel"
    Write-Host "  LlmThreshold = $LlmThreshold"
    Write-Host "  Flags:  SkipBatch=$SkipBatch  KeywordsOnly=$KeywordsOnly  SkipClassify=$SkipClassify  DryRun=$DryRun"
}

function Invoke-CLI($command, $argList) {
    if ($Verbose -or $DryRun) {
        Write-Host ""
        Write-Host "$command $($argList -join ' ')" -ForegroundColor Magenta
    }
    if ($DryRun) { return }
    & $command @argList
    if ($LASTEXITCODE -ne 0) {
        throw "$command failed with exit code $LASTEXITCODE"
    }
}

# Validate critical variables before any stage runs
Section "Configuration check"
Show-Vars
Assert-NonEmpty "Programme"    $Programme
Assert-NonEmpty "XerRoot"      $XerRoot
Assert-NonEmpty "Taxonomy"     $Taxonomy
Assert-NonEmpty "OutputsRoot"  $OutputsRoot
Assert-NonEmpty "AnalysisDir"  $AnalysisDir
Assert-NonEmpty "LlmModel"     $LlmModel
Assert-NonEmpty "LlmThreshold" $LlmThreshold
if (-not (Test-Path $XerRoot))   { throw "XerRoot does not exist: $XerRoot" }
if (-not (Test-Path $Taxonomy))  { throw "Taxonomy file not found: $Taxonomy" }
New-Item -ItemType Directory -Path $AnalysisDir -Force | Out-Null

# 1. Run the monthly pipeline for every consecutive XER pair
if (-not $SkipBatch) {
    Section "1/4  run-batch — extract incidents + memos for every month pair"
    $batchArgs = @(
        "run-batch",
        "--programme", $Programme,
        "--xer-root",  $XerRoot,
        "--output-dir", $OutputsRoot,
        "--skip-existing"
    )
    if ($PdfRoot -and (Test-Path $PdfRoot)) {
        $batchArgs += @("--pdf-root", $PdfRoot)
    }
    Invoke-CLI "schedule-impact" $batchArgs
} else {
    Section "1/4  run-batch — skipped (--SkipBatch passed)"
}

# 2. Aggregate every period's taskmemo_chunks_*.csv into one CSV
Section "2/4  aggregate-memos — combine all memo CSVs across months"
$aggArgs = @(
    "aggregate-memos",
    "--outputs-root", (Join-Path $OutputsRoot $Programme),
    "--out", $AllMemos
)
Invoke-CLI "schedule-impact" $aggArgs

if (-not (Test-Path $AllMemos)) {
    throw "Stage 2 did not produce $AllMemos — cannot proceed. Check the aggregate-memos output above."
}

# 3. Recurring-phrase mining across the whole multi-period corpus
Section "3/4  discover-keywords — recurring phrases across all memos"
$kwArgs = @(
    "discover-keywords",
    "--input",    $AllMemos,
    "--taxonomy", $Taxonomy,
    "--out",      $KeywordsDir,
    "--min-doc-count", "10"
)
Invoke-CLI "text-classify" $kwArgs

if ($KeywordsOnly) {
    Write-Host ""
    Write-Host "Stopped after keyword discovery (--KeywordsOnly passed)." -ForegroundColor Yellow
    Write-Host "Inspect $KeywordsDir\<run>\keywords.csv before re-running without -KeywordsOnly." -ForegroundColor Yellow
    exit 0
}

# 4. LLM-prompt classification against the root-cause taxonomy
#    Uses a FIXED subdirectory so re-runs resume the same run rather than
#    starting fresh. Per-row checkpoint NDJSON lives there; on re-invocation
#    every row already in the checkpoint is skipped (no LLM call).
#    To start a fresh classify run, delete $ClassifyDir before re-running.
if (-not $SkipClassify) {
    Section "4/4  classify-llm-prompt — score every memo against the taxonomy"

    # Detect existing checkpoint and report progress (best-effort)
    $checkpointPath = Join-Path $ClassifyDir "llm_prompt_checkpoint.ndjson"
    if (Test-Path $checkpointPath) {
        $doneCount = 0
        try { $doneCount = (Get-Content $checkpointPath | Measure-Object -Line).Lines } catch {}
        $totalCount = "?"
        if (Test-Path $AllMemos) {
            try {
                $lineCount = (Get-Content $AllMemos | Measure-Object -Line).Lines
                $totalCount = [Math]::Max(0, $lineCount - 1)
            } catch {}
        }
        Write-Host "Resuming previous run: $doneCount / $totalCount rows already scored." -ForegroundColor Green
    } else {
        Write-Host "Starting fresh classify run -> $ClassifyDir" -ForegroundColor Yellow
    }

    Write-Host ""
    Write-Host "NOTE: ~1-3 sec per memo on CPU. Progress bar shows ETA." -ForegroundColor Yellow
    Write-Host "      Cancel anytime with Ctrl-C — every completed memo is on disk." -ForegroundColor Yellow
    Write-Host "      Re-run the script to pick up where you left off." -ForegroundColor Yellow
    Write-Host ""

    $classifyOut = Join-Path $AnalysisDir "classify"
    $clArgs = @(
        "classify-llm-prompt",
        "--input",       $AllMemos,
        "--taxonomy",    $Taxonomy,
        "--out",         $classifyOut,
        "--resume-dir",  $ClassifyDir,
        "--model",       $LlmModel,
        "--threshold",   ([string]$LlmThreshold)
    )
    Invoke-CLI "text-classify" $clArgs

    # Final progress summary
    if (Test-Path $checkpointPath) {
        try {
            $finalCount = (Get-Content $checkpointPath | Measure-Object -Line).Lines
            Write-Host ""
            Write-Host "Checkpoint now at $finalCount rows." -ForegroundColor Green
        } catch {}
    }
} else {
    Section "4/4  classify-llm-prompt — skipped (--SkipClassify passed)"
}

Write-Host ""
Write-Host "Done. Outputs under $AnalysisDir" -ForegroundColor Green

# Multi-period theme analysis — reusable across datasets.
#
# Chains: run-batch -> aggregate-memos -> discover-keywords -> classify-llm-prompt.
# Edit the variables at the top, then run the whole script (or sections of it).
#
# Usage:
#   .\scripts\multi-period-analysis.ps1
#   .\scripts\multi-period-analysis.ps1 -SkipBatch    # if pipeline outputs already exist
#   .\scripts\multi-period-analysis.ps1 -KeywordsOnly # stop after discover-keywords
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
    [switch]$SkipClassify
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

function Section($title) {
    Write-Host ""
    Write-Host ("=" * 72) -ForegroundColor Cyan
    Write-Host $title -ForegroundColor Cyan
    Write-Host ("=" * 72) -ForegroundColor Cyan
}

# 1. Run the monthly pipeline for every consecutive XER pair
if (-not $SkipBatch) {
    Section "1/4  run-batch — extract incidents + memos for every month pair"
    $pdfArgs = if ($PdfRoot -and (Test-Path $PdfRoot)) { @("--pdf-root", $PdfRoot) } else { @() }
    schedule-impact run-batch `
        --programme    $Programme `
        --xer-root     $XerRoot `
        @pdfArgs `
        --output-dir   $OutputsRoot `
        --skip-existing
} else {
    Section "1/4  run-batch — skipped (--SkipBatch passed)"
}

# 2. Aggregate every period's taskmemo_chunks_*.csv into one CSV
Section "2/4  aggregate-memos — combine all memo CSVs across months"
$AllMemos = Join-Path $AnalysisDir "all_memos.csv"
schedule-impact aggregate-memos `
    --outputs-root (Join-Path $OutputsRoot $Programme) `
    --out          $AllMemos

# 3. Recurring-phrase mining across the whole multi-period corpus
Section "3/4  discover-keywords — recurring phrases across all memos"
text-classify discover-keywords `
    --input    $AllMemos `
    --taxonomy $Taxonomy `
    --out      (Join-Path $AnalysisDir "keywords") `
    --min-doc-count 10

if ($KeywordsOnly) {
    Write-Host ""
    Write-Host "Stopped after keyword discovery (--KeywordsOnly passed)." -ForegroundColor Yellow
    Write-Host "Inspect $((Join-Path $AnalysisDir 'keywords'))\<run>\keywords.csv before re-running with classify." -ForegroundColor Yellow
    exit 0
}

# 4. LLM-prompt classification against the root-cause taxonomy
#    Uses a FIXED subdirectory so re-runs resume the same run rather than
#    starting fresh. Per-row checkpoint NDJSON lives there; on re-invocation
#    every row already in the checkpoint is skipped (no LLM call).
#    To start a fresh classify run, delete $ClassifyDir before re-running.
if (-not $SkipClassify) {
    Section "4/4  classify-llm-prompt — score every memo against the taxonomy"
    $ClassifyDir = Join-Path $AnalysisDir "classify_run"

    # Detect existing checkpoint and report progress
    $checkpointPath = Join-Path $ClassifyDir "llm_prompt_checkpoint.ndjson"
    if (Test-Path $checkpointPath) {
        $doneCount = 0
        try { $doneCount = (Get-Content $checkpointPath | Measure-Object -Line).Lines } catch {}
        # Total rows in aggregated memo CSV (minus 1 for the header row)
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

    text-classify classify-llm-prompt `
        --input       $AllMemos `
        --taxonomy    $Taxonomy `
        --out         (Join-Path $AnalysisDir "classify") `
        --resume-dir  $ClassifyDir `
        --model       $LlmModel `
        --threshold   $LlmThreshold

    # Final progress summary
    if (Test-Path $checkpointPath) {
        $finalCount = (Get-Content $checkpointPath | Measure-Object -Line).Lines
        Write-Host ""
        Write-Host "Checkpoint now at $finalCount rows." -ForegroundColor Green
    }
} else {
    Section "4/4  classify-llm-prompt — skipped (--SkipClassify passed)"
}

Write-Host ""
Write-Host "Done. Outputs under $AnalysisDir" -ForegroundColor Green

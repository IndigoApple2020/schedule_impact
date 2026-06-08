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
if (-not $SkipClassify) {
    Section "4/4  classify-llm-prompt — score every memo against the taxonomy"
    Write-Host "WARNING: This step calls Ollama once per memo. Expect ~1-3 sec per memo." -ForegroundColor Yellow
    Write-Host "         Cancel with Ctrl-C — checkpoint will be saved for resume." -ForegroundColor Yellow
    Write-Host ""
    text-classify classify-llm-prompt `
        --input     $AllMemos `
        --taxonomy  $Taxonomy `
        --out       (Join-Path $AnalysisDir "classify") `
        --model     $LlmModel `
        --threshold $LlmThreshold
} else {
    Section "4/4  classify-llm-prompt — skipped (--SkipClassify passed)"
}

Write-Host ""
Write-Host "Done. Outputs under $AnalysisDir" -ForegroundColor Green

# Multi-period analysis — user-specific configuration.
#
# Copy this file to scripts\multi-period-analysis.config.ps1 (gitignored)
# and edit the values for your dataset. The .config.ps1 copy is never
# touched by `git pull`, so your paths survive future updates.
#
#   copy scripts\multi-period-analysis.config.example.ps1 `
#        scripts\multi-period-analysis.config.ps1

$Programme    = "HS2"
$XerRoot      = "C:\Users\admin\data\raw\xer\HS2"          # contains either {YYYY-MM}\*.xer OR flat *.xer files
$PdfRoot      = "C:\Users\admin\data\raw\pdf\HS2"          # optional — set to $null to skip
$Taxonomy     = "C:\Users\admin\Documents\git\schedule_impact\config\taxonomies\construction_root_cause.yaml"
$OutputsRoot  = "C:\Users\admin\outputs"                   # schedule_impact run-monthly outputs land here
$AnalysisDir  = "C:\Users\admin\outputs\HS2_analysis"      # this script's outputs land here
$LlmModel     = "llama3.1:8b"                              # Ollama model for classify-llm-prompt
$LlmThreshold = 0.4                                        # match threshold for matches.csv (full scores always saved)

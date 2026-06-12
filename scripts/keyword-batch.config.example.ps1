# Keyword batch - user-specific job list.
#
# Copy this file to scripts\keyword-batch.config.ps1 (gitignored) and
# edit the $Jobs array for your datasets. Each job in $Jobs runs through:
#     1. text-classify discover-keywords   (always)
#     2. text-classify classify-tfidf      (only if Taxonomy is set and
#                                           file exists)
#
# Set Taxonomy = $null (or omit the line) to do keyword discovery only.
# That's the recommended first pass for a new dataset - look at the
# discovered keywords, then group them into a YAML taxonomy, then re-run
# with Taxonomy set to score the rows against it.
#
# Optional fields with defaults:
#   IdColumn      "row_id"
#   TextColumn    "root_cause"
#   MinDocCount   5          (phrase must appear in at least this many rows)
#   MaxDocCount   5000       (ignore phrases more common than this)
#   NgramMin      1          (set to 2 if you want phrases only, no single words)
#   NgramMax      3          (largest phrase length)
#   Threshold     $null      (use taxonomy default_threshold)
#   Taxonomy      $null      (skip classify-tfidf stage)

$Jobs = @(
    @{
        Name        = "root_cause"
        Input       = "C:\Users\admin\data\issues\root_causes.csv"
        TextColumn  = "root_cause"
        Taxonomy    = "C:\Users\admin\Documents\git\schedule_impact\config\taxonomies\construction_root_cause.yaml"
        Output      = "C:\Users\admin\outputs\keyword_analysis\root_cause"
        MinDocCount = 5
    },
    @{
        Name        = "activity"
        Input       = "C:\Users\admin\data\issues\activities.csv"
        TextColumn  = "activity_description"
        Taxonomy    = $null                                                # discover only - no taxonomy yet
        Output      = "C:\Users\admin\outputs\keyword_analysis\activity"
        MinDocCount = 5
        NgramMin    = 2                                                    # phrases only - skip single words
        NgramMax    = 3
    },
    @{
        Name        = "qms_process"
        Input       = "C:\Users\admin\data\issues\qms_findings.csv"
        TextColumn  = "finding_text"
        Taxonomy    = $null
        Output      = "C:\Users\admin\outputs\keyword_analysis\qms_process"
    },
    @{
        Name        = "process"
        Input       = "C:\Users\admin\data\issues\process_observations.csv"
        TextColumn  = "observation_text"
        Taxonomy    = $null
        Output      = "C:\Users\admin\outputs\keyword_analysis\process"
    }
)

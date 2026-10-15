param(
    [double]$TimeLimit = 180,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$statusLogDirectory = Join-Path $projectRoot "results\run_logs"
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$statusLogPath = Join-Path $statusLogDirectory "rerun_total11_selected-$runId.log"
$cases = @(
    [pscustomobject]@{ R0 = 1; Rm = 9; R1 = 1 },
    [pscustomobject]@{ R0 = 1; Rm = 8; R1 = 2 },
    [pscustomobject]@{ R0 = 1; Rm = 7; R1 = 3 },
    [pscustomobject]@{ R0 = 1; Rm = 6; R1 = 4 },
    [pscustomobject]@{ R0 = 1; Rm = 5; R1 = 5 }
)

New-Item -ItemType Directory -Path $statusLogDirectory -Force | Out-Null

function Write-Status {
    param(
        [ValidateSet("INFO", "SUCCESS", "ERROR")]
        [string]$Level,
        [string]$Message
    )

    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [$Level] $Message"
    Write-Host $line
    Add-Content -LiteralPath $statusLogPath -Value $line -Encoding utf8
}

$failures = @()
$batchStarted = Get-Date
Write-Status -Level "INFO" -Message "Batch started; launcher PID=$PID; cases=$($cases.Count); TimeLimit=$TimeLimit; weights=6,3,6; probtest=t"
Push-Location -LiteralPath $projectRoot
try {
    for ($index = 0; $index -lt $cases.Count; $index++) {
        $case = $cases[$index]
        $caseName = "$($case.R0)-$($case.Rm)-$($case.R1)_636"
        $caseStarted = Get-Date
        Write-Status -Level "INFO" -Message "Case $($index + 1)/$($cases.Count) started: $caseName"

        & $PythonExe .\boom.py `
            -r0 $case.R0 `
            -rm $case.Rm `
            -r1 $case.R1 `
            -w0 6 `
            -wm 3 `
            -w1 6 `
            -tl $TimeLimit `
            --probtest t

        if ($LASTEXITCODE -ne 0) {
            $failures += $caseName
            $elapsed = [math]::Round(((Get-Date) - $caseStarted).TotalSeconds, 3)
            Write-Status -Level "ERROR" -Message "Case $($index + 1)/$($cases.Count) failed: $caseName; exit=$LASTEXITCODE; elapsed=${elapsed}s"
        }
        else {
            $elapsed = [math]::Round(((Get-Date) - $caseStarted).TotalSeconds, 3)
            Write-Status -Level "SUCCESS" -Message "Case $($index + 1)/$($cases.Count) completed: $caseName; exit=0; elapsed=${elapsed}s"
        }
    }
}
finally {
    Pop-Location
}

$batchElapsed = [math]::Round(((Get-Date) - $batchStarted).TotalSeconds, 3)
if ($failures.Count -gt 0) {
    Write-Status -Level "ERROR" -Message "Batch finished with failures; failed=$($failures -join ','); elapsed=${batchElapsed}s; log=$statusLogPath"
    throw "Failed cases: $($failures -join ', ')"
}

Write-Status -Level "SUCCESS" -Message "Batch completed successfully; passed=$($cases.Count); failed=0; elapsed=${batchElapsed}s; log=$statusLogPath"

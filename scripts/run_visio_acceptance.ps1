[CmdletBinding()]
param(
    [string]$VsdxPath,
    [int]$ExpectedPages = 1,
    [int]$ExpectedShapes = -1,
    [string]$DebugLog = ""
)

Set-StrictMode -Version Latest

function New-VisioAcceptanceResult {
    param([string]$SourceFile)

    return [ordered]@{
        status = "input"
        exit_code = 2
        source_file = $SourceFile
        expected_pages = $null
        expected_shapes = $null
        actual_pages = $null
        actual_shapes = $null
        per_page_shape_counts = @()
        document_closed = $false
        application_quit = $false
        temp_cleaned = $true
        source_unchanged = $false
        temp_directory = $null
        errors = @()
    }
}

function Add-VisioAcceptanceError {
    param(
        [System.Collections.ArrayList]$Errors,
        [string]$Message
    )

    [void]$Errors.Add($Message)
}

function Write-GateStage {
    param([string]$Message)
    if ($DebugLog) {
        Add-Content -LiteralPath $DebugLog -Value ("STAGE " + $Message) -Encoding utf8
    }
}

function Get-VisioAcceptanceSha256 {
    param([string]$Path)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        return ([System.BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
    }
}

function Invoke-VisioAcceptance {
    [CmdletBinding()]
    param(
        [string]$VsdxPath,
        [int]$ExpectedPages = 1,
        [int]$ExpectedShapes = -1,
        [scriptblock]$ApplicationFactory = { New-Object -ComObject Visio.Application }
    )

    $sourceFile = if ([string]::IsNullOrWhiteSpace($VsdxPath)) { $null } else { [System.IO.Path]::GetFileName($VsdxPath) }
    $result = New-VisioAcceptanceResult -SourceFile $sourceFile
    $result.expected_pages = $ExpectedPages
    $result.expected_shapes = $ExpectedShapes
    $errors = [System.Collections.ArrayList]::new()
    $sourceHashBefore = $null
    $sourceHashAfter = $null
    $document = $null
    $application = $null
    $activationUnavailable = $false
    $compatibilityFailure = $false

    if ([string]::IsNullOrWhiteSpace($VsdxPath)) {
        Add-VisioAcceptanceError $errors "VsdxPath is required"
    }
    elseif (-not (Test-Path -LiteralPath $VsdxPath -PathType Leaf)) {
        Add-VisioAcceptanceError $errors "VsdxPath does not exist"
    }
    elseif ($ExpectedPages -lt 1) {
        Add-VisioAcceptanceError $errors "ExpectedPages must be at least 1"
    }
    elseif ($ExpectedShapes -lt 0) {
        Add-VisioAcceptanceError $errors "ExpectedShapes must be at least 0"
    }
    elseif ([System.Threading.Thread]::CurrentThread.ApartmentState -ne [System.Threading.ApartmentState]::STA) {
        Add-VisioAcceptanceError $errors "PowerShell must run in STA mode"
    }

    if ($errors.Count -gt 0) {
        $result.errors = @($errors)
        return [pscustomobject]$result
    }

    try {
        $sourceFullPath = [System.IO.Path]::GetFullPath($VsdxPath)
        Write-GateStage "hash-before"
        $sourceHashBefore = Get-VisioAcceptanceSha256 -Path $sourceFullPath
    }
    catch {
        $compatibilityFailure = $true
        Add-VisioAcceptanceError $errors ("source hash failed: " + $_.Exception.Message)
    }

    if (-not $compatibilityFailure) {
        try {
            $application = & $ApplicationFactory
            if ($null -eq $application) {
                throw "Visio.Application factory returned null"
            }
        }
        catch {
            $activationUnavailable = $true
            Add-VisioAcceptanceError $errors ("Visio COM activation unavailable: " + $_.Exception.Message)
        }
    }

    if (-not $compatibilityFailure -and -not $activationUnavailable) {
        try {
            # Headless automation is required: on this machine (remote
            # session), a visible Visio window makes opening hang.
            $application.Visible = $false
            # visAlertResponseCancel (2) cancels prompts.
            $application.AlertResponse = 2
            # The candidate is opened in place, never a copy. On this Visio
            # 2016 installation, opening a byte-identical copy hangs regardless
            # of directory or open flags, while the original opens immediately.
            # The candidate is never saved: Close() without arguments followed
            # by SHA-256 verification guarantees the source bytes are intact.
            Write-GateStage ("before-open visible=" + $application.Visible + " target=" + $sourceFullPath)
            $document = $application.Documents.Open($sourceFullPath)
            Write-GateStage "opened"
            if ($null -eq $document) {
                throw "Visio returned no document"
            }

            Write-GateStage "counting"
            $pageCount = [int]$document.Pages.Count
            $perPageCounts = [System.Collections.ArrayList]::new()
            for ($pageIndex = 1; $pageIndex -le $pageCount; $pageIndex++) {
                [void]$perPageCounts.Add([int]$document.Pages.Item($pageIndex).Shapes.Count)
            }
            Write-GateStage "counted"
            $shapeCount = ($perPageCounts | Measure-Object -Sum).Sum
            if ($null -eq $shapeCount) {
                $shapeCount = 0
            }
            $result.actual_pages = $pageCount
            $result.actual_shapes = [int]$shapeCount
            $result.per_page_shape_counts = @($perPageCounts)

            if ($pageCount -ne $ExpectedPages) {
                throw "expected $ExpectedPages page(s), found $pageCount"
            }
            if ($shapeCount -ne $ExpectedShapes) {
                throw "expected $ExpectedShapes shape(s), found $shapeCount"
            }
        }
        catch {
            $compatibilityFailure = $true
            Add-VisioAcceptanceError $errors ("Visio open/count check failed: " + $_.Exception.Message)
        }
    }

    if ($null -ne $document) {
        try {
            Write-GateStage "closing"
            $document.Close()
            $result.document_closed = $true
            Write-GateStage "closed"
        }
        catch {
            $compatibilityFailure = $true
            Add-VisioAcceptanceError $errors ("document close failed: " + $_.Exception.Message)
        }
    }

    if ($null -ne $application) {
        try {
            Write-GateStage "quitting"
            $application.Quit()
            $result.application_quit = $true
            Write-GateStage "quit"
        }
        catch {
            $compatibilityFailure = $true
            Add-VisioAcceptanceError $errors ("Visio quit failed: " + $_.Exception.Message)
        }
    }

    try {
        Write-GateStage "hash-after"
        $sourceHashAfter = Get-VisioAcceptanceSha256 -Path $sourceFullPath
        $result.source_unchanged = ($sourceHashBefore -eq $sourceHashAfter)
        if (-not $result.source_unchanged) {
            throw "source file SHA-256 changed during acceptance"
        }
    }
    catch {
        $compatibilityFailure = $true
        $result.source_unchanged = $false
        Add-VisioAcceptanceError $errors ("source hash verification failed: " + $_.Exception.Message)
    }

    if ($activationUnavailable -and -not $compatibilityFailure) {
        $result.status = "environment"
        $result.exit_code = 2
    }
    elseif ($compatibilityFailure) {
        $result.status = "failure"
        $result.exit_code = 1
    }
    else {
        $result.status = "pass"
        $result.exit_code = 0
    }
    Write-GateStage ("result " + $result.exit_code)
    $result.errors = @($errors)
    return [pscustomobject]$result
}

if ($MyInvocation.InvocationName -ne ".") {
    $acceptance = Invoke-VisioAcceptance -VsdxPath $VsdxPath -ExpectedPages $ExpectedPages -ExpectedShapes $ExpectedShapes
    $acceptance | ConvertTo-Json -Compress -Depth 6
    exit [int]$acceptance.exit_code
}

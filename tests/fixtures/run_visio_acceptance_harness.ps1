[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("invalid-input", "missing-com", "success", "open-failure", "count-failure")]
    [string]$Scenario
)

$script:FixtureState = [ordered]@{
    document_closed = $false
    application_quit = $false
}

$skillRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $skillRoot "scripts/run_visio_acceptance.ps1")

$sourcePath = Join-Path ([System.IO.Path]::GetTempPath()) ("vsdx-acceptance-fixture-" + [guid]::NewGuid().ToString("N") + ".vsdx")
[System.IO.File]::WriteAllBytes($sourcePath, [byte[]](1, 2, 3, 4, 5, 6))

function New-FakeApplication {
    param(
        [int]$PageCount = 1,
        [int]$ShapesPerPage = 39,
        [switch]$FailOpen
    )

    $pages = @()
    for ($index = 1; $index -le $PageCount; $index++) {
        $pages += [pscustomobject]@{ Shapes = [pscustomobject]@{ Count = $ShapesPerPage } }
    }
    $pageCollection = [pscustomobject]@{ Count = $pages.Count; Items = $pages }
    $pageCollection | Add-Member -MemberType ScriptMethod -Name Item -Value {
        param([int]$Index)
        return $this.Items[$Index - 1]
    }

    $document = [pscustomobject]@{ Pages = $pageCollection }
    $document | Add-Member -MemberType ScriptMethod -Name Close -Value {
        $script:FixtureState.document_closed = $true
    }

    $documents = [pscustomobject]@{ Document = $document; FailOpen = [bool]$FailOpen; LastOpenPath = $null }
    $documents | Add-Member -MemberType ScriptMethod -Name Open -Value {
        param([string]$Path)
        $this.LastOpenPath = $Path
        if ($this.FailOpen) {
            throw "fake Visio open failure"
        }
        return $this.Document
    }

    $application = [pscustomobject]@{ Documents = $documents; AlertResponse = 0; Visible = $true }
    $application | Add-Member -MemberType ScriptMethod -Name Quit -Value {
        $script:FixtureState.application_quit = $true
    }
    return $application
}

$applicationFactory = $null
$fakeApplication = $null
$pathForInvocation = $sourcePath
$expectedShapes = 39

switch ($Scenario) {
    "invalid-input" {
        $pathForInvocation = Join-Path ([System.IO.Path]::GetTempPath()) "vsdx-acceptance-missing.vsdx"
        $applicationFactory = { throw "factory must not be called" }
    }
    "missing-com" {
        $applicationFactory = { throw "fake COM activation unavailable" }
    }
    "success" {
        $fakeApplication = New-FakeApplication
        $applicationFactory = { return $fakeApplication }
    }
    "open-failure" {
        $fakeApplication = New-FakeApplication -FailOpen
        $applicationFactory = { return $fakeApplication }
    }
    "count-failure" {
        $fakeApplication = New-FakeApplication -ShapesPerPage 38
        $applicationFactory = { return $fakeApplication }
    }
}

try {
    $invokeParameters = @{
        VsdxPath = $pathForInvocation
        ExpectedPages = 1
        ExpectedShapes = $expectedShapes
        ApplicationFactory = $applicationFactory
    }
    $result = Invoke-VisioAcceptance @invokeParameters
    $temporaryDirectory = $result.temp_directory
    $fixtureTempExistsAfter = -not [string]::IsNullOrWhiteSpace($temporaryDirectory) -and (Test-Path -LiteralPath $temporaryDirectory)
    if ($fixtureTempExistsAfter) {
        Remove-Item -LiteralPath $temporaryDirectory -Force -Recurse -ErrorAction SilentlyContinue
    }
    $result | Add-Member -NotePropertyName fixture_temp_exists_after -NotePropertyValue (-not [string]::IsNullOrWhiteSpace($temporaryDirectory) -and (Test-Path -LiteralPath $temporaryDirectory)) -Force
    $result | Add-Member -NotePropertyName document_closed -NotePropertyValue $script:FixtureState.document_closed -Force
    $result | Add-Member -NotePropertyName application_quit -NotePropertyValue $script:FixtureState.application_quit -Force
    $alertResponse = if ($null -ne $fakeApplication) { $fakeApplication.AlertResponse } else { $null }
    $result | Add-Member -NotePropertyName alert_response -NotePropertyValue $alertResponse -Force
    $visibleAfter = if ($null -ne $fakeApplication) { $fakeApplication.Visible } else { $null }
    $result | Add-Member -NotePropertyName visible_after -NotePropertyValue $visibleAfter -Force
    $openPath = if ($null -ne $fakeApplication) { $fakeApplication.Documents.LastOpenPath } else { $null }
    $result | Add-Member -NotePropertyName open_path -NotePropertyValue $openPath -Force
    $result | Add-Member -NotePropertyName source_path_used -NotePropertyValue ([System.IO.Path]::GetFullPath($pathForInvocation)) -Force
    $result | ConvertTo-Json -Compress -Depth 6
    exit [int]$result.exit_code
}
finally {
    Remove-Item -LiteralPath $sourcePath -Force -ErrorAction SilentlyContinue
}

<#
.SYNOPSIS
  Flatten Word's composed VML figures into single PNG files.

.DESCRIPTION
  Two figures in the XMILE specification are not single images. Word stores
  each as a <v:group>: an EMF base drawing plus smaller PNG shapes positioned
  on top of it (the nested module boxes). Neither part alone shows the whole
  figure, and EMF does not render in a browser.

  This script renders the EMF and composites the overlay shapes at their
  recorded coordinates, producing one flat PNG per figure. It uses
  System.Drawing, so it only runs on Windows -- which is why its output is
  committed to the repository as a source asset. The AsciiDoc build then needs
  no EMF support and runs anywhere.

  Re-run only if the figures change in the Word source.

.PARAMETER Docx
  Path to the .docx holding the figures.

.PARAMETER OutDir
  Directory to write the composed PNGs into.
#>
param(
    [Parameter(Mandatory = $true)][string]$Docx,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$Scale = 4
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.IO.Compression.FileSystem

# Output names, in the order the groups appear in the document.
$figureNames = @('figure-submodel-scope-a-d.png', 'figure-submodel-scope-a-x.png')

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("xmile-fig-" + [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

try {
    $zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $Docx))
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -like 'word/media/*' -or
                $entry.FullName -eq 'word/document.xml' -or
                $entry.FullName -eq 'word/_rels/document.xml.rels') {
                $dest = Join-Path $work ($entry.FullName -replace '/', '\')
                New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
            }
        }
    } finally {
        $zip.Dispose()
    }

    $relsXml = Get-Content (Join-Path $work 'word\_rels\document.xml.rels') -Raw
    $rels = @{}
    foreach ($m in [regex]::Matches($relsXml, 'Id="([^"]+)"[^>]*Target="([^"]+)"')) {
        $rels[$m.Groups[1].Value] = $m.Groups[2].Value
    }

    $docXml = Get-Content (Join-Path $work 'word\document.xml') -Raw
    $groupRe = '<v:group\b[^>]*coordsize="(\d+),(\d+)"[^>]*>(.*?)</v:group>'
    $shapeRe = '<v:shape\b[^>]*style="([^"]*)"[^>]*>\s*<v:imagedata r:id="([^"]+)"'

    New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
    $index = 0

    foreach ($g in [regex]::Matches($docXml, $groupRe, 'Singleline')) {
        if ($index -ge $figureNames.Count) { break }

        $coordW = [double]$g.Groups[1].Value
        $coordH = [double]$g.Groups[2].Value
        $shapes = [regex]::Matches($g.Groups[3].Value, $shapeRe, 'Singleline')
        if ($shapes.Count -eq 0) { continue }

        # The first shape spans the group and fixes the output resolution.
        $baseRel = $rels[$shapes[0].Groups[2].Value]
        $basePath = Join-Path $work ('word\' + ($baseRel -replace '/', '\'))
        $baseImg = [System.Drawing.Image]::FromFile($basePath)
        $outW = [int]($baseImg.Width * $Scale)
        $outH = [int]($baseImg.Height * $Scale)

        $bmp = New-Object System.Drawing.Bitmap($outW, $outH)
        $gfx = [System.Drawing.Graphics]::FromImage($bmp)
        $gfx.Clear([System.Drawing.Color]::White)
        $gfx.SmoothingMode = 'AntiAlias'
        $gfx.InterpolationMode = 'HighQualityBicubic'
        $gfx.PixelOffsetMode = 'HighQuality'

        foreach ($s in $shapes) {
            $style = $s.Groups[1].Value
            $rel = $rels[$s.Groups[2].Value]
            $path = Join-Path $work ('word\' + ($rel -replace '/', '\'))

            function Get-StyleValue([string]$text, [string]$key, [double]$fallback) {
                $m = [regex]::Match($text, "(?:^|;)$key`:(-?\d+(?:\.\d+)?)")
                if ($m.Success) { return [double]$m.Groups[1].Value }
                return $fallback
            }

            # VML coordinates are relative to the group's coordsize.
            $left = Get-StyleValue $style 'left' 0
            $top = Get-StyleValue $style 'top' 0
            $w = Get-StyleValue $style 'width' $coordW
            $h = Get-StyleValue $style 'height' $coordH

            $rect = New-Object System.Drawing.RectangleF(
                [float]($left / $coordW * $outW), [float]($top / $coordH * $outH),
                [float]($w / $coordW * $outW), [float]($h / $coordH * $outH))

            $img = [System.Drawing.Image]::FromFile($path)
            try { $gfx.DrawImage($img, $rect) } finally { $img.Dispose() }
        }

        $outPath = Join-Path $OutDir $figureNames[$index]
        $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-Output ("{0}  {1}x{2}  ({3} shapes)" -f $figureNames[$index], $outW, $outH, $shapes.Count)

        $gfx.Dispose(); $bmp.Dispose(); $baseImg.Dispose()
        $index++
    }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}

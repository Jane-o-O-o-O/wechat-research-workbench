$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$bitmaps = [System.Collections.Generic.List[byte[]]]::new()
$sizes = @(16, 24, 32, 48, 64, 128, 256)
foreach ($size in $sizes) {
    $image = [Drawing.Bitmap]::new($size, $size, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [Drawing.Graphics]::FromImage($image)
    $graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.ScaleTransform($size / 64.0, $size / 64.0)
    $shape = [Drawing.Drawing2D.GraphicsPath]::new()
    $shape.AddArc(2, 2, 22, 22, 180, 90)
    $shape.AddArc(40, 2, 22, 22, 270, 90)
    $shape.AddArc(40, 40, 22, 22, 0, 90)
    $shape.AddArc(2, 40, 22, 22, 90, 90)
    $shape.CloseFigure()
    $base = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(46, 36, 39))
    $graphics.FillPath($base, $shape)
    $lead = [Drawing.Pen]::new([Drawing.Color]::FromArgb(255, 155, 135), 4.4)
    $trail = [Drawing.Pen]::new([Drawing.Color]::FromArgb(158, 232, 205), 4.0)
    foreach ($pen in @($lead, $trail)) { $pen.StartCap = $pen.EndCap = [Drawing.Drawing2D.LineCap]::Round; $pen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round }
    $graphics.DrawLines($trail, [Drawing.PointF[]]@([Drawing.PointF]::new(51,22),[Drawing.PointF]::new(53,24),[Drawing.PointF]::new(53,42),[Drawing.PointF]::new(48,46),[Drawing.PointF]::new(41,46),[Drawing.PointF]::new(41,53),[Drawing.PointF]::new(34,46)))
    $graphics.DrawLines($lead, [Drawing.PointF[]]@([Drawing.PointF]::new(14,14),[Drawing.PointF]::new(42,14),[Drawing.PointF]::new(47,19),[Drawing.PointF]::new(47,33),[Drawing.PointF]::new(42,38),[Drawing.PointF]::new(28,38),[Drawing.PointF]::new(18,46),[Drawing.PointF]::new(18,38),[Drawing.PointF]::new(14,38),[Drawing.PointF]::new(10,34),[Drawing.PointF]::new(10,19),[Drawing.PointF]::new(14,14)))
    $graphics.DrawLines($trail, [Drawing.PointF[]]@([Drawing.PointF]::new(19,27),[Drawing.PointF]::new(24,23),[Drawing.PointF]::new(31,28),[Drawing.PointF]::new(37,23)))
    $stream = [IO.MemoryStream]::new()
    $image.Save($stream, [Drawing.Imaging.ImageFormat]::Png)
    $bitmaps.Add($stream.ToArray())
    foreach ($resource in @($stream, $lead, $trail, $base, $shape, $graphics, $image)) { $resource.Dispose() }
}
$file = [IO.File]::Create((Join-Path $PSScriptRoot 'workbench.ico'))
$writer = [IO.BinaryWriter]::new($file)
try {
    $writer.Write([uint16]0); $writer.Write([uint16]1); $writer.Write([uint16]$sizes.Count)
    $offset = 6 + 16 * $sizes.Count
    for ($i = 0; $i -lt $sizes.Count; $i++) {
        $dimension = [byte]($sizes[$i] % 256)
        $writer.Write($dimension); $writer.Write($dimension); $writer.Write([uint16]0)
        $writer.Write([uint16]1); $writer.Write([uint16]32)
        $writer.Write([uint32]$bitmaps[$i].Length); $writer.Write([uint32]$offset)
        $offset += $bitmaps[$i].Length
    }
    foreach ($bitmap in $bitmaps) { $writer.Write([byte[]]$bitmap) }
} finally { $writer.Dispose() }

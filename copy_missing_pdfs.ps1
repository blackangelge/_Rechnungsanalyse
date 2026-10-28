<#
Kopiert fehlende Beleg-PDFs vom Netzlaufwerk in den lokalen Storage.

Vorgehen: Fuer JEDEN Import-Ordner, der im Ziel-Storage existiert (= die migrierten Imports,
z.B. DGH_2026), werden aus dem gleichnamigen Quellordner nur die FEHLENDEN *.pdf kopiert.
Vorhandene Dateien werden NIE ueberschrieben (robocopy /XC /XN /XO). Quellordner, die es im
Ziel nicht gibt (z.B. alte 2025er oder _v2-Ordner), werden ignoriert.

Beispiele (im Projektordner ausfuehren):
  # 1) Trockenlauf - zeigt nur, was kopiert wuerde (robocopy /L):
  powershell -ExecutionPolicy Bypass -File .\copy_missing_pdfs.ps1 -DryRun
  # 2) Wirklich kopieren:
  powershell -ExecutionPolicy Bypass -File .\copy_missing_pdfs.ps1
#>
param(
  [string]$Source = "Z:\storage",
  [string]$Target = "C:\docker\_Rechnungsanalyse_Test\_Rechnungsanalyse\storage",
  [switch]$DryRun
)

if (-not (Test-Path -LiteralPath $Source)) { Write-Error "Quelle nicht gefunden: $Source"; exit 1 }
if (-not (Test-Path -LiteralPath $Target)) { Write-Error "Ziel nicht gefunden: $Target"; exit 1 }

Write-Host ("Quelle: {0}" -f $Source)
Write-Host ("Ziel:   {0}" -f $Target)
if ($DryRun) { Write-Host "(Trockenlauf - robocopy /L, es wird nichts kopiert)" }

# /XO /XN /XC = nur Dateien kopieren, die im Ziel FEHLEN (nie ueberschreiben); /NFL /NDL /NP = knappe Ausgabe
$flags = @("/XO", "/XN", "/XC", "/NFL", "/NDL", "/NP", "/R:1", "/W:2")
if ($DryRun) { $flags += "/L" }

$errorDirs = 0
foreach ($dir in (Get-ChildItem -LiteralPath $Target -Directory | Sort-Object Name)) {
  $src = Join-Path $Source $dir.Name
  if (-not (Test-Path -LiteralPath $src)) { continue }   # kein passender Quellordner -> ignorieren
  Write-Host ("`n### {0}" -f $dir.Name)
  robocopy $src $dir.FullName "*.pdf" @flags
  if ($LASTEXITCODE -ge 8) { Write-Warning ("robocopy-Fehler in {0} (Code {1})" -f $dir.Name, $LASTEXITCODE); $errorDirs++ }
}

Write-Host "`n=== Fertig ==="
Write-Host "Je Import zeigt robocopy oben unter 'Dateien/Files' die Spalte 'Kopiert/Copied' = uebernommene PDFs."
if ($errorDirs -gt 0) { Write-Host ("Ordner mit Fehlern: {0}" -f $errorDirs) }
if ($DryRun) { Write-Host "Das war ein Trockenlauf. Zum echten Kopieren ohne -DryRun erneut ausfuehren." }

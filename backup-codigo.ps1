# Crea un ZIP con el codigo fuente del bot y la app Expo, sin secretos ni dependencias generadas.
# Uso desde la raiz del proyecto:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\backup-codigo.ps1

$ErrorActionPreference = "Stop"
$origen = Split-Path -Parent $MyInvocation.MyCommand.Path
$fecha = Get-Date -Format "yyyy-MM-dd_HHmm"
$zipSalida = Join-Path $origen "codigo_$fecha.zip"
$temp = Join-Path ([System.IO.Path]::GetTempPath()) "spotplus-codigo_$fecha"

# Se copia la estructura completa para no omitir archivos de mobile/ ni assets.
$carpetasExcluir = @("venv", ".venv", ".git", "__pycache__", ".pytest_cache", "node_modules", ".expo", "dist", "web-build", "build", ".gradle", ".cxx")

try {
    New-Item -ItemType Directory -Path $temp -Force | Out-Null

    $pendientes = [System.Collections.Generic.Stack[string]]::new()
    $pendientes.Push($origen)
    $archivos = [System.Collections.Generic.List[System.IO.FileInfo]]::new()

    while ($pendientes.Count -gt 0) {
        $directorio = $pendientes.Pop()
        $elementos = Get-ChildItem -LiteralPath $directorio -Force -ErrorAction SilentlyContinue

        foreach ($elemento in $elementos) {
            $rutaRelativa = $elemento.FullName.Substring($origen.Length).TrimStart('\\')
            $partes = $rutaRelativa -split '[\\/]'

            if ($elemento.PSIsContainer) {
                if ($elemento.Name -notin $carpetasExcluir) {
                    $pendientes.Push($elemento.FullName)
                }
                continue
            }

            if ($elemento.Name -notlike ".env*" -and
                $elemento.Name -notlike "*.zip" -and
                $elemento.Name -notlike "*.pyc" -and
                $elemento.Name -notlike "*.tsbuildinfo" -and
                -not ($partes | Where-Object { $_ -in $carpetasExcluir })) {
                $archivos.Add($elemento)
            }
        }
    }

    $archivos = $archivos | Sort-Object FullName

    if (-not $archivos) {
        throw "No se encontraron archivos de codigo para empaquetar."
    }

    foreach ($archivo in $archivos) {
        $rutaRelativa = $archivo.FullName.Substring($origen.Length).TrimStart('\\')
        $destino = Join-Path $temp $rutaRelativa
        New-Item -ItemType Directory -Path (Split-Path -Parent $destino) -Force | Out-Null
        Copy-Item -LiteralPath $archivo.FullName -Destination $destino
        Write-Host "Agregado: $rutaRelativa"
    }

    Compress-Archive -Path (Join-Path $temp "*") -DestinationPath $zipSalida -Force
    Write-Host ""
    Write-Host "Listo. ZIP creado en:" -ForegroundColor Green
    Write-Host $zipSalida
    Write-Host "No se incluyeron .env, venv ni archivos de compilacion." -ForegroundColor Cyan
}
finally {
    if (Test-Path $temp) {
        Remove-Item $temp -Recurse -Force
    }
}
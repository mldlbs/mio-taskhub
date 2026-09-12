$env:MIO_TASKHUB_PORT = "48622"
$proc = Start-Process -FilePath "E:\work\code\agent-dev\mio-taskhub\dist\mio-taskhub\mio-taskhub.exe" -ArgumentList "hub" -PassThru
Start-Sleep -Seconds 5
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:48622/metrics" -TimeoutSec 10
    if ($resp.Content -match "taskhub_thread_alive") { Write-Host "Metrics OK" }
    if ($resp.Content -match "opentelemetry") { Write-Host "OTel metrics OK" }
} catch {
    Write-Host "Error: $($_.Exception.Message)"
}
$proc.Kill()
Write-Host "Done"
# Check sources in entries API
$uri = "http://170.239.86.106/api/entries/all"
$body = @{limit=500} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri $uri -Method POST -Body $body -ContentType "application/json"
    
    Write-Host "`n=== ENTRIES API SOURCE ANALYSIS ===" -ForegroundColor Cyan
    Write-Host "Total entries: $($response.total)" -ForegroundColor Yellow
    
    $sourceGroups = $response.entries | Group-Object source | Sort-Object Count -Descending
    
    Write-Host "`nUnique sources: $($sourceGroups.Count)" -ForegroundColor Green
    Write-Host "`n--- Source Distribution ---" -ForegroundColor Magenta
    
    $sourceGroups | ForEach-Object {
        $pct = [math]::Round(($_.Count / $response.total) * 100, 1)
        Write-Host "  $($_.Name): $($_.Count) entries ($pct%)"
    }
    
    Write-Host "`n--- All unique sources ---" -ForegroundColor Cyan
    $sourceGroups | ForEach-Object { Write-Host "  - $($_.Name)" }
    
} catch {
    Write-Host "Error: $_" -ForegroundColor Red
}

gitleaks detect --no-git --redact
if ($LASTEXITCODE -ne 0) { Write-Error "Secrets detected!"; exit 1 }
Write-Host "No secrets detected."

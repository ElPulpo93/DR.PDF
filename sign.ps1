# Signs a file (the app or the installer) with your organization's code-signing certificate.
# Used by build.bat when these environment variables are set:
#   DRPDF_SIGN_PFX        path to the code-signing certificate (.pfx)
#   DRPDF_SIGN_PASSWORD   its password
#   DRPDF_SIGN_TIMESTAMP  optional timestamp server (default http://timestamp.digicert.com;
#                         "none" to skip - signatures then expire with the certificate)
param([Parameter(Mandatory = $true)][string]$File)
$ErrorActionPreference = "Stop"

$pfx = $env:DRPDF_SIGN_PFX
if (-not $pfx) { Write-Host "No certificate set (DRPDF_SIGN_PFX) - skipping signing of $File"; exit 0 }
if (-not (Test-Path $pfx)) { Write-Error "Certificate file not found: $pfx"; exit 1 }

$flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($pfx, $env:DRPDF_SIGN_PASSWORD, $flags)
if (-not $cert.HasPrivateKey) { Write-Error "The certificate has no private key."; exit 1 }

$ts = $env:DRPDF_SIGN_TIMESTAMP
if (-not $ts) { $ts = "http://timestamp.digicert.com" }
$args = @{ FilePath = $File; Certificate = $cert; HashAlgorithm = "SHA256" }
if ($ts -ne "none") { $args.TimestampServer = $ts }

$result = Set-AuthenticodeSignature @args
# "UnknownError" = signed, but the certificate isn't trusted on this PC (e.g. a test certificate)
if ($result.Status -ne "Valid" -and $result.Status -ne "UnknownError") {
    Write-Error "Signing $File failed: $($result.Status) $($result.StatusMessage)"; exit 1
}
Write-Host "Signed $File ($($result.Status)) by $($cert.Subject)"

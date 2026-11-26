@echo off
rem Opens the book catalog (index.html in this folder) in the browser.
rem A tiny web server runs on this computer only (localhost), so books open with one click.
rem Close this window to stop it. Starting it again while it runs just opens the browser.
rem Signed-in devices save their reading places in the .sync folder (library-sync.ps1, next to this file).
rem Uses PowerShell, which is part of Windows: nothing to install.
set "LIB_SELF=%~f0"
set "LIB_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText($env:LIB_SELF); iex $t.Substring($t.IndexOf('#POWER' + 'SHELL'))"
if errorlevel 1 pause
exit /b

#POWERSHELL
$root = [IO.Path]::GetFullPath($env:LIB_ROOT)
# Accounts and reading-place sync, stored in .sync\ (loaded from text, like this script)
$syncScript = [IO.Path]::Combine($root, 'library-sync.ps1')
if (-not [IO.File]::Exists($syncScript)) { Write-Host "Could not start: library-sync.ps1 is missing in $root"; exit 1 }
. ([ScriptBlock]::Create([IO.File]::ReadAllText($syncScript)))
$store = New-SyncStore $root
$types = @{ '.html' = 'text/html; charset=utf-8'; '.js' = 'text/javascript; charset=utf-8';
            '.css' = 'text/css'; '.json' = 'application/json'; '.jpg' = 'image/jpeg';
            '.jpeg' = 'image/jpeg'; '.png' = 'image/png'; '.gif' = 'image/gif';
            '.epub' = 'application/epub+zip'; '.woff2' = 'font/woff2'; '.svg' = 'image/svg+xml'; '.webmanifest' = 'application/manifest+json' }

function Send($res, [byte[]]$bytes, $type) {
  $res.ContentType = $type
  $res.ContentLength64 = $bytes.Length
  $res.OutputStream.Write($bytes, 0, $bytes.Length)
}

# A /__sync/ request: body by Content-Length (413 over the limit), answer from the store
function ServeSync($ctx) {
  $req = $ctx.Request; $res = $ctx.Response
  $length = $req.ContentLength64   # -1 when not given: no body
  if ($length -gt $SyncBodyLimit) {
    $answer = New-SyncError 413 'too large'
    $res.KeepAlive = $false        # the unread body would corrupt a kept-alive connection
  } else {
    $body = New-Object byte[] ([Math]::Max([long]0, $length))
    $off = 0
    while ($off -lt $body.Length) {
      $n = $req.InputStream.Read($body, $off, $body.Length - $off)
      if ($n -le 0) { break }
      $off += $n
    }
    if ($off -lt $body.Length) { $short = New-Object byte[] $off; [Array]::Copy($body, $short, $off); $body = $short }
    $headers = @{}
    foreach ($k in $req.Headers.AllKeys) { $headers[$k] = $req.Headers[$k] }
    try { $answer = Invoke-SyncRequest $store $req.HttpMethod $req.RawUrl $headers $body }
    catch { $answer = New-SyncError 500 'server error' }
  }
  $res.StatusCode = $answer.Status
  if ($answer.Status -ne 204) { $res.ContentType = $answer.Type }
  $res.AddHeader('Cache-Control', 'no-store')
  $res.ContentLength64 = $answer.Body.Length
  if ($answer.Body.Length -gt 0) { $res.OutputStream.Write($answer.Body, 0, $answer.Body.Length) }
  $path = $req.RawUrl.Split('?')[0]
  if ($path -ceq '/__sync/register' -or $path -ceq '/__sync/login') {   # never the password
    if ($path -ceq '/__sync/register') { $what = 'registration' } else { $what = 'sign-in' }
    if ($answer.Status -eq 200) { $how = 'ok' } else { $how = 'refused' }
    Write-Host ("  " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "  $what $how ($($answer.Status))")
  }
}

function Serve($ctx) {
  $res = $ctx.Response
  try {
    $method = $ctx.Request.HttpMethod
    $raw = $ctx.Request.RawUrl
    $isSync = $raw.StartsWith('/__sync/', [StringComparison]::Ordinal)
    if (($method -ceq 'GET' -or $method -ceq 'POST') -and $isSync) { ServeSync $ctx; return }
    if ($method -ceq 'PUT' -and $raw.Split('?')[0] -ceq '/__sync/data') { ServeSync $ctx; return }
    if ($method -cne 'GET' -and $method -cne 'HEAD') { $res.StatusCode = 405; return }
    if (Test-SyncPrivate $raw) { $res.StatusCode = 404; return }   # .sync\ is never served
    # RawUrl, not Url: Url is built from an already decoded path, so a '#' in a file name
    # (sent as %23) would cut the path there and the book would not be found
    $rel = [Uri]::UnescapeDataString($raw.Split('?')[0]).TrimStart('/')
    if ($rel -eq '__library_root') {
      # lets a launcher check that this port serves this very library
      Send $res ([Text.Encoding]::UTF8.GetBytes($root)) 'text/plain; charset=utf-8'
      return
    }
    if ($rel -eq '') { $rel = 'index.html' }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $rel))
    if ($path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and [IO.File]::Exists($path) -and
        -not (Test-SyncPrivateFile $root $path)) {
      $type = $types[[IO.Path]::GetExtension($path).ToLower()]
      if (-not $type) { $type = 'application/octet-stream' }
      Send $res ([IO.File]::ReadAllBytes($path)) $type
    } else {
      $res.StatusCode = 404
    }
  } catch {
    try { $res.StatusCode = 500 } catch {}
  } finally {
    try { $res.Close() } catch {}
  }
}

function Request($port) {
  $r = [Net.HttpWebRequest]::Create("http://localhost:$port/__library_root")
  $r.Timeout = 3000
  $r.Proxy = $null
  return $r
}

# Library already running on this port? Returns $true if it is this one.
function IsThisLibrary($port) {
  try {
    $resp = (Request $port).GetResponse()
    $text = (New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)).ReadToEnd()
    $resp.Close()
    return $text -eq $root
  } catch { return $false }
}

# Ports are tried in order and the first one is normally used: reading places are
# stored per address, so the same port keeps them. Ports used by other programs are skipped.
$listener = $null
foreach ($p in 48765..48784) {
  if (IsThisLibrary $p) { Start-Process "http://localhost:$p/index.html"; exit 0 }
  $l = New-Object Net.HttpListener
  $l.Prefixes.Add("http://localhost:$p/")
  try { $l.Start() } catch { continue }
  # Make sure requests to this port really reach us (another program may own it)
  $req = Request $p
  $pending = $req.BeginGetResponse($null, $null)
  $ar = $l.BeginGetContext($null, $null)
  if ($ar.AsyncWaitHandle.WaitOne(4000)) {
    Serve ($l.EndGetContext($ar))
    try { $req.EndGetResponse($pending).Close() } catch {}
    $listener = $l; $port = $p
    break
  }
  $req.Abort()
  $l.Close()
}
if (-not $listener) { Write-Host "Could not start: ports 48765-48784 are busy."; exit 1 }

$url = "http://localhost:$port/index.html"
$host.UI.RawUI.WindowTitle = "Library - close this window to stop"
Write-Host "Library: $url"
Write-Host "Folder:  $root"
try { $code = Get-SyncCode $store } catch { $code = "(can't read .sync\code.txt: $($_.Exception.Message))" }
Write-Host "Library code: $code"
Write-Host "Close this window to stop."
Start-Process $url
while ($listener.IsListening) {
  Serve ($listener.GetContext())
}

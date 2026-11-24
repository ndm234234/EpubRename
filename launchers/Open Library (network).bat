@echo off
rem Opens the book catalog (index.html in this folder) for this computer AND your home network,
rem so you can read on a phone or tablet connected to the same Wi-Fi.
rem Only devices with home-network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) get an answer;
rem files are only read, nothing can be changed. Anyone on your Wi-Fi who knows the address can open it.
rem The first time, Windows asks whether PowerShell may use the network: allow it for Private networks.
rem Close this window to stop. Uses PowerShell, which is part of Windows: nothing to install.
set "LIB_SELF=%~f0"
set "LIB_ROOT=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText($env:LIB_SELF); iex $t.Substring($t.IndexOf('#POWER' + 'SHELL'))"
if errorlevel 1 pause
exit /b

#POWERSHELL
$root = [IO.Path]::GetFullPath($env:LIB_ROOT)
$types = @{ '.html' = 'text/html; charset=utf-8'; '.js' = 'text/javascript; charset=utf-8';
            '.css' = 'text/css'; '.json' = 'application/json'; '.jpg' = 'image/jpeg';
            '.jpeg' = 'image/jpeg'; '.png' = 'image/png'; '.gif' = 'image/gif';
            '.epub' = 'application/epub+zip' }

# This computer and home-network addresses only (no answer to anything from the internet)
function IsHomeAddress([Net.IPAddress]$ip) {
  if ($ip.IsIPv4MappedToIPv6) { $ip = $ip.MapToIPv4() }
  if ([Net.IPAddress]::IsLoopback($ip)) { return $true }
  $b = $ip.GetAddressBytes()
  if ($b.Length -eq 4) {
    return ($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
           ($b[0] -eq 192 -and $b[1] -eq 168) -or ($b[0] -eq 169 -and $b[1] -eq 254)
  }
  return $ip.IsIPv6LinkLocal -or (($b[0] -band 0xfe) -eq 0xfc)
}

# ---- who is using the library: shown in this window
# The catalog and the reader send a small "still here" every minute and "bye" when the page
# closes; a device that sends nothing for $silent seconds is shown as gone.
$silent = 180; $byeGrace = 10   # going from the catalog to a book closes one page and opens another
$devices = @{}; $refused = @{}

function Say($line) { Write-Host ("  " + (Get-Date -Format 'HH:mm:ss') + "  " + $line) }

function DeviceKind($ua) {
  if (-not $ua) { $ua = '' }
  $system = if ($ua -match 'iPhone') { 'iPhone' } elseif ($ua -match 'iPad') { 'iPad' } elseif ($ua -match 'Android') { 'Android' } `
            elseif ($ua -match 'Windows') { 'Windows' } elseif ($ua -match 'Macintosh') { 'Mac' } elseif ($ua -match 'Linux') { 'Linux' } else { '' }
  $browser = if ($ua -match 'Edg') { 'Edge' } elseif ($ua -match 'YaBrowser') { 'Yandex' } elseif ($ua -match 'OPR/') { 'Opera' } `
             elseif ($ua -match 'Firefox|FxiOS') { 'Firefox' } elseif ($ua -match 'Chrome|CriOS') { 'Chrome' } elseif ($ua -match 'Safari') { 'Safari' } else { '' }
  $parts = @($system, $browser) | Where-Object { $_ }
  if ($parts) { return ($parts -join ' · ') } else { return 'unknown browser' }
}

# The device's name in the home network, if the router knows it (waits at most 0.7 s)
function HostName($ip) {
  try {
    $ar = [Net.Dns]::BeginGetHostEntry($ip, $null, $null)
    if ($ar.AsyncWaitHandle.WaitOne(700)) { $n = [Net.Dns]::EndGetHostEntry($ar).HostName; if ($n -and $n -ne $ip) { return $n } }
  } catch {}
  return ''
}

function Minutes($sec) { if ($sec -ge 90) { "$([math]::Round($sec / 60)) min" } else { "$([math]::Round($sec)) s" } }

function Seen($ip, $ua, $rel) {
  $kind = DeviceKind $ua; $key = "$ip|$kind"; $now = [DateTime]::UtcNow
  $d = $devices[$key]
  if (-not $d) {
    $name = if ($ip -eq '127.0.0.1' -or $ip -eq '::1') { 'this computer' } else { HostName $ip }
    $label = if ($name) { "$ip ($name) · $kind" } else { "$ip · $kind" }
    $d = @{ first = $now; label = $label; book = ''; bye = $null }
    $devices[$key] = $d
    Say "+ $label — connected    [devices: $($devices.Count)]"
  }
  $d.last = $now; $d.bye = $null
  if ($rel.ToLower().EndsWith('.epub')) {
    $d.book = [IO.Path]::GetFileNameWithoutExtension($rel)
    Say "> $($d.label) — «$($d.book)»"
  }
}

function Bye($ip, $ua) {
  $d = $devices["$ip|$(DeviceKind $ua)"]
  if ($d) { $d.bye = [DateTime]::UtcNow }
}

function WatchDevices {
  $now = [DateTime]::UtcNow
  foreach ($key in @($devices.Keys)) {
    $d = $devices[$key]
    if ($d.bye -and ($now - $d.bye).TotalSeconds -gt $byeGrace -and $d.last -le $d.bye) { $why = 'closed the page' }
    elseif (($now - $d.last).TotalSeconds -gt $silent) { $why = "no signal for $(Minutes $silent) (screen off, app in the background or out of Wi-Fi)" }
    else { continue }
    $devices.Remove($key)
    $book = if ($d.book) { ", was reading «$($d.book)»" } else { '' }
    Say "- $($d.label) — $why; was here $(Minutes (($d.last - $d.first).TotalSeconds))$book    [devices: $($devices.Count)]"
  }
}

function PlainIp($addr) {
  if ($addr.IsIPv4MappedToIPv6) { $addr = $addr.MapToIPv4() }
  return $addr.ToString()
}

function Head($stream, $status, $type, [long]$length) {
  $h = "HTTP/1.1 $status`r`nContent-Type: $type`r`nContent-Length: $length`r`n" +
       "Cache-Control: no-cache`r`nConnection: close`r`n`r`n"
  $bytes = [Text.Encoding]::ASCII.GetBytes($h)
  $stream.Write($bytes, 0, $bytes.Length)
}

function Plain($stream, $status, $text, $headOnly) {
  $bytes = [Text.Encoding]::UTF8.GetBytes($text)
  Head $stream $status 'text/plain; charset=utf-8' $bytes.Length
  if (-not $headOnly) { $stream.Write($bytes, 0, $bytes.Length) }
}

# One request per connection: read it, answer, close
function Serve($client) {
  try {
    $client.ReceiveTimeout = 5000
    $client.SendTimeout = 120000
    $stream = $client.GetStream()
    $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::ASCII)
    $line = $reader.ReadLine()
    $ua = ''
    while ($true) {
      $h = $reader.ReadLine(); if ($h -eq $null -or $h -eq '') { break }
      if ($h -match '^User-Agent:\s*(.*)$') { $ua = $Matches[1] }
    }
    if (-not $line) { return }
    $parts = $line.Split(' ')
    $method = $parts[0]; $headOnly = $method -eq 'HEAD'
    $addr = $client.Client.RemoteEndPoint.Address
    if (-not (IsHomeAddress $addr)) {
      $ip = PlainIp $addr
      if (-not $refused[$ip]) { $refused[$ip] = $true; Say "! refused $ip — not a home-network address" }
      Plain $stream '403 Forbidden' 'Forbidden' $headOnly; return
    }
    $ip = PlainIp $addr
    if ($parts.Length -ge 2 -and $method -eq 'POST' -and $parts[1].Split('?')[0] -eq '/__bye') {   # the page is closing
      Bye $ip $ua; Head $stream '204 No Content' 'text/plain' 0; return
    }
    if ($parts.Length -lt 2 -or ($method -ne 'GET' -and -not $headOnly)) { Plain $stream '405 Method Not Allowed' 'Method not allowed' $headOnly; return }
    # Raw path, decoded here: a '#' in a file name arrives as %23 and must stay part of the name
    $rel = [Uri]::UnescapeDataString($parts[1].Split('?')[0]).TrimStart('/')
    if ($rel -eq '__library_root') { Plain $stream '200 OK' $root $headOnly; return }   # lets a launcher recognise this library
    Seen $ip $ua $rel
    if ($rel -eq '__alive') { Head $stream '204 No Content' 'text/plain' 0; return }   # "still here" from the catalog / reader
    if ($rel -eq '') { $rel = 'index.html' }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $rel))
    if (-not ($path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and [IO.File]::Exists($path))) {
      Plain $stream '404 Not Found' 'Not found' $headOnly; return
    }
    $type = $types[[IO.Path]::GetExtension($path).ToLower()]
    if (-not $type) { $type = 'application/octet-stream' }
    $file = [IO.File]::OpenRead($path)
    try {
      Head $stream '200 OK' $type $file.Length
      if (-not $headOnly) { $file.CopyTo($stream) }
    } finally { $file.Close() }
  } catch {
  } finally {
    try { $client.Close() } catch {}
  }
}

function IsThisLibrary($port) {
  try {
    $r = [Net.HttpWebRequest]::Create("http://127.0.0.1:$port/__library_root")
    $r.Timeout = 3000; $r.Proxy = $null
    $resp = $r.GetResponse()
    $text = (New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)).ReadToEnd()
    $resp.Close()
    return $text -eq $root
  } catch { return $false }
}

# Addresses of this computer in the home network: what to type on the phone
function HomeAddresses {
  [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() |
    Where-Object { $_.OperationalStatus -eq 'Up' -and $_.NetworkInterfaceType -ne 'Loopback' } |
    ForEach-Object { $_.GetIPProperties().UnicastAddresses } |
    Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and (IsHomeAddress $_.Address) -and
                   -not $_.Address.ToString().StartsWith('169.254.') } |
    ForEach-Object { $_.Address.ToString() } | Select-Object -Unique
}

# Own port range, so this and the computer-only launcher can run at the same time
$listener = $null
foreach ($p in 48865..48884) {
  if (IsThisLibrary $p) { Start-Process "http://localhost:$p/index.html"; $port = $p; $already = $true; break }
  $l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::IPv6Any, $p)
  try {
    $l.Server.DualMode = $true          # IPv4 and IPv6 on one socket
    $l.ExclusiveAddressUse = $true      # never share the port with another program
    $l.Start()
  } catch { continue }
  $listener = $l; $port = $p
  break
}
$addresses = @(HomeAddresses)
if ($already) {
  Write-Host "The network library is already running (in another window)."
  foreach ($a in $addresses) { Write-Host "  On the phone open:  http://${a}:$port/" }
  Start-Sleep -Seconds 8
  exit 0
}
if (-not $listener) { Write-Host "Could not start: ports 48865-48884 are busy."; exit 1 }

$host.UI.RawUI.WindowTitle = "Library on the network - close this window to stop"
Write-Host ""
Write-Host "  Library is open on your home network." -ForegroundColor Green
Write-Host ""
if ($addresses.Count) {
  Write-Host "  On a phone or tablet in the same Wi-Fi open:"
  foreach ($a in $addresses) { Write-Host "      http://${a}:$port/" -ForegroundColor Cyan }
  if ($addresses.Count -gt 1) { Write-Host "  (several addresses: this computer has several networks; try the 192.168... one first)" }
} else {
  Write-Host "  This computer doesn't seem to be in a home network (no 192.168.x.x / 10.x.x.x address)." -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  On this computer:  http://localhost:$port/"
Write-Host "  Folder:            $root"
Write-Host ""
Write-Host "  The phone can't connect? Allow PowerShell in the Windows Firewall question" -ForegroundColor DarkGray
Write-Host "  (Private networks), and check that this Wi-Fi is set as a Private network." -ForegroundColor DarkGray
Write-Host "  Anyone on this Wi-Fi who knows the address can open the library (read only)." -ForegroundColor DarkGray
Write-Host "  Close this window to stop."
Write-Host ""
Write-Host "  Devices using the library:"
Start-Process "http://localhost:$port/index.html"
# Browsers open spare connections and may send nothing on them for a while: take every
# connection at once, answer those whose request has arrived, drop silent ones after 10 s
$waiting = New-Object Collections.ArrayList
$lastWatch = [DateTime]::UtcNow
while ($true) {
  if (([DateTime]::UtcNow - $lastWatch).TotalSeconds -ge 2) { WatchDevices; $lastWatch = [DateTime]::UtcNow }
  while ($listener.Pending()) { [void]$waiting.Add(@{ client = $listener.AcceptTcpClient(); since = [DateTime]::UtcNow }) }
  $busy = $false
  foreach ($w in @($waiting)) {
    if ($w.client.Available -gt 0) {
      $waiting.Remove($w); $busy = $true
      Serve $w.client
    } elseif (([DateTime]::UtcNow - $w.since).TotalSeconds -gt 10) {
      $waiting.Remove($w)
      try { $w.client.Close() } catch {}
    }
  }
  if (-not $busy) { Start-Sleep -Milliseconds 15 }
}

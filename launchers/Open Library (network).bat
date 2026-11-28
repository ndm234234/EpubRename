@echo off
rem Opens the book catalog (index.html in this folder) for this computer AND your home network,
rem so you can read on a phone or tablet connected to the same Wi-Fi.
rem Only devices with home-network addresses (192.168.x.x, 10.x.x.x, 172.16-31.x.x) get an answer;
rem files are only read; signed-in devices save their reading places in the .sync folder (library-sync.ps1,
rem next to this file). Anyone on your Wi-Fi who knows the address can open it.
rem The first time, Windows asks whether PowerShell may use the network: allow it for Private networks.
rem Close this window to stop. Uses PowerShell, which is part of Windows: nothing to install.
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

function Say($line) { Write-Host ("  " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "  " + $line) }

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

# The device a request came from; a new one is printed
function Device($ip, $ua) {
  $kind = DeviceKind $ua; $key = "$ip|$kind"; $now = [DateTime]::UtcNow
  $d = $devices[$key]
  if (-not $d) {
    $name = if ($ip -eq '127.0.0.1' -or $ip -eq '::1') { 'this computer' } else { HostName $ip }
    $base = if ($name) { "$ip ($name) · $kind" } else { "$ip · $kind" }
    $d = @{ first = $now; label = $base; base = $base; login = $null; book = ''; bye = $null }
    $devices[$key] = $d
    Say "+ $base — connected    [devices: $($devices.Count)]"
  }
  $d.last = $now; $d.bye = $null
  return $d
}

# The account a device is signed in to ($null: signed out); shown once when it changes
function Signed($ip, $ua, $login, $announce) {
  $d = Device $ip $ua
  if ($d.login -ceq $login) { return }
  $was = $d.label; $d.login = $login
  if ($login) { $d.label = "$($d.base) · $login" } else { $d.label = $d.base }
  if (-not $login) { Say "* $was — signed out" }
  elseif ($announce) { Say "* $($d.base) — $login" }
}

function Seen($ip, $ua, $rel) {
  $d = Device $ip $ua
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

# ---- the sign-in gate: from anywhere but this computer, nothing without a sign-in but the
# sign-in page (the twin of library_sync.gate; the router forwards the internet from a home address)
$signinPage = [IO.Path]::Combine($root, 'signin.html')
$openPosts = @('/__sync/login', '/__sync/register', '/__sync/session', '/__sync/logout')
$shut = @{}

# This computer asking for itself: a loopback client AND a loopback host name (a tunnel or proxy on
# this computer comes from 127.0.0.1 too, but with its own public host name)
function IsLocal($addr, $headers) {
  if ($addr.IsIPv4MappedToIPv6) { $addr = $addr.MapToIPv4() }
  if (-not [Net.IPAddress]::IsLoopback($addr)) { return $false }
  $h = ([string]$headers['Host']).Trim().ToLowerInvariant()
  if ($h.StartsWith('[')) { $h = $h.Substring(0, $h.IndexOf(']') + 1) }
  elseif ($h.Split(':').Count -eq 2) { $h = $h.Split(':')[0] }
  return (@('localhost', '127.0.0.1', '[::1]') -ccontains $h)
}

# $true: the request may go on. $false: answered 401 already (a page asked for gets signin.html,
# anything else an empty body); logged once per device until it is let in.
function Gate($rq, $method, $target, $addr, $ip, $ua) {
  $headers = $rq.Headers
  $p = $target.Split('?')[0]
  $key = "$ip|$(DeviceKind $ua)"
  $ok = IsLocal $addr $headers
  if (-not $ok -and $p -cne '/__library_root') {   # only launchers on this computer ask that one
    if (($method -ceq 'GET' -or $method -ceq 'HEAD') -and [Uri]::UnescapeDataString($p) -ceq '/signin.html') { $ok = $true }
    elseif ($method -ceq 'POST' -and $openPosts -ccontains $p) { $ok = $true }
    else {
      try { if (Test-SyncSignedIn $store $headers) { $ok = $true } } catch {}
    }
  }
  if ($ok) { $shut.Remove($key); return $true }
  if (-not $shut[$key]) { $shut[$key] = $true; Say "! $ip · $(DeviceKind $ua) — not signed in" }
  if ($method -ceq 'POST' -or $method -ceq 'PUT') { SkipBody $rq }
  $accept = [string]$headers['Accept']
  $asksPage = ([string]$headers['Sec-Fetch-Mode'] -ceq 'navigate') -or $accept.Contains('text/html') -or
              (($method -ceq 'GET' -or $method -ceq 'HEAD') -and ($p.EndsWith('/') -or $p.EndsWith('.html')))
  $bytes = New-Object byte[] 0
  $type = 'text/plain; charset=utf-8'
  if ($asksPage) {
    $type = 'text/html; charset=utf-8'
    try { $bytes = [IO.File]::ReadAllBytes($signinPage) }
    catch { $bytes = [Text.Encoding]::UTF8.GetBytes('<!doctype html><title>Library</title><p id="signin">Sign in to open the library.</p>') }
  }
  $h = "HTTP/1.1 401 Unauthorized`r`nContent-Type: $type`r`nContent-Length: $($bytes.Length)`r`n" +
       "Cache-Control: no-store`r`nConnection: close`r`n`r`n"
  $hb = [Text.Encoding]::ASCII.GetBytes($h)
  $rq.Stream.Write($hb, 0, $hb.Length)
  if ($bytes.Length -gt 0 -and $method -cne 'HEAD') { $rq.Stream.Write($bytes, 0, $bytes.Length) }
  return $false
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

# Reading a request: one deadline per request (a client sending a byte now and then can't hold
# this one-at-a-time server), the head read in chunks, the bytes after it kept as the body's start.
$requestSeconds = 25

# Up to $count bytes into $buf at $off; 0 at the end of the stream; throws past the deadline
function ReadSome($rq, [byte[]]$buf, [int]$off, [int]$count) {
  $left = ($rq.Deadline - [DateTime]::UtcNow).TotalMilliseconds
  if ($left -le 0) { throw 'request too slow' }
  $rq.Client.ReceiveTimeout = [int][Math]::Max(1, [Math]::Min(5000, $left))
  return $rq.Stream.Read($buf, $off, $count)
}

# The request line and headers up to the empty line (CRLFCRLF or LFLF, at most 64 KB) as text;
# the bytes read after it go to $rq.Rest. $null if the connection ends first or the head is too long.
function ReadHead($rq) {
  $buf = New-Object byte[] 65536
  $len = 0
  while ($true) {
    if ($len -ge $buf.Length) { return $null }
    $n = ReadSome $rq $buf $len ($buf.Length - $len)
    if ($n -le 0) { return $null }
    $i = [Math]::Max(0, $len - 2)   # an end of head may straddle the previous chunk
    $len += $n
    $end = -1
    while ($end -lt 0) {
      $i = [Array]::IndexOf($buf, [byte]10, $i, $len - $i)
      if ($i -lt 0) { break }
      if (($i -ge 1 -and $buf[$i - 1] -eq 10) -or ($i -ge 2 -and $buf[$i - 1] -eq 13 -and $buf[$i - 2] -eq 10)) { $end = $i + 1 }
      $i++
    }
    if ($end -ge 0) {
      $rest = New-Object byte[] ($len - $end)
      [Array]::Copy($buf, $end, $rest, 0, $rest.Length)
      $rq.Rest = $rest
      return [Text.Encoding]::GetEncoding(28591).GetString($buf, 0, $end)
    }
  }
}

# Content-Length as a number: 0 if missing, -1 if not a number, more than any limit if huge
function BodyLength($headers) {
  $cl = ''
  foreach ($k in @($headers.Keys)) { if ($k -eq 'Content-Length') { $cl = ([string]$headers[$k]).Trim() } }
  if ($cl -eq '') { return [long]0 }
  if ($cl -cmatch '^\+?0*([0-9]{1,15})\z') { return [long]$Matches[1] }
  if ($cl -cmatch '^\+?[0-9]+\z') { return [long]$SyncBodyLimit + 1 }   # more digits than any limit
  return [long]-1
}

# The body: the bytes already read after the head, then the rest from the stream (shorter if the
# connection ends early)
function ReadBody($rq, [long]$length) {
  $body = New-Object byte[] ([int]$length)
  $off = [int][Math]::Min($length, $rq.Rest.Length)
  [Array]::Copy($rq.Rest, 0, $body, 0, $off)
  while ($off -lt $body.Length) {
    $n = ReadSome $rq $body $off ($body.Length - $off)
    if ($n -le 0) { break }
    $off += $n
  }
  if ($off -lt $body.Length) { $short = New-Object byte[] $off; [Array]::Copy($body, $short, $off); $body = $short }
  return ,$body
}

# Before an answer that ignores the body: read it away (closing with unread bytes makes Windows
# reset the connection, and the client may never see the answer); a body over the limit is left
# to Linger after the answer
function SkipBody($rq) {
  $length = BodyLength $rq.Headers
  if ($length -gt $SyncBodyLimit) { $rq.Linger = $true }
  elseif ($length -gt 0) { [void](ReadBody $rq $length) }
}

# After answering without reading a big body: say we are done sending, then read and drop what
# still comes for a moment (bounded by the deadline), so the answer arrives before the close
function Linger($rq) {
  try {
    $rq.Client.Client.Shutdown([Net.Sockets.SocketShutdown]::Send)
    $until = [DateTime]::UtcNow.AddSeconds(2)
    if ($until -lt $rq.Deadline) { $rq.Deadline = $until }
    $scratch = New-Object byte[] 65536
    while ((ReadSome $rq $scratch 0 $scratch.Length) -gt 0) { }
  } catch {}
}

$reasons = @{ 200 = 'OK'; 204 = 'No Content'; 400 = 'Bad Request'; 401 = 'Unauthorized'; 403 = 'Forbidden';
              404 = 'Not Found'; 405 = 'Method Not Allowed'; 409 = 'Conflict'; 413 = 'Payload Too Large';
              429 = 'Too Many Requests'; 500 = 'Internal Server Error' }

# A /__sync/ request: body by Content-Length (413 over the limit: left unread, then Linger),
# answer from the store
function ServeSync($rq, $method, $target) {
  $stream = $rq.Stream
  $length = BodyLength $rq.Headers
  if ($length -lt 0) { $answer = New-SyncError 400 'bad request'; $rq.Linger = $true }
  elseif ($length -gt $SyncBodyLimit) { $answer = New-SyncError 413 'too large'; $rq.Linger = $true }
  else {
    $body = ReadBody $rq $length
    # whose request, for the window: the login typed, or the account of the device key (read
    # before a sign-out drops it); never the password or the key
    $p = $target.Split('?')[0]
    try {
      if ($p -ceq '/__sync/register' -or $p -ceq '/__sync/login') { $c = Get-SyncCredentials $body; if ($c) { $rq.Login = $c.Login } }
      else { $a = Get-SyncAuth $store $rq.Headers; if ($a) { $rq.Login = $a.Login } }
    } catch {}
    try { $answer = Invoke-SyncRequest $store $method $target $rq.Headers $body }
    catch { $answer = New-SyncError 500 'server error' }
  }
  $status = [int]$answer.Status
  $h = "HTTP/1.1 $status $($reasons[$status])`r`n"
  if ($status -ne 204) { $h += "Content-Type: $($answer.Type)`r`n" }
  $h += "Content-Length: $($answer.Body.Length)`r`nCache-Control: no-store`r`n"
  if ($answer.Cookie) { $h += "Set-Cookie: $($answer.Cookie)`r`n" }   # the sign-in cookie (library-sync.ps1)
  $h += "Connection: close`r`n`r`n"
  $bytes = [Text.Encoding]::ASCII.GetBytes($h)
  $stream.Write($bytes, 0, $bytes.Length)
  if ($answer.Body.Length -gt 0) { $stream.Write($answer.Body, 0, $answer.Body.Length) }
  return $status
}

# One request per connection: read it, answer, close
function Serve($client) {
  $rq = $null
  try {
    $client.ReceiveTimeout = 5000
    $client.SendTimeout = 120000
    $stream = $client.GetStream()
    $rq = @{ Client = $client; Stream = $stream; Rest = ([byte[]]@()); Headers = @{}; Linger = $false
             Deadline = [DateTime]::UtcNow.AddSeconds($requestSeconds) }
    $head = ReadHead $rq
    if ($null -eq $head) { return }
    $lines = @($head -split '\r?\n' | Where-Object { $_ -ne '' })
    if ($lines.Count -eq 0) { return }
    $line = $lines[0]
    $ua = ''; $headers = $rq.Headers
    foreach ($h in ($lines | Select-Object -Skip 1)) {
      $c = $h.IndexOf(':')
      if ($c -le 0) { continue }
      $name = $h.Substring(0, $c).Trim(); $value = $h.Substring($c + 1).Trim()
      $headers[$name] = $value
      if ($name -eq 'User-Agent') { $ua = $value }
    }
    $parts = $line.Split(' ')
    $method = $parts[0]; $headOnly = $method -eq 'HEAD'
    $addr = $client.Client.RemoteEndPoint.Address
    if (-not (IsHomeAddress $addr)) {
      $ip = PlainIp $addr
      if (-not $refused[$ip]) { $refused[$ip] = $true; Say "! refused $ip — not a home-network address" }
      Plain $stream '403 Forbidden' 'Forbidden' $headOnly; return
    }
    $ip = PlainIp $addr
    if ($parts.Length -ge 2 -and -not (Gate $rq $method $parts[1] $addr $ip $ua)) { return }
    if ($parts.Length -ge 2 -and $method -eq 'POST' -and $parts[1].Split('?')[0] -eq '/__bye') {   # the page is closing
      SkipBody $rq; Bye $ip $ua; Head $stream '204 No Content' 'text/plain' 0; return
    }
    if ($parts.Length -lt 2 -or @('GET', 'HEAD', 'POST', 'PUT') -cnotcontains $method) {
      SkipBody $rq; Plain $stream '405 Method Not Allowed' 'Method not allowed' $headOnly; return
    }
    $target = $parts[1]
    $isSync = $target.StartsWith('/__sync/', [StringComparison]::Ordinal)
    if (($method -ceq 'POST' -and $isSync) -or ($method -ceq 'PUT' -and $target.Split('?')[0] -ceq '/__sync/data') -or
        ($method -ceq 'GET' -and $isSync -and -not (Test-SyncPrivate $target))) {
      if ($method -ceq 'GET') { Seen $ip $ua ([Uri]::UnescapeDataString($target.Split('?')[0]).TrimStart('/')) }
      $status = ServeSync $rq $method $target
      $what = $target.Split('?')[0]; $who = $rq.Login
      if ($what -ceq '/__sync/register' -or $what -ceq '/__sync/login') {   # shown here with the login, never the password
        if ($what -ceq '/__sync/register') { $what = 'registration' } else { $what = 'sign-in' }
        if ($status -eq 200) { $how = 'ok' } else { $how = 'refused' }
        if ($who) { $tail = ": $who" } else { $tail = '' }
        Say "@ $ip · $(DeviceKind $ua) — $what $how ($status)$tail"
        if ($status -eq 200 -and $who) { Signed $ip $ua $who $false }
      }
      elseif ($what -ceq '/__sync/logout') { if ($status -lt 300) { Signed $ip $ua $null $true } }
      elseif ($who -and $status -lt 300) { Signed $ip $ua $who $true }   # a device signed in before: its first sync
      return
    }
    if ($method -ceq 'POST' -or $method -ceq 'PUT') {
      SkipBody $rq; Plain $stream '405 Method Not Allowed' 'Method not allowed' $headOnly; return
    }
    if (Test-SyncPrivate $target) { Plain $stream '404 Not Found' 'Not found' $headOnly; return }   # .sync\ is never served
    # Raw path, decoded here: a '#' in a file name arrives as %23 and must stay part of the name
    $rel = [Uri]::UnescapeDataString($parts[1].Split('?')[0]).TrimStart('/')
    if ($rel -eq '__library_root') { Plain $stream '200 OK' $root $headOnly; return }   # lets a launcher recognise this library
    Seen $ip $ua $rel
    if ($rel -eq '__alive') { Head $stream '204 No Content' 'text/plain' 0; return }   # "still here" from the catalog / reader
    if ($rel -eq '') { $rel = 'index.html' }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($root, $rel))
    if (-not ($path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -and [IO.File]::Exists($path)) -or
        (Test-SyncPrivateFile $root $path)) {
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
    if ($null -ne $rq -and $rq.Linger) { Linger $rq }
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
Write-Host "  Anyone on this Wi-Fi who knows the address can open the library; only reading places are written" -ForegroundColor DarkGray
Write-Host "  (to the .sync folder), and only by signed-in devices." -ForegroundColor DarkGray
try { $code = Get-SyncCode $store } catch { $code = "(can't read .sync\code.txt: $($_.Exception.Message))" }
Write-Host "  Library code (to create an account on a new device): $code"
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

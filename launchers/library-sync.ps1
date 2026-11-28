# Sync store for the "Open Library" .bat launchers: accounts and one versioned JSON document per
# account. The Windows PowerShell 5.1 twin of library_sync.py (read its docstring for the files and
# formats): both work on the same <library>\.sync\ folder, so a .bat and a .sh can serve one library.
#
# The server is a dumb store: all merging happens in the browser. The stored `doc` is opaque, so it
# is kept as the exact JSON text that arrived (PowerShell's own JSON cmdlets would damage it: big
# numbers, {} and [], depth); a small validating parser below finds the few values the store reads.
#
# Interface (the .bat files dot-source this file):
#   New-SyncStore $root                                   -> store
#   Invoke-SyncRequest $store $method $path $headers $body -> @{ Status; Type; Body = [byte[]]; Cookie }
#                                                            (Cookie: a Set-Cookie value, or absent)
#   Test-SyncSignedIn $store $headers                     -> the login of the device token the request
#                                                            carries (libsess cookie or Bearer), or $null
#   Test-SyncPrivate $urlPath                             -> $true for a path with a `.sync` segment
#   Test-SyncPrivateFile $root $fullPath                  -> $true if an existing file lies in .sync
#   Get-SyncCode $store                                   -> the library code (made on first call)
#   New-SyncError $status $name                           -> an error answer (400/413 before routing)
#   $SyncBodyLimit                                        -> 8 MB; a longer body is answered 413
#
# Windows PowerShell 5.1 only: no ??, no ternary, no -Parallel, no -AsHashtable. ASCII only (5.1
# reads a .ps1 without BOM in the ANSI code page).

try { Add-Type -AssemblyName System.Numerics } catch {}

$global:SyncIterations = 200000
$global:SyncBodyLimit = 8 * 1024 * 1024
$global:SyncFreeWrong = 5        # wrong tries in a row before the pause starts
$global:SyncFirstPause = 60      # seconds; doubles per further wrong try
$global:SyncMaxPause = 3600
$global:SyncJsonType = 'application/json; charset=utf-8'
$global:SyncUtf8 = New-Object Text.UTF8Encoding($false, $true)   # no BOM; throws on invalid bytes
$global:SyncRng = New-Object Security.Cryptography.RNGCryptoServiceProvider
$global:SyncEpoch = New-Object DateTime(1970, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
$global:SyncZeroSalt = New-Object byte[] 16
$global:SyncReserved = @('con', 'prn', 'aux', 'nul', 'com1', 'com2', 'com3', 'com4', 'com5', 'com6',
                         'com7', 'com8', 'com9', 'lpt1', 'lpt2', 'lpt3', 'lpt4', 'lpt5', 'lpt6', 'lpt7',
                         'lpt8', 'lpt9')
# the account's email (lower-case): the same pattern string as EMAIL_PATTERN in library_sync.py
$global:SyncEmailPattern = '(?=[^@]{1,64}@)[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+'
$global:SyncEmailMax = 128
# The sign-in cookie of the network library (library_sync.py: COOKIE, COOKIE_ATTRS, COOKIE_AGE)
$global:SyncCookie = 'libsess'
$global:SyncCookieAttrs = 'HttpOnly; Secure; SameSite=Lax; Path=/'
$global:SyncCookieAge = 34560000
$global:SyncSignedFor = 60   # seconds a token found valid is taken as valid without a lookup
# One JSON token (after optional whitespace) per match; \G makes the matches back to back, so the
# text is valid only if they reach its end. NaN/Infinity are accepted, as Python's json does.
$global:SyncJsonRe = [regex]::new(
  '\G[ \t\n\r]*(?:(?<s>"(?>(?:[^"\\\x00-\x1f]+|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*)")' +
  '|(?<n>-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|NaN|-?Infinity)' +
  '|(?<p>[{}\[\]:,])|(?<l>true|false|null))',
  [Text.RegularExpressions.RegexOptions]::CultureInvariant)
# The stored data file as both servers write it, version first: {"version": N, "doc": {...}}. Its
# head gives the version without parsing the whole document (slow in PowerShell for a big account)
$global:SyncDataHeadRe = [regex]::new(
  '\A\{"version": (0|[1-9][0-9]*), "doc": \{', [Text.RegularExpressions.RegexOptions]::CultureInvariant)
$global:SyncDataTailRe = [regex]::new(
  '\}\}[ \t\n\r]*\z', [Text.RegularExpressions.RegexOptions]::CultureInvariant)

# The same list as WORDS in library_sync.py (a test checks they match)
$global:SyncWords = (@'
able acid aged also arch area army atom aunt auto away baby back bake ball band bank barn base
bath bead beam bean bear beat bell belt bend bird bite blue boat body bold bolt bone book boot
born bowl brave bread brick bridge brook broom brown bulb bush cabin cake calm camp candy cape
card care cart cave chain chair chalk charm chess chief city clay clean clear cliff clock cloud
coal coast coat coin cold cone cook cool copper coral cord corn cove craft crane crisp crown cube
curl dark dawn deep deer desk dish dock door dove draw dream drum dune dust eagle earth east easy
edge elm ember end fair fall farm fast fawn fern field fig fine fire fish flag flame flat fleet
flint flour foam fog fold forest fork fox frame frost fruit gate gift glad glass glen glow goat
gold grain grape grass gray green grove gull hall hand harbor hare hat hawk hay heart hedge hill
honey hood hope horn house hush ice inch ink iron isle ivory jade jar jazz jolly joy keen kettle
key kind king kite knot lake lamp lane lark leaf lemon lily lime linen lion lock log loom lull
lunar maple marsh mast meadow melon mill mint mist moon moss moth mouse mud nest net night noble
north nut oak oat ocean olive orange otter owl oyster paint pale palm paper path pearl pebble pine
plain plum pond pony pool port quail quartz queen quick quiet quilt rain raven reed ribbon ridge
ring river road robin rock roof root rope rose round ruby sage sail salt sand seal shade shell
shore silk silver sky slate snow soft song south spark spice spoon spring star steam stone storm
straw stream sugar summer sun swan swift tale tea thorn tide tiger tile timber tower trail tree
tulip twig vale velvet violet wagon warm wave wheat willow wind winter wolf wood wren yard yarn
yellow zinc acorn amber angle apple apron arrow aster badge basil basket beach beacon berry birch
blade blank blaze bloom blossom bonnet border branch breeze bright bronze bubble bucket budget
butter button cactus camel canal candle canyon carpet castle cedar cellar cherry cinder circle
clover cobble comet cotton cradle crest crystal cypress daisy delta desert diary dinner dolphin
dragon drift echo elder engine fabric falcon feather ferry fiddle finch fjord flower forge fossil
fountain garden garlic ginger glider granite gravel guitar hammer harvest hazel helmet heron
hollow hornet island jacket jasmine jungle kernel kitten ladder lagoon lantern laurel lilac linden
lizard lotus magnet mango marble meteor mirror monkey mosaic nectar needle nickel noodle novel
nutmeg orchid oxygen paddle palace panda parrot pasture pepper petal piano pillow planet pocket
poppy prairie pumpkin puzzle rabbit radish rafter ranch rapids reef rocket saddle saffron salmon
sapphire satin scarlet shadow sheep shield signal sketch sparrow spider spruce square stable
statue sunset table tassel temple thistle thunder ticket toast tomato trumpet tunnel turnip turtle
umbrella valley vanilla village walnut walrus wander weasel whale whisper window wizard wonder
yacht zebra zephyr anchor arctic autumn badger banjo barrel beetle bishop bobcat bottle bounty
buckle cabbage cannon carrot ceramic chapel cheese chimney cicada citrus cobweb cricket cuckoo
currant dahlia dapple dingo dusk ermine fennel ferret firefly flute gazelle gecko glacier goblet
gopher gourd hammock heather hickory icicle jackal jigsaw kayak koala lavender lichen locket
lullaby mallow maroon meringue minnow mulberry muffin nutria oatmeal oriole osprey paprika pelican
penguin pigeon pistol plover pretzel quince radar raisin rhubarb sequoia shuttle skiff sorrel
starling sundial tangerine teapot thimble toffee vessel violin wafer whistle
'@).Split([char[]]@(' ', "`t", "`r", "`n"), [StringSplitOptions]::RemoveEmptyEntries)

# Used only where .NET lacks Rfc2898DeriveBytes(..., HashAlgorithmName) (before .NET 4.7.2) or for
# a salt shorter than 8 bytes, which .NET refuses: PBKDF2-HMAC-SHA256, one 32-byte block.
$global:SyncPbkdf2Source = @'
public static class LibrarySyncPbkdf2
{
    public static byte[] Derive(byte[] password, byte[] salt, int iterations)
    {
        using (System.Security.Cryptography.HMACSHA256 mac = new System.Security.Cryptography.HMACSHA256(password))
        {
            byte[] block = new byte[salt.Length + 4];
            System.Buffer.BlockCopy(salt, 0, block, 0, salt.Length);
            block[salt.Length + 3] = 1;
            byte[] u = mac.ComputeHash(block);
            byte[] t = (byte[])u.Clone();
            for (int i = 1; i < iterations; i++)
            {
                u = mac.ComputeHash(u);
                for (int j = 0; j < t.Length; j++) t[j] ^= u[j];
            }
            return t;
        }
    }
}
'@

# ---- small helpers

function Get-SyncNow { return ([DateTime]::UtcNow - $global:SyncEpoch).TotalSeconds }

function Get-SyncRandomBytes([int]$n) {
  $b = New-Object byte[] $n
  $global:SyncRng.GetBytes($b)
  return ,$b
}

# Uniform random integer in 0..n-1 (rejection sampling, like secrets.randbelow)
function Get-SyncRandomBelow([int]$n) {
  $range = [long]4294967296
  $limit = $range - ($range % $n)
  while ($true) {
    $v = [long][BitConverter]::ToUInt32((Get-SyncRandomBytes 4), 0)
    if ($v -lt $limit) { return [int]($v % $n) }
  }
}

function ConvertTo-SyncHex([byte[]]$bytes) {
  return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

# Like Python's bytes.fromhex: pairs of hex digits, ASCII whitespace allowed between pairs.
# $null if the text is not that.
function ConvertFrom-SyncHex([string]$text) {
  if ($text -notmatch '^(?:[ \t\n\r\f\v]*[0-9a-fA-F]{2})*[ \t\n\r\f\v]*\z') { return $null }
  $h = $text -replace '[ \t\n\r\f\v]', ''
  $bytes = New-Object byte[] ($h.Length / 2)
  for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [Convert]::ToByte($h.Substring(2 * $i, 2), 16) }
  return ,$bytes
}

# Constant-time comparison of the UTF-8 bytes of two strings (like hmac.compare_digest)
function Test-SyncEqual([string]$a, [string]$b) {
  $x = [Text.Encoding]::UTF8.GetBytes($a)
  $y = [Text.Encoding]::UTF8.GetBytes($b)
  if ($x.Length -ne $y.Length) { return $false }
  $d = 0
  for ($i = 0; $i -lt $x.Length; $i++) { $d = $d -bor ($x[$i] -bxor $y[$i]) }
  return ($d -eq 0)
}

function Test-SyncAscii([string]$s) { return ($s -cmatch '^[\x00-\x7f]*\z') }

# PBKDF2-HMAC-SHA256 over the password's UTF-8 bytes, 32 bytes, as lower-case hex
function Get-SyncPasswordHash([string]$password, [byte[]]$salt, [int]$iterations) {
  $pw = $global:SyncUtf8.GetBytes($password)   # throws on a lone surrogate, as Python's encode does
  $dk = $null
  if ($salt.Length -ge 8) {
    try {
      $kdf = [Security.Cryptography.Rfc2898DeriveBytes]::new($pw, $salt, $iterations,
                                                             [Security.Cryptography.HashAlgorithmName]::SHA256)
      try { $dk = $kdf.GetBytes(32) } finally { $kdf.Dispose() }
    } catch { $dk = $null }
  }
  if ($null -eq $dk) {
    if (-not ('LibrarySyncPbkdf2' -as [type])) { Add-Type -TypeDefinition $global:SyncPbkdf2Source }
    $dk = [LibrarySyncPbkdf2]::Derive($pw, $salt, $iterations)
  }
  return (ConvertTo-SyncHex $dk)
}

# SHA-256 (lower-case hex) of the token's hex string's ASCII bytes
function Get-SyncTokenHash([string]$token) {
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $h = $sha.ComputeHash([Text.Encoding]::ASCII.GetBytes($token)) } finally { $sha.Dispose() }
  return (ConvertTo-SyncHex $h)
}

function New-SyncToken { return (ConvertTo-SyncHex (Get-SyncRandomBytes 32)) }

# ---- the private folder

# True if any (decoded) segment of the URL path is `.sync` (any case, trailing dots/spaces ignored)
function Test-SyncPrivate([string]$path) {
  $p = [Uri]::UnescapeDataString(($path -split '[?#]', 2)[0])
  foreach ($seg in ($p -split '[/\\]')) {
    $s = $seg.TrimEnd([char[]]@('.', ' '))
    if ($s.ToLowerInvariant() -ceq '.sync' -or $s.ToUpperInvariant() -ceq '.SYNC') { return $true }
  }
  return $false
}

# True if the existing file $full (under $root) lies in .sync by the names really on disk, or if its
# names can't be told: Windows also opens a folder by its short 8.3 name (e.g. SYNC~1), which the
# URL check can't see.
function Test-SyncPrivateFile([string]$root, [string]$full) {
  $cur = $root
  $rel = $full.Substring($root.Length)
  foreach ($seg in $rel.Split([char[]]@('\', '/'), [StringSplitOptions]::RemoveEmptyEntries)) {
    try { $found = @([IO.Directory]::GetFileSystemEntries($cur, $seg)) } catch { return $true }
    if ($found.Count -ne 1) { return $true }
    $cur = $found[0]
    $name = [IO.Path]::GetFileName($cur).TrimEnd([char[]]@('.', ' '))
    if ($name.ToLowerInvariant() -ceq '.sync' -or $name.ToUpperInvariant() -ceq '.SYNC') { return $true }
  }
  return $false
}

# ---- JSON: validate, find values, keep the original text of everything else

# The node tree of $text if it is one valid JSON value, else $null. A node is a hashtable:
#   K = 'o' object, 'a' array, 's' string, 'n' number, 'l' true/false/null
#   S, E = start and end (exclusive) of the value in $text;  R = the token text (s, n, l)
#   M = members of an object: list of @{ Key = decoded name; RawKey = name as written; Node }
#   I = items of an array
function ConvertFrom-SyncJson([string]$text) {
  $stack = New-Object Collections.ArrayList
  $root = $null; $want = 'v'; $key = $null; $rawKey = $null; $end = 0; $done = $false
  # want: 'v' a value, 'v]' a value or ']', 'k' a name, 'k}' a name or '}', ':' a colon,
  #       ',' a comma or the end of the open container
  foreach ($m in $global:SyncJsonRe.Matches($text)) {
    $end = $m.Index + $m.Length
    if ($done) { return $null }
    $top = $null
    if ($stack.Count -gt 0) { $top = $stack[$stack.Count - 1] }
    $node = $null
    $gs = $m.Groups['s']
    if ($gs.Success -and ($want -ceq 'k' -or $want -ceq 'k}')) {
      $key = ConvertFrom-SyncJsonString $gs.Value; $rawKey = $gs.Value; $want = ':'
      continue
    }
    if ($gs.Success) { $g = $gs; $kind = 's' }
    elseif ($m.Groups['n'].Success) { $g = $m.Groups['n']; $kind = 'n' }
    elseif ($m.Groups['l'].Success) { $g = $m.Groups['l']; $kind = 'l' }
    else { $g = $m.Groups['p']; $kind = 'p' }
    $tok = $g.Value; $pos = $g.Index
    if ($kind -cne 'p' -or $tok -ceq '{' -or $tok -ceq '[') {
      # a value starts
      if ($want -cne 'v' -and $want -cne 'v]') { return $null }
      if ($kind -cne 'p') { $node = @{ K = $kind; S = $pos; E = $pos + $tok.Length; R = $tok } }
      elseif ($tok -ceq '{') { $node = @{ K = 'o'; S = $pos; E = 0; M = (New-Object Collections.ArrayList) } }
      else { $node = @{ K = 'a'; S = $pos; E = 0; I = (New-Object Collections.ArrayList) } }
      if ($null -eq $top) { $root = $node }
      elseif ($top.K -ceq 'o') { [void]$top.M.Add(@{ Key = $key; RawKey = $rawKey; Node = $node }) }
      else { [void]$top.I.Add($node) }
      if ($kind -ceq 'p') {
        [void]$stack.Add($node)
        if ($tok -ceq '{') { $want = 'k}' } else { $want = 'v]' }
        continue
      }
    } elseif ($tok -ceq ':') {
      if ($want -cne ':') { return $null }
      $want = 'v'
      continue
    } elseif ($tok -ceq ',') {
      if ($want -cne ',') { return $null }
      if ($top.K -ceq 'o') { $want = 'k' } else { $want = 'v' }
      continue
    } else {
      # '}' or ']' closes the open container
      if ($null -eq $top) { return $null }
      if ($tok -ceq '}') {
        if ($top.K -cne 'o' -or ($want -cne ',' -and $want -cne 'k}')) { return $null }
      } else {
        if ($top.K -cne 'a' -or ($want -cne ',' -and $want -cne 'v]')) { return $null }
      }
      $top.E = $pos + 1
      $stack.RemoveAt($stack.Count - 1)
    }
    # a value is complete (a scalar, or the container just closed)
    if ($stack.Count -eq 0) { $done = $true } else { $want = ',' }
  }
  if (-not $done) { return $null }
  if ($text.Substring($end) -cnotmatch '^[ \t\n\r]*\z') { return $null }
  return $root
}

# The value of a JSON string token (with its quotes); the token is already validated
function ConvertFrom-SyncJsonString([string]$raw) {
  $inner = $raw.Substring(1, $raw.Length - 2)
  if ($inner.IndexOf([char]92) -lt 0) { return $inner }
  $sb = New-Object Text.StringBuilder
  $i = 0
  while ($i -lt $inner.Length) {
    $j = $inner.IndexOf([char]92, $i)
    if ($j -lt 0) { [void]$sb.Append($inner.Substring($i)); break }
    [void]$sb.Append($inner.Substring($i, $j - $i))
    $c = $inner.Substring($j + 1, 1)
    $i = $j + 2
    if ($c -ceq 'u') { [void]$sb.Append([char][Convert]::ToInt32($inner.Substring($j + 2, 4), 16)); $i = $j + 6 }
    elseif ($c -ceq 'n') { [void]$sb.Append([char]10) }
    elseif ($c -ceq 't') { [void]$sb.Append([char]9) }
    elseif ($c -ceq 'r') { [void]$sb.Append([char]13) }
    elseif ($c -ceq 'b') { [void]$sb.Append([char]8) }
    elseif ($c -ceq 'f') { [void]$sb.Append([char]12) }
    else { [void]$sb.Append($c) }   # " \ /
  }
  return $sb.ToString()
}

# A JSON string literal for $s
function ConvertTo-SyncJsonString([string]$s) {
  $sb = New-Object Text.StringBuilder
  [void]$sb.Append('"')
  foreach ($ch in $s.ToCharArray()) {
    $c = [int]$ch
    if ($c -eq 34) { [void]$sb.Append('\"') }
    elseif ($c -eq 92) { [void]$sb.Append('\\') }
    elseif ($c -lt 32) { [void]$sb.Append('\u' + $c.ToString('x4')) }
    else { [void]$sb.Append($ch) }
  }
  [void]$sb.Append('"')
  return $sb.ToString()
}

# The value node of member $key of object node $node (the last one, as in Python), else $null.
# Names are compared exactly (case-sensitive).
function Get-SyncMember($node, [string]$key) {
  if ($null -eq $node -or $node.K -cne 'o') { return $null }
  $found = $null
  foreach ($m in $node.M) { if ($m.Key -ceq $key) { $found = $m.Node } }
  return $found
}

function Get-SyncRaw([string]$text, $node) { return $text.Substring($node.S, $node.E - $node.S) }

# An integer number node (Python int, not bool, not float)
function Test-SyncInt($node) {
  return ($null -ne $node -and $node.K -ceq 'n' -and $node.R -cmatch '^-?(?:0|[1-9][0-9]*)\z')
}

function ConvertTo-SyncBigInt([string]$raw) {
  return [System.Numerics.BigInteger]::Parse($raw, [Globalization.CultureInfo]::InvariantCulture)
}

function ConvertTo-SyncDouble([string]$raw) {
  try { return [double]::Parse($raw, [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture) }
  catch {   # .NET Framework refuses out-of-range numbers; Python reads them as infinity
    if ($raw.StartsWith('-')) { return [double]::NegativeInfinity } else { return [double]::PositiveInfinity }
  }
}

# Object node $node (in $text) as JSON text with member $key set to the raw JSON $rawValue. Like a
# Python dict: a repeated name keeps its first place and its last value; a new name goes last.
function Join-SyncObject([string]$text, $node, [string]$key, [string]$rawKey, [string]$rawValue) {
  $order = New-Object Collections.ArrayList
  $values = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
  $names = New-Object 'Collections.Generic.Dictionary[string,string]' ([StringComparer]::Ordinal)
  foreach ($m in $node.M) {
    if (-not $values.ContainsKey($m.Key)) { [void]$order.Add($m.Key); $names[$m.Key] = $m.RawKey }
    $values[$m.Key] = Get-SyncRaw $text $m.Node
  }
  if (-not $values.ContainsKey($key)) { [void]$order.Add($key); $names[$key] = $rawKey }
  $values[$key] = $rawValue
  $parts = New-Object Collections.ArrayList
  foreach ($k in $order) { [void]$parts.Add($names[$k] + ': ' + $values[$k]) }
  return '{' + ($parts.ToArray() -join ', ') + '}'
}

# ---- files

# A stored file exists but can't be used: reported (500) and never overwritten
function New-SyncCorrupt([string]$name) { return (New-Object IO.InvalidDataException $name) }

function Get-SyncCorruptName($e) {
  while ($null -ne $e) {
    if ($e -is [IO.InvalidDataException]) { return $e.Message }
    $e = $e.InnerException
  }
  return $null
}

function Test-SyncMissing($e) {
  while ($null -ne $e) {
    if ($e -is [IO.FileNotFoundException] -or $e -is [IO.DirectoryNotFoundException]) { return $true }
    $e = $e.InnerException
  }
  return $false
}

# The file's bytes; $null only if it does not exist (any other failure throws)
function Read-SyncBytes([string]$path) {
  try {
    $fs = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                          [IO.FileShare]([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
  } catch {
    if (Test-SyncMissing $_.Exception) { return $null }
    throw
  }
  try {
    $len = [int]$fs.Length
    $buf = New-Object byte[] $len
    $off = 0
    while ($off -lt $len) {
      $n = $fs.Read($buf, $off, $len - $off)
      if ($n -le 0) { break }
      $off += $n
    }
  } finally { $fs.Dispose() }
  if ($off -lt $len) {   # the file shrank while being read
    $short = New-Object byte[] $off
    [Array]::Copy($buf, $short, $off)
    $buf = $short
  }
  return ,$buf
}

# UTF-8 text of the bytes, one leading BOM dropped; throws on invalid UTF-8
function ConvertFrom-SyncUtf8([byte[]]$bytes) {
  $start = 0
  if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $start = 3 }
  return $global:SyncUtf8.GetString($bytes, $start, $bytes.Length - $start)
}

# @{ Text; Node } of a JSON file; $null if it does not exist; Corrupt if it can't be parsed
function Read-SyncJson([string]$path, [string]$name) {
  $bytes = Read-SyncBytes $path
  if ($null -eq $bytes) { return $null }
  try { $text = ConvertFrom-SyncUtf8 $bytes } catch { throw (New-SyncCorrupt $name) }
  $node = ConvertFrom-SyncJson $text
  if ($null -eq $node) { throw (New-SyncCorrupt $name) }
  return @{ Text = $text; Node = $node }
}

# Atomic write: UTF-8 without BOM to a temp file in the same folder, then replace the target
function Write-SyncFile([string]$path, [string]$content) {
  $dir = [IO.Path]::GetDirectoryName($path)
  [void][IO.Directory]::CreateDirectory($dir)
  $tmp = [IO.Path]::Combine($dir, '.' + [IO.Path]::GetFileName($path) + '.' + $PID + '.' +
                                  (ConvertTo-SyncHex (Get-SyncRandomBytes 4)) + '.tmp')
  try {
    $bytes = $global:SyncUtf8.GetBytes($content)
    $fs = New-Object IO.FileStream($tmp, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
      $fs.Write($bytes, 0, $bytes.Length)
      $fs.Flush($true)
    } finally { $fs.Dispose() }
    if ([IO.File]::Exists($path)) {
      [IO.File]::Replace($tmp, $path, [NullString]::Value)
    } else {
      try { [IO.File]::Move($tmp, $path) }
      catch {
        if (-not [IO.File]::Exists($path)) { throw }
        [IO.File]::Replace($tmp, $path, [NullString]::Value)   # made in between by another process
      }
    }
  } finally {
    if ([IO.File]::Exists($tmp)) { try { [IO.File]::Delete($tmp) } catch {} }
  }
}

# Exclusive lock for every read-check-write of accounts\ and data\: a byte-range lock on byte 0 of
# .sync\lock, the one Python's msvcrt.locking takes, so a .bat and library_sync.py exclude each other.
function Enter-SyncLock($store) {
  [void][IO.Directory]::CreateDirectory($store.Dir)
  $path = [IO.Path]::Combine($store.Dir, 'lock')
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  $fs = $null
  while ($true) {
    try {
      if ($null -eq $fs) {
        $fs = [IO.File]::Open($path, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
      }
      $fs.Lock(0, 1)
      return $fs
    } catch {
      if ([DateTime]::UtcNow -gt $deadline) {
        if ($null -ne $fs) { $fs.Dispose() }
        throw
      }
      Start-Sleep -Milliseconds 20
    }
  }
}

function Exit-SyncLock($fs) {
  try { $fs.Unlock(0, 1) } catch {}
  $fs.Dispose()
}

# ---- the store

function New-SyncStore([string]$root) {
  return @{
    Dir = [IO.Path]::Combine([IO.Path]::GetFullPath($root), '.sync')
    CodeThrottle = @{}   # throttle state lives in memory only
    PwThrottle = @{}
    Signed = @{}         # token -> @{ Login; Until }: the sign-in gate asks on every request
  }
}

function Read-SyncCodeText([string]$path) {
  try { $bytes = Read-SyncBytes $path } catch { $bytes = $null }   # like Python: OSError = no code
  if ($null -eq $bytes) { return '' }
  return (ConvertFrom-SyncUtf8 $bytes).Trim()   # undecodable: throws, as Python does
}

# The library code: 4 words + 2 digits, "-"-joined; made into .sync\code.txt on the first call
function Get-SyncCode($store) {
  $path = [IO.Path]::Combine($store.Dir, 'code.txt')
  $text = Read-SyncCodeText $path
  if ($text) { return $text }
  $lock = Enter-SyncLock $store
  try {
    $text = Read-SyncCodeText $path
    if ($text) { return $text }
    $parts = New-Object Collections.ArrayList
    for ($i = 0; $i -lt 4; $i++) { [void]$parts.Add($global:SyncWords[(Get-SyncRandomBelow $global:SyncWords.Count)]) }
    [void]$parts.Add((Get-SyncRandomBelow 100).ToString('00'))
    $text = $parts.ToArray() -join '-'
    Write-SyncFile $path ($text + "`n")
    return $text
  } finally { Exit-SyncLock $lock }
}

function Get-SyncAccountPath($store, [string]$login) {
  return [IO.Path]::Combine([IO.Path]::Combine($store.Dir, 'accounts'), $login + '.json')
}

function Get-SyncDataPath($store, [string]$login) {
  return [IO.Path]::Combine([IO.Path]::Combine($store.Dir, 'data'), $login + '.json')
}

# The account: @{ Text; Node; Salt; Hash; Iterations }, $null if absent; Corrupt if unusable
function Read-SyncAccount($store, [string]$login) {
  $name = $login + '.json'
  $j = Read-SyncJson (Get-SyncAccountPath $store $login) $name
  if ($null -eq $j) { return $null }
  $n = $j.Node
  $tokens = Get-SyncMember $n 'tokens'
  $it = Get-SyncMember $n 'iterations'
  $hash = Get-SyncMember $n 'hash'
  $salt = Get-SyncMember $n 'salt'
  if ($null -eq $tokens -or $tokens.K -cne 'a' -or -not (Test-SyncInt $it) -or
      $null -eq $hash -or $hash.K -cne 's' -or $null -eq $salt -or $salt.K -cne 's') {
    throw (New-SyncCorrupt $name)
  }
  $iv = ConvertTo-SyncBigInt $it.R
  # (Python would accept more than 2^31-1 iterations and never finish; .NET takes an int)
  if ([System.Numerics.BigInteger]::Compare($iv, [System.Numerics.BigInteger]::Zero) -le 0 -or
      [System.Numerics.BigInteger]::Compare($iv, (ConvertTo-SyncBigInt ([string][int]::MaxValue))) -gt 0) {
    throw (New-SyncCorrupt $name)
  }
  $hv = ConvertFrom-SyncJsonString $hash.R
  if (-not (Test-SyncAscii $hv)) { throw (New-SyncCorrupt $name) }
  $sb = ConvertFrom-SyncHex (ConvertFrom-SyncJsonString $salt.R)
  if ($null -eq $sb) { throw (New-SyncCorrupt $name) }
  return @{ Text = $j.Text; Node = $n; Salt = $sb; Hash = $hv; Iterations = [int]::Parse($it.R, [Globalization.CultureInfo]::InvariantCulture) }
}

# The stored data: @{ Text (the JSON to answer); Version; Doc (node in Text, $null if no `doc`) }
function Read-SyncData($store, [string]$login) {
  $name = $login + '.json'
  $j = Read-SyncJson (Get-SyncDataPath $store $login) $name
  if ($null -eq $j) {
    $text = '{"version": 0, "doc": null}'
    $node = ConvertFrom-SyncJson $text
  } else {
    $text = $j.Text
    $node = $j.Node
  }
  $v = Get-SyncMember $node 'version'
  if (-not (Test-SyncInt $v)) { throw (New-SyncCorrupt $name) }   # also: not an object
  return @{ Text = $text; Version = (ConvertTo-SyncBigInt $v.R); Doc = (Get-SyncMember $node 'doc') }
}

# @{ Text; Version } of the stored data without parsing the document when the file has the form
# both servers write (a head and a tail check only); any other file is parsed in full by
# Read-SyncData (Corrupt, a 500, if it can't be). For GET and PUT: a file cut short fails the tail
# check; a file broken in the middle is served as is (the browser's JSON.parse refuses it, so no
# device merges with it or sends its version back), and a PUT of that version replaces it.
function Read-SyncDataQuick($store, [string]$login) {
  $name = $login + '.json'
  $bytes = Read-SyncBytes (Get-SyncDataPath $store $login)
  if ($null -eq $bytes) { return @{ Text = '{"version": 0, "doc": null}'; Version = (ConvertTo-SyncBigInt '0') } }
  try { $text = ConvertFrom-SyncUtf8 $bytes } catch { throw (New-SyncCorrupt $name) }
  $m = $global:SyncDataHeadRe.Match($text)
  if ($m.Success -and $global:SyncDataTailRe.IsMatch($text)) {
    return @{ Text = $text; Version = (ConvertTo-SyncBigInt $m.Groups[1].Value) }
  }
  return (Read-SyncData $store $login)
}

function Write-SyncData($store, [string]$login, $version, [string]$rawDoc) {
  $v = $version.ToString([Globalization.CultureInfo]::InvariantCulture)
  Write-SyncFile (Get-SyncDataPath $store $login) ('{"version": ' + $v + ', "doc": ' + $rawDoc + '}')
}

# The lower-case token of an `Authorization: Bearer` header, or $null
function Get-SyncBearer([hashtable]$headers) {
  $auth = ''
  if ($null -ne $headers) {
    foreach ($k in @($headers.Keys)) {
      if (([string]$k).ToLowerInvariant() -ceq 'authorization') { $auth = ([string]$headers[$k]).Trim() }
    }
  }
  $m = [regex]::Match($auth, '^(\S+)\s+(.*)\z', [Text.RegularExpressions.RegexOptions]::Singleline)
  if (-not $m.Success -or $m.Groups[1].Value.ToLowerInvariant() -cne 'bearer') { return $null }
  $token = $m.Groups[2].Value.Trim().ToLowerInvariant()
  if ($token -cnotmatch '^[0-9a-f]{64}\z') { return $null }
  return $token
}

# The token in the libsess cookie, or $null
function Get-SyncCookieToken([hashtable]$headers) {
  if ($null -eq $headers) { return $null }
  foreach ($k in @($headers.Keys)) {
    if (([string]$k).ToLowerInvariant() -cne 'cookie') { continue }
    foreach ($part in ([string]$headers[$k]).Split(';')) {
      $i = $part.IndexOf('=')
      if ($i -lt 0) { continue }
      if ($part.Substring(0, $i).Trim() -cne $global:SyncCookie) { continue }
      $v = $part.Substring($i + 1).Trim().ToLowerInvariant()
      if ($v -cmatch '^[0-9a-f]{64}\z') { return $v }
    }
  }
  return $null
}

# @{ Login; Token } of the account holding the bearer token, or $null
function Get-SyncAuth($store, [hashtable]$headers) {
  $token = Get-SyncBearer $headers
  if ($null -eq $token) { return $null }
  $login = Find-SyncTokenLogin $store $token
  if ($null -eq $login) { return $null }
  return @{ Login = $login; Token = $token }
}

# The login whose device token the request carries (Bearer header or libsess cookie), or $null
function Test-SyncSignedIn($store, [hashtable]$headers) {
  $now = [DateTime]::UtcNow
  foreach ($token in @((Get-SyncBearer $headers), (Get-SyncCookieToken $headers))) {
    if ($null -eq $token) { continue }
    $hit = $store.Signed[$token]
    if ($null -ne $hit -and $hit.Until -gt $now) { return $hit.Login }
    $login = Find-SyncTokenLogin $store $token
    if ($null -ne $login) {
      $store.Signed[$token] = @{ Login = $login; Until = $now.AddSeconds($global:SyncSignedFor) }
      return $login
    }
  }
  return $null
}

# The account holding this device token, or $null
function Find-SyncTokenLogin($store, [string]$token) {
  $want = Get-SyncTokenHash $token
  $dir = [IO.Path]::Combine($store.Dir, 'accounts')
  if (-not [IO.Directory]::Exists($dir)) { return $null }
  foreach ($f in [IO.Directory]::GetFiles($dir, '*.json')) {
    if (-not $f.EndsWith('.json', [StringComparison]::OrdinalIgnoreCase)) { continue }
    $j = $null
    try { $j = Read-SyncJson $f ([IO.Path]::GetFileName($f)) }
    catch {
      if ($null -eq (Get-SyncCorruptName $_.Exception)) { throw }
      $j = $null   # someone else's broken file must not lock everybody out
    }
    if ($null -eq $j) { continue }
    $tokens = Get-SyncMember $j.Node 'tokens'
    if ($null -eq $tokens -or $tokens.K -cne 'a') { continue }
    foreach ($t in $tokens.I) {
      if ($t.K -cne 's') { continue }
      $tv = ConvertFrom-SyncJsonString $t.R
      if ((Test-SyncAscii $tv) -and (Test-SyncEqual $want $tv.ToLowerInvariant())) {
        return [IO.Path]::GetFileNameWithoutExtension($f)
      }
    }
  }
  return $null
}

# ---- answers

function New-SyncAnswer([int]$status, [string]$json) {
  if ($status -eq 204) { $bytes = New-Object byte[] 0 } else { $bytes = $global:SyncUtf8.GetBytes($json) }
  return @{ Status = $status; Type = $global:SyncJsonType; Body = $bytes }
}

function New-SyncError([int]$status, [string]$name, [string]$extra = '') {
  return (New-SyncAnswer $status ('{"error": ' + (ConvertTo-SyncJsonString $name) + $extra + '}'))
}

function New-SyncThrottled([double]$wait) {
  $w = [long][Math]::Floor($wait) + 1
  return (New-SyncError 429 'throttled' (', "wait": ' + $w))
}

# ---- throttle: wrong tries per key; after SyncFreeWrong in a row, a pause that doubles per try.
# Enter-SyncThrottle reserves an attempt (so parallel requests can't all slip in before the first
# is judged); each reserved attempt ends with Exit-SyncThrottle: $true wrong, $false right, $null release.

function Enter-SyncThrottle([hashtable]$table, [string]$key, [double]$now) {
  $s = $table[$key]
  if ($null -eq $s) { $s = @{ W = 0; U = [double]0; F = 0 }; $table[$key] = $s }
  if ($s.U -gt $now) { return [double]($s.U - $now) }
  if ($s.W -lt $global:SyncFreeWrong) { $slots = $global:SyncFreeWrong - $s.W } else { $slots = 1 }
  if ($s.F -ge $slots) { return [double]1 }
  $s.F = $s.F + 1
  return [double]0
}

function Exit-SyncThrottle([hashtable]$table, [string]$key, $wrong, [double]$now) {
  $s = $table[$key]
  if ($null -eq $s) { $s = @{ W = 0; U = [double]0; F = 0 }; $table[$key] = $s }
  $s.F = [Math]::Max(0, $s.F - 1)
  if ($null -eq $wrong) {
  } elseif ($wrong) {
    $s.W = $s.W + 1
    if ($s.W -ge $global:SyncFreeWrong) {
      $e = $s.W - $global:SyncFreeWrong
      if ($e -ge 12) { $pause = $global:SyncMaxPause }
      else { $pause = [Math]::Min($global:SyncFirstPause * [Math]::Pow(2, $e), $global:SyncMaxPause) }
      $s.U = $now + $pause
    }
  } else {
    $s.W = 0; $s.U = [double]0
  }
  if ($s.W -eq 0 -and $s.F -eq 0) {
    $table.Remove($key)
  } elseif ($table.Count -gt 10000) {   # forget keys whose pause is over and that never reached one
    foreach ($k in @($table.Keys)) {
      $v = $table[$k]
      if ($k -ne $key -and $v.F -eq 0 -and $v.U -lt $now -and $v.W -lt $global:SyncFreeWrong) { $table.Remove($k) }
    }
  }
}

# ---- requests

# @{ Body; Node } of a JSON object body, else $null
function ConvertFrom-SyncBody([byte[]]$body) {
  try { $text = $global:SyncUtf8.GetString($body) } catch { return $null }   # strict UTF-8, BOM refused
  $node = ConvertFrom-SyncJson $text
  if ($null -eq $node -or $node.K -cne 'o') { return $null }
  return @{ Text = $text; Node = $node }
}

# @{ Body; Login (the email, trimmed and lower-case); Password } or $null for a 400
function Get-SyncCredentials([byte[]]$body) {
  $b = ConvertFrom-SyncBody $body
  if ($null -eq $b) { return $null }
  $l = Get-SyncMember $b.Node 'login'
  $p = Get-SyncMember $b.Node 'password'
  if ($null -eq $l -or $l.K -cne 's' -or $null -eq $p -or $p.K -cne 's') { return $null }
  $login = ConvertFrom-SyncJsonString $l.R
  if ($login -cmatch '[^\x00-\x7f]') { return $null }   # so lowering can't make ASCII of it
  $login = $login.Trim([char[]]@(' ', "`t", "`r", "`n")).ToLowerInvariant()
  if ($login.Length -lt 3 -or $login.Length -gt $global:SyncEmailMax) { return $null }
  if ($login -cnotmatch ('^(?:' + $global:SyncEmailPattern + ')\z')) { return $null }
  if ($global:SyncReserved -ccontains $login.Split('.')[0]) { return $null }   # reserved device names on Windows
  $password = ConvertFrom-SyncJsonString $p.R
  $chars = $password.Length - [regex]::Matches($password, '[\uD800-\uDBFF][\uDC00-\uDFFF]').Count   # code points
  if ($chars -lt 1 -or $chars -gt 1024) { return $null }
  return @{ Body = $b; Login = $login; Password = $password }
}

function Invoke-SyncRegister($store, [hashtable]$headers, [byte[]]$body) {
  $cred = Get-SyncCredentials $body
  if ($null -eq $cred) { return (New-SyncError 400 'bad request') }
  $now = Get-SyncNow
  $wait = Enter-SyncThrottle $store.CodeThrottle 'code' $now
  if ($wait -gt 0) { return (New-SyncThrottled $wait) }
  try {
    $given = Get-SyncMember $cred.Body.Node 'code'
    $want = (Get-SyncCode $store).Trim().ToLowerInvariant()
    $good = $false
    if ($null -ne $given -and $given.K -ceq 's') {
      $g = (ConvertFrom-SyncJsonString $given.R).Trim().ToLowerInvariant()
      $good = Test-SyncEqual $g $want
    }
  } catch {
    Exit-SyncThrottle $store.CodeThrottle 'code' $null $now
    throw
  }
  if (-not $good) {
    Exit-SyncThrottle $store.CodeThrottle 'code' $true $now
    return (New-SyncError 403 'wrong code')
  }
  Exit-SyncThrottle $store.CodeThrottle 'code' $false $now
  $salt = Get-SyncRandomBytes 16
  $hash = Get-SyncPasswordHash $cred.Password $salt $global:SyncIterations
  $lock = Enter-SyncLock $store
  try {
    $path = Get-SyncAccountPath $store $cred.Login
    if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) { return (New-SyncError 409 'login taken') }
    $token = New-SyncToken
    Write-SyncFile $path ('{"login": "' + $cred.Login + '", "salt": "' + (ConvertTo-SyncHex $salt) +
                          '", "hash": "' + $hash + '", "iterations": ' + $global:SyncIterations +
                          ', "tokens": ["' + (Get-SyncTokenHash $token) + '"]}')
  } finally { Exit-SyncLock $lock }
  return (New-SyncAnswer 200 ('{"token": "' + $token + '"}'))
}

function Invoke-SyncLogin($store, [hashtable]$headers, [byte[]]$body) {
  $cred = Get-SyncCredentials $body
  if ($null -eq $cred) { return (New-SyncError 400 'bad request') }
  $login = $cred.Login
  $now = Get-SyncNow
  $wait = Enter-SyncThrottle $store.PwThrottle $login $now
  if ($wait -gt 0) { return (New-SyncThrottled $wait) }
  try {
    $acc = Read-SyncAccount $store $login
    if ($null -ne $acc) {
      $got = Get-SyncPasswordHash $cred.Password $acc.Salt $acc.Iterations
      $ok = Test-SyncEqual $got ($acc.Hash.ToLowerInvariant())
    } else {
      [void](Get-SyncPasswordHash $cred.Password $global:SyncZeroSalt $global:SyncIterations)   # same cost for unknown logins
      $ok = $false
    }
  } catch {
    Exit-SyncThrottle $store.PwThrottle $login $null $now
    throw
  }
  if (-not $ok) {
    Exit-SyncThrottle $store.PwThrottle $login $true $now
    return (New-SyncError 401 'unauthorized')
  }
  Exit-SyncThrottle $store.PwThrottle $login $false $now
  $lock = Enter-SyncLock $store
  try {
    $acc = Read-SyncAccount $store $login
    if ($null -eq $acc) { return (New-SyncError 401 'unauthorized') }
    $token = New-SyncToken
    $tokens = Get-SyncMember $acc.Node 'tokens'
    $items = New-Object Collections.ArrayList
    foreach ($t in $tokens.I) { [void]$items.Add((Get-SyncRaw $acc.Text $t)) }
    [void]$items.Add('"' + (Get-SyncTokenHash $token) + '"')
    $list = '[' + ($items.ToArray() -join ', ') + ']'
    Write-SyncFile (Get-SyncAccountPath $store $login) (Join-SyncObject $acc.Text $acc.Node 'tokens' '"tokens"' $list)
  } finally { Exit-SyncLock $lock }
  return (New-SyncAnswer 200 ('{"token": "' + $token + '"}'))
}

# A device signed in before the gate: its Bearer token becomes the cookie (Invoke-SyncRequest)
function Invoke-SyncSession($store, [hashtable]$headers, [byte[]]$body) {
  if ($null -eq (Get-SyncAuth $store $headers)) { return (New-SyncError 401 'unauthorized') }
  return (New-SyncAnswer 204 '')
}

function Invoke-SyncLogout($store, [hashtable]$headers, [byte[]]$body) {
  $lock = Enter-SyncLock $store
  try {
    $auth = Get-SyncAuth $store $headers
    if ($null -eq $auth) { return (New-SyncError 401 'unauthorized') }
    $store.Signed.Remove($auth.Token)   # this key is no sign-in any more, at once
    $path = Get-SyncAccountPath $store $auth.Login
    $j = Read-SyncJson $path ($auth.Login + '.json')
    $tokens = Get-SyncMember $j.Node 'tokens'
    if ($null -eq $j -or $null -eq $tokens -or $tokens.K -cne 'a') { throw 'account changed meanwhile' }
    $gone = Get-SyncTokenHash $auth.Token
    $items = New-Object Collections.ArrayList
    foreach ($t in $tokens.I) {
      if ($t.K -ceq 's' -and (ConvertFrom-SyncJsonString $t.R).ToLowerInvariant() -ceq $gone) { continue }
      [void]$items.Add((Get-SyncRaw $j.Text $t))
    }
    $list = '[' + ($items.ToArray() -join ', ') + ']'
    Write-SyncFile $path (Join-SyncObject $j.Text $j.Node 'tokens' '"tokens"' $list)
  } finally { Exit-SyncLock $lock }
  return (New-SyncAnswer 204 '')
}

function Invoke-SyncData($store, [string]$method, [hashtable]$headers, [byte[]]$body) {
  if ($method -ceq 'GET') {
    $auth = Get-SyncAuth $store $headers
    if ($null -eq $auth) { return (New-SyncError 401 'unauthorized') }
    $current = Read-SyncDataQuick $store $auth.Login
    return (New-SyncAnswer 200 $current.Text)
  }
  $lock = Enter-SyncLock $store
  try {
    $auth = Get-SyncAuth $store $headers
    if ($null -eq $auth) { return (New-SyncError 401 'unauthorized') }
    $b = ConvertFrom-SyncBody $body
    $version = $null; $doc = $null
    if ($null -ne $b) { $version = Get-SyncMember $b.Node 'version'; $doc = Get-SyncMember $b.Node 'doc' }
    if ($null -eq $b -or -not (Test-SyncInt $version) -or $null -eq $doc -or $doc.K -cne 'o') {
      return (New-SyncError 400 'bad request')
    }
    $current = Read-SyncDataQuick $store $auth.Login
    $given = ConvertTo-SyncBigInt $version.R
    if ([System.Numerics.BigInteger]::Compare($given, $current.Version) -ne 0) { return (New-SyncAnswer 409 $current.Text) }
    $next = [System.Numerics.BigInteger]::Add($current.Version, [System.Numerics.BigInteger]::One)
    Write-SyncData $store $auth.Login $next (Get-SyncRaw $b.Text $doc)
    return (New-SyncAnswer 200 ('{"version": ' + $next.ToString([Globalization.CultureInfo]::InvariantCulture) + '}'))
  } finally { Exit-SyncLock $lock }
}

function Invoke-SyncPlace($store, [hashtable]$headers, [byte[]]$body) {
  $lock = Enter-SyncLock $store
  try {
    $auth = Get-SyncAuth $store $headers
    if ($null -eq $auth) { return (New-SyncError 401 'unauthorized') }
    $b = ConvertFrom-SyncBody $body
    $bookNode = $null; $place = $null
    if ($null -ne $b) { $bookNode = Get-SyncMember $b.Node 'book'; $place = Get-SyncMember $b.Node 'place' }
    $book = ''
    if ($null -ne $bookNode -and $bookNode.K -ceq 's') { $book = ConvertFrom-SyncJsonString $bookNode.R }
    $time = Get-SyncMember $place 'time'
    if ($book.Length -eq 0 -or $null -eq $time -or $time.K -cne 'n') { return (New-SyncError 400 'bad request') }
    $current = Read-SyncData $store $auth.Login
    $doc = $current.Doc
    if ($null -eq $doc) { throw 'stored data has no doc' }   # Python: KeyError, 500
    $docText = $current.Text
    if ($doc.K -ceq 'l' -and $doc.R -ceq 'null') {
      $docText = '{"v": 1}'
      $doc = ConvertFrom-SyncJson $docText
    }
    if ($doc.K -cne 'o') { return (New-SyncError 400 'bad request') }
    $places = Get-SyncMember $doc 'places'
    $placesText = $docText
    if ($null -eq $places -or $places.K -cne 'o') {
      $placesText = '{}'
      $places = ConvertFrom-SyncJson $placesText
    }
    $old = Get-SyncMember (Get-SyncMember $places $book) 'time'
    $newer = $true
    if ($null -ne $old -and $old.K -ceq 'n') { $newer = (ConvertTo-SyncDouble $time.R) -gt (ConvertTo-SyncDouble $old.R) }
    if ($newer) {
      $newPlaces = Join-SyncObject $placesText $places $book $bookNode.R (Get-SyncRaw $b.Text $place)
      $newDoc = Join-SyncObject $docText $doc 'places' '"places"' $newPlaces
      $next = [System.Numerics.BigInteger]::Add($current.Version, [System.Numerics.BigInteger]::One)
      Write-SyncData $store $auth.Login $next $newDoc
    }
  } finally { Exit-SyncLock $lock }
  return (New-SyncAnswer 204 '')
}

# One /__sync/ request -> @{ Status; Type; Body; Cookie }. $headers: name -> value; $body: the request
# body. Cookie: the libsess cookie sign-in, registration and session set, and sign-out clears.
function Invoke-SyncRequest($store, [string]$method, [string]$path, [hashtable]$headers, [byte[]]$body) {
  $answer = Invoke-SyncRoute $store $method $path $headers $body
  $p = ($path -split '[?#]', 2)[0]
  $token = $null
  if (($p -ceq '/__sync/login' -or $p -ceq '/__sync/register') -and $answer.Status -eq 200) {
    $m = [regex]::Match([Text.Encoding]::ASCII.GetString($answer.Body), '"token":\s*"([0-9a-f]{64})"')
    if ($m.Success) { $token = $m.Groups[1].Value }
  } elseif ($p -ceq '/__sync/session' -and $answer.Status -eq 204) {
    $token = Get-SyncBearer $headers
  } elseif ($p -ceq '/__sync/logout') {   # always: the page can't clear an HttpOnly cookie itself
    $answer.Cookie = $global:SyncCookie + '=; ' + $global:SyncCookieAttrs + '; Max-Age=0'
  }
  if ($null -ne $token) {
    $answer.Cookie = $global:SyncCookie + '=' + $token + '; ' + $global:SyncCookieAttrs + '; Max-Age=' + $global:SyncCookieAge
  }
  return $answer
}

function Invoke-SyncRoute($store, [string]$method, [string]$path, [hashtable]$headers, [byte[]]$body) {
  $ErrorActionPreference = 'Stop'
  $p = ($path -split '[?#]', 2)[0]
  if ($p -ceq '/__sync/register' -or $p -ceq '/__sync/login' -or $p -ceq '/__sync/logout' -or
      $p -ceq '/__sync/session' -or $p -ceq '/__sync/place') {
    $allowed = @('POST')
  } elseif ($p -ceq '/__sync/data') {
    $allowed = @('GET', 'PUT')
  } else {
    return (New-SyncError 404 'not found')
  }
  if ($allowed -cnotcontains $method) { return (New-SyncError 405 'method not allowed') }
  if ($null -eq $body) { $body = New-Object byte[] 0 }
  if ($body.Length -gt $global:SyncBodyLimit) { return (New-SyncError 413 'too large') }
  try {
    if ($p -ceq '/__sync/register') { return (Invoke-SyncRegister $store $headers $body) }
    if ($p -ceq '/__sync/login') { return (Invoke-SyncLogin $store $headers $body) }
    if ($p -ceq '/__sync/logout') { return (Invoke-SyncLogout $store $headers $body) }
    if ($p -ceq '/__sync/session') { return (Invoke-SyncSession $store $headers $body) }
    if ($p -ceq '/__sync/place') { return (Invoke-SyncPlace $store $headers $body) }
    return (Invoke-SyncData $store $method $headers $body)
  } catch {
    $name = Get-SyncCorruptName $_.Exception
    if ($null -ne $name) { return (New-SyncError 500 'corrupt file' (', "file": ' + (ConvertTo-SyncJsonString $name))) }
    return (New-SyncError 500 'server error')
  }
}

"""Static checks of the Windows launchers (no PowerShell on the build machine; see windows-sync-checklist.md).

Cheap guards only: Windows PowerShell 5.1 syntax, ASCII, the shared word list, balanced brackets,
and that both .bat files load library-sync.ps1 and hide .sync.
"""

import re
import sys
from pathlib import Path

import pytest

LAUNCHERS = Path(__file__).resolve().parent.parent / "launchers"
PS1 = LAUNCHERS / "library-sync.ps1"
BATS = [LAUNCHERS / "Open Library.bat", LAUNCHERS / "Open Library (network).bat"]
sys.path.insert(0, str(LAUNCHERS))
from library_sync import WORDS  # noqa: E402


def _powershell(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".bat":
        text = text[text.index("\n#POWERSHELL") + 1:]
    return text


def _code_only(text):
    """The text with comments, strings and here-strings blanked out (a rough PowerShell lexer)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith(("@'", '@"'), i) and text[i + 2:i + 3] in ("\n", "\r"):
            end = text.index("\n" + text[i + 1] + "@", i + 2)
            out.append(" ")
            i = end + 3
        elif text.startswith("<#", i):
            i = text.index("#>", i) + 2
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "'":
            i += 1
            while True:
                if text[i] == "'" and text[i + 1:i + 2] == "'":
                    i += 2
                elif text[i] == "'":
                    i += 1
                    break
                else:
                    i += 1
            out.append("''")
        elif c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "`" else 1
            i += 1
            out.append('""')
        else:
            out.append(c)
            i += 1
    return "".join(out)


SOURCES = [PS1] + BATS


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_no_powershell_7_only_syntax(path):
    code = _code_only(_powershell(path))
    for pattern, what in [(r"\?\?", "?? / ??="), (r"\?\.|\?\[", "?. / ?[]"), (r"\s\?\s[^{]", "ternary ? :"),
                          (r"-AsHashtable", "-AsHashtable"), (r"-Parallel", "-Parallel"),
                          (r"\|\|", "||"), (r"&&", "&&"), (r"ConvertFrom-Json[^\n|]*-Depth", "ConvertFrom-Json -Depth"),
                          (r"(?m)^\s*class\s", "class"), (r"-NoEnumerate", "-NoEnumerate")]:
        m = re.search(pattern, code)
        assert m is None, f"{path.name}: PowerShell 7 only ({what}) near: {code[max(0, m.start() - 60):m.end() + 20]!r}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_brackets_balance(path):
    code = _code_only(_powershell(path))
    stack, pairs = [], {")": "(", "]": "[", "}": "{"}
    for lineno, line in enumerate(code.split("\n"), 1):
        for ch in line:
            if ch in "([{":
                stack.append((ch, lineno))
            elif ch in ")]}":
                assert stack and stack[-1][0] == pairs[ch], f"{path.name}: unbalanced {ch!r} at code line {lineno}"
                stack.pop()
    assert not stack, f"{path.name}: unclosed {stack[-1]}"


def test_ps1_is_ascii():
    # Windows PowerShell 5.1 reads a .ps1 without BOM in the ANSI code page
    assert PS1.read_bytes().isascii()


def test_ps1_word_list_matches_python():
    text = PS1.read_text(encoding="ascii")
    block = re.search(r"\$global:SyncWords = \(@'\r?\n(.*?)\r?\n'@\)", text, re.S).group(1)
    assert block.split() == WORDS


def test_ps1_matches_python_constants():
    text = PS1.read_text(encoding="ascii")
    assert "$global:SyncIterations = 200000" in text
    assert "$global:SyncBodyLimit = 8 * 1024 * 1024" in text
    assert "$global:SyncFreeWrong = 5" in text and "$global:SyncFirstPause = 60" in text
    assert "$global:SyncMaxPause = 3600" in text
    assert "'^[a-z0-9._-]{2,32}\\z'" in text and "'^[0-9a-f]{64}\\z'" in text
    for name in ("New-SyncStore", "Invoke-SyncRequest", "Test-SyncPrivate", "Get-SyncCode"):
        assert re.search(rf"(?m)^function {name}\b", text), name


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_bat_uses_the_sync_store(path):
    code = _powershell(path)
    assert "'library-sync.ps1'" in code and "New-SyncStore $root" in code
    assert "Test-SyncPrivate $" in code and "Test-SyncPrivateFile $root $path" in code
    assert "Invoke-SyncRequest $store" in code and "Library code" in code and "Get-SyncCode $store" in code
    assert "$SyncBodyLimit" in code

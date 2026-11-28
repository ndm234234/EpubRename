# Bold word starts ("bionic"-style reading)

## Intent

A reading mode that sets the first letters of every word in bold, so the eye catches on
word starts and glides along the line (the idea known as "bionic reading"). It works over
any font: it is a mode, not one more font. Evidence that it speeds reading is weak; the
point is the reader's own comfort, especially on a phone.

## Goals

- One switch in the text settings; it works with every font, size, layout, theme, and
  with "Scroll text".
- The reading place, bookmarks, notes, search hits and highlights work exactly as without
  the mode, and stay valid across devices where the mode is off.
- Per device: like "Scroll text", stored in this device's own storage, not in the synced
  settings.

## Non-goals

- Tuning how many letters get bold, or the weight. One fixed rule.
- A contrast (paler word endings) variant: tried in the spike, weaker to the eye and poor
  in the dark theme.

## Anti-goals

- A bookmark or reading place saved with the mode on that lands elsewhere with it off
  (or on another device). This is the one thing that makes the feature a failure.
- The page jumping away from the reading place when the mode is switched.

## Approaches

1. **Bold by rewriting the text** (picked). Each word's start is wrapped in
   `<b class="bio">`. The real bold effect. It cuts text nodes, which moves the addresses
   (CFI) the reader stores; handled below.
2. **Contrast through the CSS Custom Highlight API.** The DOM stays as it is, so addresses
   are untouched, but highlights can only change color, not weight: the effect is weak,
   and the pale endings read badly in the dark theme. Dropped after the side-by-side.
3. **A ready "bionic" font.** None with Cyrillic and our faces; would replace the user's
   font choice. Dropped.

## How addresses stay right (checked in the spike)

The text only gets wrapped, never changed, so a character's position in the chapter's text
is the same with and without the mode.

- **Range → address:** epub.js's own `EpubCFI` with ignore class `bio` gives the same
  address as on the plain text (457 of 457 words checked, points and spans).
- **Address → range:** epub.js's resolver with the ignore class is wrong in 3–5% of cases
  (mostly paragraph starts). Instead: resolve on a plain copy of the chapter (the bold
  unwrapped), then carry the start and end over to the real document by character position
  (457 of 457 exact).
- Both are hooked where epub.js converts addresses (`EpubCFI` from a range / to a range),
  for documents that have `b.bio` only, so every user of addresses (epub.js's location,
  the reader's reading place, bookmarks, notes, search, highlights) gets them without
  changes of its own. The plain copy is made once per chapter document and dropped when
  the mode changes.

## Behaviour

- Switch: a checkbox "Bold word starts" on the **Font** tab, under the font buttons, with
  a one-line note. (My call: the user thinks of it as part of choosing the font.)
- Turning it on or off re-lays the text and returns to the reading place (as a font change
  does).
- Bold letters: 1 for words of up to 3 letters, 2 for 4, otherwise 45% rounded up. Letters
  and digits count as word characters. (My call, the spike's rule.)
- Left alone: `pre`, `code`, `kbd`, `samp`, `tt`, scripts and styles.

## Left to the builder

- Where exactly the hooks sit in the code, and how the plain copy is cached.
- Checks: the existing reader suite with the mode on; a check that bookmarks/reading place
  saved with the mode on resolve to the same words with it off, and the reverse.

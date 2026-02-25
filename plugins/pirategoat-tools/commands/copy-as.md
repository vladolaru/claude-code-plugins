---
description: Copy content to clipboard formatted for the target destination (markdown by default, Slack mrkdwn or P2/Gutenberg HTML when specified)
---

You are a format-aware clipboard tool. You convert content to the target format and copy it to the system clipboard. Do what is asked; nothing more, nothing less.

## Step 1: Identify Target Format, Then Extract Content

**Arguments:** `$ARGUMENTS`

Before extracting content, determine the target format — it controls how you process everything downstream.

**Target format** — scan arguments for a destination keyword:
- `slack` or `for slack` or `mrkdwn` → Slack mrkdwn
- `p2` or `for p2` or `gutenberg` or `wordpress` → P2/Gutenberg HTML
- `markdown`, `md`, or nothing specified → Standard markdown (default)

**Content source** — one of:
- Inline text in the arguments
- A conversation reference (e.g., "the summary above", "that code block")
- A file path to read

If the content reference is ambiguous, ask the user to clarify.

## Step 2: Prepare Content

Extract the content. If it comes from a file, read it. If it references conversation context, locate and extract the relevant portion.

Strip tool artifacts (Read output line numbers, tool wrappers) so the clipboard contains clean, ready-to-paste content.

## Step 3: Format for Target

### Standard Markdown (default)

Pass content through unchanged. Only fix broken formatting (unclosed code fences, malformed links). Preserve the author's structure, wording, and style exactly.

### Slack mrkdwn

**RULE 0: Apply every rule in the checklist below.** Skipping a transformation produces broken formatting in Slack. Process the content top-to-bottom, applying each rule. Content inside inline code (`` ` ``) and fenced code blocks (`` ``` ``) is protected — leave it verbatim.

<conversion_checklist>

**1. Bold** — `**text**` → `*text*` (single asterisks)

**2. Italic** — `*text*` → `_text_` (underscores only)

**3. Bold+italic** — `***text***` → `*text*` (default to bold — Slack can't combine reliably)

**4. Strikethrough** — `~~text~~` → `~text~` (single tildes)

**5. Links** — `[text](url)` → `<url|text>` (URL first, pipe separator, angle brackets)

**6. Images** — `![alt](url)` → `<url|alt>` (convert to link — inline images unsupported)

**7. Headings** — `# Heading` through `######` → `*Heading*` (bold text, blank line before for separation)

**8. Code blocks** — Strip language identifier from opening fence: ` ```python ` → ` ``` `

**9. Lists** — `* item` → `• item` (replace `*` bullets with `•` to avoid bold conflict). Keep `- item` and `1. item` as-is. Strip checkbox syntax: `- [ ] task` → `- task`, `- [x] done` → `- done`.

**10. Tables** — Convert to preformatted code block with space-aligned columns:

````
```
Column A   Column B   Column C
value 1    value 2    value 3
```
````

**11. Blockquotes** — Keep `>` for single-level. Flatten `>>` and deeper to `>`.

**12. Horizontal rules** — Remove `---`, `***`, `___`. Use a blank line for separation.

**13. Special characters in prose** (outside code spans/blocks):
- `&` → `&amp;`
- `<` → `&lt;`
- `>` (not blockquote) → `&gt;`

**14. HTML tags** — Strip entirely.

</conversion_checklist>

#### Verification: Before/After Example

**Standard markdown input:**
```markdown
## Summary

Fixed the **auth bug** where `admin` users got ~~blocked~~.
See [the docs](https://example.com) for details.
```

**Correct Slack mrkdwn output:**
```
*Summary*

Fixed the *auth bug* where `admin` users got ~blocked~.
See <https://example.com|the docs> for details.
```

Verify your output matches this transformation pattern before copying.

### P2/Gutenberg HTML

P2 uses the WordPress Gutenberg block editor for both posts and comments. Gutenberg accepts HTML on paste and auto-converts to blocks — but only when the clipboard contains `public.html` data. Plain text markdown requires Cmd+Shift+V and produces inferior results.

**Convert markdown to clean, semantic HTML.** Map each markdown element to its Gutenberg-compatible HTML equivalent:

<p2_conversion_rules>

**1. Headings** — `## Heading` → `<h2>Heading</h2>` (use matching heading level, h2-h6)

**2. Paragraphs** — Consecutive lines of text → `<p>text</p>` (blank line = new paragraph)

**3. Bold** — `**text**` → `<strong>text</strong>`

**4. Italic** — `*text*` → `<em>text</em>`

**5. Bold+italic** — `***text***` → `<strong><em>text</em></strong>`

**6. Strikethrough** — `~~text~~` → `<s>text</s>`

**7. Inline code** — `` `code` `` → `<code>code</code>`

**8. Code blocks** — Fenced blocks → `<pre><code>content</code></pre>` (strip language identifier — Gutenberg assigns syntax highlighting separately)

**9. Links** — `[text](url)` → `<a href="url">text</a>`

**10. Images** — `![alt](url)` → `<img src="url" alt="alt" />`

**11. Unordered lists** — `- item` or `* item` → `<ul><li>item</li></ul>`

**12. Ordered lists** — `1. item` → `<ol><li>item</li></ol>`

**13. Blockquotes** — `> text` → `<blockquote><p>text</p></blockquote>`

**14. Horizontal rules** — `---` → `<hr />`

**15. Tables** — Convert to HTML table: `<table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table>`

</p2_conversion_rules>

**Also generate a plain text fallback** — strip all HTML tags to produce a readable plain text version. This ensures paste works everywhere, not just Gutenberg.

#### Verification: Before/After Example

**Standard markdown input:**
```markdown
## Summary

Fixed the **auth bug** where `admin` users got ~~blocked~~.
See [the docs](https://example.com) for details.
```

**Correct P2 HTML output:**
```html
<h2>Summary</h2>
<p>Fixed the <strong>auth bug</strong> where <code>admin</code> users got <s>blocked</s>. See <a href="https://example.com">the docs</a> for details.</p>
```

## Step 4: Copy to Clipboard

### For Markdown and Slack formats

Write the formatted content to a temp file and pipe it to the clipboard:

```bash
mkdir -p "$TMPDIR"
cat > "$TMPDIR/clipboard-content.txt" << 'CLIPBOARD_EOF'
<formatted content here>
CLIPBOARD_EOF
pbcopy < "$TMPDIR/clipboard-content.txt"
rm -f "$TMPDIR/clipboard-content.txt"
```

If `pbcopy` is unavailable (Linux), use `xclip -selection clipboard` instead.

### For P2/Gutenberg format

`pbcopy` only sets plain text on the clipboard. Gutenberg needs `public.html` data to auto-convert to blocks. Use a Swift script to set both HTML and plain text simultaneously:

```bash
mkdir -p "$TMPDIR"
cat > "$TMPDIR/set-clipboard.swift" << 'SWIFT_EOF'
import AppKit

// Read HTML from first argument, plain text from second
let htmlPath = CommandLine.arguments[1]
let plainPath = CommandLine.arguments[2]

let html = try! String(contentsOfFile: htmlPath, encoding: .utf8)
let plain = try! String(contentsOfFile: plainPath, encoding: .utf8)

let pb = NSPasteboard.general
pb.clearContents()
pb.setString(html, forType: .html)
pb.setString(plain, forType: .string)
print("OK")
SWIFT_EOF

# Write the HTML and plain text content to temp files
cat > "$TMPDIR/clipboard-html.txt" << 'HTML_EOF'
<HTML content here>
HTML_EOF

cat > "$TMPDIR/clipboard-plain.txt" << 'PLAIN_EOF'
<plain text fallback here>
PLAIN_EOF

# Set both types on the clipboard
swift "$TMPDIR/set-clipboard.swift" "$TMPDIR/clipboard-html.txt" "$TMPDIR/clipboard-plain.txt"
rm -f "$TMPDIR/set-clipboard.swift" "$TMPDIR/clipboard-html.txt" "$TMPDIR/clipboard-plain.txt"
```

If Swift is unavailable (Linux), fall back to `xclip`: `xclip -selection clipboard -t text/html < html-file`.

## Step 5: Report

Tell the user:
- What was copied (brief summary or first few lines)
- Which format was applied (markdown, Slack mrkdwn, or P2 HTML)
- Approximate length (line count or character count)
- For P2 format: confirm both HTML and plain text were set on clipboard — user can Cmd+V in P2 directly

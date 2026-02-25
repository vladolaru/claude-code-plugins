---
description: Copy content to clipboard formatted for the target destination (markdown by default, Slack mrkdwn when specified)
---

You are a format-aware clipboard tool. You convert content to the target format and copy it to the system clipboard. Do what is asked; nothing more, nothing less.

## Step 1: Identify Target Format, Then Extract Content

**Arguments:** `$ARGUMENTS`

Before extracting content, determine the target format — it controls how you process everything downstream.

**Target format** — scan arguments for a destination keyword:
- `slack` or `for slack` or `mrkdwn` → Slack mrkdwn
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

## Step 4: Copy to Clipboard

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

## Step 5: Report

Tell the user:
- What was copied (brief summary or first few lines)
- Which format was applied (markdown or Slack mrkdwn)
- Approximate length (line count or character count)

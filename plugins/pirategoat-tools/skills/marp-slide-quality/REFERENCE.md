# SlideGauge Rules Reference

Complete reference for all SlideGauge rules and their scoring impact.

## Rule Categories

### Content Rules

#### title/required (✅ Required)
- **Severity**: error
- **Deduction**: 30 points
- **Message**: "Missing title (add # or ##)"
- **Fix**: Add a heading to the slide

#### title/too_long (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 10 points
- **Threshold**: ≤35 characters
- **Message**: "Title length {X} > max 35 (shorten by {Y} chars)"
- **Fix**: Shorten the title to be more concise

#### content/too_long (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 15 points
- **Threshold**: ≤350 characters (≤450 for exercise slides)
- **Message**: "Adjusted content {X} > max {Y} (reduce by ~{Z} chars or split into 2 slides)"
- **Fix**: Remove unnecessary words or split into multiple slides

#### content/too_short (ℹ️ Info)
- **Severity**: info
- **Deduction**: 5 points
- **Threshold**: ≥50 characters
- **Message**: "Content {X} < min 50 (add ~{Y} chars)"
- **Fix**: Add more context or explanation

#### bullets/too_many (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 10 points
- **Threshold**: ≤6 bullets
- **Message**: "{X} bullets > max 6 (remove {Y} or split slide)"
- **Fix**: Consolidate bullets or split into multiple slides

#### lines/too_many (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 10 points
- **Threshold**: ≤15 lines
- **Message**: "{X} lines > max 15 (condense or split into 2 slides)"
- **Fix**: Reduce content density or split slide

### Accessibility Rules

#### accessibility/alt_required (✅ Required)
- **Severity**: error
- **Deduction**: 20 points
- **Message**: "Image missing alt text: {path}"
- **Fix**: Add alt text to images using `![alt text](path)`

#### links/bare_urls (ℹ️ Info)
- **Severity**: info
- **Deduction**: 5 points
- **Message**: "Format URL as [text](url)"
- **Fix**: Convert bare URLs to markdown links

### Color Rules

#### color/low_contrast (✅ Required)
- **Severity**: error
- **Deduction**: 20 points
- **Threshold**: ≥4.5:1 contrast ratio (WCAG AA)
- **Message**: "Low contrast {ratio:.2f}:1 < 4.5:1 WCAG AA for fg={fg} bg={bg}"
- **Fix**: Increase color contrast between foreground and background

#### color/too_many (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 10 points
- **Threshold**: ≤6 unique colors
- **Message**: "{X} unique colors > max 6 (reduce by {Y})"
- **Fix**: Use a consistent color palette

### Code Rules

#### code/too_long (⚠️ Warning)
- **Severity**: warning
- **Deduction**: 8 points
- **Threshold**: ≤10 lines (simple code), ≤5 lines (complex code)
- **Message**: "code {X} lines > max {Y} (trim {Z} lines or split)"
- **Fix**: Simplify code example or split across slides

**Complex code indicators:**
- Nested blocks (if/for/while/try/def/class)
- Comments
- Multiple statements per line

## Scoring System

### Starting Score
Each slide starts at **100 points**.

### Deductions
Points are deducted based on rule violations:
- ✅ **Error** (required): -20 to -30 points
- ⚠️ **Warning**: -8 to -15 points
- ℹ️ **Info**: -5 points

### Passing Threshold
- **Default**: 70 points
- Slides below threshold are considered failing

### Bucket Scores
Scores are also tracked by category:
- **a11y**: Accessibility issues
- **code**: Code quality issues
- **color**: Color usage issues
- **content**: Content length/structure issues
- **layout**: Layout/formatting issues

## Special Slide Types

### Exercise Slides
Slides marked with "Exercise" or "TODO" get relaxed content limits:
- Content max: 450 chars (vs 350)
- Allows more detailed instructions

Detection pattern:
```markdown
# Exercise: Your Title
# TODO: Your Title
```

## Disabling Rules

Use HTML comments to disable specific rules for a slide:

```markdown
<!-- slidegauge: disable content/too_long -->
# My Long Slide

This slide has lots of content but that's okay...
```

Multiple rules:
```markdown
<!-- slidegauge: disable content/too_long, bullets/too_many -->
```

## Configuration

SlideGauge can use a custom config file:

```json
{
  "threshold": 80,
  "rules": {
    "content": {
      "max_chars": 400,
      "max_bullets": 8
    }
  },
  "weights": {
    "content/too_long": 10
  }
}
```

```bash
slidegauge presentation.md --config myconfig.json
```

## Output Formats

### Text (--text)
Quick summary for humans:
```
Slide 1 (✓ 100) • no issues
Slide 2 (✓ 85) • content/too_long(15)
SUMMARY: avg=93.5 • passing=24/25
```

### JSON (--json)
Detailed diagnostics for programmatic use:
```json
{
  "slides": [...],
  "summary": {
    "total_slides": 25,
    "avg_score": 88.2,
    "passing": 24,
    "threshold": 70
  }
}
```

### SARIF (--sarif)
For CI/CD integration and GitHub Code Scanning.

## Best Practices

1. **Focus on failures first**: Fix slides < 70 before optimizing others
2. **Content is king**: Don't sacrifice clarity for scores
3. **Split vs compress**: Splitting slides is often better than cramming
4. **Preserve code**: Code examples are valuable, consider splitting over trimming
5. **Accessibility matters**: Always provide alt text for images
6. **Consistency**: Use consistent formatting across all slides

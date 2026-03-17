---
name: current-datetime
description: Use before depending on or presenting the current date or time in any context — writing timestamps into files (Last updated headers, document metadata), stating the date or time in responses, calculating staleness or deadlines, or any reasoning where 'right now' is a fact you need. The system prompt date is a snapshot from conversation start and may be stale; always verify with `date`. Also use for time zone conversions, scheduling across regions, Unix timestamps, date arithmetic, and determining what day a date falls on.
license: MIT
allowed-tools: Bash(date:*)
metadata:
  adapted_from: Matt Hodges' temporal-awareness skill (MIT)
  original_repo: https://github.com/hodgesmr/temporal-awareness
---

# Current Date/Time

## RULE 0: Always Verify

Run `date` BEFORE:
- Writing any date or time value into a file (`Last updated`, document metadata, timestamps in code)
- Stating the current date or time in a response
- Calculating staleness, age, or time remaining
- Any reasoning where "right now" is a fact you depend on

**Never assume.** The system prompt date is a snapshot from conversation start — it may be stale. The system prompt has NO time at all. Trust `date` output, not memory or inference.

### Most Common Command

```bash
date +"%Y-%m-%d %H:%M"    # 2026-03-17 14:39 — for Last updated headers
```

---

## Quick Reference: Date

| Task | GNU date (Linux) | BSD date (macOS) |
|------|------------------|------------------|
| Current date | `date +%Y-%m-%d` | `date +%Y-%m-%d` |
| Day of week | `date +%A` | `date +%A` |
| Specific date's day | `date -d "2025-03-15" +%A` | `date -j -f "%Y-%m-%d" "2025-03-15" +%A` |
| Add days | `date -d "+7 days" +%Y-%m-%d` | `date -v +7d +%Y-%m-%d` |
| Days between | `echo $(( ($(date -d "2025-12-31" +%s) - $(date -d "2025-01-01" +%s)) / 86400 ))` | `echo $(( ($(date -j -f "%Y-%m-%d" "2025-12-31" +%s) - $(date -j -f "%Y-%m-%d" "2025-01-01" +%s)) / 86400 ))` |

## Quick Reference: Time

| Task | GNU date (Linux) | BSD date (macOS) |
|------|------------------|------------------|
| Current time (24h) | `date +%H:%M:%S` | `date +%H:%M:%S` |
| Full datetime | `date +"%Y-%m-%d %H:%M:%S"` | `date +"%Y-%m-%d %H:%M:%S"` |
| ISO 8601 | `date -Iseconds` | `date +%Y-%m-%dT%H:%M:%S%z` |
| Unix timestamp | `date +%s` | `date +%s` |
| From timestamp | `date -d @1704067200` | `date -r 1704067200` |
| Add hours | `date -d "+3 hours"` | `date -v +3H` |
| Time in other TZ | `TZ="America/New_York" date` | `TZ="America/New_York" date` |

## Common Time Zones

| Region | TZ Identifier | UTC Offset |
|--------|---------------|------------|
| Pacific (US/Canada) | `America/Los_Angeles` | UTC-8/-7 |
| Mountain (US/Canada) | `America/Denver` | UTC-7/-6 |
| Central (US/Canada/Mexico) | `America/Chicago` | UTC-6/-5 |
| Eastern (US/Canada) | `America/New_York` | UTC-5/-4 |
| South America East | `America/Sao_Paulo` | UTC-3 |
| Western Europe (UK/PT) | `Europe/London` | UTC+0/+1 |
| Central Europe (DE/FR/IT/ES) | `Europe/Paris` | UTC+1/+2 |
| Eastern Europe (RO/BG/GR/UA/FI) | `Europe/Bucharest` | UTC+2/+3 |
| Gulf (UAE/Oman) | `Asia/Dubai` | UTC+4 |
| South Africa | `Africa/Johannesburg` | UTC+2 |
| India | `Asia/Kolkata` | UTC+5:30 |
| Southeast Asia (SG/MY/PH) | `Asia/Singapore` | UTC+8 |
| China / Hong Kong | `Asia/Shanghai` | UTC+8 |
| Japan / Korea | `Asia/Tokyo` | UTC+9 |
| Australia East | `Australia/Sydney` | UTC+10/+11 |
| New Zealand | `Pacific/Auckland` | UTC+12/+13 |

Use `TZ="identifier" date` to get time in any zone.

## Localization

Day/month names (`%A`, `%B`) use system locale. Force English when needed:

```bash
LC_TIME=C date +%A           # Thursday (not "joi", "giovedì", etc.)
```

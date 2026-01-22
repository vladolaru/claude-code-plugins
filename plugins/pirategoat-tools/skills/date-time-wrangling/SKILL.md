---
name: date-time-wrangling
description: Use when answering questions about current date/time, time zones, what day a date falls on, deadline calculations, scheduling across time zones, Unix timestamps, or any temporal reasoning - especially before writing time-sensitive outputs like meeting invites, deadline reminders, or coordinating across regions.
license: MIT
allowed-tools: Bash(date:*)
metadata:
  adapted_from: Matt Hodges' temporal-awareness skill (MIT)
  original_repo: https://github.com/hodgesmr/temporal-awareness
---

# Date/Time Wrangling

## RULE 0: Verify Before Responding

Run `date` to get the current date/time BEFORE answering any date-sensitive question or writing date-aware output. Trust the command output over any date in your system prompt.

## When to Use

Run `date` first when:
- User asks about current date, time, or day of week
- User asks what day a specific date falls on
- Calculating deadlines, durations, or time until/since events
- Converting between time zones or scheduling across regions
- Working with Unix timestamps (epoch time)
- Writing outputs involving schedules, meetings, or deadlines

## Quick Reference: Date

| Task | GNU date (Linux) | BSD date (macOS) |
|------|------------------|------------------|
| Current date | `date +%Y-%m-%d` | `date +%Y-%m-%d` |
| Day of week | `date +%A` | `date +%A` |
| Specific date's day | `date -d "2025-03-15" +%A` | `date -j -f "%Y-%m-%d" "2025-03-15" +%A` |
| Add days | `date -d "+7 days" +%Y-%m-%d` | `date -v +7d +%Y-%m-%d` |
| Relative (next Fri) | `date -d "next Friday" +%A` | `date -v +fri +%A` |

## Quick Reference: Time

| Task | GNU date (Linux) | BSD date (macOS) |
|------|------------------|------------------|
| Current time (24h) | `date +%H:%M:%S` | `date +%H:%M:%S` |
| Current time (12h) | `date +"%I:%M %p"` | `date +"%I:%M %p"` |
| Full datetime | `date +"%Y-%m-%d %H:%M:%S"` | `date +"%Y-%m-%d %H:%M:%S"` |
| ISO 8601 | `date -Iseconds` | `date +%Y-%m-%dT%H:%M:%S%z` |
| Unix timestamp | `date +%s` | `date +%s` |
| From timestamp | `date -d @1704067200` | `date -r 1704067200` |
| Add hours | `date -d "+3 hours"` | `date -v +3H` |
| Current timezone | `date +%Z` | `date +%Z` |
| Time in other TZ | `TZ="America/New_York" date` | `TZ="America/New_York" date` |

**Detect platform:**
```bash
if date --version >/dev/null 2>&1; then echo "GNU"; else echo "BSD"; fi
```

## Localization

Day/month names (`%A`, `%B`, `%p`) use system locale. Force English when needed for cross-team coordination or international audiences:

```bash
LC_TIME=C date +%A                    # Thursday (not "joi", "giovedì", etc.)
LC_TIME=C date "+%B %d, %Y"           # January 22, 2026
```

**When to localize:**
- **Use English (`LC_TIME=C`)**: Cross-region coordination, international docs, APIs, logs
- **Use locale (default)**: User-facing output matching user's language preference

**Locale-independent formats** (always safe):
- `%Y-%m-%d` → `2026-01-22`
- `%H:%M:%S` → `14:30:45`
- `%s` → `1737561045` (Unix timestamp)

## Examples

**Current date/time (both platforms):**
```bash
date +%Y-%m-%d                # 2025-01-15
date +%H:%M:%S                # 14:30:45
date +"%Y-%m-%d %H:%M:%S"     # 2025-01-15 14:30:45
date +%A                      # Wednesday
```

**Date arithmetic:**
```bash
# GNU: date -d "2025-06-01 +30 days" +%Y-%m-%d
# BSD: date -j -f "%Y-%m-%d" -v +30d "2025-06-01" +%Y-%m-%d
```

**Time arithmetic:**
```bash
# GNU: date -d "+3 hours 30 minutes" +"%H:%M"
# BSD: date -v +3H -v +30M +"%H:%M"
```

**Days between dates:**
```bash
# GNU: echo $(( ($(date -d "2025-12-31" +%s) - $(date -d "2025-01-01" +%s)) / 86400 ))
# BSD: echo $(( ($(date -j -f "%Y-%m-%d" "2025-12-31" +%s) - $(date -j -f "%Y-%m-%d" "2025-01-01" +%s)) / 86400 ))
```

**Time zones:**
```bash
date +%Z                              # Current timezone (e.g., PST)
TZ="America/New_York" date +%H:%M     # Time in New York
TZ="Europe/London" date +%H:%M        # Time in London
TZ="Asia/Tokyo" date +%H:%M           # Time in Tokyo
```

**Unix timestamps:**
```bash
date +%s                              # Current time as epoch (e.g., 1704067200)
# GNU: date -d @1704067200            # Convert epoch to human-readable
# BSD: date -r 1704067200             # Convert epoch to human-readable
```

## Workflow Examples

**Date query:**
User: "What day of the week is July 4th, 2026?"
1. Run: `date -d "July 4, 2026" +%A` (GNU) or `date -j -f "%B %d, %Y" "July 4, 2026" +%A` (BSD)
2. Output: `Saturday`
3. Respond: "July 4th, 2026 falls on a Saturday."

**Time zone coordination:**
User: "Schedule a meeting for 3pm my time (PST) - what time is that in London?"
1. Run: `TZ="Europe/London" date -d "15:00 PST"` (GNU) or set TZ and calculate +8h offset
2. Output: `23:00` (London is UTC+0, PST is UTC-8)
3. Respond: "3pm PST is 11pm in London."

## Common Time Zones

| Region | TZ Identifier | UTC Offset |
|--------|---------------|------------|
| **Americas** |||
| Pacific (US/Canada) | `America/Los_Angeles` | UTC-8/-7 |
| Mountain (US/Canada) | `America/Denver` | UTC-7/-6 |
| Central (US/Canada/Mexico) | `America/Chicago` | UTC-6/-5 |
| Eastern (US/Canada) | `America/New_York` | UTC-5/-4 |
| South America East | `America/Sao_Paulo` | UTC-3 |
| **Europe** |||
| Western (UK/Portugal) | `Europe/London` | UTC+0/+1 |
| Central (DE/FR/IT/ES) | `Europe/Paris` | UTC+1/+2 |
| Eastern (RO/BG/GR/UA/FI) | `Europe/Bucharest` | UTC+2/+3 |
| **Middle East / Africa** |||
| Gulf (UAE/Oman) | `Asia/Dubai` | UTC+4 |
| South Africa | `Africa/Johannesburg` | UTC+2 |
| **Asia** |||
| India | `Asia/Kolkata` | UTC+5:30 |
| Southeast Asia (SG/MY/PH) | `Asia/Singapore` | UTC+8 |
| China / Hong Kong | `Asia/Shanghai` | UTC+8 |
| Japan / Korea | `Asia/Tokyo` | UTC+9 |
| **Oceania** |||
| Australia East | `Australia/Sydney` | UTC+10/+11 |
| New Zealand | `Pacific/Auckland` | UTC+12/+13 |

Use `TZ="identifier" date` to get time in any zone. Full list: `timedatectl list-timezones` (Linux) or check `/usr/share/zoneinfo/`.

# course_ics_analysis

A command-line tool for students to make sense of their university ICS calendar export.

It reads a raw `.ics` file (e.g. exported from Google Calendar or Rapla), filters events by date and course, and produces two things:

- **Two clean ICS files** — one for regular lectures (Vorlesung), one for exams — ready to import into any calendar app
- **A per-course briefing** — a structured overview of every session: date, day, type, and professor

Designed for the DBE program at HHZ, but works with any ICS calendar that follows the `CODE: Title - Type (Professor)` summary convention.

---

## Examples

### 1. See what's coming this semester

```cmd
python cal_filter.py ss26.ics --list-courses
```

```
════════════════════════════════════════════════════════════════════════════
  DBE11  ·  Entrepreneurship
════════════════════════════════════════════════════════════════════════════
  Date        Day  Type                        Professor
  ──────────  ───  ──────────────────────────  ──────────────────────────────
  2026-03-11  Wed  Vorlesung                   J. Münch
  2026-03-12  Thu  Vorlesung                   J. Münch
  2026-06-23  Tue  Prüfungsleistung            J. Münch
  2026-06-24  Wed  Prüfungsleistung            J. Münch
                                               ──────────────────────────────
                                               4 × Vorlesung   2 × Exam
...
════════════════════════════════════════════════════════════════════════════
  SUMMARY
════════════════════════════════════════════════════════════════════════════
  13 courses   |   54 Vorlesung total   |   13 Exam total
════════════════════════════════════════════════════════════════════════════
```

#### Save the briefing as Markdown

```cmd
python cal_filter.py ss26.ics --list-courses --save
```

Writes `ss26_briefing.md` (named after the `semester` field in config) next to the input file — renders nicely in VS Code, Obsidian, and GitHub.

### 2. Split the calendar into two importable ICS files

```cmd
python cal_filter.py ss26.ics
```

Generates `ss26_vorlesung.ics` and `ss26_exam.ics` with a compact summary:

```
  DBE11  Entrepreneurship                    [ 4×V +  2×E]
  DBE12  Digital Business                    [ 3×V +  1×E]
  DBE13  Software Management                 [ 4×V +  2×E]
  ...
```

### 3. Filter to specific courses (override the config)

```cmd
python cal_filter.py ss26.ics --courses "DBE11,DBE14" --list-courses
```

---

## CLI Reference

```
python cal_filter.py <input.ics> [options]
```

| Option | Description |
|--------|-------------|
| `--list-courses` | Print detailed per-course briefing, no files written |
| `--list-courses --save [PATH]` | Print briefing and save as Markdown; PATH defaults to `<semester>_briefing.md` in the output directory |
| `--from DATE` | Earliest date to include (`YYYY-MM-DD`), overrides config |
| `--courses KEYWORDS` | Comma-separated keywords matched against SUMMARY, overrides config |
| `--out-dir DIR` | Output directory for generated ICS files, overrides config |

**CLI arguments take priority over `cal_config.toml`** — useful for one-off runs without editing the config.

---

## Configuration — `cal_config.toml`

Edit this file to set your defaults. CLI arguments override these for a single run.

```toml
# Semester label — used as the prefix for all output filenames.
# e.g. "ss26" → ss26_vorlesung.ics, ss26_exam.ics, ss26_briefing.md
# Leave empty to use the default name "target_semester".
semester = "ss26"

# Include only events on or after this date
from_date = "2026-03-10"

# Course keyword filter (case-insensitive, matched against SUMMARY)
# Leave empty to include all courses
# e.g. course_keywords = ["DBE11", "DBE12", "Entrepreneurship"]
course_keywords = []

# Keywords that classify an event as an exam
exam_keywords = [
    "prüfung",
    "prüfungsleistung",
    "exam",
    "presentation",
]

# Events whose SUMMARY matches any of these are excluded entirely
# (higher priority than course_keywords)
# e.g. exclude_keywords = ["Projekttag", "Feiertag"]
exclude_keywords = ["Prüfungszeitraum", "Vorlesungsfreie Zeit", "Gruppe A", "Gruppe B"]

# Output directory for generated ICS files
# Leave empty to write next to the input file
out_dir = ""
```

### Filter logic

```
Raw events
  → Date filter        (>= from_date)
  → Course filter      (course_keywords; empty = all pass)
  → Exclude filter     (exclude_keywords; match = skip)
  → Classification     (exam_keywords match → Exam file, else → Vorlesung file)
```

---

## File Structure

```
cal_analysis/
├── cal_filter.py       # Main script
├── cal_config.toml     # Global configuration
├── ss26.ics            # Source calendar (input)
├── ss26_vorlesung.ics  # Generated: lecture events
├── ss26_exam.ics       # Generated: exam events
├── ss26_briefing.md    # Generated: course briefing (--list-courses --save)
└── archive/            # Old scripts and data
```

Output filenames are determined by the `semester` field in `cal_config.toml`.

---

## Dependencies

Python 3.11+ (built-in `tomllib`) or Python 3.10 + `tomli`:

```cmd
pip install tomli
```

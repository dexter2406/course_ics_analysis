"""
cal_filter.py  —  ICS Calendar Filter & Splitter
=================================================
Splits an ICS file into separate Vorlesung and Exam files,
with optional date and course-keyword filtering.

Configuration is read from cal_config.toml (same folder as this script).
CLI arguments override config values for one-off use.

Usage:
    python cal_filter.py <input.ics> [options]

Options:
    --from DATE         Earliest date to include (YYYY-MM-DD)
    --courses KEYWORDS  Comma-separated keywords matched against SUMMARY
    --out-dir DIR       Output directory (default: same folder as input)
    --list-courses      Print deduplicated course+professor summary and exit

Examples:
    # Show full deduplicated course list
    python cal_filter.py ss26.ics --list-courses

    # Split into vorlesung + exam ICS using config settings
    python cal_filter.py ss26.ics

    # One-off override: only DBE11 and DBE12
    python cal_filter.py ss26.ics --courses "DBE11,DBE12"
"""

import re
import sys
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


try:
    import tomllib          # Python 3.11+
except ModuleNotFoundError:
    try:
        import tomli as tomllib   # pip install tomli  (Python 3.10)
    except ModuleNotFoundError:
        sys.exit("ERROR: No TOML library found. Run: pip install tomli")


# ─── Defaults (fallback when no config file is present) ────────────────────────
DEFAULT_FROM_DATE = date(2026, 3, 10)

# Fallback exam keywords — overridden by cal_config.toml if present
EXAM_KEYWORDS: list[str] = [
    "prüfung",
    "prüfungsleistung",
    "exam",
    "presentation",
]
# ───────────────────────────────────────────────────────────────────────────────


# ─── Config ─────────────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    """
    Load cal_config.toml.  Returns {} if the file does not exist.
    Exits with an error message if the file is present but malformed.
    """
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as exc:
        sys.exit(f"ERROR: Failed to parse {path.name}: {exc}")


# ─── ICS Parsing ───────────────────────────────────────────────────────────────

def unfold_lines(text: str) -> list[str]:
    """Merge RFC 5545 folded continuation lines."""
    result = []
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and result:
            result[-1] += raw[1:]
        else:
            result.append(raw)
    return result


def extract_props(vevent_text: str) -> dict[str, str]:
    """
    Return a key→value dict for all properties in a VEVENT block.
    Handles folded lines and ;PARAM=VALUE key variants.
    First occurrence of each key wins (important for DTSTART).
    """
    props: dict[str, str] = {}
    for line in unfold_lines(vevent_text):
        if ":" not in line:
            continue
        key_part, _, value = line.partition(":")
        key = key_part.split(";")[0].strip().upper()
        if key not in props:
            props[key] = value.strip()
    return props


def parse_event_date(dtstart_value: str) -> date | None:
    """
    Extract a date from a DTSTART value string (the part after the colon).

    Handles:
      - "20260312T080000Z"   →  2026-03-12
      - "20260312T080000"    →  2026-03-12
      - "20260312"           →  2026-03-12
    """
    v = dtstart_value.strip()[:8]
    try:
        return datetime.strptime(v, "%Y%m%d").date()
    except ValueError:
        return None


def split_ics(content: str) -> tuple[list[str], list[tuple[str, dict]], list[str]]:
    """
    Split ICS file content into:
      header  — lines before the first BEGIN:VEVENT
      events  — list of (raw_block_text, props_dict)
      footer  — lines after the last END:VEVENT

    Raw blocks are preserved verbatim (no content change).
    """
    header: list[str] = []
    footer: list[str] = []
    events: list[tuple[str, dict]] = []

    in_vevent = False
    header_done = False
    current_lines: list[str] = []
    trailing: list[str] = []  # lines after the most recent END:VEVENT

    for raw_line in content.splitlines(keepends=True):
        stripped = raw_line.rstrip("\r\n")

        if stripped == "BEGIN:VEVENT":
            header_done = True
            in_vevent = True
            current_lines = [raw_line]
            trailing = []  # reset; anything before this was footer candidate
        elif stripped == "END:VEVENT" and in_vevent:
            current_lines.append(raw_line)
            block_text = "".join(current_lines)
            events.append((block_text, extract_props(block_text)))
            in_vevent = False
            current_lines = []
        elif in_vevent:
            current_lines.append(raw_line)
        elif not header_done:
            header.append(raw_line)
        else:
            trailing.append(raw_line)

    footer = trailing
    return header, events, footer


def build_ics(header: list[str], event_blocks: list[str], footer: list[str]) -> str:
    """Reassemble an ICS file from its parts."""
    return "".join(header) + "".join(event_blocks) + "".join(footer)


# ─── Classification Helpers ────────────────────────────────────────────────────

def is_exam(summary: str) -> bool:
    """True if the summary contains any exam keyword."""
    s = summary.lower()
    return any(kw in s for kw in EXAM_KEYWORDS)


def matches_course_filter(summary: str, keywords: list[str]) -> bool:
    """True if summary matches any of the given keywords (or no filter set)."""
    if not keywords:
        return True
    s = summary.lower()
    return any(kw.lower() in s for kw in keywords)


def matches_exclude(summary: str, keywords: list[str]) -> bool:
    """True if summary matches any exclude keyword."""
    if not keywords:
        return False
    s = summary.lower()
    return any(kw.lower() in s for kw in keywords)


def normalize_course_name(summary: str) -> str:
    """
    Strip exam suffixes so the same course is grouped together.

    e.g.  "DBE11: Entrepreneurship - Prüfungsleistung (J. Münch)"
          → "DBE11: Entrepreneurship (J. Münch)"
    """
    s = summary.strip()
    pattern = r"\s*-\s*(" + "|".join(re.escape(k) for k in EXAM_KEYWORDS) + r")\b"
    s = re.sub(pattern, "", s, flags=re.IGNORECASE).strip()
    return s


def extract_course_code(summary: str) -> str:
    """
    Extract the leading course code, e.g. "DBE11" or "DBE21/31".
    Returns empty string if none found.
    """
    m = re.match(r"^([A-Z]+\d+(?:/[A-Z]*\d+)*)", summary.strip())
    return m.group(1) if m else ""


def extract_professor(normalized_summary: str) -> str:
    """
    Extract the professor name from a normalized SUMMARY string.

    The professor is always the first parenthetical group that contains a dot
    (abbreviated initial like "J. Münch" or "M. Aiello/ I. Georgievski").
    Venue annotations such as "(HHZ (026/027))" or "(Online)" never contain a dot.

    Must be called on the *normalized* summary (after normalize_course_name())
    so exam suffixes are already stripped.

    Returns empty string if no professor parenthetical is found.
    """
    for m in re.finditer(r"\(([^()]*)\)", normalized_summary):
        if "." in m.group(1):
            return m.group(1).strip()
    return ""


def extract_exam_label(summary: str) -> str:
    """
    Extract the specific exam type label as written in the summary.

    e.g. "DBE11: Entrepreneurship - Prüfungsleistung (J. Münch)" → "Prüfungsleistung"
         "DBE14: Distributed Systems - Presentation (M. Aiello)" → "Presentation"

    Returns empty string if no match (caller should fall back to a default).
    """
    pattern = r"\s*-\s*(" + "|".join(re.escape(k) for k in EXAM_KEYWORDS) + r")\b"
    m = re.search(pattern, summary, flags=re.IGNORECASE)
    return m.group(1) if m else ""


def extract_title(normalized_summary: str) -> str:
    """
    Extract the course title: strip the leading course code and the professor
    parenthetical, keeping only the bare course name.

    Examples:
      "DBE11: Entrepreneurship (J. Münch)"                → "Entrepreneurship"
      "DBE21/31: Elective Cloud-based Web App (U. Brei.)" → "Elective Cloud-based Web App"
      "DBE14: Distributed Systems (M. Aiello/ I. Georg.)" → "Distributed Systems"
      "Studienkommissionssitzung"                         → "Studienkommissionssitzung"
    """
    s = normalized_summary.strip()
    # Strip "CODE: " prefix
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    # Truncate at the first professor parenthetical (first parens containing a dot)
    for m in re.finditer(r"\(([^()]*)\)", s):
        if "." in m.group(1):
            s = s[: m.start()].strip()
            break
    return s


# ─── Course Summary ────────────────────────────────────────────────────────────

@dataclass
class EventRecord:
    event_date: date
    event_type: str        # original label: "Vorlesung", "Prüfungsleistung", "Presentation", …
    is_exam: bool          # True if classified as an exam event
    professor: str         # from this specific event's summary
    original_summary: str  # raw SUMMARY from ICS, unmodified


@dataclass
class CourseEntry:
    code: str              # e.g. "DBE11"  — primary grouping key
    title: str             # e.g. "Entrepreneurship" (no code, no professor)
    events: list[EventRecord] = field(default_factory=list)

    @property
    def vorlesung_count(self) -> int:
        return sum(1 for e in self.events if not e.is_exam)

    @property
    def exam_count(self) -> int:
        return sum(1 for e in self.events if e.is_exam)


def build_course_map(
    all_events: list[tuple[str, dict]],
    course_keywords: list[str],
    from_date: date | None = None,
    exclude_keywords: list[str] | None = None,
) -> dict[str, CourseEntry]:
    """
    Iterate events and accumulate a course map keyed by (code, title).

    Grouping key combines course code AND title so that shared codes like
    "DBE21/31" (used by multiple Elective courses) are not collapsed together.
    Mandatory courses with unique codes (DBE11, DBE14, …) are unaffected.

    Filters applied: from_date, course_keywords, exclude_keywords.
    """
    course_map: dict[str, CourseEntry] = {}
    for _block, props in all_events:
        summary = props.get("SUMMARY", "").strip()
        if not summary:
            continue
        if from_date is not None:
            event_date = parse_event_date(props.get("DTSTART", ""))
            if event_date is None or event_date < from_date:
                continue
        if not matches_course_filter(summary, course_keywords):
            continue
        if matches_exclude(summary, exclude_keywords or []):
            continue

        event_date = parse_event_date(props.get("DTSTART", ""))
        if event_date is None:
            continue

        norm = normalize_course_name(summary)
        code = extract_course_code(norm) or "OTHER"
        if code == "OTHER":
            title = "OTHER"
            key   = "OTHER"
        else:
            title = extract_title(norm)
            key   = f"{code}::{title}"  # unique per distinct course

        if key not in course_map:
            course_map[key] = CourseEntry(code=code, title=title)

        professor = extract_professor(norm)
        exam      = is_exam(summary)
        if code == "OTHER":
            event_type = summary  # show original calendar title as the type
        elif exam:
            event_type = extract_exam_label(summary) or "Exam"
        else:
            event_type = "Vorlesung"
        course_map[key].events.append(
            EventRecord(
                event_date=event_date,
                event_type=event_type,
                is_exam=exam,
                professor=professor,
                original_summary=summary,
            )
        )

    # Sort each course's events by date
    for entry in course_map.values():
        entry.events.sort(key=lambda e: e.event_date)

    return course_map


# Day-of-week abbreviations (Monday=0)
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def print_course_list(course_map: dict[str, CourseEntry], ics_name: str) -> None:
    """Compact summary: one line per course with event counts."""
    total_v = sum(e.vorlesung_count for e in course_map.values())
    total_e = sum(e.exam_count for e in course_map.values())

    print("\n" + "=" * 68)
    print(f"  Course List  —  {ics_name}")
    print("=" * 68)
    for entry in sorted(course_map.values(), key=lambda e: (e.code, e.title)):
        tag = f"[{entry.vorlesung_count:>2}\u00d7V + {entry.exam_count:>2}\u00d7E]"
        label = f"{entry.code}  {entry.title}"
        print(f"  {label:<50}  {tag}")
    print("=" * 68)
    print(f"  {len(course_map)} courses  |  {total_v} Vorlesung  |  {total_e} Exam")
    print("=" * 68 + "\n")


def print_course_briefing(course_map: dict[str, CourseEntry]) -> None:
    """Detailed briefing: per-course table of all event dates, types, and professors."""
    DASH  = "\u2500" * 30  # ──────────────────────────────
    all_types = [ev.event_type for e in course_map.values() for ev in e.events]
    TW    = max((len(t) for t in all_types), default=18)
    TW    = max(TW, len("Type"))  # at least as wide as the header
    W     = 2 + 10 + 2 + 3 + 2 + TW + 2 + 30  # auto-fit separator width
    SEP   = "\u2550" * W   # ══════

    total_v = sum(e.vorlesung_count for e in course_map.values())
    total_e = sum(e.exam_count for e in course_map.values())

    print()
    for entry in sorted(course_map.values(), key=lambda e: (e.code, e.title)):
        print(SEP)
        print(f"  {entry.code}  \u00b7  {entry.title}")
        print(SEP)
        print(f"  {'Date':<10}  {'Day':<3}  {'Type':<{TW}}  Professor")
        print(f"  {'─'*10}  {'─'*3}  {'─'*TW}  {DASH}")
        for ev in entry.events:
            dow = _DOW[ev.event_date.weekday()]
            prof = ev.professor or "—"
            print(f"  {ev.event_date}  {dow}  {ev.event_type:<{TW}}  {prof}")
        print(f"  {'':10}  {'':3}  {'':>{TW}}  {DASH}")
        if entry.code != "OTHER":
            vc, ec = entry.vorlesung_count, entry.exam_count
            print(f"  {'':10}  {'':3}  {'':>{TW}}  {vc} \u00d7 Vorlesung   {ec} \u00d7 Exam")
        print()

    print(SEP)
    print(f"  SUMMARY")
    print(SEP)
    print(f"  {len(course_map)} courses   |   {total_v} Vorlesung total   |   {total_e} Exam total")
    print(SEP + "\n")


def format_course_briefing_md(course_map: dict[str, CourseEntry]) -> str:
    """Render the course briefing as a Markdown string."""
    lines: list[str] = []
    total_v = sum(e.vorlesung_count for e in course_map.values())
    total_e = sum(e.exam_count for e in course_map.values())

    for entry in sorted(course_map.values(), key=lambda e: (e.code, e.title)):
        is_other = entry.code == "OTHER"
        heading = "OTHER" if is_other else f"{entry.code} · {entry.title}"
        lines.append(f"## {heading}\n")

        if is_other:
            lines.append("| Date | Day | Type |")
            lines.append("|------|-----|------|")
            for ev in entry.events:
                dow = _DOW[ev.event_date.weekday()]
                lines.append(f"| {ev.event_date} | {dow} | {ev.event_type} |")
        else:
            lines.append("| Date | Day | Type | Professor |")
            lines.append("|------|-----|------|-----------|")
            for ev in entry.events:
                dow = _DOW[ev.event_date.weekday()]
                prof = ev.professor or "—"
                lines.append(f"| {ev.event_date} | {dow} | {ev.event_type} | {prof} |")
            vc, ec = entry.vorlesung_count, entry.exam_count
            lines.append(f"\n**{vc} \u00d7 Vorlesung \u00b7 {ec} \u00d7 Exam**")

        lines.append("\n---\n")

    lines.append("## Summary\n")
    lines.append(f"{len(course_map)} courses | {total_v} Vorlesung total | {total_e} Exam total\n")
    return "\n".join(lines)


# ─── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Phase 1: Load config ───────────────────────────────────────────────────
    global EXAM_KEYWORDS  # declare before any use of EXAM_KEYWORDS in this scope

    raw_cfg = load_config(Path(__file__).parent / "cal_config.toml")

    cfg_from_date        = raw_cfg.get("from_date", str(DEFAULT_FROM_DATE))
    cfg_courses          = raw_cfg.get("course_keywords", [])
    cfg_exam_keywords    = raw_cfg.get("exam_keywords", EXAM_KEYWORDS)
    cfg_exclude_keywords = raw_cfg.get("exclude_keywords", [])
    cfg_out_dir          = raw_cfg.get("out_dir", "")
    cfg_semester         = raw_cfg.get("semester", "").strip() or "target_semester"

    # Rebind module-level EXAM_KEYWORDS so is_exam() and normalize_course_name()
    # pick up the configured list automatically.
    EXAM_KEYWORDS = cfg_exam_keywords

    # ── Phase 2: Parse CLI args (None = "not provided, use config") ────────────
    parser = argparse.ArgumentParser(
        description="Filter and split an ICS calendar into Vorlesung and Exam files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Path to the input .ics file")
    parser.add_argument(
        "--from", dest="from_date", default=None, metavar="DATE",
        help="Earliest event date to include (YYYY-MM-DD). Overrides config.",
    )
    parser.add_argument(
        "--courses", default=None, metavar="KEYWORDS",
        help="Comma-separated keywords matched against SUMMARY. Overrides config.",
    )
    parser.add_argument(
        "--out-dir", default=None, metavar="DIR",
        help="Output directory. Overrides config.",
    )
    parser.add_argument(
        "--list-courses", action="store_true",
        help="Print deduplicated course+professor summary and exit (no files written).",
    )
    parser.add_argument(
        "--save", nargs="?", const=True, default=None, metavar="PATH",
        help="Save briefing as Markdown. PATH is optional; defaults to <stem>_briefing.md next to the input file.",
    )

    args = parser.parse_args()

    # ── Phase 3: Merge config + CLI (CLI wins when explicitly provided) ────────
    from_date_str   = args.from_date if args.from_date is not None else cfg_from_date
    course_keywords = (
        [k.strip() for k in args.courses.split(",") if k.strip()]
        if args.courses is not None
        else cfg_courses
    )
    out_dir_raw      = args.out_dir if args.out_dir is not None else cfg_out_dir
    exclude_keywords = cfg_exclude_keywords

    try:
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"ERROR: Invalid date '{from_date_str}'. Expected YYYY-MM-DD.")

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"ERROR: File not found: {input_path}")

    out_dir = Path(out_dir_raw) if out_dir_raw else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Phase 4: Parse ICS + build course map ─────────────────────────────────
    content = input_path.read_text(encoding="utf-8")
    header, all_events, footer = split_ics(content)

    course_map = build_course_map(all_events, course_keywords, from_date, exclude_keywords)

    if args.list_courses:
        print_course_briefing(course_map)
        if args.save is not None:
            if args.save is True:
                save_path = out_dir / f"{cfg_semester}_briefing.md"
            else:
                save_path = Path(args.save)
            md = format_course_briefing_md(course_map)
            save_path.write_text(md, encoding="utf-8")
            print(f"  Briefing saved \u2192 {save_path}\n")
        return

    print_course_list(course_map, cfg_semester)

    # ── Phase 5: Filter events by date + course keywords ──────────────────────
    vorlesung_blocks: list[str] = []
    exam_blocks: list[str] = []
    skipped_old = 0
    skipped_filter = 0

    for block_text, props in all_events:
        summary = props.get("SUMMARY", "").strip()
        event_date = parse_event_date(props.get("DTSTART", ""))

        if event_date is None or event_date < from_date:
            skipped_old += 1
            continue

        if not matches_course_filter(summary, course_keywords):
            skipped_filter += 1
            continue

        if matches_exclude(summary, exclude_keywords):
            skipped_filter += 1
            continue

        if is_exam(summary):
            exam_blocks.append(block_text)
        else:
            vorlesung_blocks.append(block_text)

    # ── Write output ICS files ─────────────────────────────────────────────────
    vorlesung_path = out_dir / f"{cfg_semester}_vorlesung.ics"
    exam_path      = out_dir / f"{cfg_semester}_exam.ics"

    vorlesung_path.write_text(build_ics(header, vorlesung_blocks, footer), encoding="utf-8")
    exam_path.write_text(build_ics(header, exam_blocks, footer), encoding="utf-8")

    # ── Print run summary ──────────────────────────────────────────────────────
    print(f"  Date filter  : >= {from_date}")
    print(f"  Course filter: {', '.join(course_keywords) if course_keywords else '(all)'}")
    print()
    print(f"  Vorlesung events : {len(vorlesung_blocks):>4}  →  {vorlesung_path}")
    print(f"  Exam events      : {len(exam_blocks):>4}  →  {exam_path}")
    print(f"  Skipped (old)    : {skipped_old:>4}")
    if skipped_filter:
        print(f"  Skipped (filter) : {skipped_filter:>4}")
    print()


if __name__ == "__main__":
    main()

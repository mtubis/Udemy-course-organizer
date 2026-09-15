#!/usr/bin/env python3
"""
Organize Udemy courses: export JSON -> LLM classification -> XLSX + HTML.

Usage:
    python udemy_organize.py udemy_courses.json
    python udemy_organize.py udemy_courses.json --out my_courses --batch-size 30
    python udemy_organize.py udemy_courses.json --no-ai      # no LLM, use Udemy categories

Working files (in CWD, safe to commit):
    taxonomy.json   - list of categories, created once and reused
    ai_cache.json   - classification results per course id; subsequent runs
                     only classify new courses

Requires: pip install anthropic openpyxl    # optional: pip install openai for OpenAI provider
Env vars: ANTHROPIC_API_KEY or OPENAI_API_KEY (required unless --no-ai), ANTHROPIC_MODEL (optional)
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

UDEMY_BASE = "https://www.udemy.com"
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

# --------------------------------------------------------------------------- #
# 1. Wczytanie i normalizacja eksportu
# --------------------------------------------------------------------------- #


def load_courses(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "results" in data:
        data = data["results"]
    if not isinstance(data, list):
        sys.exit("Nieznany format pliku – oczekiwano listy kursów lub {results: [...]}.")

    courses = []
    for c in data:
        minutes = c.get("estimated_content_length") or 0
        instructors = c.get("visible_instructors") or []
        courses.append(
            {
                "id": c.get("id"),
                "title": (c.get("title") or "").strip(),
                "url": UDEMY_BASE + (c.get("url") or ""),
                "headline": (c.get("headline") or "").strip(),
                "hours": round(minutes / 60, 1) if minutes else None,
                "content_info": c.get("content_info") or "",
                "num_lectures": c.get("num_lectures"),
                "udemy_category": (c.get("primary_category") or {}).get("title", ""),
                "udemy_subcategory": (c.get("primary_subcategory") or {}).get("title", ""),
                "instructors": ", ".join(i.get("display_name", "") for i in instructors),
                "language": (c.get("locale") or {}).get("simple_english_title", ""),
                "progress": c.get("completion_ratio"),
                "enrolled": (c.get("enrollment_time") or "")[:10],
                "is_practice_test": bool(c.get("is_practice_test_course")),
            }
        )
    # deduplicate by id (e.g. when merging active and archived exports)
    seen, unique = set(), []
    for c in courses:
        if c["id"] in seen:
            continue
        seen.add(c["id"])
        unique.append(c)
    return unique


# --------------------------------------------------------------------------- #
# 2. Warstwa LLM
# --------------------------------------------------------------------------- #


def get_client(provider: str):
    """Return a tuple (provider, client/module)."""
    provider = (provider or os.environ.get("UD_PROVIDER", "anthropic")).lower()
    if provider == "anthropic":
        try:
            import anthropic
        except ImportError:
            sys.exit("Missing package 'anthropic': pip install anthropic")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("Set ANTHROPIC_API_KEY or use --no-ai.")
        return "anthropic", anthropic
    elif provider == "openai":
        try:
            import openai
        except ImportError:
            sys.exit("Missing package 'openai': pip install openai")
        if not (os.environ.get("OPENAI_API_KEY") or getattr(openai, 'api_key', None)):
            sys.exit("Set OPENAI_API_KEY or configure openai.api_key, or use --no-ai.")
        return "openai", openai
    else:
        sys.exit(f"Unknown provider: {provider}. Choose 'anthropic' or 'openai'.")


def ask_json(provider_client, model: str, system: str, user: str, retries: int = 3):
    """Send a prompt and expect a pure JSON response; retry on parse errors.

    The Anthropic Python client API changed over versions; try multiple call
    patterns (messages.create, responses.create) and be flexible when
    extracting the returned text.
    """
    provider, client = provider_client if isinstance(provider_client, tuple) else ("anthropic", provider_client)
    last_err = None
    for attempt in range(retries):
        try:
            if provider == "anthropic":
                # Try Anthropics patterns
                try:
                    resp = client.messages.create(
                        model=model,
                        max_tokens=8000,
                        system=system,
                        messages=[{"role": "user", "content": user}],
                    )
                except TypeError:
                    try:
                        resp = client.messages.create(
                            model=model,
                            max_tokens=8000,
                            temperature=0,
                            system=system,
                            messages=[{"role": "user", "content": user}],
                        )
                    except Exception:
                        resp = client.responses.create(
                            model=model,
                            input=(system or "") + "\n\n" + user,
                            max_tokens_to_sample=8000,
                            temperature=0,
                        )

                # extract text
                if hasattr(resp, "content"):
                    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
                elif hasattr(resp, "output_text"):
                    text = resp.output_text
                else:
                    text = str(resp)

            elif provider == "openai":
                # Use openai module
                # prefer ChatCompletions (chat messages)
                try:
                    # Newer openai libs use openai.ChatCompletion.create
                    resp = client.ChatCompletion.create(
                        model=model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=0,
                        max_tokens=4000,
                    )
                    # extract
                    if hasattr(resp, "choices") and resp.choices:
                        ch = resp.choices[0]
                        text = getattr(ch, "message", {}).get("content") if hasattr(ch, "message") else ch.get("message", {}).get("content") if isinstance(ch, dict) else getattr(ch, "text", "")
                    else:
                        text = str(resp)
                except Exception:
                    # fallback to responses API if available
                    try:
                        resp = client.responses.create(
                            model=model,
                            input=(system or "") + "\n\n" + user,
                        )
                        if hasattr(resp, "output_text"):
                            text = resp.output_text
                        elif hasattr(resp, "choices") and resp.choices:
                            text = getattr(resp.choices[0], "text", "")
                        else:
                            text = str(resp)
                    except Exception as e:
                        raise
            else:
                raise RuntimeError(f"Unsupported provider: {provider}")

            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.S)
            return json.loads(text)

        except json.JSONDecodeError as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Model did not return valid JSON: {last_err}")


TAXONOMY_SYSTEM = """You are an assistant organizing a private library of online developer courses.
Respond ONLY with valid JSON, no comments and no code fences."""

TAXONOMY_PROMPT = """Below is a list of course titles from my Udemy account. Propose a coherent,
two-level taxonomy of categories in the format "Area / Subarea", e.g.
"Backend / Python & Django", "DevOps / Kubernetes", "AI / LLM & RAG", "Frontend / React",
"Business / Marketing", "Personal Development / Productivity".

Rules:
- 12–30 categories, matched to what's actually on the list
- use general area names in English where appropriate; keep technology names as-is
- avoid categories that would contain only 1 course — merge them into broader ones
- always include "Other / Unassigned" as the last category

Return JSON: {{"categories": ["Area / Subarea", ...]}}

Titles:
{titles}"""

CLASSIFY_SYSTEM = """You are an assistant organizing a private library of online courses.
Assign courses to the GIVEN list of categories (use those names exactly, no changes)
and provide tags. Respond ONLY with valid JSON, no code fences."""

CLASSIFY_PROMPT = """Allowed categories (use these strings exactly):
{taxonomy}

For each course return:
- "category": one of the allowed categories
- "tags": 3–6 short tags (technologies, topics, level: beginner/intermediate/advanced),
  lowercase, technology names in original spelling (e.g. "django", "docker", "rest api")
- "level": "beginner" | "intermediate" | "advanced" | "mixed" (if applicable)

Return JSON: {{"results": {{"<id>": {{"category": "...", "tags": [...], "level": "..."}}, ...}}}}

Courses:
{courses}"""


def build_taxonomy(client, model: str, courses: list[dict]) -> list[str]:
    titles = "\n".join(f"- {c['title']}" for c in courses)
    data = ask_json(client, model, TAXONOMY_SYSTEM, TAXONOMY_PROMPT.format(titles=titles))
    cats = [c.strip() for c in data.get("categories", []) if c.strip()]
    if not cats:
        raise RuntimeError("Model did not propose any categories.")
    if not any(x in c.lower() for c in cats for x in ("inne", "other", "unassigned")):
        cats.append("Other / Unassigned")
    return cats


def classify_batch(client, model: str, taxonomy: list[str], batch: list[dict]) -> dict:
    listing = "\n".join(
        f"id={c['id']} | {c['title']} | {c['headline']} | Udemy: {c['udemy_category']} > {c['udemy_subcategory']}"
        for c in batch
    )
    data = ask_json(
        client,
        model,
        CLASSIFY_SYSTEM,
        CLASSIFY_PROMPT.format(taxonomy="\n".join(f"- {t}" for t in taxonomy), courses=listing),
    )
    results = data.get("results", {})
    valid = set(taxonomy)
    fallback = next((t for t in taxonomy if any(x in t.lower() for x in ("inne", "other", "unassigned"))), taxonomy[-1])
    out = {}
    for c in batch:
        r = results.get(str(c["id"])) or {}
        cat = r.get("category") if r.get("category") in valid else fallback
        out[str(c["id"])] = {
            "category": cat,
            "tags": [str(t).lower().strip() for t in (r.get("tags") or [])][:6],
            "level": r.get("level") or "",
        }
    return out


def categorize(courses: list[dict], args) -> list[dict]:
    taxonomy_path = Path(args.taxonomy)
    cache_path = Path(args.cache)
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    if args.no_ai:
        for c in courses:
            c["category"] = f"{c['udemy_category']} / {c['udemy_subcategory']}".strip(" /")
            c["tags"] = ""
            c["level"] = ""
        return courses

    client = get_client(args.provider)

    if taxonomy_path.exists():
        taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        print(f"Taxonomy: {len(taxonomy)} categories from {taxonomy_path}")
    else:
        print("Building taxonomy from all titles...")
        taxonomy = build_taxonomy(client, args.model, courses)
        taxonomy_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Saved {len(taxonomy)} categories to {taxonomy_path} (you can edit manually).")

    todo = [c for c in courses if str(c["id"]) not in cache or args.reclassify]
    print(f"To classify: {len(todo)} of {len(courses)} courses (rest from cache).")

    for i in range(0, len(todo), args.batch_size):
        batch = todo[i : i + args.batch_size]
        print(f"  batch {i // args.batch_size + 1}: {len(batch)} courses...")
        cache.update(classify_batch(client, args.model, taxonomy, batch))
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    for c in courses:
        r = cache.get(str(c["id"]), {})
        c["category"] = r.get("category", "Other / Unassigned")
        c["tags"] = ", ".join(r.get("tags", []))
        c["level"] = r.get("level", "")
    return courses


# --------------------------------------------------------------------------- #
# 3. Eksport
# --------------------------------------------------------------------------- #

COLUMNS = [
    ("category", "Category"),
    ("title", "Title"),
    ("tags", "Tags"),
    ("level", "Level"),
    ("hours", "Hrs"),
    ("num_lectures", "Lectures"),
    ("progress", "Progress %"),
    ("language", "Language"),
    ("instructors", "Instructor"),
    ("udemy_category", "Udemy Cat."),
    ("udemy_subcategory", "Udemy Subcat."),
    ("enrolled", "Enrolled"),
    ("headline", "Description"),
    ("url", "Link"),
]


def write_xlsx(courses: list[dict], path: Path):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        sys.exit("Missing package 'openpyxl': pip install openpyxl")

    wb = Workbook()
    ws = wb.active
    ws.title = "Courses"
    ws.append([label for _, label in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="A435F0")
        cell.alignment = Alignment(vertical="center")

    for c in courses:
        ws.append([c.get(key) for key, _ in COLUMNS])
        row = ws.max_row
        link_cell = ws.cell(row=row, column=len(COLUMNS))
        link_cell.hyperlink = c["url"]
        link_cell.value = "open"
        link_cell.font = Font(color="0563C1", underline="single")
        title_cell = ws.cell(row=row, column=2)
        title_cell.hyperlink = c["url"]
        title_cell.font = Font(color="0563C1", underline="single")

    widths = {"Category": 32, "Title": 60, "Tags": 40, "Instructor": 28, "Description": 60, "Link": 10}
    for i, (_, label) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(label, 12)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    # summary sheet
    summary = wb.create_sheet("Summary")
    summary.append(["Category", "Course Count", "Total Hrs"])
    for cell in summary[1]:
        cell.font = Font(bold=True)
    agg: dict[str, list] = {}
    for c in courses:
        a = agg.setdefault(c["category"], [0, 0.0])
        a[0] += 1
        a[1] += c["hours"] or 0
    for cat, (n, h) in sorted(agg.items(), key=lambda kv: -kv[1][0]):
        summary.append([cat, n, round(h, 1)])
    summary.column_dimensions["A"].width = 36
    summary.column_dimensions["B"].width = 14
    summary.column_dimensions["C"].width = 16

    wb.save(path)


HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>My Udemy Courses</title>
<style>
  :root {{ --accent:#a435f0; --muted:#666; --border:#e4e4e4; }}
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px; color:#1c1d1f; background:#fafafa; }}
  h1 {{ margin: 0 0 4px; font-size: 22px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 16px; }}
  .bar {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; align-items:center; }}
  input, select {{ padding: 8px 10px; font-size: 14px; border:1px solid var(--border); border-radius:6px; background:#fff; }}
  input {{ flex: 1 1 320px; }}
  table {{ width:100%; border-collapse: collapse; background:#fff; font-size: 14px; }}
  th, td {{ text-align:left; padding: 8px 10px; border-bottom:1px solid var(--border); vertical-align: top; }}
  th {{ background:#f3f3f3; cursor:pointer; user-select:none; position: sticky; top:0; white-space: nowrap; }}
  th.sorted::after {{ content: " \\25BE"; }} th.sorted.asc::after {{ content: " \\25B4"; }}
  td.num {{ text-align:right; white-space:nowrap; }}
  a {{ color:#0b5cad; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  .cat {{ display:inline-block; background:#f0e6fb; color:#5a1f9e; border-radius:12px; padding:2px 10px; font-size:12px; white-space:nowrap; }}
  .tag {{ display:inline-block; background:#eef; border-radius:4px; padding:1px 6px; font-size:12px; margin:1px 2px 1px 0; }}
  .headline {{ color: var(--muted); font-size: 12px; margin-top: 2px; }}
  .prog {{ height:6px; background:#eee; border-radius:3px; width:70px; display:inline-block; vertical-align:middle; }}
  .prog > i {{ display:block; height:100%; background:var(--accent); border-radius:3px; }}
</style></head><body>
<h1>My Udemy Courses</h1>
<div class="meta">{count} courses · {hours} h total · generated {date}</div>
<div class="bar">
    <input id="q" type="search" placeholder="Search (title, tags, description, instructor)…" autofocus>
    <select id="cat"><option value="">All categories</option>{cat_options}</select>
    <select id="lvl"><option value="">Any level</option>
        <option>beginner</option><option>intermediate</option><option>advanced</option><option>mixed</option></select>
    <span id="shown" class="meta"></span>
</div>
<table id="t"><thead><tr>
    <th data-k="category">Category</th><th data-k="title">Course</th><th data-k="tags">Tags</th>
    <th data-k="level">Level</th><th data-k="hours" data-num>Hrs</th><th data-k="progress" data-num>Progress</th>
    <th data-k="language">Language</th><th data-k="enrolled">Enrolled</th>
</tr></thead><tbody></tbody></table>
<script>
const DATA = {data};
const tbody = document.querySelector('#t tbody');
let sortKey = 'category', sortAsc = true;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
function render() {{
  const q = document.getElementById('q').value.toLowerCase().trim();
  const cat = document.getElementById('cat').value, lvl = document.getElementById('lvl').value;
  const terms = q.split(/\\s+/).filter(Boolean);
  let rows = DATA.filter(c => {{
    if (cat && c.category !== cat) return false;
    if (lvl && c.level !== lvl) return false;
    const hay = [c.title, c.tags, c.headline, c.instructors, c.category].join(' ').toLowerCase();
    return terms.every(t => hay.includes(t));
  }});
  rows.sort((a, b) => {{
    let x = a[sortKey], y = b[sortKey];
    if (typeof x === 'number' || typeof y === 'number') {{ x = x ?? -1; y = y ?? -1; return sortAsc ? x - y : y - x; }}
    x = String(x ?? ''); y = String(y ?? '');
    return sortAsc ? x.localeCompare(y, 'en') : y.localeCompare(x, 'en');
  }});
  tbody.innerHTML = rows.map(c => `<tr>
    <td><span class="cat">${{esc(c.category)}}</span></td>
    <td><a href="${{esc(c.url)}}" target="_blank" rel="noopener">${{esc(c.title)}}</a>
        <div class="headline">${{esc(c.headline)}}${{c.instructors ? ' — ' + esc(c.instructors) : ''}}</div></td>
    <td>${{String(c.tags||'').split(',').filter(Boolean).map(t => `<span class="tag">${{esc(t.trim())}}</span>`).join('')}}</td>
    <td>${{esc(c.level)}}</td>
    <td class="num">${{c.hours ?? ''}}</td>
    <td class="num"><span class="prog"><i style="width:${{c.progress||0}}%"></i></span> ${{c.progress ?? 0}}%</td>
    <td>${{esc(c.language)}}</td><td>${{esc(c.enrolled)}}</td></tr>`).join('');
  document.getElementById('shown').textContent = `${{rows.length}} / ${{DATA.length}}`;
  document.querySelectorAll('th').forEach(th => th.classList.toggle('sorted', th.dataset.k === sortKey));
  document.querySelectorAll('th').forEach(th => th.classList.toggle('asc', th.dataset.k === sortKey && sortAsc));
}}
document.querySelectorAll('th').forEach(th => th.onclick = () => {{
  if (sortKey === th.dataset.k) sortAsc = !sortAsc; else {{ sortKey = th.dataset.k; sortAsc = !th.hasAttribute('data-num'); }}
  render();
}});
['q','cat','lvl'].forEach(id => document.getElementById(id).oninput = render);
render();
</script></body></html>"""


def write_html(courses: list[dict], path: Path):
    cats = sorted({c["category"] for c in courses})
    cat_options = "".join(f'<option value="{html.escape(c)}">{html.escape(c)}</option>' for c in cats)
    total_hours = round(sum(c["hours"] or 0 for c in courses), 1)
    page = HTML_TEMPLATE.format(
        count=len(courses),
        hours=total_hours,
        date=datetime.now().strftime("%Y-%m-%d"),
        cat_options=cat_options,
        data=json.dumps(courses, ensure_ascii=False).replace("</", "<\\/"),
    )
    path.write_text(page, encoding="utf-8")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="udemy_courses.json from browser export (DevTools console)")
    p.add_argument("--out", default="udemy_courses", help="output files prefix (without extension)")
    p.add_argument("--taxonomy", default="taxonomy.json")
    p.add_argument("--cache", default="ai_cache.json")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--provider", choices=["anthropic", "openai"], default=os.environ.get("UD_PROVIDER", "anthropic"), help="LLM provider to use")
    p.add_argument("--batch-size", type=int, default=30)
    p.add_argument("--reclassify", action="store_true", help="ignore cache and reclassify all courses")
    p.add_argument("--no-ai", action="store_true", help="no LLM — use Udemy categories")
    p.add_argument("--skip-practice-tests", action="store_true", help="skip practice test courses")
    args = p.parse_args()

    courses = load_courses(Path(args.input))
    if args.skip_practice_tests:
        courses = [c for c in courses if not c["is_practice_test"]]
    print(f"Loaded {len(courses)} courses.")

    courses = categorize(courses, args)
    courses.sort(key=lambda c: (c["category"], c["title"].lower()))

    xlsx = Path(f"{args.out}.xlsx")
    htm = Path(f"{args.out}.html")
    write_xlsx(courses, xlsx)
    write_html(courses, htm)
    print(f"Done: {xlsx} and {htm}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Render MedicalRecord chart XMLs as a browsable HTML chart view.

Read-only: the source .xml files are never modified. The only file written is
the output HTML, which defaults to a temp directory OUTSIDE the repo because it
contains the records in plaintext.

Usage:
    python3 view_charts.py                       # renders test-data/Chart
    python3 view_charts.py <file-or-dir>         # renders anything you point it at
    python3 view_charts.py <dir> -o out.html     # choose the output path
    python3 view_charts.py --open                # render, then open in the browser

Coverage is lossless: known clinical fields get the chart layout, and any
element or attribute the layout doesn't recognise -- at any depth -- is still
rendered, so a schema change can never silently hide data.
"""

import argparse
import datetime
import html
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d")

# Field names that get the redacted-looking treatment wherever they appear.
SENSITIVE_TAGS = {"ssn", "socialsecuritynumber", "mrn", "memberid"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def esc(value):
    return html.escape(str(value if value is not None else ""))


def parse_date(raw):
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def age_from(dob):
    if not dob:
        return None
    today = datetime.date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if 0 <= years < 130 else None


def text_of(node, path, default=""):
    found = node.find(path) if node is not None else None
    if found is None or found.text is None:
        return default
    return found.text.strip()


def initials(name):
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "??"
    return (parts[0][:2] if len(parts) == 1 else parts[0][0] + parts[-1][0]).upper()


def humanise(tag):
    """CamelCase / snake_case tag -> readable label."""
    out, prev_lower = [], False
    for ch in str(tag):
        if ch in "_-":
            out.append(" ")
            prev_lower = False
            continue
        if ch.isupper() and prev_lower:
            out.append(" ")
        out.append(ch)
        prev_lower = ch.islower() or ch.isdigit()
    label = "".join(out).strip()
    return label[:1].upper() + label[1:] if label else str(tag)


def field_row(label, value, sensitive=False):
    cls = ' class="sensitive"' if sensitive else ""
    shown = esc(value) if str(value or "").strip() else "&mdash;"
    return (f'<div class="field"><dt>{esc(label)}</dt>'
            f"<dd{cls}>{shown}</dd></div>")


# --------------------------------------------------------------------------
# lossless fallback rendering
# --------------------------------------------------------------------------

def generic_tree(elem, depth=0):
    """Render an arbitrary element and everything beneath it."""
    attrs = ""
    if elem.attrib:
        attrs = ' <span class="attrs">' + " ".join(
            f'<span class="attr-k">{esc(k)}</span>=<span class="attr-v">"{esc(v)}"</span>'
            for k, v in elem.attrib.items()
        ) + "</span>"

    children = list(elem)
    if not children:
        value = (elem.text or "").strip()
        return (f'<div class="gen-row" style="--d:{depth}">'
                f'<span class="gen-k">{esc(elem.tag)}</span>{attrs}'
                f'<span class="gen-v">{esc(value)}</span></div>')

    inner = "".join(generic_tree(child, depth + 1) for child in children)
    return (f'<div class="gen-row gen-parent" style="--d:{depth}">'
            f'<span class="gen-k">{esc(elem.tag)}</span>{attrs}</div>{inner}')


def extras_html(elem, known_tags=(), known_attrs=()):
    """Every attribute and child of `elem` the caller did not already render.

    Leaf children and stray attributes become normal field rows so they blend
    into the chart layout; children with structure fall back to a tree.
    """
    if elem is None:
        return ""
    known_tags, known_attrs = set(known_tags), set(known_attrs)
    rows, trees = [], []

    for key, value in elem.attrib.items():
        if key not in known_attrs:
            rows.append(field_row(f"@{key}", value))

    for child in elem:
        if child.tag in known_tags:
            continue
        if len(child) == 0:
            rows.append(field_row(
                humanise(child.tag),
                (child.text or "").strip(),
                sensitive=str(child.tag).lower() in SENSITIVE_TAGS,
            ))
            # attributes hanging off an otherwise-leaf child
            for key, value in child.attrib.items():
                rows.append(field_row(f"{humanise(child.tag)} @{key}", value))
        else:
            trees.append(generic_tree(child))

    out = ""
    if rows:
        out += f'<dl class="fields extra">{"".join(rows)}</dl>'
    if trees:
        out += f'<div class="generic extra">{"".join(trees)}</div>'
    return out


def attr_chips(elem, known_attrs=()):
    """Inline chips for unexpected attributes on an element."""
    known = set(known_attrs)
    leftover = [(k, v) for k, v in elem.attrib.items() if k not in known]
    if not leftover:
        return ""
    return " ".join(
        f'<span class="chip">{esc(k)}: {esc(v)}</span>' for k, v in leftover
    )


# --------------------------------------------------------------------------
# chart rendering
# --------------------------------------------------------------------------

def render_chart(path, root, raw_xml):
    patient = root.find("Patient")
    name = text_of(patient, "Name", "(no name)")
    record_id = root.get("patientId") or root.get("id") or ""
    dob_raw = text_of(patient, "DateOfBirth")
    dob = parse_date(dob_raw)
    age = age_from(dob)
    gender = text_of(patient, "Gender")

    dob_shown = dob_raw + (f"  \u00b7  age {age}" if age is not None else "")

    demo = "".join([
        field_row("MRN", record_id),
        field_row("Date of birth", dob_shown),
        field_row("Gender", {"F": "Female", "M": "Male"}.get(gender, gender)),
        field_row("SSN", text_of(patient, "SSN"), sensitive=True),
    ])
    # anything else on <Patient> or on the root element itself
    demo_extra = extras_html(patient,
                             known_tags={"Name", "DateOfBirth", "Gender", "SSN", "Contact"})
    root_extra = extras_html(root,
                             known_tags={"Patient", "Encounter", "Diagnoses"},
                             known_attrs={"patientId", "id"})

    contact = patient.find("Contact") if patient is not None else None
    contact_rows = "".join([
        field_row("Phone", text_of(contact, "Phone")),
        field_row("Email", text_of(contact, "Email")),
        field_row("Address", text_of(contact, "Address")),
    ])
    contact_extra = extras_html(contact, known_tags={"Email", "Phone", "Address"})

    # --- encounters -------------------------------------------------------
    encounters = root.findall("Encounter")
    encounters_html = ""
    if encounters:
        items = []
        for enc in sorted(encounters, key=lambda e: e.get("date", ""), reverse=True):
            raw_date = enc.get("date", "")
            parsed = parse_date(raw_date)
            shown = parsed.strftime("%b %d, %Y") if parsed else (raw_date or "undated")
            chips = attr_chips(enc, known_attrs={"date"})
            more = extras_html(enc, known_tags={"Provider", "Note"}, known_attrs=enc.attrib.keys())
            note = esc(text_of(enc, "Note")) or "<em>No note recorded.</em>"
            items.append(
                '<li class="encounter">'
                f'<div class="enc-date"><span class="enc-day">{esc(shown)}</span></div>'
                '<div class="enc-body">'
                f'<div class="enc-provider">{esc(text_of(enc, "Provider", "Unknown provider"))}'
                f'{(" " + chips) if chips else ""}</div>'
                f'<p class="enc-note">{note}</p>{more}</div></li>'
            )
        encounters_html = (
            f'<section class="block"><h3>Encounters<span class="count">{len(items)}</span></h3>'
            f'<ol class="timeline">{"".join(items)}</ol></section>'
        )

    # --- diagnoses --------------------------------------------------------
    dx_container = root.find("Diagnoses")
    diagnoses_html = ""
    if dx_container is not None:
        primary = dx_container.get("primaryCode", "")
        dx_list = sorted(dx_container.findall("Diagnosis"),
                         key=lambda d: int(d.get("rank") or 99))
        rows = []
        for dx in dx_list:
            code = dx.get("code", "")
            is_primary = bool(code) and code == primary
            badge = '<span class="badge">primary</span>' if is_primary else ""
            chips = attr_chips(dx, known_attrs={"code", "codeType", "rank"})
            rows.append(
                "<tr>"
                f'<td class="rank">{esc(dx.get("rank", ""))}</td>'
                f'<td class="code"><span class="code-pill{" primary" if is_primary else ""}">'
                f"{esc(code)}</span>{badge}</td>"
                f'<td class="desc">{esc((dx.text or "").strip())}'
                f'{("<br>" + chips) if chips else ""}</td>'
                f'<td class="ctype">{esc(dx.get("codeType", ""))}</td></tr>'
            )
        dx_extra = extras_html(dx_container, known_tags={"Diagnosis"},
                               known_attrs={"primaryCode"})
        if rows or dx_extra:
            table = ""
            if rows:
                table = ('<table class="dx"><thead><tr><th>#</th><th>Code</th>'
                         "<th>Description</th><th>Type</th></tr></thead>"
                         f'<tbody>{"".join(rows)}</tbody></table>')
            diagnoses_html = (
                f'<section class="block"><h3>Diagnoses<span class="count">{len(rows)}</span></h3>'
                f"{table}{dx_extra}</section>"
            )

    other_html = ""
    if root_extra:
        other_html = (f'<section class="block"><h3>Other elements</h3>{root_extra}</section>')

    blob = " ".join(filter(None, [
        name, record_id, path.name, text_of(contact, "Phone"),
        text_of(contact, "Email"), text_of(contact, "Address"),
    ])).lower()

    return f"""
<article class="chart" data-search="{esc(blob)}">
  <header class="chart-head">
    <div class="avatar">{esc(initials(name))}</div>
    <div class="who">
      <h2>{esc(name)}</h2>
      <div class="sub">{esc(record_id) or "no MRN"}</div>
    </div>
    <div class="src" title="{esc(path)}">{esc(path.name)}</div>
  </header>
  <div class="cols">
    <section class="block"><h3>Demographics</h3>
      <dl class="fields">{demo}</dl>{demo_extra}</section>
    <section class="block"><h3>Contact</h3>
      <dl class="fields">{contact_rows}</dl>{contact_extra}</section>
  </div>
  {encounters_html}{diagnoses_html}{other_html}
  <details class="raw"><summary>Raw XML</summary><pre>{esc(raw_xml)}</pre></details>
</article>
"""


CSS = """
*, *::before, *::after { box-sizing: border-box; }
:root {
  --bg: #f4f5f7; --card: #fff; --ink: #1a1d21; --muted: #6b7280;
  --line: #e3e6ea; --accent: #1f6feb; --accent-soft: #eaf2fe;
  --warn: #b4530a; --warn-soft: #fdf1e3;
}
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased; }
.wrap { max-width: 940px; margin: 0 auto; padding: 24px 20px 80px; }
.topbar { position: sticky; top: 0; z-index: 5; background: var(--bg);
  padding: 14px 0 16px; border-bottom: 1px solid var(--line); margin-bottom: 24px; }
.topbar h1 { margin: 0 0 3px; font-size: 19px; letter-spacing: -0.01em; }
.topbar .meta { color: var(--muted); font-size: 13px; }
.topbar code { background: var(--card); border: 1px solid var(--line);
  border-radius: 4px; padding: 1px 5px; font-size: 12px; }
#filter { width: 100%; margin-top: 12px; padding: 9px 12px; font-size: 14px;
  border: 1px solid var(--line); border-radius: 8px; background: var(--card);
  color: var(--ink); font-family: inherit; }
#filter:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
#empty { display: none; color: var(--muted); padding: 40px 0; text-align: center; }

.chart { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 22px; overflow: hidden; }
.chart-head { display: flex; align-items: center; gap: 13px; padding: 16px 20px;
  border-bottom: 1px solid var(--line); background: #fbfcfd; }
.avatar { width: 40px; height: 40px; flex: none; border-radius: 50%;
  background: var(--accent-soft); color: var(--accent);
  display: grid; place-items: center; font-weight: 650; font-size: 14px; }
.who { flex: 1; min-width: 0; }
.who h2 { margin: 0; font-size: 17px; letter-spacing: -0.01em; }
.who .sub { color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.src { font: 11px/1 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--muted); background: var(--bg); border: 1px solid var(--line);
  padding: 5px 8px; border-radius: 6px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; max-width: 42%; }

.cols { display: grid; grid-template-columns: 1fr 1fr; }
.cols .block { border-right: 1px solid var(--line); }
.cols .block:last-child { border-right: 0; }
.block { padding: 16px 20px; border-bottom: 1px solid var(--line); }
.chart > .block:last-of-type { border-bottom: 0; }
.block h3 { margin: 0 0 11px; font-size: 11px; font-weight: 650;
  text-transform: uppercase; letter-spacing: 0.07em; color: var(--muted); }
.count { background: var(--bg); border: 1px solid var(--line); border-radius: 20px;
  padding: 1px 7px; margin-left: 6px; font-size: 10px; letter-spacing: 0; }

.fields { margin: 0; display: grid; gap: 9px; }
.field { display: grid; grid-template-columns: 112px 1fr; gap: 10px; align-items: baseline; }
.field dt { color: var(--muted); font-size: 12.5px; }
.field dd { margin: 0; word-break: break-word; }
.sensitive { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px; background: var(--warn-soft); color: var(--warn);
  padding: 1px 6px; border-radius: 4px; display: inline-block; }
.extra { margin-top: 9px; padding-top: 9px; border-top: 1px dashed var(--line); }
.extra .field dt::after { content: " *"; color: var(--accent); }

.timeline { list-style: none; margin: 0; padding: 0; }
.encounter { display: grid; grid-template-columns: 116px 1fr; gap: 14px; padding: 11px 0; }
.encounter + .encounter { border-top: 1px dashed var(--line); }
.enc-day { font-size: 12.5px; color: var(--accent); font-weight: 600;
  font-variant-numeric: tabular-nums; }
.enc-provider { font-weight: 600; font-size: 14px; margin-bottom: 3px; }
.enc-note { margin: 0; color: #384049; font-size: 14px; }
.chip { display: inline-block; font-size: 11px; font-weight: 500; color: var(--muted);
  background: var(--bg); border: 1px solid var(--line);
  border-radius: 20px; padding: 1px 8px; margin-left: 4px; vertical-align: middle; }

.dx { width: 100%; border-collapse: collapse; font-size: 14px; }
.dx th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--muted); font-weight: 600; padding: 0 10px 7px 0; border-bottom: 1px solid var(--line); }
.dx td { padding: 9px 10px 9px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
.dx tr:last-child td { border-bottom: 0; }
.dx .rank { color: var(--muted); width: 26px; font-variant-numeric: tabular-nums; }
.dx .code { width: 138px; white-space: nowrap; }
.code-pill { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px; background: var(--bg); border: 1px solid var(--line);
  padding: 2px 6px; border-radius: 5px; }
.code-pill.primary { background: var(--accent-soft); border-color: #c3dafc; color: var(--accent); }
.badge { margin-left: 6px; font-size: 9.5px; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--accent); font-weight: 650; }
.dx .ctype { color: var(--muted); font-size: 12.5px; white-space: nowrap; }

.generic { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.gen-row { padding: 2px 0; padding-left: calc(var(--d) * 16px); }
.gen-k { color: var(--accent); }
.gen-v { color: var(--ink); margin-left: 8px; }
.gen-parent .gen-k { font-weight: 650; }
.attrs { color: var(--muted); }
.attr-v { color: var(--warn); }

.raw { border-top: 1px solid var(--line); background: #fbfcfd; }
.raw summary { cursor: pointer; padding: 11px 20px; font-size: 12px;
  color: var(--muted); user-select: none; list-style: none; }
.raw summary::-webkit-details-marker { display: none; }
.raw summary::before { content: "\\25b8  "; }
.raw[open] summary::before { content: "\\25be  "; }
.raw summary:hover { color: var(--accent); }
.raw pre { margin: 0; padding: 0 20px 18px; overflow-x: auto;
  font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color: #384049; }

@media (max-width: 680px) {
  .cols { grid-template-columns: 1fr; }
  .cols .block { border-right: 0; }
  .encounter { grid-template-columns: 1fr; gap: 4px; }
  .field { grid-template-columns: 96px 1fr; }
  .src { display: none; }
}
@media print {
  body { background: #fff; }
  .topbar, .raw { display: none; }
  .chart { break-inside: avoid; border-color: #ccc; margin-bottom: 14px; }
}
"""

JS = """
const filter = document.getElementById('filter');
const charts = Array.from(document.querySelectorAll('.chart'));
const empty = document.getElementById('empty');
filter.addEventListener('input', () => {
  const q = filter.value.trim().toLowerCase();
  let shown = 0;
  charts.forEach(c => {
    const hit = !q || c.dataset.search.includes(q);
    c.style.display = hit ? '' : 'none';
    if (hit) shown++;
  });
  empty.style.display = shown ? 'none' : 'block';
});
"""


# --------------------------------------------------------------------------
# output location / opening
# --------------------------------------------------------------------------

def is_wsl():
    version = Path("/proc/version")
    if not version.exists():
        return False
    return "microsoft" in version.read_text(errors="ignore").lower()


def default_output():
    """Somewhere outside the repo that a Windows browser can still reach."""
    if is_wsl():
        for base in sorted(Path("/mnt/c/Users").glob("*/AppData/Local/Temp")):
            if os.access(base, os.W_OK):
                return base / "chart_view.html"
    return Path(tempfile.gettempdir()) / "chart_view.html"


def windows_path(path):
    """/mnt/c/foo -> C:\\foo, else None."""
    parts = path.resolve().parts
    if len(parts) > 2 and parts[1] == "mnt" and len(parts[2]) == 1:
        return f"{parts[2].upper()}:\\" + "\\".join(parts[3:])
    return None


def open_in_browser(path):
    win = windows_path(path)
    if win:
        subprocess.run(["explorer.exe", win], check=False)
        return True
    import webbrowser
    return webbrowser.open(path.as_uri())


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Render MedicalRecord chart XMLs as browsable HTML (read-only).")
    ap.add_argument("target", nargs="?", default="test-data/Chart",
                    help="XML file or directory to render (default: test-data/Chart)")
    ap.add_argument("-o", "--out", default=None,
                    help="output HTML path (default: a temp dir outside the repo)")
    ap.add_argument("--open", action="store_true", dest="do_open",
                    help="open the result in your browser when done")
    args = ap.parse_args()

    target = Path(args.target)
    if target.is_file():
        files = [target]
    elif target.is_dir():
        files = sorted(target.rglob("*.xml"))
    else:
        sys.exit(f"error: no such file or directory: {target}")
    if not files:
        sys.exit(f"error: no .xml files found under {target}")

    out = Path(args.out) if args.out else default_output()
    out.parent.mkdir(parents=True, exist_ok=True)

    body, failures = [], []
    for path in files:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            failures.append((path, exc))
            body.append(
                f'<article class="chart" data-search="{esc(path.name).lower()}">'
                '<header class="chart-head"><div class="avatar">!</div>'
                f'<div class="who"><h2>Could not parse</h2><div class="sub">{esc(exc)}</div></div>'
                f'<div class="src">{esc(path.name)}</div></header>'
                f'<details class="raw" open><summary>Raw file</summary>'
                f"<pre>{esc(raw)}</pre></details></article>"
            )
            continue
        body.append(render_chart(path, root, raw))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chart view &mdash; {esc(target)}</title>
<style>{CSS}</style>
</head><body>
<div class="wrap">
  <div class="topbar">
    <h1>Chart view</h1>
    <div class="meta">{len(files)} record{"s" if len(files) != 1 else ""} from
      <code>{esc(target)}</code> &middot; generated {stamp}</div>
    <input id="filter" type="search" autocomplete="off"
           placeholder="Filter by name, MRN, phone, file&hellip;">
  </div>
  {"".join(body)}
  <div id="empty">No charts match that filter.</div>
</div>
<script>{JS}</script>
</body></html>
"""
    out.write_text(doc, encoding="utf-8")

    print(f"Rendered {len(files) - len(failures)}/{len(files)} charts")
    print(f"  -> {out.resolve()}")
    win = windows_path(out)
    if win:
        print(f"  -> {win}   (paste into your browser)")
    for path, exc in failures:
        print(f"  ! parse failed: {path} ({exc})", file=sys.stderr)
    if args.do_open:
        open_in_browser(out)


if __name__ == "__main__":
    main()

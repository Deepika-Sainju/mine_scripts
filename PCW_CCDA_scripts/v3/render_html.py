#!/usr/bin/env python3
r"""
render_html.py — Render C-CDA XML files to HTML with the HL7 CDA stylesheet (xsl\cda.xsl next to this script).

Used by flatfile_to_ccda.py --render-html, or on its own:
    python render_html.py "C:\data\PCW\ccda_output"                 -> C:\data\PCW\ccda_output_html\[<RGN_CD>\]<stem>.html
    python render_html.py "C:\data\PCW\ccda_output" -o "C:\data\html"
    python render_html.py "C:\data\PCW\ccda_output\MA_12345678.xml" -> a single file

Needs lxml (Python's standard library has no XSLT):  pip install lxml
A folder is searched recursively; region subfolders (<out>\MA\...) are mirrored under the HTML folder.
The stylesheet reads xsl\assets\cda_l10n.xml for its labels, so keep the xsl folder intact, either next to this script
or one level up (the shared PCW_CCDA_scripts\xsl).
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_XSL = next((p for p in (os.path.join(HERE, "xsl", "cda.xsl"), os.path.join(HERE, os.pardir, "xsl", "cda.xsl"))
                    if os.path.isfile(p)), os.path.join(HERE, "xsl", "cda.xsl"))


class RenderSetupError(Exception):
    """lxml missing or stylesheet not found — nothing can be rendered."""


def html_dir_for(xml_dir):
    """<folder> -> <folder>_html, a sibling folder (trailing separators ignored)."""
    return os.path.normpath(xml_dir) + "_html"


def make_renderer(xsl_path=DEFAULT_XSL):
    """Compile the stylesheet once and return render(xml_path, html_path).  Raises RenderSetupError up front, so a
    caller can check before doing any other work."""
    try:
        from lxml import etree
    except ImportError:
        raise RenderSetupError("HTML rendering needs the lxml package. Install it with:  pip install lxml") from None
    if not os.path.isfile(xsl_path):
        raise RenderSetupError(f"stylesheet not found: {xsl_path}")
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    xslt = etree.XSLT(etree.parse(xsl_path, parser), access_control=etree.XSLTAccessControl(read_file=True))

    def render(xml_path, html_path):
        result = xslt(etree.parse(xml_path, parser))
        for e in xslt.error_log:  # xsl:message output (e.g. "attr is not legal in td") — informational
            if "is not legal in" not in e.message:
                print(f"  [xslt] {os.path.basename(xml_path)}: {e.message}", file=sys.stderr)
        with open(html_path, "wb") as f:
            f.write(etree.tostring(result, pretty_print=True, method="html", encoding="utf-8",
                                   doctype=result.docinfo.doctype))

    return render


def render_files(xml_paths, html_dir, render):
    """Render each XML to <html_dir>/<stem>.html.  One bad file does not stop the rest.  Returns (ok, failed)."""
    os.makedirs(html_dir, exist_ok=True)
    ok, failed = 0, []
    for xml_path in xml_paths:
        html_path = os.path.join(html_dir, os.path.splitext(os.path.basename(xml_path))[0] + ".html")
        try:
            render(xml_path, html_path)
            ok += 1
        except Exception as exc:  # report and carry on with the next member
            failed.append(xml_path)
            print(f"ERROR: could not render {xml_path}: {exc}", file=sys.stderr)
    return ok, failed


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Render C-CDA XML file(s) to HTML with cda.xsl")
    ap.add_argument("input", help="a C-CDA .xml file, or a folder of them")
    ap.add_argument("-o", "--out-dir", help="HTML folder (default: <input folder>_html)")
    ap.add_argument("--xsl", default=DEFAULT_XSL, help="stylesheet (default: xsl\\cda.xsl next to this script)")
    args = ap.parse_args(argv)
    src = os.path.normpath(args.input.strip().strip('"').strip())  # cmd.exe: "C:\dir\" arrives as C:\dir"
    if os.path.isdir(src):
        xml_paths = sorted(glob.glob(os.path.join(src, "**", "*.xml"), recursive=True))
        default_out = html_dir_for(src)
    elif os.path.isfile(src):
        xml_paths = [src]
        default_out = html_dir_for(os.path.dirname(src) or ".")
    else:
        ap.error(f"not found: {src}")
    if not xml_paths:
        ap.error(f"no .xml files in {src}")
    try:
        render = make_renderer(args.xsl)
    except RenderSetupError as exc:
        ap.error(str(exc))
    html_dir = os.path.normpath(args.out_dir.strip().strip('"')) if args.out_dir else default_out
    ok, failed = 0, []
    by_dir = {}  # keep each region subfolder separate in the HTML output
    for p in xml_paths:
        rel = os.path.relpath(os.path.dirname(p), src) if os.path.isdir(src) else "."
        by_dir.setdefault(rel, []).append(p)
    for rel, paths in by_dir.items():
        n, f = render_files(paths, os.path.normpath(os.path.join(html_dir, rel)), render)
        ok, failed = ok + n, failed + f
    print(f"Rendered {ok} of {len(xml_paths)} file(s) to {html_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

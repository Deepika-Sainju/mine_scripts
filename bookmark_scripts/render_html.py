#!/usr/bin/env python3
"""Render a CDA XML to HTML with the (unmodified) HL7 cda.xsl using lxml.

Usage: python3 render_html.py input.xml output.html [--xsl xsl/cda.xsl]
"""
import argparse
import sys
from pathlib import Path

from lxml import etree

HERE = Path(__file__).resolve().parent


def render(xml_path: str, html_path: str, xsl_path: str) -> None:
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    xslt = etree.XSLT(etree.parse(xsl_path, parser), access_control=etree.XSLTAccessControl(read_file=True))
    src = etree.parse(xml_path, parser)
    result = xslt(src)
    for e in xslt.error_log:  # xsl:message output (e.g. "attr is not legal in td") — informational
        if "is not legal in" not in e.message:
            print(f"  [xslt] {e.message}", file=sys.stderr)
    Path(html_path).write_bytes(etree.tostring(result, pretty_print=True, method="html", encoding="utf-8", doctype=result.docinfo.doctype))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--xsl", default=str(HERE / "xsl" / "cda.xsl"))
    a = ap.parse_args(argv)
    render(a.input, a.output, a.xsl)
    print(f"wrote {a.output} ({Path(a.output).stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

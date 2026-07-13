#!/usr/bin/env python3
"""
De-identify member / claim / provider CSV files for safe external sharing.

Consistent pseudonymization: the same identifier (e.g. a member id) is always
replaced by the same token across every file, so member<->claim<->provider
joins still work after de-identification.

Two-step, review-first workflow (stdlib only, no pip installs needed):

  1) INIT  -- inspect the files and write a config you can eyeball/edit:
       python deidentify_csv.py init \
           --member  /path/member.csv \
           --claim   /path/claim.csv \
           --provider /path/provider.csv \
           --config  deid_config.json

     Open deid_config.json and confirm/adjust the action chosen for each
     column (see ACTIONS below). This is your chance to catch a PHI column
     that auto-detection missed BEFORE anything is shared.

  2) RUN   -- apply the config and produce de-identified copies:
       python deidentify_csv.py run --config deid_config.json --outdir deid_out

     Outputs (in --outdir):
       <name>.deid.csv          one per input file -- SAFE to share
       crosswalk.json           original <-> token map + secrets -- DO NOT SHARE

The crosswalk is what lets a re-run stay consistent and lets you re-link to
the originals internally. Keep it private (treat it like the raw PHI).

ACTIONS (per column):
  keep             leave the value unchanged
  redact           blank the value out
  pseudonym:NS     replace with a stable token in namespace NS
                   (member / claim / provider / or any custom name)
  name             replace with a stable fake human name
  date:shift       shift the date by a stable per-entity offset (HIPAA-style
                   date shifting; preserves intervals/age, hides real dates)
  date:year        keep only the year (YYYY-01-01)
  date:remove      blank the date
  mask             show only the last 4 chars, e.g. SSN -> ***-**-6789
  zip3             keep first 3 ZIP digits (000 for HIPAA-restricted prefixes)
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sys

# --------------------------------------------------------------------------
# Auto-detection: map a column header to a default action by name pattern.
# Order matters -- first match wins. Edit the generated config to override.
# --------------------------------------------------------------------------
DETECT_RULES = [
    (r"\bnpi\b|provider.*npi",                          "pseudonym:provider"),
    (r"provider.*(id|num|no|key)|prov.*id|rendering|billing.*prov|servicing.*prov", "pseudonym:provider"),
    (r"\btin\b|tax.*id|ein",                            "mask"),
    (r"claim.*(id|num|no|line|key)|clm.*(id|num|no)|claimid", "pseudonym:claim"),
    (r"member.*id|memberid|mbr.*id|subscriber.*id|patient.*id|person.*id|enrollee.*id|insured.*id", "pseudonym:member"),
    (r"\bssn\b|social.*sec",                            "mask"),
    (r"\bmbi\b|medicare.*(id|num)|hicn|hic.*num",       "mask"),
    (r"dob|date.*birth|birth.*date|birthdate",          "date:shift"),
    (r"date|dos|svc.*dt|service.*dt|admit|discharge|paid|from.*dt|to.*dt|thru", "date:shift"),
    (r"first.*name|last.*name|middle.*name|full.*name|fname|lname|\bname\b|mname", "name"),
    (r"email",                                          "redact"),
    (r"phone|fax|mobile|cell|\btel\b",                  "mask"),
    (r"addr|street|address",                            "redact"),
    (r"\bcity\b",                                        "redact"),
    (r"zip|postal",                                     "zip3"),
]

# ZIP3 prefixes restricted to "000" under HIPAA Safe Harbor (low population).
RESTRICTED_ZIP3 = {
    "036", "059", "063", "102", "203", "556", "692", "790",
    "821", "823", "830", "831", "878", "879", "884", "890", "893",
}

DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d",
    "%d-%b-%Y", "%d-%b-%y", "%Y%m%d", "%m-%d-%Y",
    "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
]


def detect_action(header):
    h = header.strip().lower()
    for pattern, action in DETECT_RULES:
        if re.search(pattern, h):
            return action
    return "keep"


def sniff_headers(path):
    with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(8192)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.get_dialect("excel")
        reader = csv.reader(f, dialect)
        headers = next(reader, [])
        delim = dialect.delimiter
    return headers, delim


# --------------------------------------------------------------------------
# INIT
# --------------------------------------------------------------------------
def cmd_init(args):
    files = []
    for role, path in (("member", args.member), ("claim", args.claim),
                       ("provider", args.provider)):
        if not path:
            continue
        if not os.path.isfile(path):
            sys.exit(f"ERROR: {role} file not found: {path}")
        headers, delim = sniff_headers(path)
        columns = {h: detect_action(h) for h in headers}
        files.append({
            "role": role,
            "path": os.path.abspath(path),
            "delimiter": delim,
            # For date:shift, dates are offset by an amount tied to this column's
            # value (so all of a member's dates move together). Falls back to a
            # global offset if the key column is blank/missing.
            "date_shift_key": _guess_member_key(headers),
            "columns": columns,
        })

    if not files:
        sys.exit("ERROR: pass at least one of --member / --claim / --provider")

    config = {
        "_README": "Review every column action before running. See ACTIONS in "
                   "deidentify_csv.py. Run: python deidentify_csv.py run "
                   "--config <this file> --outdir deid_out",
        "files": files,
    }
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"Wrote config -> {args.config}\n")
    for fc in files:
        print(f"[{fc['role']}] {fc['path']}  (delimiter={fc['delimiter']!r})")
        for col, act in fc["columns"].items():
            flag = "" if act == "keep" else "  <-- de-identified"
            print(f"    {col:<30} {act}{flag}")
        print(f"    date_shift_key = {fc['date_shift_key']}\n")
    print("Next: review/edit the config, then run:")
    print(f"  python {os.path.basename(__file__)} run --config {args.config} --outdir deid_out")


def _guess_member_key(headers):
    for h in headers:
        if re.search(r"member.*id|memberid|mbr.*id|subscriber.*id|patient.*id|person.*id", h.lower()):
            return h
    return None


# --------------------------------------------------------------------------
# Crosswalk (shared, persistent, consistent across files and re-runs)
# --------------------------------------------------------------------------
class Crosswalk:
    NAME_PREFIX = {"member": "MBR", "claim": "CLM", "provider": "PRV"}
    FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
             "Avery", "Quinn", "Skyler", "Cameron", "Drew", "Reese", "Rowan",
             "Sage", "Blake", "Hayden", "Emerson", "Finley", "Harper"]
    LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
            "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez",
            "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
            "Jackson", "Martin"]

    def __init__(self, path):
        self.path = path
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        self.salt = data.get("salt") or secrets.token_hex(16)
        self.maps = data.get("maps", {})          # namespace -> {original: token}
        self.counters = data.get("counters", {})  # namespace -> int
        self.names = data.get("names", {})         # original -> fake name
        self.shifts = data.get("shifts", {})       # key -> day offset

    def pseudonym(self, namespace, value):
        value = "" if value is None else str(value)
        if value == "":
            return ""
        ns = self.maps.setdefault(namespace, {})
        if value not in ns:
            self.counters[namespace] = self.counters.get(namespace, 0) + 1
            prefix = self.NAME_PREFIX.get(namespace, namespace.upper()[:3] or "ID")
            ns[value] = f"{prefix}_{self.counters[namespace]:08d}"
        return ns[value]

    def fake_name(self, value):
        value = "" if value is None else str(value).strip()
        if value == "":
            return ""
        if value not in self.names:
            d = hashlib.sha256((self.salt + "|name|" + value).encode()).digest()
            self.names[value] = f"{self.FIRST[d[0] % len(self.FIRST)]} {self.LAST[d[1] % len(self.LAST)]}"
        return self.names[value]

    def date_offset(self, key):
        """Stable per-key offset in [-364, +364] days. Global if key is blank."""
        key = "__global__" if not key else str(key)
        if key not in self.shifts:
            d = hashlib.sha256((self.salt + "|shift|" + key).encode()).digest()
            n = int.from_bytes(d[:4], "big")
            self.shifts[key] = (n % 729) - 364
        return self.shifts[key]

    def save(self):
        if not self.path:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "_WARNING": "CONTAINS RE-IDENTIFICATION DATA -- DO NOT SHARE.",
                "salt": self.salt,
                "counters": self.counters,
                "maps": self.maps,
                "names": self.names,
                "shifts": self.shifts,
            }, f, indent=2)


# --------------------------------------------------------------------------
# Per-value transforms
# --------------------------------------------------------------------------
def parse_date(value):
    v = value.strip()
    if not v:
        return None, None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(v, fmt), fmt
        except ValueError:
            continue
    return None, None


def apply_action(action, value, xwalk, shift_key_value):
    value = "" if value is None else value
    if action == "keep":
        return value
    if action == "redact" or action == "date:remove":
        return ""
    if action.startswith("pseudonym:"):
        return xwalk.pseudonym(action.split(":", 1)[1], value)
    if action == "name":
        return xwalk.fake_name(value)
    if action == "mask":
        s = str(value)
        tail = s[-4:] if len(s) > 4 else s
        return ("*" * max(0, len(s) - len(tail))) + tail if s else ""
    if action == "zip3":
        digits = re.sub(r"\D", "", str(value))
        if len(digits) < 3:
            return ""
        p3 = digits[:3]
        return "000" if p3 in RESTRICTED_ZIP3 else p3
    if action.startswith("date:"):
        parsed, fmt = parse_date(str(value))
        if parsed is None:
            return value  # not a parseable date; leave as-is
        if action == "date:year":
            return parsed.replace(month=1, day=1).strftime(fmt)
        if action == "date:shift":
            offset = xwalk.date_offset(shift_key_value)
            return (parsed + dt.timedelta(days=offset)).strftime(fmt)
        return value
    # Unknown action -> fail loud so a typo in the config can't leak PHI.
    raise ValueError(f"Unknown action: {action!r}")


# --------------------------------------------------------------------------
# RUN
# --------------------------------------------------------------------------
def cmd_run(args):
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    files = config.get("files", [])
    if not files:
        sys.exit("ERROR: config has no 'files' entry")

    os.makedirs(args.outdir, exist_ok=True)
    xwalk = Crosswalk(os.path.join(args.outdir, "crosswalk.json"))

    for fc in files:
        path = fc["path"]
        delim = fc.get("delimiter", ",")
        col_actions = fc["columns"]
        shift_key = fc.get("date_shift_key")
        base = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(args.outdir, f"{base}.deid.csv")

        rows_in = rows_out = 0
        with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as fin, \
             open(out_path, "w", newline="", encoding="utf-8") as fout:
            reader = csv.DictReader(fin, delimiter=delim)
            fieldnames = reader.fieldnames or []
            writer = csv.DictWriter(fout, fieldnames=fieldnames, delimiter=",")
            writer.writeheader()
            for row in reader:
                rows_in += 1
                skv = row.get(shift_key) if shift_key else None
                # Resolve the shift key to its member-namespace pseudonym base so
                # the offset is stable per member even though we never store the
                # real id in the output.
                new_row = {}
                for col in fieldnames:
                    action = col_actions.get(col, "keep")
                    new_row[col] = apply_action(action, row.get(col, ""), xwalk, skv)
                writer.writerow(new_row)
                rows_out += 1

        print(f"[{fc.get('role','?')}] {path}\n    -> {out_path}  ({rows_out} rows)")

    xwalk.save()
    print(f"\nCrosswalk -> {xwalk.path}  (DO NOT SHARE this file)")
    print(f"De-identified CSVs in: {args.outdir}  (safe to share)")


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="De-identify member/claim/provider CSVs.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="inspect files and write a reviewable config")
    p_init.add_argument("--member")
    p_init.add_argument("--claim")
    p_init.add_argument("--provider")
    p_init.add_argument("--config", default="deid_config.json")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="apply the config and write de-identified CSVs")
    p_run.add_argument("--config", default="deid_config.json")
    p_run.add_argument("--outdir", default="deid_out")
    p_run.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()




############## running commands 
# python deidentify_csv.py init --member  "ECLAT_IWH_Membership_File.csv" --claim "ECLAT_IWH_Claims_File_Additional_Practices.csv" --provider ECLAT_IWH_Provider_File.csv --config  deid_config.json

# python deidentify_csv.py run --config deid_config.json --outdir deid_out
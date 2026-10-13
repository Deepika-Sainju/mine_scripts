#!/usr/bin/env python3
"""Rename every .none file in a folder to <original>_<SUFFIX>.none"""

import os

# ---- config ----
FOLDER = r"C:\Users\deepika.sainju\Documents\Bookmark\20260915\transformed_ccdas_20260915"
SUFFIX = "20260912"          # the value that goes after the underscore
EXTENSION = ".none"
DRY_RUN = False              # set False to actually rename
# ----------------


def rename_files(folder, suffix, extension, dry_run=True):
    if not os.path.isdir(folder):
        raise NotADirectoryError("Not a directory: %s" % folder)

    renamed = skipped = 0

    for name in sorted(os.listdir(folder)):
        src = os.path.join(folder, name)

        if not os.path.isfile(src):
            continue
        if not name.endswith(extension):
            continue

        stem, ext = os.path.splitext(name)

        # don't double-tag a file that already ends with _<suffix>
        if stem.endswith("_" + suffix):
            print("SKIP (already tagged): %s" % name)
            skipped += 1
            continue

        new_name = "%s_%s%s" % (stem, suffix, ext)
        dst = os.path.join(folder, new_name)

        if os.path.exists(dst):
            print("SKIP (target exists):  %s -> %s" % (name, new_name))
            skipped += 1
            continue

        print("%s: %s -> %s" % ("WOULD RENAME" if dry_run else "RENAMED", name, new_name))
        if not dry_run:
            os.rename(src, dst)
        renamed += 1

    print("\n%d file(s) %s, %d skipped." % (renamed, "to rename" if dry_run else "renamed", skipped))


if __name__ == "__main__":
    rename_files(FOLDER, SUFFIX, EXTENSION, DRY_RUN)

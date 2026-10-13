"""Remove a given string from subfolder and .none file names in a folder.

Only the top level of MAIN_FOLDER is touched -- files inside the subfolders
are left completely alone.

    STRING_TO_REMOVE = "_20260905"

    mainfolder/
      1234_20260905/          ->  1234/
        report_20260905.csv   ->  unchanged
      1234_20260905.none      ->  1234.none

Usage:
    1. Set MAIN_FOLDER and STRING_TO_REMOVE below (or pass them on the
       command line).
    2. Run once as-is to see the dry-run output.
    3. Re-run with --apply to actually rename.
"""

import os
import sys

MAIN_FOLDER = r"C:\Users\deepika.sainju\Documents\Bookmark\20260905\CCDAs_formatted_wo_date"
STRING_TO_REMOVE = "_20260905"

FILE_EXT = ".none"     # which files to rename; set to None to rename every file


def strip_string(main_folder, string_to_remove, dry_run=True):
    renamed = skipped = 0

    # listdir() is non-recursive: only the top level is touched.
    for name in sorted(os.listdir(main_folder)):
        old_path = os.path.join(main_folder, name)

        if os.path.isdir(old_path):
            stem, ext = name, ""
        else:
            stem, ext = os.path.splitext(name)
            if FILE_EXT is not None and ext.lower() != FILE_EXT.lower():
                continue               # leave anything else alone

        stripped = stem.replace(string_to_remove, "")
        if stripped == stem:
            continue                   # string not present
        if not stripped:
            print("SKIP (name would be empty): {}".format(name))
            skipped += 1
            continue

        new_name = stripped + ext
        new_path = os.path.join(main_folder, new_name)

        if os.path.exists(new_path):
            print("SKIP (target exists): {} -> {}".format(name, new_name))
            skipped += 1
            continue

        print("{}{} -> {}".format("[dry-run] " if dry_run else "", name, new_name))
        if not dry_run:
            os.rename(old_path, new_path)
        renamed += 1

    print("\n{} renamed{}, {} skipped".format(
        renamed, " (dry run)" if dry_run else "", skipped))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--apply"]
    folder = args[0] if len(args) > 0 else MAIN_FOLDER
    text = args[1] if len(args) > 1 else STRING_TO_REMOVE
    dry_run = "--apply" not in sys.argv[1:]

    if not os.path.isdir(folder):
        sys.exit("Not a folder: {}".format(folder))
    if not text:
        sys.exit("STRING_TO_REMOVE is empty - nothing to remove.")

    print("Folder : {}".format(folder))
    print("Removing: {!r}".format(text))
    if dry_run:
        print("DRY RUN - nothing will be renamed. Re-run with --apply to commit.")
    print("")

    strip_string(folder, text, dry_run=dry_run)

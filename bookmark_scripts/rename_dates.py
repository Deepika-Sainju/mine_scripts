"""
Rename subfolders and their matching .none files in a parent folder,
changing an old date suffix to a new one.

    parent_folder/
        1234_20260914/      ->  1234_20260912/
            abc.xml         (untouched)
            xyz.xml         (untouched)
        1234_20260914.none  ->  1234_20260912.none

Only the top-level subfolder names and .none file names are changed.
Nothing inside the subfolders is touched.
"""

import os

# ---------------------------------------------------------------- settings --
PARENT_FOLDER = r"C:\Users\deepika.sainju\Documents\Bookmark\20260915\transformed_ccdas_20260915"   # <-- put your parent folder here

OLD_DATE = "20260910"   # date currently in the names
NEW_DATE = "20260915"   # date you want instead

DRY_RUN = False          # True  = only print what would happen
                        # False = actually rename
# -----------------------------------------------------------------------------

OLD_SUFFIX = "_" + OLD_DATE
NEW_SUFFIX = "_" + NEW_DATE


def new_name_for(name, is_dir):
    """Return the new name if `name` should be renamed, otherwise None."""
    if is_dir:
        base, ext = name, ""
    else:
        base, ext = os.path.splitext(name)
        if ext.lower() != ".none":
            return None

    if not base.endswith(OLD_SUFFIX):
        return None

    return base[: -len(OLD_SUFFIX)] + NEW_SUFFIX + ext


def main():
    if not os.path.isdir(PARENT_FOLDER):
        print("ERROR: parent folder not found: %s" % PARENT_FOLDER)
        return

    print("Parent folder : %s" % PARENT_FOLDER)
    print("Rename        : %s -> %s" % (OLD_SUFFIX, NEW_SUFFIX))
    print("Mode          : %s" % ("DRY RUN (nothing will change)" if DRY_RUN else "LIVE"))
    print("-" * 70)

    renamed = 0
    skipped = 0
    errors = 0

    for name in sorted(os.listdir(PARENT_FOLDER)):
        old_path = os.path.join(PARENT_FOLDER, name)
        is_dir = os.path.isdir(old_path)

        new_name = new_name_for(name, is_dir)
        if new_name is None:
            continue

        new_path = os.path.join(PARENT_FOLDER, new_name)
        kind = "DIR " if is_dir else "FILE"

        if os.path.exists(new_path):
            print("[SKIP]   %s %s  ->  %s  (target already exists)" % (kind, name, new_name))
            skipped += 1
            continue

        if DRY_RUN:
            print("[DRY]    %s %s  ->  %s" % (kind, name, new_name))
            renamed += 1
        else:
            try:
                os.rename(old_path, new_path)
                print("[RENAME] %s %s  ->  %s" % (kind, name, new_name))
                renamed += 1
            except OSError as exc:
                print("[ERROR]  %s %s  ->  %s  (%s)" % (kind, name, new_name, exc))
                errors += 1

    print("-" * 70)
    print("%s: %d   Skipped: %d   Errors: %d"
          % ("Would rename" if DRY_RUN else "Renamed", renamed, skipped, errors))


if __name__ == "__main__":
    main()

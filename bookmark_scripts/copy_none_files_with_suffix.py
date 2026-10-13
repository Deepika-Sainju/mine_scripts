"""Copy every .none file from a source folder to a destination folder,
adding a suffix before the extension.

    SOURCE_FOLDER/12345.none  ->  DESTINATION_FOLDER/12345_20260917.none

Only the top level of SOURCE_FOLDER is read; subfolders are not touched.
"""

import os
import shutil

# --- set these ---------------------------------------------------------------
SOURCE_FOLDER = r'C:\Users\deepika.sainju\Documents\Bookmark\20260917\CCDAs'       # folder containing the .none files
DESTINATION_FOLDER = r'C:\Users\deepika.sainju\Documents\Bookmark\20260917\transformed_ccdas_20260917'  # folder the renamed copies go into
SUFFIX = '20260917'       # goes after an underscore: 12345.none -> 12345_20260917.none

EXTENSION = '.none'
OVERWRITE = False         # False = skip a file that already exists in the destination
DRY_RUN = False            # True  = only print what would happen, copy nothing
                          # False = actually copy
# -----------------------------------------------------------------------------

if not SOURCE_FOLDER or not os.path.isdir(SOURCE_FOLDER):
    print(f"Error: Source folder not found or path not set: {SOURCE_FOLDER}")
    exit()

if not DESTINATION_FOLDER:
    print("Error: DESTINATION_FOLDER not set")
    exit()

if not SUFFIX:
    print("Error: SUFFIX not set")
    exit()

if not DRY_RUN:
    os.makedirs(DESTINATION_FOLDER, exist_ok=True)

print(f"Source:      {SOURCE_FOLDER}")
print(f"Destination: {DESTINATION_FOLDER}")
print(f"Suffix:      _{SUFFIX}")
print(f"Mode:        {'DRY RUN - nothing will be copied' if DRY_RUN else 'COPYING'}\n")

copied = 0
skipped = 0
errors = 0

for name in sorted(os.listdir(SOURCE_FOLDER)):
    source_path = os.path.join(SOURCE_FOLDER, name)

    if not os.path.isfile(source_path):
        continue

    stem, ext = os.path.splitext(name)
    if ext.lower() != EXTENSION.lower():
        continue

    # don't double-tag a file that already ends with _<suffix>
    if stem.endswith(f"_{SUFFIX}"):
        new_name = name
    else:
        new_name = f"{stem}_{SUFFIX}{ext}"

    destination_path = os.path.join(DESTINATION_FOLDER, new_name)

    if os.path.exists(destination_path) and not OVERWRITE:
        print(f"SKIP (already there): {new_name}")
        skipped += 1
        continue

    if DRY_RUN:
        print(f"Would copy: {name} -> {new_name}")
        copied += 1
        continue

    try:
        shutil.copy2(source_path, destination_path)
        print(f"Copied: {name} -> {new_name}")
        copied += 1
    except Exception as e:
        print(f"Error copying {name}: {e}")
        errors += 1

print(f"\n{'='*60}")
print(f"{'Would copy:' if DRY_RUN else 'Copied: ':<9}{copied}")
print(f"{'Skipped:':<9}{skipped}")
print(f"{'Errors:':<9}{errors}")
print(f"{'='*60}")
if DRY_RUN:
    print("DRY RUN - nothing was copied. Set DRY_RUN = False to apply.")

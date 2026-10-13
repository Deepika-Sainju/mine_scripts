import csv
import os

import pandas as pd

INPUT_CSV = r"C:\Users\deepika.sainju\Documents\Bookmark\20260917\3191_Bookmark_ClaimsToLoad_166867_7054.csv"
OUTPUT_CSV = r"C:\Users\deepika.sainju\Documents\Bookmark\20260917\3191_Bookmark_ClaimsToLoad_166867_7054_with_date_20260917.csv"
COLUMN = "membermetallevel"
SUFFIX = "_20260917"


def add_suffix(value):
    name, ext = os.path.splitext(str(value))
    return name + SUFFIX + ext


df = pd.read_csv(INPUT_CSV, dtype=str)
df[COLUMN] = df[COLUMN].apply(add_suffix)
# QUOTE_ALL puts quotes around every value, like the original file
df.to_csv(OUTPUT_CSV, index=False, quoting=csv.QUOTE_ALL)

print("Done:", OUTPUT_CSV)

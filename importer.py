#!/usr/bin/env python3
"""
CSV to Database Import Tool

Loads messy CSV files into a proper SQL database, cleaning up the usual
problems on the way in - inconsistent column names, missing values,
duplicate rows, and numeric outliers worth flagging before someone
builds a report on top of bad data.

This is basically the tool I wish existed the first time someone asked
me to "just import this CSV into a database" and it turned out to mean
"import this CSV that has three different date formats and a column
called ' Customer Name ' with a leading space in it."

Quick tour:
    python importer.py profile --file customers.csv
    python importer.py import --file customers.csv --table customers
    python importer.py list-tables
    python importer.py preview --table customers
    python importer.py schema --table customers

Everything lands in data/warehouse.db by default, and profiling charts
go into reports/.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless, no display needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "data" / "warehouse.db"
REPORTS_DIR = BASE_DIR / "reports"


def normalize_column_name(name):
    """Turns 'Customer Name ' or 'Order-ID' into 'customer_name' /
    'order_id'. Messy headers are probably the single most common thing
    that breaks a naive pd.read_csv().to_sql() pipeline - trailing
    spaces, mixed case, punctuation SQL doesn't want in a column name."""
    name = name.strip().lower()
    name = re.sub(r"[^\w]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "unnamed_column"


def clean_dataframe(df, dedupe=False):
    """
    Runs the standard cleanup pass and returns (clean_df, notes) - notes
    being a list of plain-English strings describing what changed, so
    whoever's running this can see it rather than having their data
    quietly rewritten underneath them.
    """
    notes = []
    original_columns = list(df.columns)
    df.columns = [normalize_column_name(c) for c in df.columns]

    renamed = [f"'{o}' -> '{n}'" for o, n in zip(original_columns, df.columns) if o != n]
    if renamed:
        notes.append(f"Renamed {len(renamed)} column(s): {', '.join(renamed[:5])}" +
                     (" ..." if len(renamed) > 5 else ""))

    # whitespace on text columns is a classic source of "why doesn't
    # this GROUP BY work" bugs, so strip it everywhere
    text_cols = df.select_dtypes(include=["object", "string"]).columns
    for col in text_cols:
        df[col] = df[col].astype(str).str.strip().replace({"nan": np.nan, "": np.nan})

    before_rows = len(df)
    duplicate_count = int(df.duplicated().sum())
    if duplicate_count:
        notes.append(f"Found {duplicate_count} exact duplicate row(s)")
        if dedupe:
            df = df.drop_duplicates().reset_index(drop=True)
            notes.append(f"Dropped duplicates, {before_rows - len(df)} row(s) removed")

    return df, notes


def find_outliers(series, threshold=3.5):
    """
    Flags values sitting way outside the normal range for a column,
    using a median/MAD-based robust z-score instead of a plain mean/std
    one. The difference actually matters here - one huge outlier drags
    the mean and inflates the std dev right along with it, which can
    hide the very thing you're trying to catch. Median and MAD barely
    budge when a single value is way out of line, so the outlier still
    stands out instead of getting averaged away into invisibility.
    """
    values = series.dropna().to_numpy(dtype=float)
    if len(values) < 3:
        return 0
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad == 0:
        return 0
    robust_z = 0.6745 * (values - median) / mad
    return int(np.sum(np.abs(robust_z) > threshold))


def profile_data(args):
    """Looks at a CSV or an existing table and reports on its shape and quality."""
    if args.file:
        df = pd.read_csv(args.file)
        source_label = args.file
    else:
        conn = sqlite3.connect(args.db)
        df = pd.read_sql_query(f"SELECT * FROM {args.table}", conn)
        conn.close()
        source_label = args.table

    print(f"\nProfile: {source_label}")
    print(f"Rows: {len(df):,}   Columns: {len(df.columns)}")

    null_counts = df.isnull().sum()
    dup_count = int(df.duplicated().sum())
    print(f"Duplicate rows: {dup_count}")

    print(f"\n{'column':<25}{'dtype':<12}{'nulls':<10}{'unique':<10}{'notes'}")
    for col in df.columns:
        dtype = str(df[col].dtype)
        nulls = f"{null_counts[col]} ({null_counts[col] / len(df) * 100:.0f}%)" if len(df) else "0"
        unique = df[col].nunique()
        extra = ""
        if pd.api.types.is_numeric_dtype(df[col]):
            outliers = find_outliers(df[col])
            if outliers:
                extra = f"{outliers} possible outlier(s)"
        print(f"{col:<25}{dtype:<12}{nulls:<10}{unique:<10}{extra}")

    REPORTS_DIR.mkdir(exist_ok=True)
    safe_label = re.sub(r"[^\w]+", "_", Path(source_label).stem)

    # missing values chart - only bother if something's actually missing
    if null_counts.sum() > 0:
        fig, ax = plt.subplots(figsize=(8, 5))
        null_counts[null_counts > 0].sort_values(ascending=False).plot(kind="bar", ax=ax, color="#C44E52")
        ax.set_title(f"Missing Values by Column - {source_label}")
        ax.set_ylabel("Missing count")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        missing_path = REPORTS_DIR / f"missing_values_{safe_label}.png"
        plt.savefig(missing_path, dpi=150)
        plt.close(fig)
        print(f"\nSaved: {missing_path}")

    # one histogram per numeric column, laid out in a grid
    numeric_cols = df.select_dtypes(include=np.number).columns
    if len(numeric_cols) > 0:
        n_cols = min(3, len(numeric_cols))
        n_rows = int(np.ceil(len(numeric_cols) / n_cols))
        fig, axes = plt.subplots(n_rows, n_cols, figsize=(5 * n_cols, 4 * n_rows), squeeze=False)
        for i, col in enumerate(numeric_cols):
            ax = axes[i // n_cols][i % n_cols]
            df[col].dropna().plot(kind="hist", bins=20, ax=ax, color="#4C72B0", edgecolor="white")
            ax.set_title(col)
        for j in range(len(numeric_cols), n_rows * n_cols):
            axes[j // n_cols][j % n_cols].axis("off")
        plt.tight_layout()
        dist_path = REPORTS_DIR / f"distributions_{safe_label}.png"
        plt.savefig(dist_path, dpi=150)
        plt.close(fig)
        print(f"Saved: {dist_path}")


def import_csv(args):
    db_path = Path(args.db)
    db_path.parent.mkdir(exist_ok=True)

    print(f"Reading {args.file} ...")

    if args.chunksize:
        # for files too big to comfortably fit in memory - clean and
        # write chunk by chunk instead of loading it all at once.
        #
        # heads up: --dedupe here only catches duplicates within a
        # single chunk, not across the whole file, since each chunk
        # gets cleaned on its own. if you need an exact whole-file
        # dedupe on something this big, either skip --chunksize, or
        # dedupe the table in a second pass after import.
        conn = sqlite3.connect(db_path)
        total_rows = 0
        total_dupes = 0
        first_chunk = True
        mode = args.mode

        for chunk in pd.read_csv(args.file, chunksize=args.chunksize):
            clean_chunk, notes = clean_dataframe(chunk, dedupe=args.dedupe)
            total_dupes += sum(1 for n in notes if "duplicate" in n.lower())
            write_mode = mode if first_chunk else "append"
            clean_chunk.to_sql(args.table, conn, if_exists=write_mode, index=False)
            total_rows += len(clean_chunk)
            first_chunk = False
            print(f"  wrote chunk of {len(clean_chunk)} rows (running total: {total_rows})")

        conn.close()
        print(f"\nImported {total_rows:,} row(s) into '{args.table}' in {db_path}")
        return

    df = pd.read_csv(args.file)
    original_rows = len(df)
    df, notes = clean_dataframe(df, dedupe=args.dedupe)

    print(f"\nRead {original_rows:,} row(s), {len(df.columns)} column(s)")
    for note in notes:
        print(f"  - {note}")

    null_total = int(df.isnull().sum().sum())
    if null_total:
        print(f"  - {null_total} missing value(s) across all columns (left as NULL, not filled in)")

    conn = sqlite3.connect(db_path)
    df.to_sql(args.table, conn, if_exists=args.mode, index=False)
    conn.close()

    print(f"\nImported {len(df):,} row(s) into '{args.table}' in {db_path} (mode: {args.mode})")


def list_tables(args):
    conn = sqlite3.connect(args.db)
    tables = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn
    )
    if tables.empty:
        print("No tables found. Import something first with `python importer.py import ...`")
        conn.close()
        return

    print(f"Tables in {args.db}:")
    for name in tables["name"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  {name:<30} {count:,} row(s)")
    conn.close()


def preview_table(args):
    conn = sqlite3.connect(args.db)
    try:
        df = pd.read_sql_query(f"SELECT * FROM {args.table} LIMIT {args.rows}", conn)
    except pd.errors.DatabaseError:
        print(f"No table called '{args.table}' in {args.db}")
        conn.close()
        return
    conn.close()
    print(df.to_string(index=False))


def show_schema(args):
    conn = sqlite3.connect(args.db)
    info = conn.execute(f"PRAGMA table_info({args.table})").fetchall()
    conn.close()

    if not info:
        print(f"No table called '{args.table}' in {args.db}")
        return

    print(f"Schema for '{args.table}':")
    print(f"{'column':<25}{'type':<12}{'nullable'}")
    for _, name, col_type, notnull, _, _ in info:
        print(f"{name:<25}{col_type:<12}{'no' if notnull else 'yes'}")


def main():
    parser = argparse.ArgumentParser(description="Clean and import CSV files into a SQL database.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="clean a CSV and load it into the database")
    p_import.add_argument("--file", required=True)
    p_import.add_argument("--table", required=True)
    p_import.add_argument("--db", default=str(DEFAULT_DB))
    p_import.add_argument("--mode", choices=["replace", "append", "fail"], default="replace",
                           help="what to do if the table already exists (default: replace)")
    p_import.add_argument("--dedupe", action="store_true", help="drop exact duplicate rows")
    p_import.add_argument("--chunksize", type=int, help="read/write in chunks of N rows, for large files")
    p_import.set_defaults(func=import_csv)

    p_profile = sub.add_parser("profile", help="inspect a CSV or table's quality before/after import")
    p_profile.add_argument("--file", help="profile a CSV file directly")
    p_profile.add_argument("--table", help="profile a table already in the database")
    p_profile.add_argument("--db", default=str(DEFAULT_DB))
    p_profile.set_defaults(func=profile_data)

    p_list = sub.add_parser("list-tables", help="show every table in the database")
    p_list.add_argument("--db", default=str(DEFAULT_DB))
    p_list.set_defaults(func=list_tables)

    p_preview = sub.add_parser("preview", help="show the first few rows of a table")
    p_preview.add_argument("--table", required=True)
    p_preview.add_argument("--rows", type=int, default=10)
    p_preview.add_argument("--db", default=str(DEFAULT_DB))
    p_preview.set_defaults(func=preview_table)

    p_schema = sub.add_parser("schema", help="show a table's column types")
    p_schema.add_argument("--table", required=True)
    p_schema.add_argument("--db", default=str(DEFAULT_DB))
    p_schema.set_defaults(func=show_schema)

    args = parser.parse_args()

    if args.command == "profile" and not args.file and not args.table:
        parser.error("profile needs either --file or --table")

    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

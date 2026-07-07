# CSV to Database Import Tool

Loads CSV files into a proper SQL database and cleans up the usual mess
along the way instead of just dumping raw rows in. pandas does the
cleaning and reshaping, numpy handles the outlier detection, matplotlib
gives you a quick visual profile, and SQLite is the actual database.

I built this after realizing "just import this CSV into a database" is
basically never that simple in practice - it's "import this CSV that has
a column called ' Customer Name ' with a leading space, a couple of
duplicate rows from a double-submitted form, some blanks where someone
forgot to fill in a field, and one number that's obviously a typo."
This handles all of that and tells you exactly what it did, instead of
either silently mangling your data or silently leaving the mess in place
for you to find later.

## What it does

Normalizes column names - `Customer ID` becomes `customer_id`, whitespace
gets stripped, punctuation gets swapped out - so nothing breaks a SQL
query down the line just because a header had a stray space in it.

Strips whitespace from text fields too, and treats empty strings as
actual NULLs rather than blank text sitting there pretending to be data.

Catches exact duplicate rows and drops them if you pass `--dedupe`.

Flags numeric outliers using a median/MAD-based robust z-score rather
than a plain mean/std one - worth explaining why, since it's not the
obvious choice: a plain z-score can actually miss the outlier it's
supposed to catch, because one huge value drags the mean up and inflates
the standard deviation right along with it. Median and MAD barely move
when there's a single value way out of line, so the outlier still stands
out instead of getting smoothed away by its own presence.

Beyond that - it reports null counts, unique counts, and dtypes per
column before you commit to anything, generates two profiling charts
(missing values by column, and a histogram grid for every numeric
column), handles chunked reading/writing for files too big to load into
memory comfortably, and supports `replace`, `append`, or `fail` modes for
what happens if the target table's already there.

## Setup

```bash
git clone <your repo url>
cd csv-to-db-import-tool
pip install -r requirements.txt
```

There's a `sample_customers.csv` in here with realistic messiness baked
in on purpose - inconsistent header spacing, one duplicate row, a couple
of missing values, one outlier - so the cleaning pipeline actually has
something to do on your first run instead of just importing perfectly
clean data and looking like it didn't do anything.

## Usage

**Profile a CSV before importing it**, so you know what you're actually
dealing with:

```bash
python importer.py profile --file sample_customers.csv
```

```
Profile: sample_customers.csv
Rows: 11   Columns: 6
Duplicate rows: 1

column                   dtype       nulls     unique    notes
Customer ID              int64       0 (0%)    10
 Customer Name           str         0 (0%)    9
Email                    str         0 (0%)    9
Signup Date              str         0 (0%)    9
Annual Spend             float64     2 (18%)   7         1 possible outlier(s)
Region                   str         0 (0%)    4

Saved: reports/missing_values_sample_customers.png
Saved: reports/distributions_sample_customers.png
```

**Import it**, dropping the one exact duplicate along the way:

```bash
python importer.py import --file sample_customers.csv --table customers --dedupe
```

This prints exactly what got cleaned up front - renamed columns,
duplicates dropped, how many nulls are left over - before it tells you
the final row count that actually landed in the database.

**See what's in the database:**

```bash
python importer.py list-tables
python importer.py schema --table customers
python importer.py preview --table customers --rows 5
```

**Re-profile after import**, straight from the table this time instead of
the original file:

```bash
python importer.py profile --table customers
```

**Append instead of replace:**

```bash
python importer.py import --file new_batch.csv --table customers --mode append
```

**Big file? Read and write it in chunks:**

```bash
python importer.py import --file huge_export.csv --table events --chunksize 50000
```

One honest caveat on that last one, since I'd rather you hit this in the
README than find out the hard way: `--dedupe` combined with `--chunksize`
only catches duplicates *within* a single chunk, not across the whole
file - each chunk gets cleaned independently before it's written. If you
need a true whole-file dedupe on something too big to load all at once,
skip `--chunksize` if you can, or dedupe the table in a second pass after
the import finishes.

## Where things end up

The database defaults to `data/warehouse.db` (override with `--db`), and
profiling charts land in `reports/`. Both are git-ignored, along with the
database file itself - a client's actual data has no business sitting in
a public repo.

## Ideas for extending this

- Swap SQLite for Postgres/MySQL via SQLAlchemy - the cleaning and
  profiling logic wouldn't need to change at all, just the connection
- Auto-detect and parse date columns instead of leaving them as plain text
- A `--dry-run` flag that shows what would be imported without touching
  the database
- Column-level type overrides for when pandas guesses wrong (a zip code
  column turning into an integer and losing its leading zeros is the
  classic one)
- A fuzzy-duplicate check - same email with different casing, say - on
  top of the exact-match check that's already there

## License

MIT - use it, extend it, build paid client work on top of it.

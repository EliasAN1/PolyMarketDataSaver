# Recovering a truncated SQLite `pmdsaver.db`

This notes how a torrented `pmdsaver.db` that opened with `database disk image is malformed` was diagnosed and salvaged. The result was a new, internally consistent file — not a perfect reconstruction of the original.

Do not merge a malformed file into a healthy database. Salvage into a **new** file, integrity-check that file, then merge only the salvage.

## Why the file was malformed

The collector uses SQLite **WAL** (`journal_mode=WAL`). The live database is the combination of:

- `pmdsaver.db` — the main image
- `pmdsaver.db-wal` — recent pages not yet checkpointed into the main file
- `pmdsaver.db-shm` — shared-memory index for WAL

BitTorrent (or any copy of a file that is still being written) does not take a consistent snapshot. Typical ways this file got truncated:

1. The collector was still running while the torrent was built or seeded, so different pieces came from different moments in time.
2. Only `pmdsaver.db` was copied; the newest pages were still in `-wal`.
3. The torrent finished while the file was still growing.

A green hash in the torrent client only means “this is the file you added.” If that file was already inconsistent, every peer gets a consistent copy of a **broken** database.

## Diagnosis (this incident)

File: ~20.2 GB `pmdsaver.db` with no `-wal` / `-shm` next to it.

| Check | Result |
| --- | --- |
| Magic | Valid `SQLite format 3` header |
| `sqlite_master` | Readable (`windows`, `odds_ticks`, `price_ticks`, `twap_ticks`, `candle_volume`) |
| Page size | 4096 bytes |
| Header `page_count` | **5,301,657** |
| Pages actually on disk (`file_size / 4096`) | **5,301,132** |
| Missing | **525 pages** (~2.1 MB) at the **end** of the file |
| `PRAGMA quick_check` | Failed on page **5301657** — the last page the header expected |

The header claimed a larger file than existed. SQLite then walked a b-tree pointer into a page that was never written (or never copied).

```text
expected_size = header_page_count * page_size
pages_on_disk = file_size // page_size
bytes_short   = expected_size - file_size
```

If `pages_on_disk < header_page_count` and `file_size % page_size == 0`, the image is truncated, not randomly scrambled.

## What still worked

After pointing SQLite at the real page count (see below), whole tables whose b-trees did not use the missing tail copied cleanly:

| Table | Rows recovered |
| --- | --- |
| `windows` | 1,583 (all of them) |
| `twap_ticks` | 418,352 |
| `candle_volume` | 3,166 |

`odds_ticks` and `price_ticks` were huge. Their rightmost leaf pages lived in the missing tail, so:

- A **primary-key lookup** (`WHERE id = ?`) could still find a high id.
- A **range scan** (`WHERE id BETWEEN ? AND ?` / `INSERT … SELECT`) failed earlier, because the scan walks the tree and hits a corrupt/missing page before the last lookup-able id.

So “last id that `SELECT` by PK still returns” is **not** a safe copy cutoff. The safe cutoff is the last range that copies without `database disk image is malformed`.

## Salvage procedure

Scripts used at the time were one-off and are not in this repo. The steps are:

### 1. Copy, then patch the header page count

Work on a **copy**. Save the original 100-byte header first.

SQLite stores `page_count` as a big-endian uint32 at **offset 28** of the database header.

Set it to `file_size // page_size` (here: `5,301,132`) so SQLite stops looking for pages past EOF.

That does **not** repair the b-trees. It only makes the file size and the header agree so you can read the pages that are still there.

### 2. Create an empty destination database

Open a new file (here: `pmdsaver.fixed.db`), apply this project’s `SCHEMA` from `pmdsaver.db` (`PRAGMA journal_mode=WAL` plus `CREATE TABLE` / indexes), and `ATTACH` the patched source as `src`.

### 3. Copy intact tables in full

For tables that `COUNT(*)` and a full `INSERT INTO dest SELECT … FROM src` without error:

```sql
INSERT INTO main.windows SELECT … FROM src.windows;
```

Use **named columns**, not `SELECT *`. Column order in an old file can disagree with a newly created schema (extra columns such as `outcome_source` were added later). `SELECT *` into a `NOT NULL` column in the wrong position failed here (`created_at_ms`).

Keep the original `id` values so `window_id` foreign keys still match.

### 4. Copy broken tables by id range, then back off

For `odds_ticks` / `price_ticks`:

1. Probe the highest id that still loads with `SELECT … WHERE id = ?`.
2. Copy in chunks, e.g. `WHERE id > lo AND id <= hi`, committing each chunk.
3. When a chunk raises `malformed`, lower `hi` and retry. Do not keep scanning into the corrupt region.

Practical cutoffs used on this file (sqlite_sequence on the source was higher):

| Table | sqlite_sequence (source) | Copied through |
| --- | --- | --- |
| `odds_ticks` | ~204,468,463 | `id <= 204,000,000` |
| `price_ticks` | ~18,328,756 | `id <= 18,000,000` |

Trying to pick up the remaining tail row-by-row (skip on error) was too slow on a 20 GB file and was aborted. The missing rows were the **last stretch of collection**, not random holes in the middle.

Reset `sqlite_sequence` on the destination to the copied max ids so a collector can continue without colliding.

### 5. Integrity-check the new file

```sql
PRAGMA quick_check;
```

The salvaged file (`pmdsaver.fixed.db`, ~21.6 GB) returned `ok`. The original torrented `pmdsaver.db` stayed malformed.

## What was lost

- **Windows:** all 1,583 rows kept.
- **Odds:** last ~447k ticks not copied. About the last **5–6 windows** have no usable CLOB tape.
- **Exchange prices:** last ~328k ticks not copied. About **49 windows** (~4 hours) have no `price_ticks`.
- **TWAP / candle volume:** copied in full for this file.

That is enough for merge and backtest on the recovered span. It is not a bit-for-bit restore of the other PC’s last hours.

## After salvage

Merge only the **fixed** file:

```powershell
python -m pmdsaver.mergedb path\to\pmdsaver.fixed.db
```

Do not merge the truncated `pmdsaver.db`.

## How to copy or share a database without repeating this

1. **Stop** the collector (Ctrl+C / close the exe) and wait for a clean exit.
2. Copy **`pmdsaver.db` and any `pmdsaver.db-wal` / `pmdsaver.db-shm`** next to it, or run a checkpoint first:

   ```sql
   PRAGMA wal_checkpoint(TRUNCATE);
   ```

   If checkpoint returns busy, something still has the database open. `sqlite3.backup()` to a new file is a reliable snapshot while the DB is otherwise idle.
3. Do not torrent or zip a `pmdsaver.db` that is still being written.
4. After copy, on the destination: `PRAGMA quick_check;` should return `ok` before you merge.

# Plan: Prune Committed iterm2_samples Tree and Parameterize Scraper

**Date:** 2026-06-13
**Branch:** worktree-agent-af90cffb3e379768d

## What Was Done

Removed 343 dead committed files from `scripts/iterm2_samples/` and
parameterized the regenerator script so samples can be downloaded on demand
without polluting the git history.

## Files Removed

343 files were deleted from `scripts/iterm2_samples/` via `git rm -r`. These
consisted of:

- ~30 raw `.its` archive files (iTerm2 plugin bundles, zip format)
- ~313 files inside `scripts/iterm2_samples/extracted/` — extracted source
  trees that showed severe triple-nesting due to the dedup bug (see below)

None of these files were referenced by any import, CI step, or live execution
path. The only reference was the `OUTPUT_DIR` constant inside
`scripts/download_samples.py` itself.

## .gitignore Update

`scripts/iterm2_samples/` was appended to `.gitignore` so that regenerated
output cannot be accidentally re-committed.

## Argparse Flags Added to `scripts/download_samples.py`

| Flag | Default | Description |
|------|---------|-------------|
| `--out-dir` | `scripts/iterm2_samples` | Directory for downloaded files |
| `--timeout` | `30` | HTTP request timeout in seconds |
| `--samples-json` | `scripts/it2api_samples.json` | Path to the samples JSON index |
| `--user-agent` | `iterm2-sample-downloader/1.0` | User-Agent for HTTP requests |
| `--extract` | (flag, off by default) | Extract .its archives after download |

## Dedup Bug Fixed

The committed `extracted/` tree showed triple nesting such as:

```
extracted/runcmd/runcmd/runcmd/runcmd.py
```

The root cause: `.its` archives contain a top-level directory named after the
sample (e.g. `runcmd/`). When extracted into `extracted/runcmd/` without
stripping that top-level dir, the result is `extracted/runcmd/runcmd/`. A
second extraction pass doubled the nesting again.

**Fix:** `extract_its_archive()` in the rewritten script detects when the
archive has exactly one top-level entry whose name matches the sample name, and
lifts its contents up one level using a `tempfile` staging area. The result is
`extracted/<name>/<contents>` with no duplication.

## How to Regenerate

```bash
# Download raw .its and .py files only
python scripts/download_samples.py

# Download and also extract .its archives (dedup-safe)
python scripts/download_samples.py --extract

# Custom output location and timeout
python scripts/download_samples.py --out-dir /tmp/samples --timeout 60

# Use a different samples index
python scripts/download_samples.py --samples-json path/to/custom.json
```

The script reads `scripts/it2api_samples.json` for the list of documentation
page URLs, fetches each page, extracts the `.its` or `.py` link, and downloads
it. No iTerm2 connection or running daemon is required.

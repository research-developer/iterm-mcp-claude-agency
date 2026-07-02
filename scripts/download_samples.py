#!/usr/bin/env python3
"""Download iTerm2 Python API sample scripts from documentation pages.

Sample files (.its archives and .py scripts) are generated on demand by running
this script. They are NOT committed to the repository; scripts/iterm2_samples/
is listed in .gitignore. Re-run this script whenever you need fresh copies.

Usage:
    python scripts/download_samples.py [OPTIONS]

Options:
    --out-dir       Directory for downloaded files (default: scripts/iterm2_samples)
    --timeout       HTTP request timeout in seconds (default: 30)
    --samples-json  Path to the samples JSON index (default: scripts/it2api_samples.json)
    --user-agent    User-Agent header for HTTP requests
                    (default: iterm2-sample-downloader/1.0)
    --extract       Extract .its zip archives into <out-dir>/extracted/ after download

Dedup note: .its archives produced by iTerm2 already contain a top-level
directory named after the sample (e.g. runcmd/). When extracting, this script
lifts that top-level directory up so extraction of foo.its into extracted/foo/
yields extracted/foo/<files> rather than extracted/foo/foo/<files> (triple
nesting when done naively twice).
"""

import argparse
import json
import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path


DEFAULT_SAMPLES_JSON = Path(__file__).parent / "it2api_samples.json"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "iterm2_samples"
DEFAULT_USER_AGENT = "iterm2-sample-downloader/1.0"
DEFAULT_TIMEOUT = 30


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download iTerm2 Python API sample scripts from documentation pages.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for downloaded files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--samples-json",
        type=Path,
        default=DEFAULT_SAMPLES_JSON,
        help=f"Path to the samples JSON index (default: {DEFAULT_SAMPLES_JSON})",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help=f"User-Agent header for HTTP requests (default: {DEFAULT_USER_AGENT})",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Extract .its zip archives into <out-dir>/extracted/ after download",
    )
    return parser.parse_args()


def extract_script_link(html: str, base_url: str) -> str | None:
    """Extract .its or .py link from the HTML page."""
    # Look for links ending in .its or .py
    pattern = r'href=["\']([^"\']*\.(?:its|py))["\']'
    matches = re.findall(pattern, html)

    if matches:
        link = matches[0]
        # Handle relative URLs
        if not link.startswith("http"):
            # Get base URL directory
            base = base_url.rsplit("/", 1)[0]
            link = f"{base}/{link}"
        return link
    return None


def download_file(url: str, output_path: Path, user_agent: str, timeout: int) -> bool:
    """Download a file from URL to output path.

    Args:
        url: URL to download from.
        output_path: Local path to write the downloaded content to.
        user_agent: User-Agent header value for the HTTP request.
        timeout: Request timeout in seconds.

    Returns:
        True on success, False on failure.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
            output_path.write_bytes(content)
            return True
    except Exception as e:
        print(f"  Error downloading {url}: {e}")
        return False


def extract_its_archive(archive_path: Path, extract_base: Path) -> bool:
    """Extract a .its zip archive, fixing nested directory duplication.

    .its archives produced by iTerm2 contain a top-level directory named
    after the sample (e.g. foo/). A naive extraction into extract_base/foo/
    would yield extract_base/foo/foo/ — triple-nested after a second pass.
    This function detects that pattern and lifts the inner directory up so
    the result is extract_base/foo/<contents>.

    Args:
        archive_path: Path to the .its zip file.
        extract_base: Parent directory under which to create the sample folder.

    Returns:
        True on success, False on failure.
    """
    sample_name = archive_path.stem  # e.g. "runcmd" from "runcmd.its"
    dest_dir = extract_base / sample_name

    if not zipfile.is_zipfile(archive_path):
        print(f"  Not a zip archive: {archive_path.name}")
        return False

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(tmp_path)

            # Determine if the archive has a single top-level directory that
            # duplicates the sample name (the dedup bug trigger).
            top_level = list(tmp_path.iterdir())
            if (
                len(top_level) == 1
                and top_level[0].is_dir()
                and top_level[0].name == sample_name
            ):
                # Lift contents up one level to avoid foo/foo/ nesting.
                inner = top_level[0]
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(inner, dest_dir)
            else:
                # No duplication detected; extract as-is.
                if dest_dir.exists():
                    shutil.rmtree(dest_dir)
                shutil.copytree(tmp_path, dest_dir)

        return True
    except Exception as e:
        print(f"  Error extracting {archive_path.name}: {e}")
        return False


def main() -> None:
    """Download (and optionally extract) iTerm2 API sample scripts."""
    args = parse_args()

    # Load samples JSON
    with open(args.samples_json) as f:
        samples = json.load(f)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    failed = 0
    extracted = 0
    extract_failed = 0

    extract_dir = args.out_dir / "extracted"
    if args.extract:
        extract_dir.mkdir(parents=True, exist_ok=True)

    for name, url in samples.items():
        print(f"Processing: {name}")
        print(f"  URL: {url}")

        try:
            # Fetch the HTML page
            req = urllib.request.Request(url, headers={"User-Agent": args.user_agent})
            with urllib.request.urlopen(req, timeout=args.timeout) as response:
                html = response.read().decode("utf-8")

            # Extract script link
            script_url = extract_script_link(html, url)

            if script_url:
                print(f"  Script: {script_url}")

                # Determine output filename
                filename = script_url.rsplit("/", 1)[-1]
                output_path = args.out_dir / filename

                # Download the script
                if download_file(script_url, output_path, args.user_agent, args.timeout):
                    print(f"  Saved: {output_path}")
                    downloaded += 1

                    # Optionally extract .its archives
                    if args.extract and output_path.suffix == ".its":
                        if extract_its_archive(output_path, extract_dir):
                            print(f"  Extracted to: {extract_dir / output_path.stem}")
                            extracted += 1
                        else:
                            extract_failed += 1
                else:
                    failed += 1
            else:
                print("  No .its or .py link found")
                failed += 1

        except Exception as e:
            print(f"  Error: {e}")
            failed += 1

    summary = f"\nDone! Downloaded: {downloaded}, Failed: {failed}"
    if args.extract:
        summary += f", Extracted: {extracted}, Extract errors: {extract_failed}"
    print(summary)


if __name__ == "__main__":
    main()

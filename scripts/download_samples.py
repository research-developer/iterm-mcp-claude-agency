#!/usr/bin/env python3
"""Download iTerm2 Python API sample scripts from documentation pages."""

import json
import os
import re
import urllib.request
from pathlib import Path

SAMPLES_JSON = Path(__file__).parent / "it2api_samples.json"
OUTPUT_DIR = Path(__file__).parent / "iterm2_samples"


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


def download_file(url: str, output_path: Path) -> bool:
    """Download a file from URL to output path."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            output_path.write_bytes(content)
            return True
    except Exception as e:
        print(f"  Error downloading {url}: {e}")
        return False


def main():
    # Load samples JSON
    with open(SAMPLES_JSON) as f:
        samples = json.load(f)

    OUTPUT_DIR.mkdir(exist_ok=True)

    downloaded = 0
    failed = 0

    for name, url in samples.items():
        print(f"Processing: {name}")
        print(f"  URL: {url}")

        try:
            # Fetch the HTML page
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as response:
                html = response.read().decode("utf-8")

            # Extract script link
            script_url = extract_script_link(html, url)

            if script_url:
                print(f"  Script: {script_url}")

                # Determine output filename
                filename = script_url.rsplit("/", 1)[-1]
                output_path = OUTPUT_DIR / filename

                # Download the script
                if download_file(script_url, output_path):
                    print(f"  Saved: {output_path}")
                    downloaded += 1
                else:
                    failed += 1
            else:
                print("  No .its or .py link found")
                failed += 1

        except Exception as e:
            print(f"  Error: {e}")
            failed += 1

    print(f"\nDone! Downloaded: {downloaded}, Failed: {failed}")


if __name__ == "__main__":
    main()

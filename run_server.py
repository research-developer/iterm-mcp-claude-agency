#!/usr/bin/env python3
"""DEPRECATED: kept so existing Claude Desktop configs keep working.

Run `iterm-mcp install --desktop` to migrate, then delete this file.
Now routes through the shim, so Desktop shares the singleton daemon too.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iterm_mcpy.shim import run_shim

run_shim()

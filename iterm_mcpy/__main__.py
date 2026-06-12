"""Allow `python -m iterm_mcpy <subcommand>` (used by shim to spawn daemon).

Only the `daemon` subcommand is handled here for now; everything else
delegates to iterm_mcpy.main (Task 5 consolidates the full CLI there).
"""
import sys

if len(sys.argv) > 1 and sys.argv[1] == "daemon":
    from iterm_mcpy.daemon import run_daemon
    run_daemon()
else:
    from iterm_mcpy.main import main
    main()

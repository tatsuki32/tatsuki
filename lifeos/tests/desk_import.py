"""desk/server.py and desk/sync.py are scripts, not a package: import them for the tests."""
import os
import sys

from lifeos.lib import paths

sys.path.insert(0, os.path.join(paths.REPO, "desk"))
import server  # noqa: E402,F401
import sync  # noqa: E402,F401

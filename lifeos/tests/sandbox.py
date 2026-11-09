"""Test base class: everything stays in a temporary folder. See lifeos/README.md."""
import os
import shutil
import tempfile
import unittest

_CLEARED = ("LIFEOS_DESK_DATA", "LIFEOS_DESK_API", "LIFEOS_TOOLS_ROOT", "LIFEOS_TRANSCRIBER",
            "LIFEOS_TELEGRAM_TOKEN", "LIFEOS_TELEGRAM_CHAT_ID", "LIFEOS_TELEGRAM_SECRETARY_TOKEN",
            "LIFEOS_GROQ_API_KEY", "LIFEOS_GCAL_URL")


class SandboxCase(unittest.TestCase):
    """LIFEOS_DATA points at a temp folder, so no real data, Desk server or Telegram is touched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lifeos-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = dict(os.environ)
        self.addCleanup(self._restore)
        for k in _CLEARED:
            os.environ.pop(k, None)
        os.environ["LIFEOS_DATA"] = os.path.join(self.tmp, "data")

    def _restore(self):
        os.environ.clear()
        os.environ.update(self._env)

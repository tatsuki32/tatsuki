"""Mixed into the morning brief by tools.brief_parts(). Reads only."""
import os

from lifeos.lib.tools import load_file


def lines(day):
    return load_file(os.path.dirname(os.path.abspath(__file__)), "core").brief_lines(day)

#!/bin/bash
# Installs OmniVoice (k2-fsa, https://github.com/k2-fsa/OmniVoice) for the Desk Reader's Esperanto read-aloud. Run once on the Mac:
#   bash desk/omnivoice/setup.sh
# Everything goes into desk/omnivoice/engine/ (out of git): uv, Python 3.11, torch 2.8, omnivoice and the model (~2.45 GB from
# Hugging Face, k2-fsa/OmniVoice, CC-BY-NC: personal use only). Nothing else on the Mac is changed. Remove the folder to undo.
set -euo pipefail
cd "$(dirname "$0")"
E="$PWD/engine"
mkdir -p "$E"
export UV_CACHE_DIR="$E/uv-cache" UV_PYTHON_INSTALL_DIR="$E/python" HF_HOME="$E/hf"
if [ ! -x "$E/uvenv/bin/uv" ]; then
  echo "1/4 uv (from PyPI)…"
  /usr/bin/python3 -m venv "$E/uvenv"
  "$E/uvenv/bin/pip" install -q --upgrade pip uv
fi
UV="$E/uvenv/bin/uv"
if [ ! -x "$E/venv/bin/python" ]; then
  echo "2/4 Python 3.11…"
  "$UV" python install 3.11
  "$UV" venv --python 3.11 "$E/venv"
fi
echo "3/4 torch 2.8 and omnivoice (PyPI)…"
"$UV" pip install --python "$E/venv/bin/python" torch==2.8.0 torchaudio==2.8.0 omnivoice soundfile
echo "4/4 the model (~2.45 GB from Hugging Face) and a test sentence…"
"$E/venv/bin/python" "$PWD/speak_server.py" --selftest
echo "Done. The Desk starts the voice by itself when an Esperanto book is read aloud."

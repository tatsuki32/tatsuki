#!/bin/bash
# 文字起こしエンジン(whisper.cpp)のビルドとモデルのダウンロード。何度実行しても安全。
set -euo pipefail
cd "$(dirname "$0")"
VER=v1.9.5
MODEL=ggml-large-v3-turbo.bin;  MODEL_SIZE=1624555275
VAD=ggml-silero-v5.1.2.bin;     VAD_SIZE=885098
mkdir -p engine/models

fetch() { # url path size
  local have=0
  [ -f "$2" ] && have=$(stat -f%z "$2")
  if [ "$have" = "$3" ]; then echo "OK: $2"; return; fi
  echo "ダウンロード: $2"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$2.part" "$1"
  [ "$(stat -f%z "$2.part")" = "$3" ] || { echo "サイズ不一致: $2"; exit 1; }
  mv "$2.part" "$2"
}

fetch "https://huggingface.co/ggml-org/whisper-vad/resolve/main/$VAD" "engine/models/$VAD" "$VAD_SIZE"

if [ ! -x engine/whisper-cli ]; then
  python3 -m venv engine/buildenv
  engine/buildenv/bin/pip install -q --disable-pip-version-check --no-cache-dir --retries 10 --timeout 120 cmake
  curl -fL --retry 5 -o engine/src.tar.gz "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/$VER.tar.gz"
  rm -rf engine/src && mkdir -p engine/src
  tar xzf engine/src.tar.gz -C engine/src --strip-components=1 --exclude '.idea' --exclude '.vscode' --exclude '.github'
  engine/buildenv/bin/cmake -S engine/src -B engine/src/build -DCMAKE_BUILD_TYPE=Release \
    -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
  engine/buildenv/bin/cmake --build engine/src/build -j 4 --target whisper-cli
  cp engine/src/build/bin/whisper-cli engine/whisper-cli
  rm -rf engine/src engine/src.tar.gz engine/buildenv
fi

fetch "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$MODEL" "engine/models/$MODEL" "$MODEL_SIZE"
echo "セットアップ完了"

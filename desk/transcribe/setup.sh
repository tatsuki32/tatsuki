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
# 対面の話者分け(sherpa-onnx)。区間の検出(pyannote)と声の特徴(3D-Speaker CAM++)のモデル
SHERPA=v2.0.2
SHERPA_PKG=sherpa-onnx-$SHERPA-osx-arm64-shared-no-tts; SHERPA_SIZE=20197239
SEG=sherpa-onnx-pyannote-segmentation-3-0;            SEG_SIZE=6958444
EMB=3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx; EMB_SIZE=28281165
REL=https://github.com/k2-fsa/sherpa-onnx/releases/download
mkdir -p engine/diar
if [ ! -x engine/diar/bin/sherpa-onnx-offline-speaker-diarization ]; then
  fetch "$REL/$SHERPA/$SHERPA_PKG.tar.bz2" "engine/diar/pkg.tar.bz2" "$SHERPA_SIZE"
  tar xjf engine/diar/pkg.tar.bz2 -C engine/diar --strip-components=1
  rm engine/diar/pkg.tar.bz2
  # 使うのは話者分けの1本だけ
  find engine/diar/bin -type f ! -name sherpa-onnx-offline-speaker-diarization -delete
  rm -rf engine/diar/include
fi
if [ ! -f engine/diar/segmentation.onnx ]; then
  fetch "$REL/speaker-segmentation-models/$SEG.tar.bz2" "engine/diar/seg.tar.bz2" "$SEG_SIZE"
  tar xjf engine/diar/seg.tar.bz2 -C engine/diar
  mv "engine/diar/$SEG/model.onnx" engine/diar/segmentation.onnx
  rm -rf "engine/diar/$SEG" engine/diar/seg.tar.bz2
fi
fetch "$REL/speaker-recongition-models/$EMB" "engine/diar/embedding.onnx" "$EMB_SIZE"

# 会議の相手の声(Mac の音)を録る小さなアプリ。作り直すと「画面収録」の許可が外れるので、無いときだけ作る
TAP="$HOME/Applications/TatsukiAudioTap.app"
if [ ! -x "$TAP/Contents/MacOS/TatsukiAudioTap" ]; then
  mkdir -p "$TAP/Contents/MacOS"
  swiftc -O -framework ScreenCaptureKit -framework CoreMedia "../app/audiotap.swift" -o "$TAP/Contents/MacOS/TatsukiAudioTap"
  cp ../app/audiotap-info.plist "$TAP/Contents/Info.plist"
  codesign -f -s - -i com.tatsuki.desk.audiotap "$TAP"
fi
echo "セットアップ完了"

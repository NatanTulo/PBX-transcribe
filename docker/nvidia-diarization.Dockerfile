FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    PYANNOTE_METRICS_ENABLED=0 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/workspace/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential ffmpeg libsndfile1 python3 python3-dev python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv \
    && python -m pip install --no-cache-dir --upgrade pip setuptools wheel Cython packaging \
    && python -m pip install --no-cache-dir \
       torch torchaudio --index-url https://download.pytorch.org/whl/cu128 \
    && python -m pip install --no-cache-dir "nemo_toolkit[asr]>=2.5,<3"

WORKDIR /workspace
ENTRYPOINT ["python", "-m", "pbx_transcribe.cli"]

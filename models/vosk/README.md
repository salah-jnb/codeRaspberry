# Vosk wake-word models

This directory is populated automatically at runtime by
[scripts/download_vosk_model.py](../../scripts/download_vosk_model.py) based on
the `VOSK_LANGUAGE` setting in `.env`.

Layout after a successful download for `VOSK_LANGUAGE=ar`:

```
models/vosk/
├── ar/                       # extracted vosk-model-ar-mgb2-0.4
│   ├── am/
│   ├── conf/
│   ├── graph/
│   ├── ivector/
│   └── ...
```

Run a manual download for a different language:

```bash
python -m scripts.download_vosk_model fr
python -m scripts.download_vosk_model en
```

Available languages: see `MODEL_REGISTRY` in `scripts/download_vosk_model.py`.

# Trainable test base for VOOL adaptation

This directory contains a minimal local Transformers checkpoint derived from `sshleifer/tiny-gpt2`.

Purpose:
- prove the closed adaptation loop end-to-end
- provide a tiny trainable fallback when no larger trainable base is staged locally
- keep VOOL honest about the difference between Ollama inference models and trainable Transformers bases

Limits:
- this is a test-grade base, not the production intelligence base
- its quality is far below Qwen/Llama-class runtime models
- it exists so LoRA training, eval, canary, promotion, and rollback can run for real

Source reference:
- https://huggingface.co/sshleifer/tiny-gpt2

License:
- unknown-test-only
- do not treat this as final redistribution policy for production bundles

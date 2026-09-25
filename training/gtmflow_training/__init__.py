"""GTMFlow Phase 8: reproducible LoRA fine-tuning of the grounded generator.

Fits on the Phase 7 training dataset (train split only), tunes on the
validation dataset (checkpoint selection), and never loads the test
dataset -- that is reserved for the final evaluation (Phase 9).
"""

"""Example-weighted causal-LM loss.

Phase 7 assigns every training example a weight (structure-cap-v1:
min(1, 5 / structure_group_size)). The training objective is the
weight-normalized mean of per-example losses:

    L = sum_i w_i * l_i / sum_i w_i

where l_i is example i's mean token cross-entropy over its answer tokens.
Examples are fed one per micro-batch, so each micro-batch contributes
w_i / mean(w) * l_i and gradient accumulation averages them; the expected
value of that average over an epoch equals L exactly. Validation loss is
unweighted (weights describe training-data redundancy, not evaluation).
"""
from __future__ import annotations

import torch
import torch.nn.functional as F


def weight_normalizer(weights: list[float]) -> float:
    if not weights or any(w <= 0 for w in weights):
        raise ValueError("weights must be non-empty and positive")
    return sum(weights) / len(weights)


def answer_token_loss(logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
    """Mean cross-entropy over one example's answer tokens.
    logits: [N, vocab] at the positions that predict each answer token."""
    return F.cross_entropy(logits.float(), targets, reduction="mean")


def weighted_loss(example_loss: torch.Tensor, weight: float, normalizer: float) -> torch.Tensor:
    return example_loss * (weight / normalizer)

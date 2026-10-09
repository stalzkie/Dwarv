"""DWARV_PLAN.md section 11.4: our own weight-quantization methods,
compared at a fixed bit-width via perplexity on held-out code. Operates
directly on PyTorch Linear-layer weight tensors -- independent of GGUF/
llama.cpp -- so the experiment isolates exactly what the quantization
method itself contributes, before any downstream format/engine enters
the picture.
"""

import torch


def naive_rtn_quantize(weight: torch.Tensor, bits: int) -> torch.Tensor:
    """Baseline: per-output-channel symmetric round-to-nearest, no
    calibration data used at all. weight shape: (out_features, in_features)."""
    qmax = 2 ** (bits - 1) - 1
    max_abs = weight.abs().amax(dim=1, keepdim=True).clamp(min=1e-8)
    scale = max_abs / qmax
    q = torch.clamp(torch.round(weight / scale), -qmax - 1, qmax)
    return q * scale


def activation_aware_quantize(
    weight: torch.Tensor,
    bits: int,
    input_scale: torch.Tensor,
    alpha: float = 0.5,
) -> torch.Tensor:
    """Ours: per-input-channel activation-aware quantization. `input_scale`
    is the per-input-channel mean activation magnitude measured on real
    calibration data (see calibrate.py). Weight channels that see larger
    activations get scaled up before quantizing -- so the same number of
    quantization levels covers a proportionally smaller range for them,
    reducing rounding error exactly where it matters most for the model's
    actual output -- then scaled back down afterward. Mathematically a
    no-op in full precision; the only effect is which weights are "hard"
    to represent once rounded to `bits`.

    `alpha` controls how much of the activation magnitude to fold into the
    scale (0 = plain RTN, 1 = fully activation-scaled). 0.5 here is a
    reasonable starting point (geometric mean between the two extremes),
    not a value we tuned or are claiming is optimal.

    weight shape: (out_features, in_features); input_scale shape: (in_features,)
    """
    s = input_scale.clamp(min=1e-5).pow(alpha)
    s = s / s.mean()  # keep overall magnitude comparable to the unscaled weight
    scaled_weight = weight * s.unsqueeze(0)

    qmax = 2 ** (bits - 1) - 1
    max_abs = scaled_weight.abs().amax(dim=1, keepdim=True).clamp(min=1e-8)
    scale = max_abs / qmax
    q = torch.clamp(torch.round(scaled_weight / scale), -qmax - 1, qmax)
    dequantized_scaled = q * scale
    return dequantized_scaled / s.unsqueeze(0)


def select_best_alpha(
    weight: torch.Tensor,
    bits: int,
    input_scale: torch.Tensor,
    candidates: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0),
) -> float:
    """Real AWQ does this: don't assume a fixed alpha helps -- search a
    small grid and keep whichever minimizes a reconstruction error that's
    weighted by activation importance (error on a channel the model barely
    uses matters less than error on a channel it relies on heavily). Chosen
    using only calibration-derived `input_scale`, never eval data -- no
    leakage into the number we later report.

    This also guards against the real failure mode this module's own first
    attempt hit: RTN's per-output-row scale is `max_abs` across *all*
    input channels in that row, so boosting a few channels can inflate the
    shared row-wise step and coarsen quantization for every channel in the
    row, including ones that weren't supposed to be protected. Searching
    alpha (with 0.0 = plain RTN always in the candidate set) means the
    search can never do worse than the naive baseline for a given layer.
    """
    best_alpha, best_err = 0.0, float("inf")
    weight_sq_importance = input_scale.clamp(min=1e-5).pow(2).unsqueeze(0)  # (1, in_features)
    for alpha in candidates:
        dequantized = activation_aware_quantize(weight, bits, input_scale, alpha=alpha)
        weighted_sq_err = ((weight - dequantized) ** 2 * weight_sq_importance).mean().item()
        if weighted_sq_err < best_err:
            best_err = weighted_sq_err
            best_alpha = alpha
    return best_alpha

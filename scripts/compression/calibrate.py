"""Runs the real model over the calibration corpus and records each
targeted Linear layer's mean per-input-channel activation magnitude -- the
real signal quant_methods.activation_aware_quantize() uses. This is the
"calibration" step: no gradients, no training, just observing what the
model's own activations actually look like on real code.
"""

import torch
import torch.nn as nn


def collect_activation_scales(
    model, tokenizer, calib_texts: list[str], target_module_suffixes: tuple[str, ...]
) -> dict[str, torch.Tensor]:
    """Returns {module_name: Tensor[in_features]} -- mean abs activation per
    input channel, averaged over every forward call during calibration."""
    sums: dict[str, torch.Tensor] = {}
    counts: dict[str, int] = {}
    hooks = []

    def make_hook(name):
        def hook(module, inputs, output):
            x = inputs[0].detach()
            x = x.reshape(-1, x.shape[-1]).abs().mean(dim=0).float().cpu()
            if name not in sums:
                sums[name] = x.clone()
                counts[name] = 1
            else:
                sums[name] += x
                counts[name] += 1

        return hook

    for name, module in model.named_modules():
        if isinstance(module, nn.Linear) and any(
            name.endswith(suffix) for suffix in target_module_suffixes
        ):
            hooks.append(module.register_forward_hook(make_hook(name)))

    if not hooks:
        raise ValueError(
            f"no Linear layers matched target_module_suffixes={target_module_suffixes} "
            "-- check the model's actual module names"
        )

    model.eval()
    with torch.no_grad():
        for text in calib_texts:
            ids = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            model(**ids)

    for h in hooks:
        h.remove()

    return {name: sums[name] / counts[name] for name in sums}

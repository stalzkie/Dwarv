"""DWARV_PLAN.md section 11.4: prove our activation-aware quantization
preserves more quality than naive RTN at the SAME bit-width, on a real
model, with real measured perplexity -- then re-run on a larger model to
check the advantage holds at scale.

Scope, stated plainly: this quantizes only `down_proj` layers (one
well-known activation-sensitive projection), not the whole model -- a
representative, apples-to-apples comparison (both methods touch the exact
same layers), not a claim about full-model compression quality. The
calibration/eval corpus (scripts/compression/calib_corpus/) is a small,
real, hand-written set of 10 Python snippets -- hackathon-scale, not an
academic benchmark; honestly small, not fabricated.

Usage:
    python run_experiment.py --model Qwen/Qwen2.5-Coder-0.5B-Instruct --bits 3
"""

import argparse
import copy
import json
import sys
from pathlib import Path

import torch
import torch.nn as nn
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).parent))
from calib_corpus.python_samples import SAMPLES  # noqa: E402
from calibrate import collect_activation_scales  # noqa: E402
from perplexity import compute_perplexity  # noqa: E402
from quant_methods import (  # noqa: E402
    activation_aware_quantize,
    naive_rtn_quantize,
    select_best_alpha,
)

TARGET_SUFFIXES = ("down_proj",)


def apply_quantization(model, bits, method, activation_scales=None):
    """Returns a NEW model (deep copy) with every targeted Linear layer's
    weight replaced by its quantize-then-dequantize ("fake quant") version
    -- simulates the precision loss without needing a real bit-packed
    format. Standard methodology for isolating a quantization method's
    quality impact (used in the GPTQ/AWQ papers themselves)."""
    model = copy.deepcopy(model)
    chosen_alphas = {}
    for name, module in model.named_modules():
        if isinstance(module, nn.Linear) and any(name.endswith(s) for s in TARGET_SUFFIXES):
            with torch.no_grad():
                if method == "naive":
                    module.weight.copy_(naive_rtn_quantize(module.weight, bits))
                elif method == "ours":
                    scale = activation_scales[name]
                    alpha = select_best_alpha(module.weight, bits, scale)
                    chosen_alphas[name] = alpha
                    module.weight.copy_(
                        activation_aware_quantize(module.weight, bits, scale, alpha=alpha)
                    )
    return model, chosen_alphas


def run(model_name: str, bits: int) -> dict:
    print(f"loading {model_name} ...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForCausalLM.from_pretrained(model_name, torch_dtype=torch.float32)
    model.eval()

    calib_texts = SAMPLES[:7]
    eval_texts = SAMPLES[7:]

    print("baseline (fp32) perplexity ...")
    baseline_ppl = compute_perplexity(model, tokenizer, eval_texts)
    print(f"  {baseline_ppl:.4f}")

    print(f"naive RTN @ {bits}-bit ...")
    naive_model, _ = apply_quantization(model, bits, "naive")
    naive_ppl = compute_perplexity(naive_model, tokenizer, eval_texts)
    print(f"  {naive_ppl:.4f}")
    del naive_model

    print("calibrating activation scales on real code ...")
    activation_scales = collect_activation_scales(model, tokenizer, calib_texts, TARGET_SUFFIXES)

    print(f"ours (activation-aware, alpha searched per layer) @ {bits}-bit ...")
    ours_model, chosen_alphas = apply_quantization(model, bits, "ours", activation_scales)
    ours_ppl = compute_perplexity(ours_model, tokenizer, eval_texts)
    print(f"  {ours_ppl:.4f}  (alphas: {chosen_alphas})")
    del ours_model

    result = {
        "model": model_name,
        "bits": bits,
        "quantized_layers": TARGET_SUFFIXES,
        "n_calib_texts": len(calib_texts),
        "n_eval_texts": len(eval_texts),
        "baseline_fp32_ppl": baseline_ppl,
        "naive_rtn_ppl": naive_ppl,
        "ours_activation_aware_ppl": ours_ppl,
        "ours_chosen_alphas": chosen_alphas,
        "naive_degradation_pct": (naive_ppl - baseline_ppl) / baseline_ppl * 100,
        "ours_degradation_pct": (ours_ppl - baseline_ppl) / baseline_ppl * 100,
        "ours_beats_naive": ours_ppl < naive_ppl,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--bits", type=int, default=3)
    args = parser.parse_args()

    result = run(args.model, args.bits)
    print(json.dumps(result, indent=2))

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)
    safe_name = args.model.replace("/", "_")
    out_path = out_dir / f"{safe_name}_bits{args.bits}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"saved to {out_path}")


if __name__ == "__main__":
    main()

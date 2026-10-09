# Prior art: weight quantization / model compression

Verified notes on weight-quantization and model-compression techniques that
could extend Dwarv's existing compression work (`src/dwarv/models/requantize.py`,
`scripts/compression/`). Primary sources only (arXiv papers, official
GitHub repos, llama.cpp's own PRs/docs) — read each source before citing;
do not trust a blog's paraphrase. Numbers quoted below are taken directly
from the cited source; where a specific figure could not be confirmed from
a primary source in the time available, this file says "not independently
verified" rather than inventing or rounding one.

This is a companion to `docs/PRIOR_ART.md` (which covers related *products*
— LMForge, TinyForge, etc.). This file is scoped to the *compression
techniques themselves*, one section per technique, each covering:

- **(a) Mechanism** — what it actually does to the weights.
- **(b) Calibration / retraining requirements** — zero-shot, calibration-data-only, or backprop/fine-tuning.
- **(c) Reported numbers** — the paper's own measured compression ratio and quality numbers, model/dataset named.
- **(d) Implementation / GGUF status** — whether a real, maintained open-source implementation exists, and whether it integrates with GGUF/llama.cpp specifically.
- **(e) Verdict for Dwarv** — directly integrable, conceptual inspiration only, or not applicable (with why).

Context already established and not re-derived here: Dwarv's own
`scripts/compression/quant_methods.py` experiment (naive per-channel RTN vs.
an "activation-aware" variant scaling weights by `calibration-measured
per-input-channel activation magnitude^alpha`, `alpha=0.5`, before
per-output-channel symmetric rounding) is explicitly framed as "ours" —
inspired by AWQ's core idea but a hackathon-scale simplification, not a
reimplementation; see the AWQ section below for exactly how it differs.
`src/dwarv/models/requantize.py` already wraps the real `llama-quantize
--allow-requantize` CLI. GGML has no sparse-tensor kernels (confirmed via
`ggml-org/llama.cpp` discussion #521), which is why unstructured
pruning (SparseGPT/Wanda-style) was already ruled out and only *structured*
pruning is considered below.

---

## 1. GPTQ

- **(a) Mechanism.** One-shot post-training quantization using approximate
  second-order (Hessian) information: for each layer, weights are quantized
  column-by-column, and the rounding error at each column is compensated by
  adjusting the remaining not-yet-quantized columns, using the inverse of
  the layer's Hessian computed from calibration activations. (arXiv 2210.17323,
  abstract: "a new one-shot weight quantization method based on approximate
  second-order information".)
- **(b) Calibration / retraining.** Needs calibration data (used to compute
  per-layer activation Hessians) but **no backprop or retraining** — it is
  explicitly a one-shot, post-training method
  (https://github.com/IST-DASLab/gptq README: "post-training quantization
  method with no retraining involved").
- **(c) Reported numbers.** From the GPTQ GitHub README's own WikiText2
  perplexity table for LLaMA (https://github.com/IST-DASLab/gptq):

  | Model | FP16 | 4-bit GPTQ | 3-bit GPTQ | 3-bit-g128 GPTQ |
  |---|---|---|---|---|
  | LLaMA-7B | 5.68 | 6.09 | 8.07 | 6.61 |
  | LLaMA-13B | 5.09 | 5.36 | 6.63 | 5.62 |
  | LLaMA-30B | 4.10 | 4.45 | 5.69 | 4.80 |
  | LLaMA-65B | 3.53 | 3.84 | 5.04 | 4.17 |

  The paper's abstract (arXiv 2210.17323) also reports quantizing GPT models
  up to 175B parameters to 3-4 bits "with negligible accuracy degradation,"
  and end-to-end inference speedups of roughly 3.25x (A100) / 4.5x (A6000).
- **(d) Implementation / GGUF status.** Real, maintained reference
  implementation at https://github.com/IST-DASLab/gptq, and the widely-used
  AutoGPTQ/ExLlama ecosystems build on it. **No GGUF/llama.cpp integration**
  — the repo and its CUDA kernels target its own runtime (and AutoGPTQ/
  ExLlama), not GGML; the GPTQ README makes no mention of llama.cpp or GGUF.
- **(e) Verdict.** Conceptual inspiration only. GPTQ's Hessian-based error
  compensation is a plausible *next* step past Dwarv's current naive-RTN
  vs. activation-aware comparison in `scripts/compression/quant_methods.py`
  (which does no error compensation across columns at all), but it is a
  real engineering lift — computing/inverting per-layer Hessians — and has
  no existing path into `llama-quantize`'s own quant levels. Not directly
  integrable into the GGUF pipeline; worth reading as the next fidelity
  tier for the "ours" experiment if time allows.

## 2. AWQ

- **(a) Mechanism.** Activation-aware weight quantization: AWQ observes
  that "not all weights in an LLM are equally important" and that salient
  weight channels should be identified from **activation** magnitude, not
  weight magnitude (arXiv 2306.00978 abstract). Rather than keeping salient
  weights in higher precision (which is hardware-unfriendly), AWQ applies a
  per-channel equivalent scaling transform that protects salient channels
  before quantizing everything uniformly, searched per-channel/group rather
  than fixed a priori.
- **(b) Calibration / retraining.** Needs calibration data only (activation
  statistics collected offline); explicitly **no backpropagation or
  reconstruction** — the paper states this is why it "generalizes to
  different domains and modalities without overfitting the calibration
  set" (arXiv 2306.00978 abstract).
- **(c) Reported numbers.** From the paper's own Table 4 (arXiv
  2306.00978, confirmed via the HTML rendering), WikiText-2 perplexity at
  INT3-g128:

  | Model | RTN | GPTQ | GPTQ-Reorder | AWQ |
  |---|---|---|---|---|
  | Llama-2-7B | 6.66 | 6.43 | 6.42 | **6.24** |
  | Llama-2-13B | 5.52 | 5.48 | 5.41 | **5.32** |
  | Llama-2-70B | 3.98 | 3.88 | 3.86 | **3.74** |
  | LLaMA-7B | 7.01 | 8.81 | 6.53 | **6.35** |
  | LLaMA-65B | 4.24 | 4.17 | 4.21 | **3.95** |

  AWQ beats RTN and both GPTQ variants at every size shown. The accompanying
  TinyChat runtime reports >3x speedup over Huggingface FP16 on desktop/
  mobile GPUs (same abstract).
- **(d) Implementation / GGUF status.** Real, maintained implementation at
  https://github.com/mit-han-lab/llm-awq, and the community AutoAWQ project
  (https://github.com/casper-hansen/AutoAWQ). **Partial GGUF integration
  exists**: llama.cpp PR #4593, "Add AWQ ... for llama, llama2, mpt, and
  mistral models" (https://github.com/ggml-org/llama.cpp/pull/4593), was
  merged into `ggml-org/llama.cpp` master on 2023-12-27. It does not
  reimplement AWQ's quantization — it applies AWQ's precomputed per-channel
  scales to the weights before GGUF's own k-quant quantization runs, so the
  AWQ scales reduce the error that k-quant then introduces, rather than AWQ
  producing the final quantized values itself.
- **(e) Verdict — directly comparable to Dwarv's own experiment.** AWQ is
  the acknowledged ancestor of Dwarv's "ours" method in
  `scripts/compression/quant_methods.py::activation_aware_quantize`, and
  it's worth being precise about how close that simplification actually is:
  - **Same core idea**: both use per-input-channel activation magnitude,
    measured on calibration data, to decide which weight channels get
    "easier" quantization.
  - **Different in practice**: AWQ determines its scale via a small grid
    search per channel group to directly minimize output error, and its
    stated rationale singles out *salient* channels specifically (the
    hardware-friendly alternative to leaving ~1% of weights at higher
    precision). Dwarv's version instead applies a single global exponent
    (`activation_scale.pow(alpha)` with a fixed `alpha=0.5`, normalized by
    the mean) uniformly to every channel — there is no search, no
    salient-channel identification, and no per-group tuning. It is AWQ's
    *intuition*, not AWQ's *algorithm*.
  - This gap is itself the honest next experiment: swap the fixed-`alpha`
    scaling for a small per-channel grid search over scale factors
    (minimizing actual output MSE on the calibration set, as AWQ does)
    and see whether it beats the current fixed-alpha version at the same
    bit-width on the same `down_proj` layers. That is incremental, stays
    inside the existing fake-quant harness, and is the most direct way to
    make Dwarv's own "ours" method less of a simplification.
  - Separately, PR #4593's scale-application approach is real and merged,
    but only for llama/llama2/mpt/mistral architectures, not confirmed for
    Qwen2.5-Coder; would need verification against the installed
    `llama-quantize` before relying on it operationally.

## 3. GGUF's own k-quants and i-quants (already shipping, lowest-effort lever)

- **(a) Mechanism — k-quants.** Added in `ggml-org/llama.cpp` PR #1684,
  authored by Iwan Kawrakow (@ikawrakow), merged 2023-06-05
  (https://github.com/ggml-org/llama.cpp/pull/1684). Weights are grouped
  into superblocks of nested sub-blocks; each sub-block gets its own scale
  (and, for "type-1" formulations, its own minimum), themselves quantized
  at reduced precision (4-6 bits), so the overall bits-per-weight is a
  mix of the quant level plus per-block scale/min overhead. This is the
  `Q2_K`...`Q6_K` family Dwarv already targets via `llama-quantize`.
- **(a) Mechanism — i-quants.** Added in PR #4773, also by @ikawrakow
  (https://github.com/ggml-org/llama.cpp/pull/4773), explicitly described
  in the PR as borrowing ideas from QuIP# (see section 4): a lattice-based
  codebook (inspired by QuIP#'s E8-lattice approach) selects a fixed set of
  256 representative points, chosen to maximize coverage and minimize the
  worst-case distance from any unselected point to its nearest selected
  one, then encodes a sign pattern per group of 8 weights (forcing an even
  number of sign flips, picking the least-important weight by `w * x^2` to
  flip when parity doesn't already hold) plus a 4-bit per-block scale. This
  gives `IQ2_XXS` 2.0625 bits/weight overall (64 bits per 32-weight block).
- **(c) Reported numbers.** k-quants, PR #1684, LLaMA-7B WikiText perplexity:
  F16 baseline 5.9066; Q2_K 6.7764; Q3_K_M 6.1503; Q4_K_S 6.0215; Q5_K_S
  5.9419; Q6_K 5.9110 (within ~0.1% of F16). i-quants, PR #4773, at context
  length 4096: Mistral-7B IQ2_XXS 1.855 GiB / PPL 6.446; LLaMA-v2-7B 1.728
  GiB / PPL 7.067; LLaMA-v2-70B 17.03 GiB / PPL 4.079 — the PR states these
  are "similar to QuIP#" at the same bit-width.
- **(d) Implementation / GGUF status.** This *is* the GGUF/llama.cpp
  format — both are merged, maintained, and already present in every
  llama.cpp release Dwarv downloads, exposed directly as `llama-quantize`
  target levels.
- **(e) Verdict — directly integrable, and already integrated.** This is
  the lowest-effort lever of all: Dwarv's `requantize.py` already calls
  `llama-quantize --allow-requantize <in> <out> <LEVEL>` (section 11.1 of
  `DWARV_PLAN.md`); extending the set of `LEVEL` values it's willing to
  target to the `IQ*` family (e.g. `IQ3_XXS`, `IQ2_XXS`) for very
  memory-constrained machines is a config/UI change, not a new mechanism —
  no new research, no new code path, just verifying (per `DWARV_PLAN.md`'s
  own stated discipline) that the installed `llama-quantize` build actually
  supports the i-quant levels before offering them, since i-quants also
  benefit from an importance matrix (`--imatrix`) that k-quants do not
  require, which is an operational detail to confirm against the installed
  binary rather than assume.

## 4. QuIP / QuIP#

- **(a) Mechanism — QuIP.** Two steps: (1) an adaptive rounding procedure
  that minimizes a quadratic proxy objective (similar in spirit to GPTQ's
  Hessian-based correction), and (2) "incoherence processing" — multiplying
  weights and the Hessian by random orthogonal matrices before and after
  quantization so that no single weight or rounding direction dominates
  (arXiv 2307.13304 abstract).
- **(a) Mechanism — QuIP#.** Upgrades QuIP in two ways (arXiv 2402.04396
  abstract): randomized Hadamard transforms instead of generic random
  orthogonal matrices for incoherence processing (faster, better
  theoretical properties), and vector quantization via a lattice codebook
  built on the E8 lattice (optimal known 8-dimensional unit-ball packing)
  to exploit the near-Gaussian distribution of incoherence-processed
  weights, instead of QuIP's scalar rounding.
- **(b) Calibration / retraining.** QuIP's abstract does not state a
  calibration-data requirement explicitly beyond its Hessian-based
  objective (consistent with GPTQ-style per-layer calibration). QuIP#'s
  abstract states it "incorporates fine-tuning mechanisms to enhance
  fidelity to original model performance" — i.e., it is not purely
  calibration-only the way AWQ/HQQ are; exact calibration-set size and
  fine-tuning cost were **not independently verified** from the abstract
  alone.
- **(c) Reported numbers.** QuIP's abstract states it is among "the first
  LLM quantization methods that produce viable results using only two bits
  per weight," but does not give a specific perplexity table in the
  abstract text retrieved — **not independently verified** beyond that
  qualitative claim. QuIP# targets "extreme compression regimes (≤4 bits
  per weight)" and claims to outperform existing PTQ methods and "enable
  new behaviors in PTQ scaling" (arXiv 2402.04396 abstract); accepted at
  ICML 2024. Specific QuIP# perplexity numbers were not retrieved from the
  abstract — see section 3 above for llama.cpp's own i-quant numbers, which
  the PR author states land "similar to QuIP#" at matched bit-width, which
  is the closest verified comparison point available here.
- **(d) Implementation / GGUF status.** QuIP# has a real, maintained
  reference implementation at
  https://github.com/Cornell-RelaxML/quip-sharp. **No GGUF/llama.cpp
  integration** — a llama.cpp feature request for "QuIP Sharp Support"
  (https://github.com/ggml-org/llama.cpp/issues/4386) was opened and
  closed without the format being natively added; QuIP# ships its own
  CUDA kernels for its lattice codebook rather than targeting GGML.
- **(e) Verdict.** Not applicable as a direct integration — no path into
  `llama-quantize`, and the core lattice-codebook idea is already the
  documented inspiration for llama.cpp's own i-quants (section 3), which
  Dwarv can use today with zero new engineering. Conceptual inspiration
  only, and largely already captured by i-quants; reading QuIP# itself
  adds little beyond what the i-quant PR already took from it.

## 5. SpinQuant

- **(a) Mechanism.** Applies learned rotation matrices to weight and
  activation matrices inside the transformer so that, in exact arithmetic,
  the rotation is a no-op (same output), but after quantization the
  rotated matrices have fewer outliers and quantize more accurately. The
  paper notes "some random rotations lead to much better quantization than
  others," motivating learning rather than fixing the rotation (arXiv
  2405.16406 abstract).
- **(b) Calibration / retraining.** Requires optimization: the official
  repo (https://github.com/facebookresearch/SpinQuant) confirms
  gradient-based learning of the rotations via Cayley optimization
  ("Learning rotation with Cayley optimization greatly enhance the final
  performance"), with training scripts exposing
  `per_device_train_batch_size` for a "rotation optimization" step — i.e.
  this needs backprop, not just calibration statistics. Exact calibration
  set size and step count were **not independently verified** from the
  README.
- **(c) Reported numbers.** From arXiv 2405.16406's abstract: 4-bit
  quantization of weights, activations, and KV-cache on LLaMA-2 7B and
  LLaMA-3 8B; LLaMA-2 7B's zero-shot reasoning accuracy is only 2.9 points
  below full precision, "surpasses LLM-QAT by 19.1 points and SmoothQuant
  by 25.0 points"; for LLaMA-3 8B it reduces the gap to full precision by
  up to 45.1% relative to QuaRot.
- **(d) Implementation / GGUF status.** Real, maintained implementation at
  https://github.com/facebookresearch/SpinQuant, with support confirmed for
  PyTorch/HuggingFace Transformers and ExecuTorch (GPU/mobile-GPU-oriented).
  **No GGUF/llama.cpp support** is mentioned anywhere in the repo.
- **(e) Verdict — not applicable, for two independent reasons.** (1) No
  GGUF path exists. (2) It requires gradient-based optimization of the
  rotation matrices, which conflicts with Dwarv's stated no-training/no-
  backprop constraint for this phase of work, same category of conflict as
  BitNet below. Purely informative reading.

## 6. HQQ (Half-Quadratic Quantization)

- **(a) Mechanism.** Formulates weight quantization as a half-quadratic
  (robust-statistics-style) optimization that solves for scale/zero-point
  per group directly, without needing to observe activations at all —
  the project's own framing: "a fast and accurate model quantizer that
  skips the need for calibration data" (https://github.com/mobiusml/hqq
  README).
- **(b) Calibration / retraining.** **No calibration data required at
  all** — confirmed directly from the repo's own FAQ: "Quantize the
  largest models, without calibration data, in just a few minutes." An
  optional extension ("HQQ+") adds trainable LoRA adapters via PEFT for
  extra quality at very low bit-depths, but that is explicitly optional,
  not required for base HQQ.
- **(c) Reported numbers.** The README does not include specific
  perplexity/accuracy figures inline and defers to separate blog posts for
  benchmarks — **not independently verified** here; no specific number is
  claimed in this document as a result.
- **(d) Implementation / GGUF status.** Real, actively maintained
  implementation at https://github.com/mobiusml/hqq, with confirmed
  integration into HuggingFace Transformers, PyTorch, and vLLM. **No
  GGUF/llama.cpp integration** — a llama.cpp feature request for HQQ
  support (https://github.com/ggml-org/llama.cpp/issues/4782) was opened
  and closed without being added, reportedly because HQQ's own
  implementation was changing too rapidly to pin to a tagged version.
- **(e) Verdict.** Conceptually the most relevant *constraint match* of
  everything surveyed here — zero calibration data is an even weaker
  requirement than Dwarv's own activation-aware experiment (which already
  uses a small calibration corpus) — but there is no GGUF path and no
  evidence the gap has since closed. Conceptual inspiration only for the
  `scripts/compression/` experiment: if a future iteration wants to try a
  calibration-free variant alongside naive RTN and activation-aware RTN,
  HQQ's half-quadratic solve (rather than straight round-to-nearest) is
  the right method name to go read in full, not just its abstract.

## 7. SqueezeLLM

- **(a) Mechanism.** Two techniques combined (arXiv 2306.07629 abstract):
  (1) sensitivity-based non-uniform quantization, searching for an optimal
  per-weight bit-level assignment using second-order (Hessian-derived)
  sensitivity information rather than uniform bucket spacing; (2)
  dense-and-sparse decomposition, pulling outlier/high-sensitivity weight
  values out into a separate sparse matrix kept at higher precision, so
  the bulk dense matrix can be quantized more aggressively.
- **(b) Calibration / retraining.** Framed by the paper as a
  "post-training quantization framework" — no full retraining; uses
  second-order (Hessian-style) information computed from calibration data,
  same general family as GPTQ. Exact calibration set size was **not
  independently verified** here.
- **(c) Reported numbers.** From the abstract: "ultra-low precisions of up
  to 3-bit"; at 3-bit on LLaMA models, SqueezeLLM "reduces the perplexity
  gap from the FP16 baseline by up to 2.1x as compared to the state-of-the-
  art methods with the same memory requirement"; up to 2.3x inference
  speedup on an A6000 GPU versus baseline. Specific absolute perplexity
  values were not retrieved from the abstract/README — **not independently
  verified** beyond the relative 2.1x/2.3x figures above, which are the
  paper's own.
- **(d) Implementation / GGUF status.** Real, maintained implementation at
  https://github.com/SqueezeAILab/SqueezeLLM, requiring a custom CUDA
  runtime (`setup_cuda.py install`) and proprietary `.pt` checkpoint
  format; confirmed integration with vLLM. **No GGUF/llama.cpp
  integration** mentioned anywhere in the repo.
- **(e) Verdict — not applicable for Dwarv's engine, conceptually
  interesting for the sparse-outlier idea only.** The dense-and-sparse
  split is a genuinely different idea from anything in
  `scripts/compression/quant_methods.py` (which quantizes every weight
  uniformly within a layer), but it requires a CUDA runtime and a format
  GGML has no equivalent for — pulling a handful of outlier weights out
  of a GGUF tensor into a separate sparse side-structure is not something
  `llama-quantize`/GGML supports today. Conceptual inspiration only, and
  lower priority than AWQ or HQQ as reading material given it needs more
  new infrastructure to even prototype in the existing fake-quant harness.

## 8. AQLM

- **(a) Mechanism.** Generalizes classical additive quantization (from
  information retrieval) to LLM weights: multiple codebooks are learned
  jointly and applied additively to reconstruct each weight group, with
  codebook parameters optimized jointly across each transformer block
  ("input-adaptive" additive quantization) (arXiv 2401.06118 abstract).
- **(b) Calibration / retraining.** Requires GPU-based iterative
  optimization, explicitly heavier than GPTQ-style calibration — the
  official repo (https://github.com/Vahe1994/AQLM) states quantizing a 7B
  model takes roughly 1 day on a single 80GB A100 (reducible to ~14.5
  hours with 2 GPUs), and a 70B model takes 10-14 days on a single GPU or
  about 3.75 days on 8 A100s. This is calibration-time optimization, not
  full model retraining, but it is squarely a GPU-optimization workload,
  not a cheap one-shot pass.
- **(c) Reported numbers.** From the AQLM repo's own WikiText-2 results
  table: Llama-2-7B 2-bit (1x16 scheme) PPL 5.92; Llama-2-13B 2-bit PPL
  5.22; Llama-2-7B 1-bit (1x8 scheme) PPL 7.85; Mistral-7B 2-bit PPL 5.40;
  Mixtral-8x7B 2-bit PPL 3.35. The paper's abstract claims AQLM is "the
  first scheme that is Pareto optimal in terms of accuracy-vs-model-size
  when compressing to less than 3 bits per parameter," and that its
  inference implementations "match or outperform optimized FP16
  implementations for speed."
- **(d) Implementation / GGUF status.** Real, maintained implementation at
  https://github.com/Vahe1994/AQLM, confirmed integrated with HuggingFace
  Transformers (`from_pretrained`) via custom CUDA/Triton/Numba kernels.
  **Confirmed no GGUF or llama.cpp support** — stated directly in the
  repo. A `ggml-org/llama.cpp` discussion (#5063, "Even more quantization
  types?") separately notes AQLM's 2-/3-bit results are state-of-the-art
  among published methods, while observing AQLM 3-bit performs comparably
  to llama.cpp's own `Q3_K_M` — i.e. GGUF's existing k-quants are already
  in the same quality neighborhood at 3-bit, without AQLM's multi-day
  GPU optimization cost.
- **(e) Verdict — not applicable.** No GGUF integration exists, the
  per-model calibration cost (days on an A100) is incompatible with
  Dwarv's "runs locally, no network, no heavyweight GPU optimization step"
  posture, and the community's own comparison suggests the achievable
  quality gain over existing k-quants at matched bit-width is modest. Not
  worth prototyping; informative reading only, mainly as the upper bound
  of "how much quality is theoretically on the table at 2-3 bits if you're
  willing to spend a GPU-day calibrating."

## 9. BitNet / BitNet b1.58

- **(a) Mechanism.** BitNet (arXiv 2310.11453) introduces `BitLinear`, a
  drop-in replacement for linear layers that uses 1-bit weights. BitNet
  b1.58 (arXiv 2402.17764) generalizes this to **ternary** weights, every
  parameter constrained to `{-1, 0, 1}`.
- **(b) Calibration / retraining — the critical caveat for Dwarv.** Both
  variants are trained **from scratch**, not calibrated or fine-tuned from
  an existing dense checkpoint. BitNet's abstract states BitLinear is
  designed "to train 1-bit weights from scratch." BitNet b1.58's full text
  confirms the models in its own experiments "were trained from scratch,
  with 1.58-bit weights and 8-bit activations" (arXiv 2402.17764). This
  directly conflicts with Dwarv's "no training" constraint — there is no
  documented path to take an already-trained dense Qwen2.5-Coder GGUF and
  ternarize it after the fact with these methods; the ternary behavior is
  baked in during pretraining.
- **(c) Reported numbers.** From BitNet b1.58's own Table 1 (arXiv
  2402.17764, HTML rendering): WikiText perplexity, LLaMA FP16 vs. BitNet
  b1.58 at matched size and training tokens — 700M: 12.33 vs. 12.87; 1.3B:
  11.25 vs. 11.29; 3B: 10.04 vs. **9.91** (BitNet b1.58 slightly *better*
  at 3B). The paper's own stated finding: "BitNet b1.58 starts to match
  full precision LLaMA LLM at 3B model size in terms of perplexity," with
  zero-shot accuracy across ARC-easy/ARC-challenge/HellaSwag/BoolQ/PIQA/
  OpenBookQA/Winogrande also converging (~49-50% average) at 3B. Efficiency
  at 3B: 3.55x less memory, 2.71x faster latency than the FP16 baseline; at
  70B scale, 4.1x speedup and a claimed 71.4x reduction in matrix-
  multiplication arithmetic energy on a 7nm process node.
- **(d) Implementation / GGUF status.** Microsoft's own `bitnet.cpp`
  (https://github.com/microsoft/BitNet; also published as "Bitnet.cpp:
  Efficient Edge Inference for Ternary LLMs," ACL 2025, arXiv 2502.11880)
  is a separate inference framework for running BitNet-family ternary
  checkpoints. Mainline llama.cpp separately added native ternary support:
  PR #8151, "ggml-quants: ternary packing for TriLMs and BitNet b1.58"
  (https://github.com/ggml-org/llama.cpp/pull/8151, author @compilade),
  adding `TQ1_0` (1.6875 bits/weight) and `TQ2_0` (2.0625 bits/weight)
  GGUF quant types. The PR's own numbers: a 728.84M-parameter BitNet
  b1.58-large model is 1391.26 MiB in F16, 176.65 MiB as `TQ1_0`, 207.03
  MiB as `TQ2_0`; `TQ1_0` is reported as usually slightly faster than
  `Q4_K`, and `TQ2_0` as the fastest quant on AVX2 hardware. Crucially, this
  support only *packs/runs* a model that is **already ternary** (via
  `convert_hf_to_gguf.py --outtype tq1_0`/`tq2_0`) — it does not ternarize
  an arbitrary pretrained dense model like Qwen2.5-Coder; that would need
  the from-scratch training the mechanism section above describes.
- **(e) Verdict — not applicable for the existing bundled model suite.**
  Dwarv's three bundled models are standard dense Qwen2.5-Coder checkpoints
  that were never trained with BitNet-style ternary constraints, and
  nothing in the primary sources above describes a supported way to
  retrofit ternary weights onto an already-dense model without training
  from scratch — which Section 2.2 of `DWARV_PLAN.md` rules out as a
  non-goal. Pure informative reading: useful to know llama.cpp already has
  first-class ternary GGUF types ready to go, in case a future phase ever
  considers bundling a genuinely BitNet-native model instead of
  Qwen2.5-Coder, but not something to prototype against the current suite.

## 10. Structured pruning (ShortGPT, LLM-Pruner)

Ties back to `DWARV_PLAN.md` section 11.3's existing lower-priority note:
unstructured pruning is already ruled out because GGML has no sparse
kernels (discussion #521); only *structured* pruning — removing whole
layers or whole coupled structures (heads, blocks) — produces a real
compute/memory benefit under GGML's dense kernels, because the result is
simply a smaller dense tensor, not a sparse one.

- **(a) Mechanism — ShortGPT.** Introduces a "Block Influence" (BI) metric
  per transformer layer, computed from hidden-state similarity across a
  calibration set, then **deletes whole redundant layers** outright based
  on their BI score — no neuron-level surgery, no retraining step at all
  (arXiv 2403.03853).
- **(a) Mechanism — LLM-Pruner.** Uses gradient information to detect
  dependency structures within the model (which weights/heads/channels are
  coupled) and removes whole coupled structures together (e.g. entire
  attention heads) so the result stays a valid, smaller dense model (arXiv
  2305.11627).
- **(b) Calibration / retraining.** ShortGPT uses a calibration set (the
  paper specifically names PG19-style unlabelled text samples) to compute
  BI scores, and explicitly performs **no retraining** at all after layer
  removal (arXiv 2403.03853, HTML rendering). LLM-Pruner **does** require a
  short recovery fine-tune after pruning: "the performance of pruned models
  can be efficiently recovered through tuning techniques, LoRA, in merely 3
  hours, requiring only 50K data" (arXiv 2305.11627) — specifically LoRA
  tuning on 50,000 Alpaca samples, on a single GPU.
- **(c) Reported numbers.** ShortGPT on LLaMA2-13B: removing 10 of 40
  layers (25%) drops MMLU from 55.0 to 52.2. On LLaMA2-7B at a 27.1%
  pruning ratio, average benchmark retention is 86.31% (MMLU 45.39 ->
  43.96). On Baichuan2-7B at a 24.2% pruning ratio, average retention is
  85.10% (MMLU 53.87 -> 45.77) (arXiv 2403.03853, HTML rendering).
  LLM-Pruner on LLaMA-7B at a 20% parameter-reduction ratio: 89.8%
  performance retention with no post-training, rising to 94.97% retention
  after the 3-hour LoRA recovery step (arXiv 2305.11627, HTML rendering).
- **(d) Implementation / GGUF status.** LLM-Pruner has a maintained
  reference implementation (validated on LLaMA, Vicuna, ChatGLM per its
  abstract); ShortGPT's own code availability was **not independently
  verified** in this pass (not confirmed from the abstract/HTML page
  retrieved). **Neither has any GGUF/llama.cpp-specific integration** —
  both operate on the original PyTorch/HuggingFace checkpoint, producing a
  smaller dense checkpoint that would then need to go through llama.cpp's
  normal `convert_hf_to_gguf.py` + `llama-quantize` pipeline like any other
  model, same as today.
- **(e) Verdict.** Conceptual inspiration / a real but larger-scope future
  step than anything else here, consistent with `DWARV_PLAN.md` section
  11.3 already flagging structured pruning as "kept in the plan, lower
  priority." The mechanism fits GGML's dense kernels (an actually-smaller
  model, not a sparse one), and LLM-Pruner's own numbers are a genuine
  quality bar to beat. But both require running against the **original
  HuggingFace checkpoint before GGUF conversion** — they are not something
  `llama-quantize` or any GGUF-side tool can apply to an already-quantized
  `.gguf` file the way `requantize.py` does — so this is architecturally a
  different, heavier pipeline stage (pruning happens upstream of
  quantization, not as an alternative to it) and a materially bigger lift
  than section 11.1's local requantization lever. Not a hackathon-scope
  prototype target; reading material for a genuinely "next phase" step.

---

## Recommended next step for Dwarv

Given the hackathon's CPU-first, GGUF/llama.cpp, no-retraining constraints,
exactly two items above are worth prototyping next, and the rest are
informative reading only:

1. **GGUF's own i-quants (section 3) — ship this first, essentially for
   free.** `src/dwarv/models/requantize.py` already wraps `llama-quantize
   --allow-requantize`; the only work is verifying the installed build
   supports `IQ2_XXS`/`IQ3_XXS` and (per the PR) whether an `--imatrix`
   importance matrix is needed for good quality at those levels, then
   adding those levels to whatever decides target quant in `models/suite.py`.
   No new research, no new quantization code — this is strictly lower
   effort than everything else surveyed here and extends exactly the lever
   section 11.1/11.2 of `DWARV_PLAN.md` already committed to.
2. **A real per-channel scale search for `scripts/compression/`'s "ours"
   method (AWQ, section 2) — the natural next experiment, not a product
   change.** The current `activation_aware_quantize` uses a single global
   `alpha=0.5` exponent rather than AWQ's grid-searched per-channel/group
   scale minimizing actual output error. Implementing that search (still
   inside the existing fake-quant harness, still only on `down_proj`, same
   Qwen2.5-Coder-0.5B model) would make the comparison a legitimately
   closer reproduction of AWQ's own method rather than just "AWQ's
   intuition," and the paper's own numbers (AWQ beating GPTQ and RTN at
   every LLaMA/Llama-2 size tested in Table 4) give a concrete target to
   check the hackathon-scale version against.

Purely informative reading, not worth prototyping in this phase: GPTQ and
SqueezeLLM (real quality gains, but no GGUF path and real engineering cost
to even fake-quant-simulate properly); QuIP/QuIP# (its core idea is already
what llama.cpp's i-quants borrowed, so reading it adds little beyond what
section 3 already covers); SpinQuant and AQLM (both need GPU-based
optimization — Cayley SGD training and multi-GPU-day codebook fitting,
respectively — incompatible with a CPU-first, no-training posture, and
neither has a GGUF path); HQQ (genuinely matches Dwarv's calibration-light
spirit better than anything else here, but still has no GGUF integration,
and its own llama.cpp feature request was closed, not merged); BitNet/
BitNet b1.58 (requires training from scratch, which Section 2.2 of
`DWARV_PLAN.md` rules out as a non-goal, regardless of how good llama.cpp's
native `TQ1_0`/`TQ2_0` ternary support already is); ShortGPT/LLM-Pruner
(a real, lower-priority-but-legitimate future step per section 11.3, but
architecturally a pre-GGUF-conversion pipeline stage, not an extension of
`requantize.py`).

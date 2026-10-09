"""Real, standard perplexity measurement: exp(mean cross-entropy over
held-out tokens). Lower = the model assigns higher probability to the
actual next token = less quality lost. This is the metric the experiment
uses to compare quantization methods -- not a proxy we invented.
"""

import torch


def compute_perplexity(model, tokenizer, texts: list[str]) -> float:
    model.eval()
    total_nll = 0.0
    total_tokens = 0
    with torch.no_grad():
        for text in texts:
            ids = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            input_ids = ids["input_ids"]
            if input_ids.shape[1] < 2:
                continue
            out = model(input_ids=input_ids, labels=input_ids)
            n_tokens = input_ids.shape[1] - 1  # HF shifts labels internally
            total_nll += out.loss.item() * n_tokens
            total_tokens += n_tokens
    if total_tokens == 0:
        raise ValueError("no tokens to evaluate -- eval texts too short")
    return torch.exp(torch.tensor(total_nll / total_tokens)).item()

raise NotImplementedError(
    "Step 5: choose_model(hardware, models=[small, medium, large]) -> (ModelId, Explanation). "
    "Picks the largest bundled Qwen2.5-Coder size whose measured RSS (configs/models.yaml) "
    "fits sys_available_mb - safety_margin, and builds a human-readable explanation from the "
    "real numbers used -- never say anything the numbers don't support."
)

"""Heuristic vision capability inference from model IDs.

Used when provider APIs / LiteLLM cost maps do not expose vision metadata
(common for Bedrock cross-region IDs, Bifrost routes, and self-hosted
OpenAI-compatible endpoints such as vLLM).
"""

# Known Bedrock vision-capable models (for fallback when base model not in region)
BEDROCK_VISION_MODELS = frozenset(
    {
        "anthropic.claude-3",
        "anthropic.claude-4",
        "amazon.nova-pro",
        "amazon.nova-lite",
        "amazon.nova-premier",
    }
)

# Known Bifrost/OpenAI-compatible vision-capable model families where the
# source API does not expose this metadata directly.
BIFROST_VISION_MODEL_FAMILIES = frozenset(
    {
        "anthropic/claude-3",
        "anthropic/claude-4",
        "amazon/nova-pro",
        "amazon/nova-lite",
        "amazon/nova-premier",
        "openai/gpt-4o",
        "openai/gpt-4.1",
        "google/gemini",
        "meta-llama/llama-3.2",
        "mistral/pixtral",
        "qwen/qwen2.5-vl",
        "qwen/qwen-vl",
        # Self-hosted / vLLM IDs (no vendor prefix). Gemma 3+ and Gemma 4 are
        # natively multimodal; LiteLLM cost map does not know these slugs.
        "gemma4",
        "gemma-4",
        "gemma3",
        "gemma-3",
    }
)


def infer_vision_support(model_id: str) -> bool:
    """Infer vision support from model ID when base model metadata unavailable.

    Used for providers like Bedrock, Bifrost, and OpenAI-Compatible (vLLM)
    where vision support may need to be inferred from naming conventions.
    """
    model_id_lower = model_id.lower()
    if any(vision_model in model_id_lower for vision_model in BEDROCK_VISION_MODELS):
        return True

    normalized_model_id = model_id_lower.replace(".", "/")
    return any(
        vision_model in normalized_model_id
        for vision_model in BIFROST_VISION_MODEL_FAMILIES
    )

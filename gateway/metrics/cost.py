"""Cost estimation for DeepSeek API usage."""

# Default pricing (per 1M tokens)
_DEFAULT_INPUT_PRICE = 0.14  # $ per 1M input tokens
_DEFAULT_OUTPUT_PRICE = 0.42  # $ per 1M output tokens


class CostCalculator:
    def __init__(
        self,
        input_price_per_1m: float = _DEFAULT_INPUT_PRICE,
        output_price_per_1m: float = _DEFAULT_OUTPUT_PRICE,
    ):
        self.input_price = input_price_per_1m
        self.output_price = output_price_per_1m

    def cost_for_tokens(
        self, prompt_tokens: int, completion_tokens: int
    ) -> float:
        """Estimated cost in USD for a given number of tokens."""
        cost = (
            (prompt_tokens / 1_000_000) * self.input_price
            + (completion_tokens / 1_000_000) * self.output_price
        )
        return round(cost, 6)

    def savings_from_cached_tokens(
        self, tokens_saved: int
    ) -> float:
        """Estimated savings in USD from cached tokens.

        We assume cached tokens are primarily input/prompt tokens.
        """
        return round(
            (tokens_saved / 1_000_000) * self.input_price, 6
        )

    def report(self, prompt: int, completion: int, saved: int) -> dict:
        spent = self.cost_for_tokens(prompt, completion)
        saved_usd = self.savings_from_cached_tokens(saved)
        return {
            "cost_spent_usd": spent,
            "cost_saved_usd": saved_usd,
            "net_savings_usd": round(saved_usd - spent, 6),
            "total_prompt_tokens": prompt,
            "total_completion_tokens": completion,
            "estimated_input_cost_per_1m": self.input_price,
            "estimated_output_cost_per_1m": self.output_price,
        }

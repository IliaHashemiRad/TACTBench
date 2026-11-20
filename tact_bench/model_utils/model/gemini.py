import json
import os

from tact_bench.model_utils.api.datapoint import Datapoint
from tact_bench.model_utils.model.chat import ChatModel, Message
from tact_bench.model_utils.model.completion import approx_cost_for_datapoint, approx_prompt_str
from tact_bench.model_utils.model.general_model import wrap_temperature
from tact_bench.model_utils.model.utils import approx_num_tokens

DEFAULT_GEMINI_MODEL = "gemini-1.5-pro"
DEFAULT_MAX_TOKENS = 8192
ENV_VAR_API_KEY = "GOOGLE_API_KEY"

PRICE_PER_INPUT_TOKEN_MAP = {
    "gemini-1.5-pro": 3.5 / 1000000,
    "gemini-1.5-flash": 0.075 / 1000000,
    "gemini-2.0-flash": 0.1 / 1000000,
    "gemini-2.5-pro-preview-05-06": 1.25 / 1000000,
    "gemini-2.5-flash-preview-04-17": 0.15 / 1000000,
}
INPUT_PRICE_PER_TOKEN_FALLBACK = 3.5 / 1000000

CAPABILITY_SCORE_MAP = {
    "gemini-1.5-pro": 0.8,
    "gemini-1.5-flash": 0.5,
    "gemini-2.0-flash": 0.6,
    "gemini-2.5-pro-preview-05-06": 1.0,
    "gemini-2.5-flash-preview-04-17": 0.7,
}
CAPABILITY_SCORE_FALLBACK = 0.5

# TODO: implement
LATENCY_MS_PER_OUTPUT_TOKEN_MAP = {}
# TODO: implement
LATENCY_MS_PER_OUTPUT_TOKEN_FALLBACK = 0.0

MAX_CONTEXT_LENGTH_MAP = {
    "gemini-1.5-pro": 1048576,
    "gemini-1.5-flash": 1048576,
    "gemini-2.0-flash": 1048576,
    "gemini-2.5-pro-preview-05-06": 1048576,
    "gemini-2.5-flash-preview-04-17": 1048576,
}
MAX_CONTEXT_LENGTH_FALLBACK = 1048576


class GeminiModel(ChatModel):
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        import google.generativeai as genai

        if model is None:
            self.model = DEFAULT_GEMINI_MODEL
        else:
            self.model = model

        if api_key is None:
            api_key = os.getenv(ENV_VAR_API_KEY)
            if api_key is None:
                raise ValueError(f"{ENV_VAR_API_KEY} environment variable is not set")
        genai.configure(api_key=api_key)
        self.client = genai.GenerativeModel(self.model)
        self.temperature = temperature

    def _remap_messages(self, messages: list[dict[str, str]]) -> tuple[str | None, list[dict]]:
        """Convert messages to Gemini format (system instruction + contents).

        Gemini uses a separate system_instruction parameter and expects
        alternating 'user'/'model' roles in contents.
        """
        system_parts = []
        contents = []

        for message in messages:
            role = message["role"]
            content = message["content"]
            if role == "system":
                system_parts.append(content)
            elif role == "assistant":
                contents.append({"role": "model", "parts": [content]})
            else:
                contents.append({"role": "user", "parts": [content]})

        # Merge consecutive messages with the same role
        merged = []
        for msg in contents:
            if merged and merged[-1]["role"] == msg["role"]:
                merged[-1]["parts"].extend(msg["parts"])
            else:
                merged.append(msg)

        system_instruction = "\n\n".join(system_parts) if system_parts else None
        return system_instruction, merged

    def generate_message(
        self,
        messages: list[Message],
        force_json: bool,
        temperature: float | None = None,
    ) -> Message:
        import google.generativeai as genai

        if temperature is None:
            temperature = self.temperature
        msgs = self.build_generate_message_state(messages)
        system_instruction, contents = self._remap_messages(msgs)

        generation_config = genai.types.GenerationConfig(
            temperature=wrap_temperature(temperature),
            max_output_tokens=DEFAULT_MAX_TOKENS,
        )
        if force_json:
            generation_config.response_mime_type = "application/json"

        # Recreate model with system instruction if present
        if system_instruction:
            model = genai.GenerativeModel(
                self.model, system_instruction=system_instruction
            )
        else:
            model = self.client

        res = model.generate_content(
            contents,
            generation_config=generation_config,
        )
        return self.handle_generate_message_response(
            prompt=msgs, content=res.text, force_json=force_json
        )

    def get_approx_cost(self, dp: Datapoint) -> float:
        cost_per_token = PRICE_PER_INPUT_TOKEN_MAP.get(self.model, INPUT_PRICE_PER_TOKEN_FALLBACK)
        return approx_cost_for_datapoint(dp=dp, price_per_input_token=cost_per_token)

    def get_latency(self, dp: Datapoint) -> float:
        latency_per_output_token = LATENCY_MS_PER_OUTPUT_TOKEN_MAP.get(
            self.model, LATENCY_MS_PER_OUTPUT_TOKEN_FALLBACK
        )
        return approx_cost_for_datapoint(dp=dp, price_per_input_token=latency_per_output_token)

    def get_capability(self) -> float:
        return CAPABILITY_SCORE_MAP.get(self.model, CAPABILITY_SCORE_FALLBACK)

    def supports_dp(self, dp: Datapoint) -> bool:
        prompt = approx_prompt_str(dp)
        return approx_num_tokens(prompt) <= MAX_CONTEXT_LENGTH_MAP.get(
            self.model, MAX_CONTEXT_LENGTH_FALLBACK
        )

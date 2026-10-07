import os
from typing import Iterable, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import ValidationError

try:
    # When imported as part of app.reasoning
    from .schemas import VerificationResult, Verdict
except ImportError:
    # When running this file directly
    from schemas import VerificationResult, Verdict


load_dotenv()


DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-flash-latest",
)


class StructuredOutputError(RuntimeError):
    """Raised when an LLM response violates our grounding requirements."""
    pass


class LLMClient:
    """
    Central Gemini client for the reasoning layer.

    Important:
    We do NOT parse free-form JSON using regex or markdown stripping.

    Gemini is asked to return data matching VerificationResult directly.
    """

    def __init__(self, model: Optional[str] = None):
        api_key = os.getenv("GOOGLE_API_KEY")

        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is missing. "
                "Add it to your .env file."
            )

        self.client = genai.Client(api_key=api_key)
        self.model = model or DEFAULT_MODEL

    def generate_verification_result(
        self,
        *,
        prompt: str,
        system_instruction: str,
        valid_evidence_ids: Iterable[str],
        deterministic_verdict: Optional[str] = None,
    ) -> dict:
        """
        Generate a structured claim-verification result.

        Protection layers:

        1. Gemini native structured output
        2. Pydantic schema validation
        3. Evidence-ID validation
        4. Deterministic-verdict consistency check
        5. One retry
        6. Safe UNKNOWN fallback

        Parameters
        ----------
        prompt:
            Evidence-grounded verification prompt.

        system_instruction:
            Grounding rules from prompts.py.

        valid_evidence_ids:
            IDs actually supplied to the model.

        deterministic_verdict:
            Optional verdict produced by comparison.py.
            If supplied, the LLM may not silently override it.
        """

        valid_ids = {
            str(evidence_id)
            for evidence_id in valid_evidence_ids
            if evidence_id
        }

        deterministic = self._parse_deterministic_verdict(
            deterministic_verdict
        )

        last_error = None

        # First attempt + one retry
        for attempt in range(2):
            request_prompt = prompt

            if attempt == 1:
                request_prompt += self._retry_instruction(
                    valid_ids=valid_ids,
                    deterministic_verdict=deterministic,
                    previous_error=last_error,
                )

            try:
                result = self._request_structured_result(
                    prompt=request_prompt,
                    system_instruction=system_instruction,
                )

                self._validate_grounding(
                    result=result,
                    valid_evidence_ids=valid_ids,
                    deterministic_verdict=deterministic,
                )

                return result.model_dump(mode="json")

            except Exception as exc:
                last_error = exc

        # Never crash the verification pipeline and never guess.
        return self._unknown_fallback(last_error)

    # def _request_structured_result(
    #     self,
    #     *,
    #     prompt: str,
    #     system_instruction: str,
    # ) -> VerificationResult:
    #     """
    #     Call Gemini using native structured output.
    #     """

    #     response = self.client.models.generate_content(
    #         model=self.model,
    #         contents=prompt,
    #         config=types.GenerateContentConfig(
    #             temperature=0.0,
    #             system_instruction=system_instruction,

    #             # Force JSON output.
    #             response_mime_type="application/json",

    #             # Force our Pydantic schema.
    #             response_schema=VerificationResult,
    #         ),
    #     )

    #     raw = response.text

    #     if not raw:
    #         raise StructuredOutputError(
    #             "Gemini returned an empty response."
    #         )

    #     try:
    #         result = VerificationResult.model_validate_json(raw)
    #     except ValidationError as exc:
    #         raise StructuredOutputError(
    #             f"Response failed schema validation: {exc}"
    #         ) from exc

    #     return result

    def _request_structured_result(
        self,
        *,
        prompt: str,
        system_instruction: str,
    ) -> VerificationResult:
        """
        Call Gemini using native structured output.
        """

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=VerificationResult,
            ),
        )

        # Preferred path:
        # Gemini SDK parses the JSON directly into our Pydantic model.
        if response.parsed is not None:
            if isinstance(response.parsed, VerificationResult):
                return response.parsed

            # Defensive fallback in case the SDK returns a dict.
            return VerificationResult.model_validate(
                response.parsed
            )

        # Fallback if parsed is unexpectedly unavailable.
        raw = response.text

        if not raw:
            raise StructuredOutputError(
                "Gemini returned an empty structured response."
            )

        try:
            return VerificationResult.model_validate_json(raw)

        except ValidationError as exc:
            raise StructuredOutputError(
                f"Response failed schema validation: {exc}"
            ) from exc
    @staticmethod
    def _validate_grounding(
        *,
        result: VerificationResult,
        valid_evidence_ids: set[str],
        deterministic_verdict: Optional[Verdict],
    ) -> None:
        """
        Validate things a static JSON schema cannot validate.

        Example:
        The schema can guarantee evidence_ids is a list of strings,
        but only our Python code knows which evidence IDs actually exist.
        """

        # --------------------------------------------------
        # 1. Prevent hallucinated evidence IDs
        # --------------------------------------------------

        returned_ids = set(result.evidence_ids)

        invalid_ids = returned_ids - valid_evidence_ids

        if invalid_ids:
            raise StructuredOutputError(
                "LLM cited evidence IDs that were not supplied: "
                + ", ".join(sorted(invalid_ids))
            )

        # --------------------------------------------------
        # 2. A positive/negative verdict should cite evidence
        # --------------------------------------------------

        if (
            result.verdict
            in {
                Verdict.SUPPORTED,
                Verdict.CONTRADICTED,
                Verdict.PARTIALLY_SUPPORTED,
            }
            and not result.evidence_ids
        ):
            raise StructuredOutputError(
                f"{result.verdict.value} verdict requires at least "
                "one valid evidence ID."
            )

        # UNKNOWN is explicitly allowed to contain:
        #
        # evidence_ids = []
        #
        # because there may simply be no usable evidence.

        # --------------------------------------------------
        # 3. Deterministic numerical result has priority
        # --------------------------------------------------

        if (
            deterministic_verdict is not None
            and result.verdict != deterministic_verdict
        ):
            raise StructuredOutputError(
                "LLM verdict conflicts with deterministic numerical "
                f"verdict. LLM={result.verdict.value}, "
                f"deterministic={deterministic_verdict.value}"
            )

    @staticmethod
    def _parse_deterministic_verdict(
        verdict: Optional[str],
    ) -> Optional[Verdict]:
        """
        Convert comparison.py output into our Verdict enum.
        """

        if verdict is None:
            return None

        try:
            return Verdict(verdict)
        except ValueError as exc:
            raise ValueError(
                f"Invalid deterministic verdict: {verdict}"
            ) from exc

    @staticmethod
    def _retry_instruction(
        *,
        valid_ids: set[str],
        deterministic_verdict: Optional[Verdict],
        previous_error: Optional[Exception],
    ) -> str:
        """
        Add stricter instructions for the single retry.
        """

        evidence_text = (
            ", ".join(sorted(valid_ids))
            if valid_ids
            else "NONE"
        )

        deterministic_text = (
            deterministic_verdict.value
            if deterministic_verdict
            else "NONE"
        )

        error_text = (
            str(previous_error)
            if previous_error
            else "Validation failed."
        )

        return f"""

IMPORTANT CORRECTION:

Your previous response failed validation.

Validation problem:
{error_text}

Valid evidence IDs:
{evidence_text}

Authoritative deterministic verdict, if present:
{deterministic_text}

Correct the response.

Use only valid evidence IDs.
Do not invent evidence.
If evidence is insufficient, return UNKNOWN.
If a deterministic verdict is supplied, do not contradict it.
"""

    @staticmethod
    def _unknown_fallback(
        error: Optional[Exception],
    ) -> dict:
        """
        Safe fallback after both structured-output attempts fail.

        We return UNKNOWN rather than guessing.
        """

        reason = (
            "The reasoning layer could not produce a valid, "
            "evidence-grounded structured result."
        )

        if error:
            reason += f" Validation failure: {type(error).__name__}."

        fallback = VerificationResult(
            verdict=Verdict.UNKNOWN,
            confidence=0.0,
            reason=reason,
            evidence_ids=[],
        )

        return fallback.model_dump(mode="json")


def get_llm_client(
    model: Optional[str] = None,
) -> LLMClient:
    return LLMClient(model=model)
# import os
# import json
# from typing import Any, Optional

# from google import genai

# DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# class LLMClient:

#     def __init__(self, model:Optional[str] = None):
#         api_key = os.getenv("GOOGLE_API_KEY")
#         if not api_key:
#             raise RuntimeError("GOOGLE API KEY  not set. Add the genini api key to the .env file")
#         self .client = genai. Client(api_key=api_key)
#         self.model = model or DEFAULT_MODEL

#     def generate(self, prompt: str,
#                  *,
#                  system_instruction: Optional[str] = None,
#                  temperature : float = 0.0
#                  ) -> str :
#         contents = prompt

#         if system_instruction:
#             contents = (
#                 f"SYSTEM INSTRUCTION:\n"
#                 f"{system_instruction}\n\n"
#                 f"USER INPUT:\n"
#                 f"{prompt}"
#             )
#         try:
#             response = self.client.models.generate_content(
#                 model = self.model,
#                 contents = contents,
#                 config={
#                     "temperature": temperature
#                 },
#             )
#         except Exception as exc:
#             raise RuntimeError( f"LLM request failed: {type(exc).__name__}: {exc}") from exc

#         text = (response.text or "").strip()
#         if not text:
#             raise RuntimeError("LLM returned empty response")

#         return text

#     def generate_json(self, prompt : str,*,system_instruction: Optional[str] = None, temperature : float = 0.0) -> dict[str, Any]:
#         json_instruction = ("Return ONLY valid JSON. "
#             "Do not include markdown fences, explanations, or commentary."
#             )
#         combined_instruction = (
#             f"{system_instruction or ''}\n\n"
#             f"{json_instruction}"
#         ).strip()

#         raw = self.generate(
#             prompt, system_instruction=system_instruction,
#             temperature=temperature,
#         )
#         cleaned = self._clean_json_response(raw)

#         try:
#             data = json.loads(cleaned)
#         except json.JSONDecodeError as exc:
#             raise RuntimeError(
#                 "LLM returned invalid JSON.\n"
#                 f"Raw response:\n{raw}") from exc

#         if not isinstance(data, dict):
#             raise RuntimeError(
#                 f"Expected a JSON object but received {type(data).__name__}."
#             )

#         return data

#     @staticmethod
#     def _clean_json_response(text : str) -> str:
#         text = text.strip()

#         if text.startswith("'''"):
#             lines = text.splitlines()
#         if lines and lines[0].strip().startswith("'''"):
#             lines = lines[1:]
#         if lines and lines[-1].strip() == "'''":
#             lines = lines[:-1]
#         text - "\n".join(lines).strip()

#         return text

# def get_llm_client(model: Optional[str] = None) -> LLMClient:

#     return LLMClient(model=model)

# if __name__ == "__main__":
#     client = get_llm_client()

#     result = client.generate(
#         "Reply with exactly: LLM connection successful."
#     )

#     print(result)
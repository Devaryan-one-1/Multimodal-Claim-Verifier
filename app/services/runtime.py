from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path
from typing import Callable, TypeVar

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)

T = TypeVar("T")
SCHEMA = TypeVar("SCHEMA", bound=BaseModel)


def model_name() -> str:
    return os.getenv(
        "GEMINI_MODEL",
        "gemini-flash-latest",
    )


def client() -> genai.Client:
    key = (
        os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )

    if not key:
        raise RuntimeError(
            "Set GOOGLE_API_KEY in .env "
            "or Streamlit secrets."
        )

    return genai.Client(api_key=key)


def retry(
    call: Callable[[], T],
    attempts: int = 4,
    on_retry=None,
) -> T:
    """
    Respect server retry delays.

    Explicit daily or zero quotas are returned immediately
    rather than repeatedly retried.
    """
    if attempts < 1:
        raise ValueError("attempts must be positive.")

    for attempt in range(attempts):
        try:
            return call()

        except Exception as exc:
            code = getattr(exc, "code", None)
            message_text = str(exc).lower()

            transient = (
                code in (429, 500, 502, 503, 504)
                or any(
                    token in message_text
                    for token in (
                        "503",
                        "429",
                        "unavailable",
                        "resource_exhausted",
                        "timeout",
                    )
                )
            )

            if not transient or attempt == attempts - 1:
                raise

            payload = getattr(exc, "details", {})

            if not isinstance(payload, dict):
                payload = {}

            error = payload.get("error", payload)

            details = (
                error.get("details") or []
                if isinstance(error, dict)
                else []
            )

            delays = []

            for detail in details:
                if not isinstance(detail, dict):
                    continue

                for violation in (
                    detail.get("violations") or []
                ):
                    if not isinstance(violation, dict):
                        continue

                    quota_id = str(
                        violation.get("quotaId", "")
                    ).lower()

                    if (
                        "perday" in quota_id
                        or "per_day" in quota_id
                        or str(
                            violation.get("quotaValue")
                        ) == "0"
                    ):
                        raise

                duration = detail.get("retryDelay")

                if (
                    isinstance(duration, str)
                    and duration.endswith("s")
                ):
                    try:
                        seconds = float(duration[:-1])

                        if (
                            math.isfinite(seconds)
                            and seconds >= 0
                        ):
                            delays.append(seconds)

                    except ValueError:
                        pass

            response = getattr(exc, "response", None)
            headers = (
                getattr(response, "headers", {}) or {}
            )

            try:
                seconds = float(
                    headers.get("Retry-After", "")
                )

                if (
                    math.isfinite(seconds)
                    and seconds >= 0
                ):
                    delays.append(seconds)

            except (TypeError, ValueError):
                pass

            rate_limited = (
                code == 429
                or "resource_exhausted" in message_text
            )

            if delays:
                delay = max(delays) + 2.0
            elif rate_limited:
                delay = 65.0
            else:
                delay = min(
                    30.0,
                    3.0 * 2 ** attempt,
                )

            if delay > 300:
                raise

            message = (
                "API limit/transient failure: "
                f"waiting {delay:.1f}s before "
                f"retry {attempt + 2}/{attempts}."
            )

            if on_retry:
                on_retry(message)
            else:
                print(message, flush=True)

            time.sleep(delay)

    raise RuntimeError("Request failed.")


def structured(
    schema: type[SCHEMA],
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
) -> SCHEMA:
    api = client()

    response = retry(
        lambda: api.models.generate_content(
            model=model or model_name(),
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
    )

    if isinstance(response.parsed, schema):
        return response.parsed

    if response.parsed is not None:
        return schema.model_validate(response.parsed)

    return schema.model_validate_json(
        response.text or ""
    )


def read(path: str | Path) -> dict:
    return json.loads(
        Path(path).read_text(encoding="utf-8")
    )


def write(
    path: str | Path,
    data: dict,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_name(
        f".{path.name}.{uuid.uuid4().hex}.tmp"
    )

    try:
        temporary.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

        temporary.replace(path)

    finally:
        temporary.unlink(missing_ok=True)


def digest(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")

    return hashlib.sha256(value).hexdigest()


def normalize(
    vector: list[float],
) -> list[float]:
    if (
        not vector
        or not all(
            math.isfinite(float(value))
            for value in vector
        )
    ):
        raise ValueError(
            "Embedding is empty or non-finite."
        )

    norm = math.sqrt(
        sum(
            float(value) ** 2
            for value in vector
        )
    )

    if norm == 0:
        raise ValueError(
            "Embedding has zero norm."
        )

    return [
        float(value) / norm
        for value in vector
    ]
# from __future__ import annotation

# import hashlib
# import json
# import math
# import os
# import time
# import uuid
# from pathlib import Path
# from typing import Callable, TypeVar

# from dotenv import load_dotenv
# from google import genai
# from google.genai import types
# from pydantic import BaseModel

# ROOT = Path(__file__).resolve().parents[2]
# load_dotenv(ROOT / ".env", override=False)
# T = TypeVar("T")
# SCHEMA = TypeVar("SCHEMA", bound=BaseModel)


# def model_name() -> str:
#     return os.getenv("GEMINI_MODEL", "gemini-flash-latest")


# def client() -> genai.Client:
#     key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
#     if not key:
#         raise RuntimeError("Set GOOGLE_API_KEY in .env or Streamlit secrets.")
#     return genai.Client(api_key=key)


# def retry(call: Callable[[], T], attempts: int = 3) -> T:
#     for attempt in range(attempts):
#         try:
#             return call()
#         except Exception as exc:
#             code = getattr(exc, "code", None)
#             transient = code in (429, 500, 502, 503, 504) or any(
#                 token in str(exc).lower()
#                 for token in ("503", "429", "unavailable", "resource_exhausted", "timeout")
#             )
#             if not transient or attempt == attempts - 1:
#                 raise
#             time.sleep(min(20, 3 * (2 ** attempt)))
#     raise RuntimeError("Request failed.")


# def structured(schema: type[SCHEMA], prompt: str, *, system: str = "", model: str | None = None) -> SCHEMA:
#     api = client()
#     response = retry(lambda: api.models.generate_content(
#         model=model or model_name(), contents=prompt,
#         config=types.GenerateContentConfig(
#             temperature=0.0, system_instruction=system,
#             response_mime_type="application/json", response_schema=schema,
#         ),
#     ))
#     if isinstance(response.parsed, schema):
#         return response.parsed
#     if response.parsed is not None:
#         return schema.model_validate(response.parsed)
#     return schema.model_validate_json(response.text or "")


# def read(path: str | Path) -> dict:
#     return json.loads(Path(path).read_text(encoding="utf-8"))


# def write(path: str | Path, data: dict) -> None:
#     path = Path(path)
#     path.parent.mkdir(parents=True, exist_ok=True)
#     temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
#     try:
#         temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
#         temporary.replace(path)
#     finally:
#         temporary.unlink(missing_ok=True)


# def digest(value: str | bytes) -> str:
#     if isinstance(value, str):
#         value = value.encode("utf-8")
#     return hashlib.sha256(value).hexdigest()


# def normalize(vector: list[float]) -> list[float]:
#     if not vector or not all(math.isfinite(float(x)) for x in vector):
#         raise ValueError("Embedding is empty or non-finite.")
#     norm = math.sqrt(sum(float(x) ** 2 for x in vector))
#     if norm == 0:
#         raise ValueError("Embedding has zero norm.")
#     return [float(x) / norm for x in vector]

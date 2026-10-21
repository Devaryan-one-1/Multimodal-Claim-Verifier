from __future__ import annotations

import os
from dataclasses import asdict, replace

import streamlit as st

from services.model_gateway import (
    ModelConfig, api_key_configured, configure, configure_openrouter_catalog,
    list_openrouter_models,
)


def _capability_problem(model, catalog, *, vision=False):
    if not model:
        return None
    item = next((item for item in catalog if item.get("id") == model), None)
    if item is None:
        return None
    if "structured_outputs" not in (item.get("supported_parameters") or []):
        return "does not advertise structured_outputs; JSON output alone is insufficient"
    if vision and "image" not in (item.get("input_modalities") or []):
        return "does not advertise image input"
    return None


def render_model_settings() -> bool:
    """Session-local controls; never change process-wide embedding settings."""
    defaults = ModelConfig.from_env()
    st.session_state.setdefault("mm_provider", defaults.provider)
    provider = st.selectbox(
        "Reasoning provider", ["gemini", "openrouter"], key="mm_provider",
        format_func=lambda value: "Gemini directly" if value == "gemini" else "OpenRouter — Gemini / OpenAI / Claude",
    )
    catalog = st.session_state.get("mm_catalog", [])
    if provider == "openrouter":
        if st.button("Refresh available models", key="mm_refresh"):
            try:
                with st.spinner("Loading the model catalog"):
                    catalog = list_openrouter_models()
                st.session_state.mm_catalog = catalog
                st.success("Catalog loaded. This did not generate a model response.")
            except Exception as exc:
                st.error(str(exc))
        family = st.selectbox("Model family", ["All", "Google Gemini", "OpenAI", "Anthropic Claude"], key="mm_family")
        prefix = {"All": "", "Google Gemini": "google/", "OpenAI": "openai/", "Anthropic Claude": "anthropic/"}[family]
        if defaults.provider == "openrouter":
            initial = {"text": defaults.text_model, "vision": defaults.vision_model, "draft": defaults.draft_model}
        else:
            initial = {"text": os.getenv("OPENROUTER_TEXT_MODEL", ""),
                       "vision": os.getenv("OPENROUTER_VISION_MODEL", ""),
                       "draft": os.getenv("OPENROUTER_DRAFT_MODEL", "")}
        models = {}
        for role, label in (("text", "Claim parsing and reasoning"), ("vision", "Charts and OCR")):
            supported = [item for item in catalog if item["id"].startswith(prefix)
                         and "structured_outputs" in (item.get("supported_parameters") or [])
                         and (role != "vision" or "image" in (item.get("input_modalities") or []))]
            options = ["Enter model ID manually"] + sorted(item["id"] for item in supported)
            picker = f"mm_pick_{role}"
            if st.session_state.get(picker) not in options:
                st.session_state[picker] = options[0]
            chosen = st.selectbox(label, options, key=picker)
            key = f"mm_openrouter_{role}"
            st.session_state.setdefault(key, initial[role])
            if chosen != options[0]:
                st.session_state[key] = chosen
            models[role] = st.text_input(f"{label} model ID", key=key).strip()
        st.session_state.setdefault("mm_openrouter_draft", initial["draft"])
        draft = st.text_input("Dataset drafting model ID (blank uses the reasoning model)", key="mm_openrouter_draft").strip()
        st.caption("The pickers show models advertising structured_outputs. A response_format-only model is insufficient. Requests still require a compatible endpoint and pass local schema validation.")
        if "mm_catalog" not in st.session_state:
            st.info("Refresh available models to check advertised capabilities. Manual IDs have not been checked against the catalog.")
    else:
        models = {}
        for role, label, value in (
            ("text", "Gemini reasoning model", os.getenv("GEMINI_MODEL", "gemini-flash-latest")),
            ("vision", "Gemini vision model", os.getenv("GEMINI_VISION_MODEL") or os.getenv("GEMINI_MODEL", "gemini-flash-latest")),
        ):
            key = f"mm_gemini_{role}"
            st.session_state.setdefault(key, value)
            models[role] = st.text_input(label, key=key).strip()
        st.session_state.setdefault("mm_gemini_draft", os.getenv("GEMINI_EVALUATION_MODEL", ""))
        draft = st.text_input("Gemini drafting model (blank uses reasoning)", key="mm_gemini_draft").strip()
    with st.expander("Fallback and output settings", expanded=False):
        enabled = st.checkbox("Allow automatic model fallback", value=defaults.allow_fallbacks, key="mm_fallbacks")
        text_fallbacks = st.text_input("Text fallback model IDs, in order (comma separated)",
                                      value=",".join(defaults.text_fallbacks), key=f"mm_text_fallbacks_{provider}")
        vision_fallbacks = st.text_input("Vision fallback model IDs, in order (comma separated)",
                                        value=",".join(defaults.vision_fallbacks), key=f"mm_vision_fallbacks_{provider}")
        tokens = st.number_input("Maximum output tokens", min_value=256, max_value=131072,
                                 value=defaults.max_output_tokens, step=256, key="mm_tokens")
        st.caption("Fallbacks use IDs for the selected provider. Evaluation automatically disables model switching. Larger output limits can increase cost.")
    selected = replace(
        defaults, provider=provider, text_model=models["text"], vision_model=models["vision"],
        draft_model=draft or models["text"], allow_fallbacks=enabled,
        text_fallbacks=tuple(dict.fromkeys(item.strip() for item in text_fallbacks.split(",") if item.strip())),
        vision_fallbacks=tuple(dict.fromkeys(item.strip() for item in vision_fallbacks.split(",") if item.strip())),
        max_output_tokens=int(tokens),
    )
    configure(selected)
    configure_openrouter_catalog(catalog if provider == "openrouter" else [])
    st.session_state.mm_active_configuration = asdict(selected)
    ready = api_key_configured(provider) and bool(selected.text_model)
    if provider == "openrouter":
        entries = [("Reasoning", selected.text_model, False),
                   ("Vision", selected.vision_model, True),
                   ("Dataset drafting", selected.draft_model, False)]
        if selected.allow_fallbacks:
            entries += [("Text fallback", name, False) for name in selected.text_fallbacks]
            entries += [("Vision fallback", name, True) for name in selected.vision_fallbacks]
        for label, name, vision in entries:
            problem = _capability_problem(name, catalog, vision=vision)
            if problem:
                st.error(f"{label}: {name} {problem}. Select a compatible model or refresh the catalog. Requests using this selection are blocked before generation.")
                if label == "Reasoning":
                    ready = False
            elif name and catalog and not any(item.get("id") == name for item in catalog):
                st.warning(f"{label}: {name} is absent from the loaded catalog. Verify the exact ID and endpoint's structured_outputs support before using it.")
    if not api_key_configured(provider):
        st.warning(f"Set {'OPENROUTER_API_KEY' if provider == 'openrouter' else 'GOOGLE_API_KEY'} in .env or Streamlit secrets.")
    if provider == "openrouter" and not selected.text_model:
        st.info("Choose a reasoning model from the catalog or enter its exact ID.")
    if not selected.vision_model:
        st.caption("Choose a vision model before processing charts or scanned pages.")
    st.caption("Embeddings stay on Gemini and use your existing Chroma collection.")
    if not api_key_configured("gemini"):
        st.warning("The Gemini key is still required for document and query embeddings.")
    return ready and api_key_configured("gemini")


def render_model_usage(results):
    calls = next((item.get("model_calls") for item in results if item.get("model_calls")), [])
    if not calls:
        return
    used = list(dict.fromkeys(event.get("actual_model") for event in calls
                             if event.get("status") == "ok" and event.get("actual_model")))
    if used:
        st.caption("Models used: " + ", ".join(used))
    with st.expander("Model calls and fallback details", expanded=False):
        st.dataframe([{"Task": event.get("task"), "Gateway": event.get("gateway_provider"),
                       "Requested": event.get("requested_model"), "Actual": event.get("actual_model"),
                       "Status": event.get("status"), "Fallback": event.get("fallback", False),
                       "HTTP": event.get("http_status"),
                       "Finish reason": event.get("finish_reason"),
                       "Error": event.get("error_message"),
                       "Seconds": event.get("duration_seconds")}
                      for event in calls], hide_index=True, use_container_width=True)
        st.json(calls, expanded=False)
        st.caption("These calls cover parsing, operand selection and reasoning for the entire submitted claim. Gemini query embeddings are separate.")

# from __future__ import annotations

# import os
# from dataclasses import asdict, replace

# import streamlit as st

# from services.model_gateway import (
#     ModelConfig, api_key_configured, configure, list_openrouter_models,
# )


# def render_model_settings() -> bool:
#     """Session-local controls; never change process-wide embedding settings."""
#     defaults = ModelConfig.from_env()
#     st.session_state.setdefault("mm_provider", defaults.provider)
#     provider = st.selectbox(
#         "Reasoning provider", ["gemini", "openrouter"], key="mm_provider",
#         format_func=lambda value: "Gemini directly" if value == "gemini" else "OpenRouter — Gemini / OpenAI / Claude",
#     )
#     catalog = st.session_state.get("mm_catalog", [])
#     if provider == "openrouter":
#         if st.button("Refresh available models", key="mm_refresh"):
#             try:
#                 with st.spinner("Loading the model catalog"):
#                     catalog = list_openrouter_models()
#                 st.session_state.mm_catalog = catalog
#                 st.success("Catalog loaded. This did not generate a model response.")
#             except Exception as exc:
#                 st.error(str(exc))
#         family = st.selectbox("Model family", ["All", "Google Gemini", "OpenAI", "Anthropic Claude"], key="mm_family")
#         prefix = {"All": "", "Google Gemini": "google/", "OpenAI": "openai/", "Anthropic Claude": "anthropic/"}[family]
#         if defaults.provider == "openrouter":
#             initial = {"text": defaults.text_model, "vision": defaults.vision_model, "draft": defaults.draft_model}
#         else:
#             initial = {"text": os.getenv("OPENROUTER_TEXT_MODEL", ""),
#                        "vision": os.getenv("OPENROUTER_VISION_MODEL", ""),
#                        "draft": os.getenv("OPENROUTER_DRAFT_MODEL", "")}
#         models = {}
#         for role, label in (("text", "Claim parsing and reasoning"), ("vision", "Charts and OCR")):
#             supported = [item for item in catalog if item["id"].startswith(prefix)
#                          and ("structured_outputs" in item["supported_parameters"] or "response_format" in item["supported_parameters"])
#                          and (role != "vision" or "image" in item["input_modalities"])]
#             options = ["Enter model ID manually"] + sorted(item["id"] for item in supported)
#             picker = f"mm_pick_{role}"
#             if st.session_state.get(picker) not in options:
#                 st.session_state[picker] = options[0]
#             chosen = st.selectbox(label, options, key=picker)
#             key = f"mm_openrouter_{role}"
#             st.session_state.setdefault(key, initial[role])
#             if chosen != options[0]:
#                 st.session_state[key] = chosen
#             models[role] = st.text_input(f"{label} model ID", key=key).strip()
#         st.session_state.setdefault("mm_openrouter_draft", initial["draft"])
#         draft = st.text_input("Dataset drafting model ID (blank uses the reasoning model)", key="mm_openrouter_draft").strip()
#         st.caption("Catalog capabilities are a filter, not a guarantee. Requests require an endpoint supporting the supplied JSON Schema and parameters.")
#     else:
#         models = {}
#         for role, label, value in (
#             ("text", "Gemini reasoning model", os.getenv("GEMINI_MODEL", "gemini-flash-latest")),
#             ("vision", "Gemini vision model", os.getenv("GEMINI_VISION_MODEL") or os.getenv("GEMINI_MODEL", "gemini-flash-latest")),
#         ):
#             key = f"mm_gemini_{role}"
#             st.session_state.setdefault(key, value)
#             models[role] = st.text_input(label, key=key).strip()
#         st.session_state.setdefault("mm_gemini_draft", os.getenv("GEMINI_EVALUATION_MODEL", ""))
#         draft = st.text_input("Gemini drafting model (blank uses reasoning)", key="mm_gemini_draft").strip()
#     with st.expander("Fallback and output settings", expanded=False):
#         enabled = st.checkbox("Allow automatic model fallback", value=defaults.allow_fallbacks, key="mm_fallbacks")
#         text_fallbacks = st.text_input("Text fallback model IDs, in order (comma separated)",
#                                       value=",".join(defaults.text_fallbacks), key=f"mm_text_fallbacks_{provider}")
#         vision_fallbacks = st.text_input("Vision fallback model IDs, in order (comma separated)",
#                                         value=",".join(defaults.vision_fallbacks), key=f"mm_vision_fallbacks_{provider}")
#         tokens = st.number_input("Maximum output tokens", min_value=256, max_value=131072,
#                                  value=defaults.max_output_tokens, step=256, key="mm_tokens")
#         st.caption("Fallbacks use IDs for the selected provider. Evaluation automatically disables model switching. Larger output limits can increase cost.")
#     selected = replace(
#         defaults, provider=provider, text_model=models["text"], vision_model=models["vision"],
#         draft_model=draft or models["text"], allow_fallbacks=enabled,
#         text_fallbacks=tuple(dict.fromkeys(item.strip() for item in text_fallbacks.split(",") if item.strip())),
#         vision_fallbacks=tuple(dict.fromkeys(item.strip() for item in vision_fallbacks.split(",") if item.strip())),
#         max_output_tokens=int(tokens),
#     )
#     configure(selected)
#     st.session_state.mm_active_configuration = asdict(selected)
#     ready = api_key_configured(provider) and bool(selected.text_model)
#     if not api_key_configured(provider):
#         st.warning(f"Set {'OPENROUTER_API_KEY' if provider == 'openrouter' else 'GOOGLE_API_KEY'} in .env or Streamlit secrets.")
#     if provider == "openrouter" and not selected.text_model:
#         st.info("Choose a reasoning model from the catalog or enter its exact ID.")
#     if not selected.vision_model:
#         st.caption("Choose a vision model before processing charts or scanned pages.")
#     st.caption("Embeddings stay on Gemini and use your existing Chroma collection.")
#     if not api_key_configured("gemini"):
#         st.warning("The Gemini key is still required for document and query embeddings.")
#     return ready and api_key_configured("gemini")


# def render_model_usage(results):
#     calls = next((item.get("model_calls") for item in results if item.get("model_calls")), [])
#     if not calls:
#         return
#     used = list(dict.fromkeys(event.get("actual_model") for event in calls
#                              if event.get("status") == "ok" and event.get("actual_model")))
#     if used:
#         st.caption("Models used: " + ", ".join(used))
#     with st.expander("Model calls and fallback details", expanded=False):
#         st.dataframe([{"Task": event.get("task"), "Gateway": event.get("gateway_provider"),
#                        "Requested": event.get("requested_model"), "Actual": event.get("actual_model"),
#                        "Status": event.get("status"), "Fallback": event.get("fallback", False),
#                        "Seconds": event.get("duration_seconds")}
#                       for event in calls], hide_index=True, use_container_width=True)
#         st.caption("These calls cover parsing, operand selection and reasoning for the entire submitted claim. Gemini query embeddings are separate.")

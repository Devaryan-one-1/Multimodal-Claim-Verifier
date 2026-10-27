"""Run from the project folder: streamlit run app/main.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT / "app"))
load_dotenv(PROJECT_ROOT / ".env", override=False)

st.set_page_config(page_title="Claim Verifier", page_icon=":material/fact_check:", layout="wide")

# Hosted Streamlit can use secrets; local development keeps the existing .env.
for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EVALUATION_MODEL",
             "GEMINI_EMBEDDING_MODEL", "GEMINI_EMBEDDING_DIMENSIONS"):
    if not os.getenv(name):
        try:
            value = st.secrets.get(name)
            if value:
                os.environ[name] = str(value)
        except (FileNotFoundError, KeyError):
            pass
if not os.getenv("GOOGLE_API_KEY") and os.getenv("GEMINI_API_KEY"):
    os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

# OpenRouter settings are separate from GEMINI_EMBEDDING_*.
for name in ("OPENROUTER_API_KEY", "LLM_PROVIDER", "OPENROUTER_TEXT_MODEL",
             "OPENROUTER_VISION_MODEL", "OPENROUTER_DRAFT_MODEL", "LLM_ALLOW_FALLBACKS",
             "LLM_TEXT_FALLBACK_MODELS", "LLM_VISION_FALLBACK_MODELS", "LLM_TIMEOUT_SECONDS",
             "LLM_MAX_OUTPUT_TOKENS", "OPENROUTER_SEND_TEMPERATURE", "GEMINI_VISION_MODEL"):
    if not os.getenv(name):
        try:
            value = st.secrets.get(name)
            if value is not None:
                os.environ[name] = str(value)
        except (FileNotFoundError, KeyError):
            pass

from services.observability import configure_logging
configure_logging()

from ui.workspace import run_dashboard

run_dashboard()

# """Run from the project folder: streamlit run app/main.py"""
# from __future__ import annotation

# import os
# import sys
# from pathlib import Path

# import streamlit as st
# from dotenv import load_dotenv

# PROJECT_ROOT = Path(__file__).resolve().parents[1]
# os.chdir(PROJECT_ROOT)
# sys.path.insert(0, str(PROJECT_ROOT / "app"))
# load_dotenv(PROJECT_ROOT / ".env", override=False)

# st.set_page_config(page_title="Claim Verifier", page_icon=":material/fact_check:", layout="wide")

# # Hosted Streamlit can use secrets; local development keeps the existing .env.
# for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_EVALUATION_MODEL",
#              "GEMINI_EMBEDDING_MODEL", "GEMINI_EMBEDDING_DIMENSIONS"):
#     if not os.getenv(name):
#         try:
#             value = st.secrets.get(name)
#             if value:
#                 os.environ[name] = str(value)
#         except (FileNotFoundError, KeyError):
#             pass
# if not os.getenv("GOOGLE_API_KEY") and os.getenv("GEMINI_API_KEY"):
#     os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]

# from ui.workspace import run_dashboar

# run_dashboard()

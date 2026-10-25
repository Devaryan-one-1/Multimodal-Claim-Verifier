"""Optional standalone entry point, using the same data/ and model settings."""
from dotenv import load_dotenv
load_dotenv()

import streamlit as st
st.set_page_config(page_title="Hallucination resistance", layout="wide")

from ui.model_settings import render_model_settings
from ui.hallucination import render_hallucination_panel

with st.sidebar:
    render_model_settings()
render_hallucination_panel()

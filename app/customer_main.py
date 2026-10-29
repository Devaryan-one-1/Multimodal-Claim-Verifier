"""Public entry point. Keep app/main.py restricted to your internal environment."""
from __future__ import annotations
import sys
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
load_dotenv(PROJECT / ".env", override=False)
st.set_page_config(page_title="Financial claim review", page_icon=":material/fact_check:", layout="wide")
from pilot.store import initialize
from pilot.auth import require_access
from pilot.ui import run


def customer_page():
    initialize()
    access = require_access()
    run(access)


# Explicit navigation disables automatic exposure of legacy app/pages entries.
# The authentication gate runs inside the only registered customer page.
st.navigation([st.Page(customer_page, title="Financial reviews")]).run()

# """Public entry point. Keep app/main.py restricted to your internal environment."""
# from __future__ import annotations
# import sys
# from pathlib import Path
# import streamlit as st
# from dotenv import load_dotenv

# PROJECT = Path(__file__).resolve().parents[1]
# sys.path.insert(0, str(PROJECT / "app"))
# load_dotenv(PROJECT / ".env", override=False)
# st.set_page_config(page_title="Financial claim review", page_icon=":material/fact_check:", layout="wide")
# from pilot.store import initialize
# from pilot.auth import require_access
# from pilot.ui import run


# def customer_page():
#     initialize()
#     access = require_access()
#     run(access)


# # Explicit navigation disables automatic exposure of legacy app/pages entries.
# # The authentication gate runs inside the only registered customer page.
# st.navigation([st.Page(customer_page, title="Financial reviews")]).run()

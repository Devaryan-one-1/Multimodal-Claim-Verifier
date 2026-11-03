"""Customer-only responsive styles; native Streamlit controls keep their state."""
import streamlit as st

MIN_WIDTH = 375
PHONE_BREAKPOINT = 767

CUSTOMER_CSS = """
<style>
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {
    min-width: 375px;
}
[data-testid="stMainBlockContainer"] { min-width: 0; }
[data-testid="stMain"] button:focus-visible,
[data-testid="stPopoverBody"] button:focus-visible,
[data-testid="stMain"] a:focus-visible,
.customer-pdf-preview:focus-visible {
    outline: 3px solid #2563eb;
    outline-offset: 3px;
}
.st-key-customer_navigation label:focus-within {
    outline: 3px solid #2563eb;
    outline-offset: 2px;
}
[data-testid="stPopoverBody"] {
    max-width: min(28rem, calc(100vw - 24px));
    max-height: calc(100dvh - 96px);
    overflow-y: auto;
    overflow-wrap: anywhere;
}
@media (max-width: 767px) {
    [data-testid="stMainBlockContainer"] {
        padding: calc(4rem + env(safe-area-inset-top, 0px)) 1rem
                 calc(6rem + env(safe-area-inset-bottom, 0px));
    }
    [data-testid="stMain"] h1 { font-size: 1.75rem; overflow-wrap: anywhere; }
    [data-testid="stMain"] h2 { font-size: 1.4rem; overflow-wrap: anywhere; }
    [data-testid="stMain"] h3 { overflow-wrap: anywhere; }
    [data-testid="stMain"] [data-testid="stButton"] > button,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] > button,
    [data-testid="stPopoverBody"] [data-testid="stButton"] > button,
    [data-testid="stPopoverBody"] [data-testid="stFormSubmitButton"] > button {
        width: 100%;
        min-height: 44px;
        white-space: normal;
    }
    [data-testid="stMain"] input,
    [data-testid="stMain"] textarea,
    [data-testid="stPopoverBody"] input,
    [data-testid="stPopoverBody"] textarea { font-size: 16px !important; }
    [data-baseweb="select"] { min-width: 0; min-height: 44px; }
    .st-key-customer_account_menu [data-testid="stPopoverButton"] { min-height: 44px; }
    [data-testid="stMain"] pre { max-width: 100%; overflow-x: auto; }
    .st-key-customer_review_layout [data-testid="stHorizontalBlock"],
    .st-key-customer_export_actions [data-testid="stHorizontalBlock"] {
        flex-direction: column;
        align-items: stretch;
    }
    .st-key-customer_review_layout [data-testid="stColumn"],
    .st-key-customer_export_actions [data-testid="stColumn"] {
        width: 100% !important;
        flex: 1 1 100% !important;
        min-width: 0 !important;
    }
    .st-key-customer_navigation {
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        min-width: 375px;
        z-index: 100;
        margin: 0;
        padding: 8px max(8px, env(safe-area-inset-right, 0px))
                 max(8px, env(safe-area-inset-bottom, 0px))
                 max(8px, env(safe-area-inset-left, 0px));
        background: var(--st-background-color, var(--background-color, #ffffff));
        border-top: 1px solid #aeb8c5;
        box-shadow: 0 -3px 12px rgb(0 0 0 / 8%);
    }
    .st-key-customer_navigation [role="radiogroup"] {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 4px;
        width: 100%;
    }
    .st-key-customer_navigation label[data-baseweb="radio"] {
        min-height: 44px;
        margin: 0;
        padding: 4px;
        border-radius: 8px;
        justify-content: center;
    }
    .st-key-customer_navigation label[data-baseweb="radio"] p {
        font-size: 14px;
        white-space: nowrap;
        color: var(--st-text-color, var(--text-color, #172033));
    }
    .st-key-customer_navigation label[data-baseweb="radio"]:has(input:checked) {
        box-shadow: inset 0 -3px #2563eb;
        font-weight: 600;
    }
}
</style>
"""


def apply_layout():
    # Only static trusted CSS; no customer text, script or media is inserted.
    st.html(CUSTOMER_CSS)


def navigation(key):
    with st.container(key="customer_navigation"):
        return st.radio("Navigation", ["Reviews", "Documents", "History"], horizontal=True,
                        label_visibility="collapsed", key=key+"area")

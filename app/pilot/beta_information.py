"""Customer beta information and validated operator-provided feedback links."""
import os
import re
from urllib.parse import quote, urlencode, urlsplit


def feedback_target(env=None):
    env = os.environ if env is None else env
    url = env.get("BETA_FEEDBACK_URL", "").strip()
    email = env.get("BETA_SUPPORT_EMAIL", "").strip()
    if url and not any(ord(char) < 33 for char in url):
        try:
            parts = urlsplit(url)
            if (parts.scheme == "https" and parts.hostname and "." in parts.hostname
                    and not parts.username and not parts.password and not parts.fragment):
                return url
        except ValueError:
            pass
    if re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", email):
        return "mailto:"+quote(email, safe="@.+-")+"?"+urlencode({"subject": "Financial claim review beta feedback"})
    return None


def render(st):
    with st.expander("Beta information & feedback"):
        st.markdown("**What this beta checks**")
        st.write("Check numerical financial assertions against selected PDF reports, with source values, calculations and page citations. Review the cited page before relying on a verdict.")
        st.write(f"Use unlocked PDFs up to {os.getenv('CUSTOMER_MAX_UPLOAD_MB', '10')} MB and {os.getenv('CUSTOMER_MAX_PAGES', '500')} pages. Scans, complex tables and charts can be missed or misread. Split long claims into smaller groups; each review supports up to eight atomic assertions.")
        st.write("Negated numerical claims and cross-document comparisons are not supported in this beta. Compound claims can fail or be misinterpreted; inspect every returned statement, quantity, date and direction. UNKNOWN means the available evidence was insufficient. Failed or paused processing is shown separately and needs a retry.")
        st.markdown("**Your data and model credits**")
        st.write("Your account uses Supabase authentication. Reports, extracted evidence, vectors, reviews and decisions are stored by the app for your workspace. In cloud mode they use private Supabase storage and Postgres; local mode uses the operator's persistent storage.")
        st.write("Claims, selected evidence and table/chart page images may be sent through OpenRouter to your chosen model providers. Text used for document and query embeddings is sent to the app's Google service; enabled OCR may also use Google. Only upload reports you have permission to share with these services. Provider retention depends on their policies and your settings.")
        st.write("Your OpenRouter key is stored encrypted and used server-side for your model requests, which use your credits and model limits. Google embeddings use the app operator's quota. Free models can be rate-limited or unavailable.")
        st.markdown("**Retention and deletion**")
        st.write("Beta data is kept until you request deletion; there is no automatic expiry. Removing a report from Documents queues deletion of its source and search index. Saved review snapshots, extracted evidence in those reviews, decisions and history remain. Removing your OpenRouter key does not delete these records.")
        st.write("Contact beta support to request removal of retained reviews or your workspace/account. Copies in backups and records held by external providers follow their separate retention policies; deletion from the app does not erase those copies immediately.")
        st.markdown("**Report a problem**")
        st.write("Share the request reference, diagnostic code and what you expected. Keep PDFs, private claims, passwords, email codes and API keys out of public feedback channels. Request a private support conversation for account or data-removal issues.")
        target = feedback_target()
        if target:
            st.link_button("Send beta feedback / request private support", target, use_container_width=True)
        else:
            st.info("Contact the person who invited you to this beta for feedback or a data-removal request.")

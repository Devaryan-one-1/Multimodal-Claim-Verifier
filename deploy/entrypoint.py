"""Start exactly the customer UI or one worker after local preflight checks."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deploy.preflight import check


def main():
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    if role not in {"web", "worker"}:
        raise SystemExit("Choose web or worker.")
    errors = check()
    if errors:
        for error in errors:
            print("Preflight: " + error, file=sys.stderr)
        raise SystemExit(1)
    command = ([sys.executable, "-m", "streamlit", "run", "app/customer_main.py",
                "--server.address=0.0.0.0", "--server.port="+str(int(os.getenv("PORT", "8501"))), "--server.headless=true",
                "--server.enableCORS=true", "--server.enableXsrfProtection=true",
                "--server.enableStaticServing=false", "--server.maxUploadSize=10",
                "--client.showErrorDetails=none", "--browser.gatherUsageStats=false"]
               if role == "web" else [sys.executable, "app/pilot_worker.py"])
    os.execv(sys.executable, command)


if __name__ == "__main__":
    main()

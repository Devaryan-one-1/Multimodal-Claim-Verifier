"""Build-time public model cache; no customer data, keys or generation calls."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
from retrieval.reranker import _get_model

if __name__ == "__main__":
    model = _get_model()
    model.predict([("public build smoke test", "public build smoke test")])
    print("Pinned reranker loaded and inference smoke passed.")

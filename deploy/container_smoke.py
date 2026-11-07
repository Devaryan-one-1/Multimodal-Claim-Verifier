"""Real Linux image checks with public fixture data and no network/provider calls."""
from importlib.metadata import version
import hashlib
import io
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    from cryptography.fernet import Fernet
    from PIL import Image
    from pypdf import PdfReader
    import pymupdf
    import chromadb
    import psycopg
    assert psycopg.pq.version() > 0
    import pilot.ui
    import pilot.auth
    from pilot.pdf_limits import check_pdf
    from retrieval.reranker import _get_model

    assert not Path("/app/app/main.py").exists()
    assert not Path("/app/app/pages").exists()
    cipher = Fernet(Fernet.generate_key())
    assert cipher.decrypt(cipher.encrypt(b"public-fixture")) == b"public-fixture"

    with pymupdf.open() as pdf:
        page = pdf.new_page(width=612, height=792)
        page.insert_text((72, 72), "Docker source fixture: 14.9 billion")
        data = pdf.tobytes()
    check_pdf(data)
    reader = PdfReader(io.BytesIO(data))
    assert len(reader.pages) == 1
    assert "14.9 billion" in reader.pages[0].extract_text()
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        pix = pdf[0].get_pixmap(alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        output = io.BytesIO()
        image.save(output, format="PNG")
        assert output.getvalue().startswith(b"\x89PNG\r\n\x1a\n")

    # Synthetic vectors are supplied explicitly. No embedding/model download.
    with tempfile.TemporaryDirectory(prefix="chroma-image-check-") as folder:
        client = chromadb.PersistentClient(path=folder)
        collection = client.get_or_create_collection("docker-smoke", embedding_function=None)
        vector = [1.0]+[0.0]*767
        collection.add(ids=["public-fixture"], embeddings=[vector], documents=["public fixture"],metadatas=[{"document_id":"f"*32}])
        assert collection.query(query_embeddings=[vector], n_results=1)["ids"] == [["public-fixture"]]
        client._system.stop()
        before={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).rglob("*") if p.is_file()}
        from scripts.migrate_supabase import exported_vectors
        copied=list(exported_vectors(Path(folder),"docker-smoke",["f"*32]))
        assert len(copied)==1 and copied[0][1]["embeddings"]==[vector]
        after={p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).rglob("*") if p.is_file()}
        assert before==after

    # The build warms this cache as root. Prove non-root/offline runtime can use it.
    score = _get_model().predict([("public fixture", "public fixture")])
    assert len(score) == 1
    print("Customer-only imports, native Postgres driver/encryption, PDF/PNG, copied-index export and offline reranker passed.")
    for package in ("streamlit", "pillow", "pypdf", "setuptools", "chromadb", "psycopg"):
        print(f"{package}=={version(package)}")


if __name__ == "__main__":
    main()

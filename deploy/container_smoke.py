"""Real Linux image checks with public fixture data and no network/provider calls."""
from importlib.metadata import version, distribution, PackageNotFoundError
from importlib.util import find_spec
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def check_cloud_vectors():
    """Exercise real adapter/SQL construction with the DB boundary mocked."""
    from retrieval.cloud_vectors import Collection
    from retrieval.embeddings import collection
    from pilot import cloud_db

    cursor, connection = MagicMock(), MagicMock()
    connection.__enter__.return_value = cursor
    cursor.execute.return_value = cursor
    cursor.fetchone.return_value = {"model": "gemini-embedding-001", "dimensions": 768}
    cursor.fetchall.side_effect = [[], [{"id": "fixture", "document": "public fixture",
                                      "metadata": json.dumps({"page": 1}), "distance": 0.0}]]
    workspace, document = "a"*32, "f"*32
    vector = [1.0]+[0.0]*767
    with patch.dict(os.environ, {"CUSTOMER_STORAGE": "supabase", "CUSTOMER_WORKSPACE_ID": workspace}), \
            patch.object(cloud_db, "db", return_value=connection):
        adapter = collection()
        assert isinstance(adapter, Collection)
        result = adapter.query(query_embeddings=[vector], where={"document_id": document},
                               n_results=1, include=["metadatas", "documents", "distances"])
        assert result["ids"] == [["fixture"]] and result["metadatas"] == [[{"page": 1}]]
        query, args = cursor.execute.call_args.args
        assert "embedding <=> ?::extensions.vector" in query
        assert "workspace=? AND collection=? AND document_id=?" in query
        assert args[1:4] == [workspace, adapter.name, document]
        try:
            adapter.query(query_embeddings=[vector], where={}, n_results=1)
        except ValueError:
            pass
        else:
            raise AssertionError("Cloud query accepted a missing document scope")


def check_local_vectors():
    import chromadb
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


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else "local"
    if mode not in {"cloud", "local"}:
        raise SystemExit("Choose cloud or local smoke checks.")
    assert Path(__file__).with_name("cloud-only").is_file() == (mode == "cloud")
    from cryptography.fernet import Fernet
    from PIL import Image
    from pypdf import PdfReader
    import pymupdf
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

    if mode == "cloud":
        assert find_spec("chromadb") is None
        try:
            distribution("chromadb")
        except PackageNotFoundError:
            pass
        else:
            raise AssertionError("Chroma distribution is installed in the cloud image")
        check_cloud_vectors()
        print("Cloud pgvector dispatch and document/workspace SQL scope passed (DB boundary mocked); Chroma absent.")
    else:
        check_local_vectors()
        print("Local native Chroma roundtrip and copied-index export passed.")

    # The build warms this cache as root. Prove non-root/offline runtime can use it.
    score = _get_model().predict([("public fixture", "public fixture")])
    assert len(score) == 1
    print("Customer-only imports, native Postgres driver/encryption, PDF/PNG and offline reranker passed.")
    packages = ("streamlit", "pillow", "pypdf", "setuptools", "psycopg") + (("chromadb",) if mode == "local" else ())
    for package in packages:
        print(f"{package}=={version(package)}")


if __name__ == "__main__":
    main()

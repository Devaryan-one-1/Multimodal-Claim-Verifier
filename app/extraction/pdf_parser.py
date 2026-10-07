import os
from pypdf import PdfReader

Storage_Root = "data/documents"

def parsepdf(document_id: str) ->dict:
    pdf_path = os.path.join(Storage_Root,document_id,"original.pdf")
    if not os.path.exists(pdf_path):
        raise FileNotFoundError("PDF",pdf_path," not present")
    reader = PdfReader(pdf_path)
    page_count = len(reader.pages)

    meta = reader.metadata or {}
    metadata = {
        "title":meta.get('/Title'),
        "author": meta.get("/Author"),
        "subject": meta.get("/Subject"),
        "creator": meta.get("/Creator"),
        "producer": meta.get("/Producer"),
    } 
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append({f"page_number":i+1,"Text": text})

    return {
        "document_id": document_id,
        "page_count": page_count,
        "metadata": metadata,
        "pages": pages
    }

if __name__ == "__main__":
    import sys
    import json

    result = parsepdf(sys.argv[1])
    summary = {k:v for k,v in result.items() if k != "pages"}
    pages = {k:v for k,v in result.items() if k == "pages"}
    print(json.dumps(summary,indent = 2))
    print(json.dumps(pages,indent = 2))
        
    # print("Extracted Text from ",len(result['pages'])," pages ", result['pages'])


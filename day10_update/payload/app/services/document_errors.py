"""Processing failures are operational errors, never factual verdicts."""

class DocumentProcessingError(RuntimeError):
    def __init__(self, message, *, code="document_processing", stage="extraction"):
        super().__init__(message)
        self.code = code
        self.stage = stage


class InvalidPDFError(DocumentProcessingError):
    def __init__(self, message):
        super().__init__(message, code="invalid_pdf", stage="validation")


class NoUsableEvidenceError(DocumentProcessingError):
    def __init__(self):
        super().__init__("No usable evidence was extracted. Check the PDF and OCR coverage before indexing.",
                         code="no_usable_evidence", stage="evidence")

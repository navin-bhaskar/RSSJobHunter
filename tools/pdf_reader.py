"""
PDF Reader Tool for extracting text, metadata, and page-by-page content from PDF documents.
Designed for resume processing, document analysis, and AI Agent integrations.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import sys
import pypdf


class PDFReaderError(Exception):
    """Custom exception raised for PDF reading errors."""
    pass


class PDFReader:
    """PDF Reader utility for extracting text and metadata from PDF files."""

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

    def validate_file(self) -> None:
        """Validates that the file exists and is a PDF."""
        if not self.file_path.exists():
            raise FileNotFoundError(f"PDF file not found: {self.file_path}")
        if not self.file_path.is_file():
            raise ValueError(f"Path is not a valid file: {self.file_path}")
        if self.file_path.suffix.lower() != ".pdf":
            raise ValueError(f"File is not a PDF (found extension: {self.file_path.suffix}): {self.file_path}")

    def extract_text(self, max_pages: Optional[int] = None) -> str:
        """
        Extracts plain text from the PDF file.
        
        Args:
            max_pages: Optional maximum number of pages to read.
            
        Returns:
            Extracted text content as a string.
        """
        self.validate_file()
        try:
            reader = pypdf.PdfReader(str(self.file_path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    raise PDFReaderError(f"PDF file is password protected: {self.file_path}")

            text_parts: List[str] = []
            total_pages = len(reader.pages)
            limit = total_pages if max_pages is None else min(max_pages, total_pages)

            for i in range(limit):
                page = reader.pages[i]
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(page_text.strip())

            return "\n\n".join(text_parts)
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError, PDFReaderError)):
                raise
            raise PDFReaderError(f"Failed to read PDF '{self.file_path}': {str(e)}") from e

    def extract_pages(self, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Extracts content page by page.
        
        Returns:
            List of dicts containing page_number, text, and char_count.
        """
        self.validate_file()
        try:
            reader = pypdf.PdfReader(str(self.file_path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception:
                    raise PDFReaderError(f"PDF file is password protected: {self.file_path}")

            pages_data: List[Dict[str, Any]] = []
            total_pages = len(reader.pages)
            limit = total_pages if max_pages is None else min(max_pages, total_pages)

            for i in range(limit):
                page = reader.pages[i]
                page_text = (page.extract_text() or "").strip()
                pages_data.append({
                    "page_number": i + 1,
                    "text": page_text,
                    "char_count": len(page_text)
                })

            return pages_data
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError, PDFReaderError)):
                raise
            raise PDFReaderError(f"Failed to read pages from '{self.file_path}': {str(e)}") from e

    def extract_metadata(self) -> Dict[str, Any]:
        """
        Extracts document metadata.
        
        Returns:
            Dictionary containing title, author, creator, producer, subject, and total pages.
        """
        self.validate_file()
        try:
            reader = pypdf.PdfReader(str(self.file_path))
            info = reader.metadata or {}
            return {
                "file_name": self.file_path.name,
                "file_size_bytes": self.file_path.stat().st_size,
                "total_pages": len(reader.pages),
                "title": info.get("/Title"),
                "author": info.get("/Author"),
                "subject": info.get("/Subject"),
                "creator": info.get("/Creator"),
                "producer": info.get("/Producer"),
                "creation_date": str(info.get("/CreationDate")) if info.get("/CreationDate") else None,
            }
        except Exception as e:
            raise PDFReaderError(f"Failed to read metadata from '{self.file_path}': {str(e)}") from e


# Convenience functions suitable for OpenAI Agent Tool registration

def read_pdf(file_path: str, max_pages: Optional[int] = None) -> str:
    """
    Read and extract text from a PDF file. Suitable as an AI Agent tool.
    
    Args:
        file_path: Path to the PDF file (e.g., 'resume.pdf').
        max_pages: Optional limit on the number of pages to read.
        
    Returns:
        Full text extracted from the PDF document.
    """
    reader = PDFReader(file_path)
    return reader.extract_text(max_pages=max_pages)


def read_pdf_pages(file_path: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read page-by-page text content from a PDF file."""
    reader = PDFReader(file_path)
    return reader.extract_pages(max_pages=max_pages)


def get_pdf_info(file_path: str) -> Dict[str, Any]:
    """Get metadata and page count for a PDF file."""
    reader = PDFReader(file_path)
    return reader.extract_metadata()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tool.pdf_reader <path_to_pdf>")
        sys.exit(1)
    
    target_path = sys.argv[1]
    try:
        r = PDFReader(target_path)
        meta = r.extract_metadata()
        print("=== PDF Metadata ===")
        for k, v in meta.items():
            print(f"  {k}: {v}")
        
        extracted = r.extract_text()
        print("\n=== Extracted Text Preview ===")
        print(extracted[:500] + ("..." if len(extracted) > 500 else ""))
    except Exception as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)

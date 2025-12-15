"""PDF form filling and signature operations."""

from pathlib import Path
from typing import Optional, Dict, Any, List
import tempfile
import shutil

from .config import get_signature_path, get_output_dir


def _get_default_output_dir() -> Path:
    """Get the configured output directory."""
    return Path(get_output_dir())


class PDFOperations:
    """Handle PDF form filling and signature placement."""

    def get_form_fields(self, pdf_path: str) -> Dict[str, Any]:
        """Get all fillable form fields from a PDF.

        Args:
            pdf_path: Path to the PDF file

        Returns:
            Dict with 'fields' list and 'total' count
        """
        from pypdf import PdfReader

        path = Path(pdf_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        reader = PdfReader(str(path))
        fields_dict = reader.get_fields()

        if not fields_dict:
            return {'fields': [], 'total': 0, 'message': 'No fillable form fields found'}

        fields = []
        for name, field in fields_dict.items():
            field_info = {
                'name': name,
                'type': self._get_field_type(field),
                'value': field.get('/V', ''),
            }
            # Add options for choice fields
            if '/Opt' in field:
                field_info['options'] = field['/Opt']
            fields.append(field_info)

        return {'fields': fields, 'total': len(fields)}

    def _get_field_type(self, field: dict) -> str:
        """Determine the type of a form field."""
        ft = field.get('/FT', '')
        if ft == '/Tx':
            return 'text'
        elif ft == '/Btn':
            return 'checkbox' if '/AS' in field else 'button'
        elif ft == '/Ch':
            return 'dropdown' if field.get('/Ff', 0) & 131072 else 'listbox'
        elif ft == '/Sig':
            return 'signature'
        return 'unknown'

    def fill_form(
        self,
        pdf_path: str,
        fields: Dict[str, str],
        output_path: Optional[str] = None,
        flatten: bool = False
    ) -> Dict[str, Any]:
        """Fill out form fields in a PDF.

        Args:
            pdf_path: Path to the source PDF
            fields: Dict mapping field names to values
            output_path: Output path (default: ~/Downloads/{name}_filled.pdf)
            flatten: Whether to flatten the form (make fields non-editable)

        Returns:
            Dict with output path and status
        """
        from pypdf import PdfReader, PdfWriter

        path = Path(pdf_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Determine output path
        if output_path:
            out_path = Path(output_path).expanduser()
        else:
            out_path = _get_default_output_dir() / f"{path.stem}_filled.pdf"

        # Ensure output directory exists
        out_path.parent.mkdir(parents=True, exist_ok=True)

        reader = PdfReader(str(path))
        writer = PdfWriter()

        # Clone the PDF
        writer.append(reader)

        # Fill form fields
        for page_num in range(len(writer.pages)):
            writer.update_page_form_field_values(
                writer.pages[page_num],
                fields,
                auto_regenerate=False
            )

        # Save the result
        with open(out_path, 'wb') as f:
            writer.write(f)

        return {
            'success': True,
            'outputPath': str(out_path),
            'fieldsUpdated': len(fields),
            'flattened': flatten
        }

    def add_signature(
        self,
        pdf_path: str,
        signature_image_path: Optional[str] = None,
        page: int = -1,
        x: Optional[float] = None,
        y: Optional[float] = None,
        width: float = 150,
        output_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Add a signature image to a PDF.

        Args:
            pdf_path: Path to the source PDF
            signature_image_path: Path to signature image (PNG/JPG), uses config default if not provided
            page: Page number (1-indexed, -1 for last page)
            x: X coordinate for signature (default: right side)
            y: Y coordinate for signature (default: bottom area)
            width: Width of signature in points (default: 150)
            output_path: Output path (default: ~/Downloads/{name}_signed.pdf)

        Returns:
            Dict with output path and status
        """
        import fitz  # PyMuPDF

        path = Path(pdf_path).expanduser()

        # Use configured default signature if not provided
        if signature_image_path:
            sig_path = Path(signature_image_path).expanduser()
        else:
            default_sig = get_signature_path()
            if not default_sig:
                raise FileNotFoundError("No signature image provided and no default configured")
            sig_path = Path(default_sig)

        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        if not sig_path.exists():
            raise FileNotFoundError(f"Signature image not found: {sig_path}")

        # Determine output path
        if output_path:
            out_path = Path(output_path).expanduser()
        else:
            out_path = _get_default_output_dir() / f"{path.stem}_signed.pdf"

        # Ensure output directory exists
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Open PDF with PyMuPDF
        doc = fitz.open(str(path))

        # Determine page (convert to 0-indexed)
        if page == -1:
            page_idx = len(doc) - 1
        else:
            page_idx = page - 1

        if page_idx < 0 or page_idx >= len(doc):
            doc.close()
            raise ValueError(f"Invalid page number: {page}. PDF has {len(doc)} pages.")

        target_page = doc[page_idx]
        page_rect = target_page.rect

        # Calculate signature dimensions (maintain aspect ratio)
        img = fitz.Pixmap(str(sig_path))
        aspect_ratio = img.height / img.width
        sig_width = width
        sig_height = width * aspect_ratio

        # Default position: bottom-right with margin
        margin = 50
        if x is None:
            x = page_rect.width - sig_width - margin
        if y is None:
            y = page_rect.height - sig_height - margin

        # Create rectangle for signature
        sig_rect = fitz.Rect(x, y, x + sig_width, y + sig_height)

        # Insert signature image
        target_page.insert_image(sig_rect, filename=str(sig_path))

        # Save the result
        doc.save(str(out_path))
        doc.close()

        return {
            'success': True,
            'outputPath': str(out_path),
            'page': page_idx + 1,
            'position': {'x': x, 'y': y, 'width': sig_width, 'height': sig_height}
        }

    def fill_and_sign(
        self,
        pdf_path: str,
        signature_image_path: Optional[str] = None,
        fields: Optional[Dict[str, str]] = None,
        page: int = -1,
        x: Optional[float] = None,
        y: Optional[float] = None,
        width: float = 150,
        output_path: Optional[str] = None,
        flatten: bool = False
    ) -> Dict[str, Any]:
        """Fill form fields and add signature in one operation.

        Args:
            pdf_path: Path to the source PDF
            signature_image_path: Path to signature image (uses config default if not provided)
            fields: Dict mapping field names to values (optional)
            page: Page number for signature (1-indexed, -1 for last)
            x, y: Signature position coordinates
            width: Signature width in points
            output_path: Output path
            flatten: Whether to flatten form fields

        Returns:
            Dict with output path and status
        """
        import fitz

        path = Path(pdf_path).expanduser()

        # Use configured default signature if not provided
        if signature_image_path:
            sig_path = Path(signature_image_path).expanduser()
        else:
            default_sig = get_signature_path()
            if not default_sig:
                raise FileNotFoundError("No signature image provided and no default configured")
            sig_path = Path(default_sig)

        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        if not sig_path.exists():
            raise FileNotFoundError(f"Signature image not found: {sig_path}")

        # Determine output path
        if output_path:
            out_path = Path(output_path).expanduser()
        else:
            out_path = _get_default_output_dir() / f"{path.stem}_filled_signed.pdf"

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Step 1: Fill form fields if provided
        if fields:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(str(path))
            writer = PdfWriter()
            writer.append(reader)

            for page_num in range(len(writer.pages)):
                writer.update_page_form_field_values(
                    writer.pages[page_num],
                    fields,
                    auto_regenerate=False
                )

            # Save to temp file for signature step
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                writer.write(tmp)
                temp_path = tmp.name
        else:
            temp_path = str(path)

        # Step 2: Add signature using PyMuPDF
        doc = fitz.open(temp_path)

        # Determine page
        if page == -1:
            page_idx = len(doc) - 1
        else:
            page_idx = page - 1

        if page_idx < 0 or page_idx >= len(doc):
            doc.close()
            raise ValueError(f"Invalid page number: {page}. PDF has {len(doc)} pages.")

        target_page = doc[page_idx]
        page_rect = target_page.rect

        # Calculate signature dimensions
        img = fitz.Pixmap(str(sig_path))
        aspect_ratio = img.height / img.width
        sig_width = width
        sig_height = width * aspect_ratio

        # Default position
        margin = 50
        if x is None:
            x = page_rect.width - sig_width - margin
        if y is None:
            y = page_rect.height - sig_height - margin

        sig_rect = fitz.Rect(x, y, x + sig_width, y + sig_height)
        target_page.insert_image(sig_rect, filename=str(sig_path))

        doc.save(str(out_path))
        doc.close()

        # Clean up temp file
        if fields and temp_path != str(path):
            Path(temp_path).unlink(missing_ok=True)

        return {
            'success': True,
            'outputPath': str(out_path),
            'fieldsUpdated': len(fields) if fields else 0,
            'signaturePage': page_idx + 1,
            'signaturePosition': {'x': x, 'y': y, 'width': sig_width, 'height': sig_height}
        }


# Singleton instance
pdf_ops = PDFOperations()

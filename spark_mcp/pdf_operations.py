"""PDF form filling and signature operations."""

from pathlib import Path
from typing import Optional, Dict, Any, List

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
        fields: Optional[Dict[str, str]] = None,
        checkboxes: Optional[Dict[str, bool]] = None,
        output_path: Optional[str] = None,
        flatten: bool = False
    ) -> Dict[str, Any]:
        """Fill out form fields in a PDF.

        Args:
            pdf_path: Path to the source PDF
            fields: Dict mapping field names to string values (for text fields)
            checkboxes: Dict mapping field names to bool values (for checkboxes)
            output_path: Output path (default: ~/Downloads/{name}_filled.pdf)
            flatten: Whether to flatten the form (make fields non-editable)

        Returns:
            Dict with output path and status
        """
        import fitz  # PyMuPDF

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

        doc = fitz.open(str(path))
        fields_updated = 0

        # Fill form fields using pymupdf widgets
        for page in doc:
            for widget in page.widgets():
                field_name = widget.field_name

                # Handle text fields
                if fields and field_name in fields:
                    widget.field_value = fields[field_name]
                    widget.update()
                    fields_updated += 1

                # Handle checkboxes
                if checkboxes and field_name in checkboxes:
                    widget.field_value = checkboxes[field_name]
                    widget.update()
                    fields_updated += 1

        # Save the result
        doc.save(str(out_path))
        doc.close()

        return {
            'success': True,
            'outputPath': str(out_path),
            'fieldsUpdated': fields_updated,
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
        checkboxes: Optional[Dict[str, bool]] = None,
        page: int = -1,
        x: Optional[float] = None,
        y: Optional[float] = None,
        width: float = 150,
        output_path: Optional[str] = None,
        flatten: bool = False,
        signature_field: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fill form fields and add signature in one operation.

        Args:
            pdf_path: Path to the source PDF
            signature_image_path: Path to signature image (uses config default if not provided)
            fields: Dict mapping field names to string values (for text fields)
            checkboxes: Dict mapping field names to bool values (for checkboxes)
            page: Page number for signature (1-indexed, -1 for last)
            x, y: Signature position coordinates (if not using signature_field)
            width: Signature width in points
            output_path: Output path
            flatten: Whether to flatten form fields
            signature_field: Name of form field to place signature in (auto-positions)

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

        doc = fitz.open(str(path))
        fields_updated = 0

        # Fill form fields using pymupdf widgets
        sig_rect = None
        sig_page_idx = None

        for page_idx, doc_page in enumerate(doc):
            for widget in doc_page.widgets():
                field_name = widget.field_name

                # Handle text fields
                if fields and field_name in fields:
                    widget.field_value = fields[field_name]
                    widget.update()
                    fields_updated += 1

                # Handle checkboxes
                if checkboxes and field_name in checkboxes:
                    widget.field_value = checkboxes[field_name]
                    widget.update()
                    fields_updated += 1

                # Find signature field position if specified
                if signature_field and field_name == signature_field:
                    sig_rect = widget.rect
                    sig_page_idx = page_idx

        # Determine signature page
        if sig_page_idx is not None:
            # Use the page where the signature field was found
            target_page_idx = sig_page_idx
        elif page == -1:
            target_page_idx = len(doc) - 1
        else:
            target_page_idx = page - 1

        if target_page_idx < 0 or target_page_idx >= len(doc):
            doc.close()
            raise ValueError(f"Invalid page number: {page}. PDF has {len(doc)} pages.")

        target_page = doc[target_page_idx]
        page_rect = target_page.rect

        # Calculate signature dimensions
        img = fitz.Pixmap(str(sig_path))
        aspect_ratio = img.height / img.width
        sig_width = width
        sig_height = width * aspect_ratio

        # Determine signature position
        if sig_rect is not None:
            # Use the form field's rect for positioning
            final_rect = sig_rect
        else:
            # Use provided coordinates or default to bottom-right
            margin = 50
            if x is None:
                x = page_rect.width - sig_width - margin
            if y is None:
                y = page_rect.height - sig_height - margin
            final_rect = fitz.Rect(x, y, x + sig_width, y + sig_height)

        target_page.insert_image(final_rect, filename=str(sig_path))

        doc.save(str(out_path))
        doc.close()

        return {
            'success': True,
            'outputPath': str(out_path),
            'fieldsUpdated': fields_updated,
            'signaturePage': target_page_idx + 1,
            'signaturePosition': {
                'x': final_rect.x0,
                'y': final_rect.y0,
                'width': final_rect.width,
                'height': final_rect.height
            }
        }


# Singleton instance
pdf_ops = PDFOperations()

"""PDF form filling and signature operations."""

import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from .config import (
    get_signature_path, get_output_dir, get_templates_dir,
    load_template, save_template as save_template_config,
    list_templates as list_templates_config, delete_template as delete_template_config
)


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
        y_from_top: Optional[float] = None,
        width: float = 150,
        output_path: Optional[str] = None,
        flatten: bool = False,
        signature_field: Optional[str] = None,
        text_annotations: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Fill form fields and add signature in one operation.

        Args:
            pdf_path: Path to the source PDF
            signature_image_path: Path to signature image (uses config default if not provided)
            fields: Dict mapping field names to string values (for text fields)
            checkboxes: Dict mapping field names to bool values (for checkboxes)
            page: Page number for signature (1-indexed, -1 for last)
            x: Signature X position in points from left
            y: Signature Y position in points (fitz coords, top of signature)
            y_from_top: Y position of signature LINE from top - signature bottom aligns here
            width: Signature width in points
            output_path: Output path
            flatten: Whether to flatten form fields
            signature_field: Name of form field to place signature in (auto-positions)
            text_annotations: List of text annotations for non-fillable blanks, each with:
                - page: Page number (1-indexed, -1 for last)
                - text: Text to add
                - x: X position in points from left
                - yFromTop: Y position from top (preferred)
                - fontSize: Font size (default: 12)

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

            # Handle y coordinate - support yFromTop where signature bottom aligns with line
            if y_from_top is not None:
                # yFromTop specifies where the signature LINE is
                # Place signature so its BOTTOM aligns with this line
                sig_top = y_from_top - sig_height
                final_rect = fitz.Rect(x, sig_top, x + sig_width, y_from_top)
            elif y is not None:
                # y is the top of the signature in fitz coords
                final_rect = fitz.Rect(x, y, x + sig_width, y + sig_height)
            else:
                # Default to bottom-right
                y = page_rect.height - sig_height - margin
                final_rect = fitz.Rect(x, y, x + sig_width, y + sig_height)

        target_page.insert_image(final_rect, filename=str(sig_path))

        # Add text annotations for non-fillable blanks
        annotations_added = 0
        if text_annotations:
            for anno in text_annotations:
                anno_page_num = anno.get('page', -1)
                text = anno.get('text', '')
                anno_x = anno.get('x', 0)
                font_size = anno.get('fontSize', 12)

                # Convert page number
                if anno_page_num == -1:
                    anno_page_idx = len(doc) - 1
                else:
                    anno_page_idx = anno_page_num - 1

                if anno_page_idx < 0 or anno_page_idx >= len(doc):
                    continue

                anno_page = doc[anno_page_idx]
                anno_page_height = anno_page.rect.height

                # Support both yFromTop (direct fitz coords) and y (from bottom)
                if 'yFromTop' in anno:
                    anno_y = anno.get('yFromTop', 0)
                else:
                    anno_y_from_bottom = anno.get('y', 0)
                    anno_y = anno_page_height - anno_y_from_bottom

                text_point = fitz.Point(anno_x, anno_y)
                anno_page.insert_text(
                    text_point,
                    text,
                    fontsize=font_size,
                    fontname="helv",
                    color=(0, 0, 0)
                )
                annotations_added += 1

        doc.save(str(out_path))
        doc.close()

        result = {
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
        if annotations_added > 0:
            result['annotationsAdded'] = annotations_added

        return result

    def annotate_pdf(
        self,
        pdf_path: str,
        annotations: List[Dict[str, Any]],
        output_path: Optional[str] = None,
        flatten: bool = False
    ) -> Dict[str, Any]:
        """Add text annotations to any PDF at specified coordinates.

        This works on any PDF, even those without fillable form fields.
        Useful for filling in blank lines on legal documents.

        Args:
            pdf_path: Path to the source PDF
            annotations: List of annotations, each with:
                - page: Page number (1-indexed, -1 for last page)
                - text: Text to add
                - x: X position in points from left
                - y: Y position in points from bottom (PDF coordinates)
                - fontSize: Font size (default: 12)
                - fontFamily: Font family (default: "helv" for Helvetica)
                - fontColor: Hex color string (default: "000000")
            output_path: Output path (default: ~/Downloads/{name}_annotated.pdf)
            flatten: Make annotations permanent (default: False)

        Returns:
            Dict with output path and status
        """
        import fitz

        path = Path(pdf_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        if output_path:
            out_path = Path(output_path).expanduser()
        else:
            out_path = _get_default_output_dir() / f"{path.stem}_annotated.pdf"

        out_path.parent.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(str(path))
        annotations_added = 0

        for anno in annotations:
            page_num = anno.get('page', -1)
            text = anno.get('text', '')
            x = anno.get('x', 0)
            font_size = anno.get('fontSize', 12)
            font_family = anno.get('fontFamily', 'helv')
            font_color_hex = anno.get('fontColor', '000000')

            # Convert page number
            if page_num == -1:
                page_idx = len(doc) - 1
            else:
                page_idx = page_num - 1

            if page_idx < 0 or page_idx >= len(doc):
                continue

            page = doc[page_idx]
            page_height = page.rect.height

            # Support both yFromTop (direct fitz coords) and y (from bottom)
            if 'yFromTop' in anno:
                y = anno.get('yFromTop', 0)
            else:
                # Convert y from bottom (PDF standard) to y from top (fitz)
                y_from_bottom = anno.get('y', 0)
                y = page_height - y_from_bottom

            # Parse hex color
            try:
                r = int(font_color_hex[0:2], 16) / 255.0
                g = int(font_color_hex[2:4], 16) / 255.0
                b = int(font_color_hex[4:6], 16) / 255.0
                color = (r, g, b)
            except (ValueError, IndexError):
                color = (0, 0, 0)

            # Insert text
            text_point = fitz.Point(x, y)
            page.insert_text(
                text_point,
                text,
                fontsize=font_size,
                fontname=font_family,
                color=color
            )
            annotations_added += 1

        doc.save(str(out_path))
        doc.close()

        return {
            'success': True,
            'outputPath': str(out_path),
            'annotationsAdded': annotations_added
        }

    def get_pdf_layout(
        self,
        pdf_path: str,
        page: Optional[int] = None,
        detect_blank_lines: bool = True
    ) -> Dict[str, Any]:
        """Analyze PDF pages to help determine annotation coordinates.

        Args:
            pdf_path: Path to the PDF file
            page: Specific page number (1-indexed), or None for all pages
            detect_blank_lines: Try to detect signature/fill lines (default: True)

        Returns:
            Dict with page dimensions and detected features
        """
        import fitz

        path = Path(pdf_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        doc = fitz.open(str(path))
        pages_info = []

        # Determine which pages to analyze
        if page is not None:
            if page == -1:
                page_indices = [len(doc) - 1]
            else:
                page_indices = [page - 1]
        else:
            page_indices = range(len(doc))

        for page_idx in page_indices:
            if page_idx < 0 or page_idx >= len(doc):
                continue

            doc_page = doc[page_idx]
            page_rect = doc_page.rect

            page_info = {
                'pageNumber': page_idx + 1,
                'width': page_rect.width,
                'height': page_rect.height,
                'textBlocks': [],
                'blankLines': []
            }

            # Extract text blocks with positions
            text_dict = doc_page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") == 0:  # Text block
                    for line in block.get("lines", []):
                        line_text = ""
                        x0 = line["bbox"][0]
                        y_from_top = line["bbox"][1]

                        for span in line.get("spans", []):
                            line_text += span.get("text", "")

                        if line_text.strip():
                            # Convert y from top to y from bottom
                            y_from_bottom = page_rect.height - y_from_top
                            page_info['textBlocks'].append({
                                'text': line_text.strip()[:100],  # Truncate long text
                                'x': round(x0, 1),
                                'y': round(y_from_bottom, 1),
                                'yFromTop': round(y_from_top, 1)
                            })

            # Detect blank lines (underscores, form lines)
            if detect_blank_lines:
                # Look for horizontal lines that might be fill-in blanks
                drawings = doc_page.get_drawings()
                for drawing in drawings:
                    if drawing.get("type") == "l":  # Line
                        items = drawing.get("items", [])
                        for item in items:
                            if item[0] == "l":  # Line item
                                p1, p2 = item[1], item[2]
                                # Check if roughly horizontal (within 5 points)
                                if abs(p1.y - p2.y) < 5 and abs(p2.x - p1.x) > 30:
                                    y_from_bottom = page_rect.height - min(p1.y, p2.y)
                                    page_info['blankLines'].append({
                                        'xStart': round(min(p1.x, p2.x), 1),
                                        'xEnd': round(max(p1.x, p2.x), 1),
                                        'y': round(y_from_bottom, 1),
                                        'yFromTop': round(min(p1.y, p2.y), 1)
                                    })

                # Also look for series of underscores
                text = doc_page.get_text()
                underscore_pattern = '_' * 5  # At least 5 underscores
                if underscore_pattern in text:
                    # Find text blocks containing underscores
                    for block in page_info['textBlocks']:
                        if underscore_pattern in block.get('text', ''):
                            block['isBlankLine'] = True

            pages_info.append(page_info)

        total_pages = len(doc)
        doc.close()

        return {
            'success': True,
            'totalPages': total_pages,
            'pages': pages_info
        }

    def save_pdf_template(
        self,
        template_name: str,
        fields: List[Dict[str, Any]],
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Save a PDF template for reuse.

        Args:
            template_name: Name for the template (e.g., "protective_order_appendix_a")
            fields: List of field definitions, each with:
                - fieldName: Name for this field (e.g., "name", "address")
                - page: Page number (1-indexed, -1 for last)
                - x: X position in points
                - y: Y position in points from bottom
                - fontSize: Font size (default: 12)
                - type: "text", "signature", or "date"
            description: Optional description of the template

        Returns:
            Dict with success status and template path
        """
        template_data = {
            "name": template_name,
            "description": description or "",
            "fields": fields
        }

        template_path = save_template_config(template_name, template_data)

        return {
            'success': True,
            'templateName': template_name,
            'templatePath': str(template_path),
            'fieldCount': len(fields)
        }

    def list_pdf_templates(self) -> Dict[str, Any]:
        """List all saved PDF templates."""
        templates = list_templates_config()
        return {
            'success': True,
            'templates': templates,
            'total': len(templates)
        }

    def delete_pdf_template(self, template_name: str) -> Dict[str, Any]:
        """Delete a saved PDF template."""
        deleted = delete_template_config(template_name)
        return {
            'success': deleted,
            'templateName': template_name,
            'message': f"Template '{template_name}' deleted" if deleted else f"Template '{template_name}' not found"
        }

    def fill_from_template(
        self,
        pdf_path: str,
        template_name: str,
        values: Dict[str, str],
        sign: bool = False,
        signature_image_path: Optional[str] = None,
        output_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """Fill a PDF using a saved template.

        Args:
            pdf_path: Path to the source PDF
            template_name: Name of the saved template
            values: Dict mapping field names to values
                - Use "auto" for date fields to insert current date
            sign: Whether to add signature (default: False)
            signature_image_path: Path to signature image (uses config default if not provided)
            output_path: Output path (default: ~/Downloads/{name}_filled.pdf)

        Returns:
            Dict with output path and status
        """
        import fitz
        from datetime import datetime

        path = Path(pdf_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        template = load_template(template_name)
        if not template:
            raise ValueError(f"Template not found: {template_name}")

        if output_path:
            out_path = Path(output_path).expanduser()
        else:
            out_path = _get_default_output_dir() / f"{path.stem}_filled.pdf"

        out_path.parent.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(str(path))
        fields_filled = 0
        signature_added = False

        for field in template.get("fields", []):
            field_name = field.get("fieldName")
            field_type = field.get("type", "text")
            page_num = field.get("page", -1)
            x = field.get("x", 0)
            y_from_bottom = field.get("y", 0)
            font_size = field.get("fontSize", 12)

            # Get value for this field
            value = values.get(field_name)
            if value is None:
                continue

            # Handle auto date
            if field_type == "date" and value.lower() == "auto":
                value = datetime.now().strftime("%B %d, %Y")

            # Handle signature field
            if field_type == "signature":
                if sign:
                    # Add signature at this position
                    if signature_image_path:
                        sig_path = Path(signature_image_path).expanduser()
                    else:
                        default_sig = get_signature_path()
                        if not default_sig:
                            continue
                        sig_path = Path(default_sig)

                    if sig_path.exists():
                        if page_num == -1:
                            page_idx = len(doc) - 1
                        else:
                            page_idx = page_num - 1

                        if 0 <= page_idx < len(doc):
                            page = doc[page_idx]
                            page_height = page.rect.height
                            y = page_height - y_from_bottom

                            # Calculate signature size
                            width = field.get("width", 150)
                            img = fitz.Pixmap(str(sig_path))
                            aspect_ratio = img.height / img.width
                            sig_height = width * aspect_ratio

                            sig_rect = fitz.Rect(x, y - sig_height, x + width, y)
                            page.insert_image(sig_rect, filename=str(sig_path))
                            signature_added = True
                continue

            # Add text annotation
            if page_num == -1:
                page_idx = len(doc) - 1
            else:
                page_idx = page_num - 1

            if 0 <= page_idx < len(doc):
                page = doc[page_idx]
                page_height = page.rect.height
                y = page_height - y_from_bottom

                text_point = fitz.Point(x, y)
                page.insert_text(
                    text_point,
                    value,
                    fontsize=font_size,
                    fontname="helv",
                    color=(0, 0, 0)
                )
                fields_filled += 1

        doc.save(str(out_path))
        doc.close()

        result = {
            'success': True,
            'outputPath': str(out_path),
            'templateName': template_name,
            'fieldsFilled': fields_filled
        }
        if sign:
            result['signatureAdded'] = signature_added

        return result


# Singleton instance
pdf_ops = PDFOperations()

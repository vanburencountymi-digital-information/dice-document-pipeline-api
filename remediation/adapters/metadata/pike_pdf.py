import os

import pikepdf

from remediation.adapters.base import MetadataAdapter


class PikePdfAdapter(MetadataAdapter):
    """Wraps pikepdf to patch the accessibility metadata gaps OpenDataLoader's tagging
    leaves behind (ADR 0010): `MarkInfo.Marked`, `Root.Lang`, title (`/Info` + XMP
    `dc:title`), `/Tabs /S` on every page, `ViewerPreferences.DisplayDocTitle`, and the
    XMP PDF/UA identification schema (`pdfuaid:part`) — the last two are catalog/metadata
    gaps veraPDF's `ua1` postcheck flags on every document (ISO 14289-1:2014 clauses 7.1
    and 5). Doesn't touch tag structure — see ADR 0010 for why this step is named
    `finalize_metadata`, not `finalize_tags`.
    """

    @property
    def name(self) -> str:
        return "pikepdf Adapter"

    def finalize(self, pdf_path: str, *, output_dir: str, title: str, lang: str) -> str:
        """Applies the metadata fixes to `pdf_path` and writes the result into `output_dir`
        under the same filename, same convention as `OCRAdapter.extract`. Returns the
        output path.
        """
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, os.path.basename(pdf_path))

        try:
            with pikepdf.open(pdf_path) as pdf:
                pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
                pdf.Root.Lang = lang
                pdf.Root.ViewerPreferences = pikepdf.Dictionary(DisplayDocTitle=True)

                with pdf.open_metadata() as meta:
                    meta["dc:title"] = title
                    meta["pdfuaid:part"] = "1"

                for page in pdf.pages:
                    page["/Tabs"] = pikepdf.Name("/S")

                pdf.save(output_path)
        except Exception as exc:
            self.raise_adapter_error(f"pikepdf failed to finalize metadata: {exc}")

        return output_path

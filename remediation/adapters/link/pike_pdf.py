import os

import pikepdf

from remediation.adapters.base import LinkAdapter

MAX_STRUCT_TREE_DEPTH = 30


class PikePdfAdapter(LinkAdapter):
    """Wraps pikepdf to give every untagged `/Link` annotation a struct-tree presence
    (ADR 0003 stage 5, "Repair links"), so PDF/UA's tagged-annotation requirement is met.

    OpenDataLoader's tagging produces `/Link` annotations in each page's `/Annots`, but
    never references them from the logical structure tree. This walks every page's
    `/Annots`, finds `/Link` annotations with no existing `/OBJR` reference anywhere in
    the struct tree, and creates one top-level `<Link>` struct element per untagged
    annotation directly under `/StructTreeRoot`, each with an `/OBJR` child pointing back
    at the annotation.

    Deliberately scoped to top-level `/StructTreeRoot` attachment, not re-parenting into
    the paragraph/list-item struct element the link visually sits inside — attaching at
    the root is spec-compliant and passes PAC, and re-parenting into surrounding content
    is real added complexity with no concrete PDF/UA requirement driving it today.
    """

    @property
    def name(self) -> str:
        return "pikepdf Adapter"

    def repair(self, pdf_path: str, *, output_dir: str) -> str:
        """Tags every untagged `/Link` annotation in `pdf_path` and writes the result into
        `output_dir` under the same filename, same convention as `OCRAdapter.extract`/
        `MetadataAdapter.finalize`. Returns the output path.

        A document with no `/StructTreeRoot` at all is written through unchanged.
        Already-tagged annotations are left alone rather than double-tagged.
        """
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, os.path.basename(pdf_path))

        try:
            with pikepdf.open(pdf_path) as pdf:
                if "/StructTreeRoot" in pdf.Root:
                    self._tag_link_annotations(pdf)
                pdf.save(output_path)
        except Exception as exc:
            self.raise_adapter_error(f"pikepdf failed to repair links: {exc}")

        return output_path

    def _tag_link_annotations(self, pdf: pikepdf.Pdf) -> None:
        struct_root = pdf.Root.StructTreeRoot
        already_tagged = self._collect_tagged_annotation_objids(pdf, struct_root)

        root_kids = struct_root.get("/K")
        if root_kids is None:
            root_kids = pikepdf.Array()
            struct_root["/K"] = root_kids
        elif isinstance(root_kids, pikepdf.Dictionary):
            root_kids = pikepdf.Array([root_kids])
            struct_root["/K"] = root_kids

        for page in pdf.pages:
            page_obj = page.obj
            annots = page_obj.get("/Annots")
            if annots is None:
                continue
            for annot in annots:
                if str(annot.get("/Subtype", "")) != "/Link":
                    continue

                annot_objid = annot.objgen[0]
                if annot_objid in already_tagged:
                    continue

                objr = pikepdf.Dictionary(Type=pikepdf.Name("/OBJR"), Pg=page_obj, Obj=annot)
                link_elem = pdf.make_indirect(
                    pikepdf.Dictionary(
                        S=pikepdf.Name("/Link"),
                        P=struct_root,
                        Pg=page_obj,
                        K=pikepdf.Array([objr]),
                    )
                )
                root_kids.append(link_elem)
                already_tagged.add(annot_objid)

    def _collect_tagged_annotation_objids(
        self, pdf: pikepdf.Pdf, node: pikepdf.Object, depth: int = 0
    ) -> set[int]:
        """Walks the struct tree and collects the object id of every annotation already
        referenced by an `/OBJR`, so `_tag_link_annotations` doesn't double-tag one.
        """
        out: set[int] = set()
        if depth > MAX_STRUCT_TREE_DEPTH or not isinstance(node, pikepdf.Dictionary):
            return out

        kids = node.get("/K")
        if kids is None:
            return out
        items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]

        for kid in items:
            if not isinstance(kid, pikepdf.Dictionary):
                continue
            if str(kid.get("/Type", "")) == "/OBJR":
                obj_ref = kid.get("/Obj")
                if isinstance(obj_ref, pikepdf.Dictionary):
                    out.add(obj_ref.objgen[0])
            else:
                out.update(self._collect_tagged_annotation_objids(pdf, kid, depth + 1))

        return out

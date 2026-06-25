"""Tag untagged /Link annotations as <Link> struct elements in a tagged PDF.

PAC 2024 "Tagged annotations" check requires every user-visible annotation to
appear in the logical structure tree. OpenDataLoader does not do this — it
produces link annotations in page /Annots but never references them from the
tag tree.

Strategy: walk each page's /Annots; find /Link annotations that have no
existing OBJR reference in the struct tree; create a top-level <Link> struct
element under the StructTreeRoot for each one, with an OBJR child pointing at
the annotation object.

Attaching directly to StructTreeRoot is technically spec-compliant and passes
PAC. Placing links inside paragraphs requires re-parenting existing struct
elements — a much more complex operation deferred to a later story.
"""

from __future__ import annotations

from pathlib import Path


def tag_link_annotations(tagged_path: Path) -> int:
    """Add missing <Link> struct elements for all untagged /Link annotations.

    Returns the number of annotations tagged.
    Modifies tagged_path in-place.
    """
    import pikepdf

    with pikepdf.open(tagged_path, allow_overwriting_input=True) as pdf:
        if "/StructTreeRoot" not in pdf.Root:
            return 0

        struct_root = pdf.Root["/StructTreeRoot"]
        already_tagged_objids = _collect_tagged_annotation_objids(pdf, struct_root)

        tagged_count = 0
        root_kids = struct_root.get("/K")
        if root_kids is None:
            root_kids = pikepdf.Array()
            struct_root["/K"] = root_kids
        elif isinstance(root_kids, pikepdf.Dictionary):
            root_kids = pikepdf.Array([root_kids])
            struct_root["/K"] = root_kids

        for page in pdf.pages:
            annots = page.get("/Annots")
            if annots is None:
                continue
            for annot_ref in annots:
                try:
                    annot = (
                        annot_ref
                        if isinstance(annot_ref, pikepdf.Dictionary)
                        else pdf.get_object(annot_ref.objgen)
                    )
                    if str(annot.get("/Subtype", "")) != "/Link":
                        continue
                    annot_objid = annot.objgen[0]
                    if annot_objid in already_tagged_objids:
                        continue

                    # Build OBJR and <Link> struct element as indirect objects
                    objr = pdf.make_indirect(
                        pikepdf.Dictionary(
                            Type=pikepdf.Name("/OBJR"),
                            Pg=page,
                            Obj=annot_ref if not isinstance(annot_ref, pikepdf.Dictionary) else pdf.make_indirect(annot),
                        )
                    )
                    link_elem = pdf.make_indirect(
                        pikepdf.Dictionary(
                            S=pikepdf.Name("/Link"),
                            P=struct_root,
                            Pg=page,
                            K=pikepdf.Array([objr]),
                        )
                    )
                    root_kids.append(link_elem)
                    already_tagged_objids.add(annot_objid)
                    tagged_count += 1
                except Exception:
                    pass

        if tagged_count:
            pdf.save()

    return tagged_count


# ── Helpers ───────────────────────────────────────────────────────────────────

def _collect_tagged_annotation_objids(pdf, node, depth: int = 0) -> set[int]:
    """Walk the struct tree and collect all annotation object IDs referenced by OBJR."""
    import pikepdf

    out: set[int] = set()
    if depth > 30 or not isinstance(node, pikepdf.Dictionary):
        return out

    kids = node.get("/K")
    if kids is None:
        return out
    items = list(kids) if isinstance(kids, pikepdf.Array) else [kids]

    for kid in items:
        try:
            child = kid if isinstance(kid, pikepdf.Dictionary) else pdf.get_object(kid.objgen)
            if str(child.get("/Type", "")) == "/OBJR":
                obj_ref = child.get("/Obj")
                if obj_ref is not None:
                    try:
                        resolved = obj_ref if isinstance(obj_ref, pikepdf.Dictionary) else pdf.get_object(obj_ref.objgen)
                        out.add(resolved.objgen[0])
                    except Exception:
                        pass
            else:
                out.update(_collect_tagged_annotation_objids(pdf, child, depth + 1))
        except Exception:
            pass

    return out

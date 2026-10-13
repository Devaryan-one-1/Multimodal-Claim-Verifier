from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

import pymupdf
import streamlit as st
from PIL import Image, ImageDraw


def render_document_viewer(
    document_id: str,
    page_count: int,
    initial_page: int = 1,
    evidence: dict[str, Any] | None = None,
    render_page_callback: Callable[..., dict[str, Any]] | None = None,
    key_prefix: str = "",
) -> int:
    if page_count < 1:
        st.info("The source document has no available pages.")
        return 1

    state_id = f"{key_prefix}::{document_id}" if key_prefix else document_id
    page_key = f"viewer_page_{state_id}"
    input_key = f"page_input_{state_id}"

    st.session_state.setdefault(
        page_key,
        _clamp(initial_page, page_count),
    )

    st.session_state.setdefault(
        input_key,
        st.session_state[page_key],
    )

    st.session_state[input_key] = _clamp(
        st.session_state[input_key],
        page_count,
    )

    st.session_state[page_key] = (
        st.session_state[input_key]
    )

    st.subheader("Source document")

    previous, number, next_page = st.columns(
        [1, 1.2, 1]
    )

    previous.button(
        "← Previous",
        key=f"previous_{state_id}",
        disabled=st.session_state[page_key] <= 1,
        on_click=_change_page,
        args=(state_id, -1, page_count),
        use_container_width=True,
    )

    number.number_input(
        "Page",
        min_value=1,
        max_value=page_count,
        step=1,
        key=input_key,
        on_change=_sync_page,
        args=(state_id,),
    )

    next_page.button(
        "Next →",
        key=f"next_{state_id}",
        disabled=st.session_state[page_key] >= page_count,
        on_click=_change_page,
        args=(state_id, 1, page_count),
        use_container_width=True,
    )

    page = int(st.session_state[page_key])

    try:
        image_path = resolve_page_image(
            document_id,
            page,
            render_page_callback,
        )

        if image_path is None:
            st.warning(
                f"Page {page} could not be rendered."
            )
            return page

        with Image.open(image_path) as source:
            image = source.convert("RGB")

    except Exception as exc:
        st.error(
            f"Could not open page {page}: {exc}"
        )
        return page

    if (
        evidence
        and evidence.get("document_id", document_id)
        == document_id
        and _evidence_page(evidence) == page
    ):
        if evidence.get("bbox") or evidence.get("location"):
            try:
                box = _image_box(
                    image,
                    evidence,
                    document_id,
                    page,
                )

                image = _draw_highlight(image, box)

                st.caption(
                    "Highlighted region: selected evidence."
                )

            except (ValueError, OSError, RuntimeError, ZeroDivisionError) as exc:
                st.caption(
                    f"Page shown without a highlight: {exc}"
                )
        else:
            st.caption(
                "This citation has no region coordinates; "
                "the full page is shown."
            )

    st.image(
        image,
        use_container_width=True,
        caption=f"Page {page} of {page_count}",
    )

    return page


def go_to_page(
    document_id: str,
    page_number: int,
) -> None:
    """
    Call before render_document_viewer,
    or from a widget callback.
    """

    page = max(1, int(page_number))

    st.session_state[
        f"viewer_page_{document_id}"
    ] = page

    st.session_state[
        f"page_input_{document_id}"
    ] = page


def _sync_page(document_id: str) -> None:
    st.session_state[
        f"viewer_page_{document_id}"
    ] = int(
        st.session_state[
            f"page_input_{document_id}"
        ]
    )


def _change_page(
    document_id: str,
    delta: int,
    page_count: int,
) -> None:
    current = st.session_state.get(
        f"page_input_{document_id}",
        1,
    )

    go_to_page(
        document_id,
        _clamp(
            int(current) + delta,
            page_count,
        ),
    )


def resolve_page_image(
    document_id: str,
    page_number: int,
    render_page_callback,
) -> Path | None:
    path = (
        Path("data/pages")
        / document_id
        / f"page_{page_number:03d}.png"
    )

    if path.is_file():
        return path

    if render_page_callback is None:
        return None

    result = render_page_callback(
        document_id=document_id,
        page_number=page_number,
    )

    if not result or not result.get("success"):
        raise RuntimeError(
            (result or {}).get(
                "message",
                "The renderer did not return "
                "a successful page.",
            )
        )

    for key in [
        "image_path",
        "path",
        "output_path",
    ]:
        returned = result.get(key)

        if returned and Path(returned).is_file():
            return Path(returned)

    return path if path.is_file() else None


def highlight_evidence(
    image: Image.Image,
    evidence: dict[str, Any],
) -> Image.Image:
    """
    Compatibility wrapper.

    The full viewer adds document and page context.
    """

    document_id = evidence.get("document_id")
    page = _evidence_page(evidence)

    box = _image_box(
        image,
        evidence,
        document_id,
        page,
    )

    return _draw_highlight(image, box)


def _image_box(
    image,
    evidence,
    document_id,
    page,
):
    bbox = (
        evidence.get("bbox")
        or evidence.get("location")
    )

    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
    ):
        raise ValueError(
            "Region coordinates are missing or invalid."
        )

    try:
        x0, y0, x1, y1 = map(float, bbox)
    except (TypeError, ValueError):
        raise ValueError(
            "Region coordinates are not numeric."
        )

    if (
        not all(
            math.isfinite(v)
            for v in [x0, y0, x1, y1]
        )
        or x1 <= x0
        or y1 <= y0
    ):
        raise ValueError(
            "Region coordinates do not define "
            "a valid rectangle."
        )

    space = str(
        evidence.get("bbox_space") or ""
    ).lower()

    kind = str(
        evidence.get("type")
        or evidence.get("kind")
        or ""
    ).lower()

    if not space:
        if all(
            0 <= v <= 1
            for v in [x0, y0, x1, y1]
        ):
            space = "normalized"

        elif kind in {
            "table",
            "text",
            "paragraph",
            "caption",
            "footnote",
            "heading",
            "table_llm",
            "text_llm",
        }:
            space = "pdf_points"

        else:
            raise ValueError(
                "The backend has not supplied "
                "the coordinate system."
            )

    if space in {
        "normalized",
        "normalized_0_1",
    }:
        sx = image.width
        sy = image.height

    elif space == "normalized_0_1000":
        # Expects x0, y0, x1, y1.
        # Convert raw Gemini y/x boxes first.
        sx = image.width / 1000
        sy = image.height / 1000

    elif space in {
        "image_pixels",
        "pixels",
    }:
        width = (
            evidence.get("width_px")
            or evidence.get("image_width")
        )

        height = (
            evidence.get("height_px")
            or evidence.get("image_height")
        )

        sx = (
            image.width / float(width)
            if width
            else 1
        )

        sy = (
            image.height / float(height)
            if height
            else 1
        )

    elif space in {
        "pdf_points",
        "pdf",
    }:
        if evidence.get("page_rotation"):
            raise ValueError(
                "This rotated page needs a "
                "rotation-aware PDF-point box."
            )

        width = evidence.get("page_width_pt")
        height = evidence.get("page_height_pt")

        if not width or not height:
            width, height = _pdf_size(
                document_id,
                page,
            )

        sx = image.width / float(width)
        sy = image.height / float(height)

    else:
        raise ValueError(
            f"Unsupported coordinate system: {space}."
        )

    if not all(
        math.isfinite(v) and v > 0
        for v in [sx, sy]
    ):
        raise ValueError(
            "Source image dimensions are invalid."
        )

    x0, x1 = x0 * sx, x1 * sx
    y0, y1 = y0 * sy, y1 * sy

    if (
        x1 <= 0
        or y1 <= 0
        or x0 >= image.width
        or y0 >= image.height
    ):
        raise ValueError(
            "The region lies outside the rendered page."
        )

    return (
        max(0, x0),
        max(0, y0),
        min(image.width - 1, x1),
        min(image.height - 1, y1),
    )


def _pdf_size(document_id, page):
    if not document_id or page is None:
        raise ValueError(
            "Document and page metadata are required "
            "for PDF coordinates."
        )

    candidates = [
        (
            Path("data/documents")
            / document_id
            / "original.pdf"
        ),
        (
            Path("data/documents")
            / f"{document_id}.pdf"
        ),
    ]

    path = next(
        (
            candidate
            for candidate in candidates
            if candidate.is_file()
        ),
        None,
    )

    if path is None:
        raise ValueError(
            "The original PDF is unavailable "
            "for coordinate conversion."
        )

    with pymupdf.open(path) as pdf:
        source = pdf.load_page(page - 1)

        if source.rotation:
            raise ValueError(
                "PDF-point highlighting requires "
                "a rotation-aware box for this rotated page."
            )

        return (
            source.rect.width,
            source.rect.height,
        )


def _draw_highlight(image, box):
    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0),
    )

    ImageDraw.Draw(overlay).rectangle(
        box,
        fill=(56, 189, 248, 45),
        outline=(14, 165, 233, 255),
        width=max(3, image.width // 400),
    )

    return Image.alpha_composite(
        image.convert("RGBA"),
        overlay,
    ).convert("RGB")


def _evidence_page(evidence):
    try:
        return int(
            evidence.get("page")
            or evidence.get("page_number")
        )
    except (TypeError, ValueError):
        return None


def _clamp(page, count):
    return max(
        1,
        min(int(page), int(count)),
    )
# from __future__ import annotations

# from pathlib import Path
# from typing import Any, Callable

# import streamlit as st
# from PIL import Image, ImageDraw


# def render_document_viewer(
#     document_id: str,
#     page_count: int,
#     initial_page: int = 1,
#     evidence: dict[str, Any] | None = None,
#     render_page_callback: Callable[..., dict[str, Any]] | None = None,
# ) -> int:
#     """
#     Render a compact PDF page viewer.

#     `render_page_callback` may be app.extraction.image_renderer.render_page.
#     """

#     if page_count < 1:
#         st.info("No rendered document pages are available.")
#         return 1

#     state_key = f"viewer_page_{document_id}"

#     if state_key not in st.session_state:
#         st.session_state[state_key] = _clamp(
#             initial_page,
#             1,
#             page_count,
#         )

#     st.subheader("Source document")

#     nav1, nav2, nav3, nav4 = st.columns(
#         [1, 1.4, 1, 1.2]
#     )

#     if nav1.button(
#         "← Previous",
#         key=f"previous_{document_id}",
#         use_container_width=True,
#         disabled=st.session_state[state_key] <= 1,
#     ):
#         st.session_state[state_key] -= 1

#     requested_page = nav2.number_input(
#         "Page",
#         min_value=1,
#         max_value=page_count,
#         value=st.session_state[state_key],
#         step=1,
#         key=f"page_input_{document_id}",
#     )

#     st.session_state[state_key] = int(requested_page)

#     if nav3.button(
#         "Next →",
#         key=f"next_{document_id}",
#         use_container_width=True,
#         disabled=st.session_state[state_key] >= page_count,
#     ):
#         st.session_state[state_key] += 1

#     zoom = nav4.select_slider(
#         "Zoom",
#         options=[75, 100, 125, 150, 175, 200],
#         value=100,
#         key=f"zoom_{document_id}",
#     )

#     page_number = st.session_state[state_key]

#     image_path = resolve_page_image(
#         document_id=document_id,
#         page_number=page_number,
#         render_page_callback=render_page_callback,
#     )

#     if image_path is None:
#         st.warning(
#             f"Page {page_number} has not been rendered yet."
#         )
#         return page_number

#     try:
#         image = Image.open(image_path).convert("RGB")
#     except Exception as exc:
#         st.error(f"Could not open the rendered page: {exc}")
#         return page_number

#     if evidence and _evidence_page(evidence) == page_number:
#         image = highlight_evidence(image, evidence)

#     base_width = min(image.width, 950)
#     display_width = int(base_width * (zoom / 100))

#     st.image(
#         image,
#         width=display_width,
#         caption=f"Page {page_number} of {page_count}",
#     )

#     return page_number


# def go_to_page(
#     document_id: str,
#     page_number: int,
# ) -> None:
#     """Set the viewer page from an evidence-card action."""

#     st.session_state[
#         f"viewer_page_{document_id}"
#     ] = max(1, int(page_number))


# def resolve_page_image(
#     document_id: str,
#     page_number: int,
#     render_page_callback: Callable[..., dict[str, Any]] | None,
# ) -> Path | None:
#     expected_path = (
#         Path("data")
#         / "pages"
#         / document_id
#         / f"page_{page_number:03d}.png"
#     )

#     if expected_path.exists():
#         return expected_path

#     if render_page_callback is None:
#         return None

#     result = render_page_callback(
#         document_id=document_id,
#         page_number=page_number,
#     )

#     if not result or not result.get("success"):
#         return None

#     returned_path = (
#         result.get("path")
#         or result.get("image_path")
#         or result.get("output_path")
#     )

#     if returned_path and Path(returned_path).exists():
#         return Path(returned_path)

#     return expected_path if expected_path.exists() else None


# def highlight_evidence(
#     image: Image.Image,
#     evidence: dict[str, Any],
# ) -> Image.Image:
#     """
#     Draw an evidence highlight.

#     Supported bbox formats:
#     - Pixel coordinates: [x0, y0, x1, y1]
#     - Normalized coordinates between 0 and 1
#     """

#     bbox = evidence.get("bbox")

#     if not bbox or len(bbox) != 4:
#         return image

#     try:
#         x0, y0, x1, y1 = map(float, bbox)
#     except (TypeError, ValueError):
#         return image

#     if max(x0, y0, x1, y1) <= 1:
#         x0 *= image.width
#         x1 *= image.width
#         y0 *= image.height
#         y1 *= image.height

#     highlighted = image.copy()
#     overlay = Image.new(
#         "RGBA",
#         highlighted.size,
#         (0, 0, 0, 0),
#     )

#     draw = ImageDraw.Draw(overlay)

#     draw.rectangle(
#         (x0, y0, x1, y1),
#         fill=(56, 189, 248, 42),
#         outline=(56, 189, 248, 255),
#         width=max(3, image.width // 300),
#     )

#     return Image.alpha_composite(
#         highlighted.convert("RGBA"),
#         overlay,
#     ).convert("RGB")


# def _evidence_page(evidence: dict[str, Any]) -> int | None:
#     value = (
#         evidence.get("page")
#         or evidence.get("page_number")
#     )

#     try:
#         return int(value)
#     except (TypeError, ValueError):
#         return None


# def _clamp(value: int, minimum: int, maximum: int) -> int:
#     return max(minimum, min(int(value), maximum))
# from __future__ import annotations

# import math
# from pathlib import Path
# from typing import Any, Callable

# import pymupdf
# import streamlit as st
# from PIL import Image, ImageDraw


# def render_document_viewer(
#     document_id: str,
#     page_count: int,
#     initial_page: int = 1,
#     evidence: dict[str, Any] | None = None,
#     render_page_callback: Callable[..., dict[str, Any]] | None = None,
# ) -> int:
#     if page_count < 1:
#         st.info("The source document has no available pages.")
#         return 1

#     page_key = f"viewer_page_{document_id}"
#     input_key = f"page_input_{document_id}"

#     st.session_state.setdefault(
#         page_key,
#         _clamp(initial_page, page_count),
#     )

#     st.session_state.setdefault(
#         input_key,
#         st.session_state[page_key],
#     )

#     st.session_state[input_key] = _clamp(
#         st.session_state[input_key],
#         page_count,
#     )

#     st.session_state[page_key] = (
#         st.session_state[input_key]
#     )

#     st.subheader("Source document")

#     previous, number, next_page = st.columns(
#         [1, 1.2, 1]
#     )

#     previous.button(
#         "← Previous",
#         key=f"previous_{document_id}",
#         disabled=st.session_state[page_key] <= 1,
#         on_click=_change_page,
#         args=(document_id, -1, page_count),
#         use_container_width=True,
#     )

#     number.number_input(
#         "Page",
#         min_value=1,
#         max_value=page_count,
#         step=1,
#         key=input_key,
#         on_change=_sync_page,
#         args=(document_id,),
#     )

#     next_page.button(
#         "Next →",
#         key=f"next_{document_id}",
#         disabled=st.session_state[page_key] >= page_count,
#         on_click=_change_page,
#         args=(document_id, 1, page_count),
#         use_container_width=True,
#     )

#     page = int(st.session_state[page_key])

#     try:
#         image_path = resolve_page_image(
#             document_id,
#             page,
#             render_page_callback,
#         )

#         if image_path is None:
#             st.warning(
#                 f"Page {page} could not be rendered."
#             )
#             return page

#         with Image.open(image_path) as source:
#             image = source.convert("RGB")

#     except Exception as exc:
#         st.error(
#             f"Could not open page {page}: {exc}"
#         )
#         return page

#     if (
#         evidence
#         and evidence.get("document_id", document_id)
#         == document_id
#         and _evidence_page(evidence) == page
#     ):
#         if evidence.get("bbox") or evidence.get("location"):
#             try:
#                 box = _image_box(
#                     image,
#                     evidence,
#                     document_id,
#                     page,
#                 )

#                 image = _draw_highlight(image, box)

#                 st.caption(
#                     "Highlighted region: selected evidence."
#                 )

#             except (ValueError, OSError, RuntimeError) as exc:
#                 st.caption(
#                     f"Page shown without a highlight: {exc}"
#                 )
#         else:
#             st.caption(
#                 "This citation has no region coordinates; "
#                 "the full page is shown."
#             )

#     st.image(
#         image,
#         use_container_width=True,
#         caption=f"Page {page} of {page_count}",
#     )

#     return page


# def go_to_page(
#     document_id: str,
#     page_number: int,
# ) -> None:
#     """
#     Call before render_document_viewer,
#     or from a widget callback.
#     """

#     page = max(1, int(page_number))

#     st.session_state[
#         f"viewer_page_{document_id}"
#     ] = page

#     st.session_state[
#         f"page_input_{document_id}"
#     ] = page


# def _sync_page(document_id: str) -> None:
#     st.session_state[
#         f"viewer_page_{document_id}"
#     ] = int(
#         st.session_state[
#             f"page_input_{document_id}"
#         ]
#     )


# def _change_page(
#     document_id: str,
#     delta: int,
#     page_count: int,
# ) -> None:
#     current = st.session_state.get(
#         f"page_input_{document_id}",
#         1,
#     )

#     go_to_page(
#         document_id,
#         _clamp(
#             int(current) + delta,
#             page_count,
#         ),
#     )


# def resolve_page_image(
#     document_id: str,
#     page_number: int,
#     render_page_callback,
# ) -> Path | None:
#     path = (
#         Path("data/pages")
#         / document_id
#         / f"page_{page_number:03d}.png"
#     )

#     if path.is_file():
#         return path

#     if render_page_callback is None:
#         return None

#     result = render_page_callback(
#         document_id=document_id,
#         page_number=page_number,
#     )

#     if not result or not result.get("success"):
#         raise RuntimeError(
#             (result or {}).get(
#                 "message",
#                 "The renderer did not return "
#                 "a successful page.",
#             )
#         )

#     for key in [
#         "image_path",
#         "path",
#         "output_path",
#     ]:
#         returned = result.get(key)

#         if returned and Path(returned).is_file():
#             return Path(returned)

#     return path if path.is_file() else None


# def highlight_evidence(
#     image: Image.Image,
#     evidence: dict[str, Any],
# ) -> Image.Image:
#     """
#     Compatibility wrapper.

#     The full viewer adds document and page context.
#     """

#     document_id = evidence.get("document_id")
#     page = _evidence_page(evidence)

#     box = _image_box(
#         image,
#         evidence,
#         document_id,
#         page,
#     )

#     return _draw_highlight(image, box)


# def _image_box(
#     image,
#     evidence,
#     document_id,
#     page,
# ):
#     bbox = (
#         evidence.get("bbox")
#         or evidence.get("location")
#     )

#     if (
#         not isinstance(bbox, (list, tuple))
#         or len(bbox) != 4
#     ):
#         raise ValueError(
#             "Region coordinates are missing or invalid."
#         )

#     try:
#         x0, y0, x1, y1 = map(float, bbox)
#     except (TypeError, ValueError):
#         raise ValueError(
#             "Region coordinates are not numeric."
#         )

#     if (
#         not all(
#             math.isfinite(v)
#             for v in [x0, y0, x1, y1]
#         )
#         or x1 <= x0
#         or y1 <= y0
#     ):
#         raise ValueError(
#             "Region coordinates do not define "
#             "a valid rectangle."
#         )

#     space = str(
#         evidence.get("bbox_space") or ""
#     ).lower()

#     kind = str(
#         evidence.get("type")
#         or evidence.get("kind")
#         or ""
#     ).lower()

#     if not space:
#         if all(
#             0 <= v <= 1
#             for v in [x0, y0, x1, y1]
#         ):
#             space = "normalized"

#         elif kind in {
#             "table",
#             "text",
#             "paragraph",
#             "caption",
#             "footnote",
#             "heading",
#             "table_llm",
#             "text_llm",
#         }:
#             space = "pdf_points"

#         else:
#             raise ValueError(
#                 "The backend has not supplied "
#                 "the coordinate system."
#             )

#     if space in {
#         "normalized",
#         "normalized_0_1",
#     }:
#         sx = image.width
#         sy = image.height

#     elif space == "normalized_0_1000":
#         # Expects x0, y0, x1, y1.
#         # Convert raw Gemini y/x boxes first.
#         sx = image.width / 1000
#         sy = image.height / 1000

#     elif space in {
#         "image_pixels",
#         "pixels",
#     }:
#         width = (
#             evidence.get("width_px")
#             or evidence.get("image_width")
#         )

#         height = (
#             evidence.get("height_px")
#             or evidence.get("image_height")
#         )

#         sx = (
#             image.width / float(width)
#             if width
#             else 1
#         )

#         sy = (
#             image.height / float(height)
#             if height
#             else 1
#         )

#     elif space in {
#         "pdf_points",
#         "pdf",
#     }:
#         if evidence.get("page_rotation"):
#             raise ValueError(
#                 "This rotated page needs a "
#                 "rotation-aware PDF-point box."
#             )

#         width = evidence.get("page_width_pt")
#         height = evidence.get("page_height_pt")

#         if not width or not height:
#             width, height = _pdf_size(
#                 document_id,
#                 page,
#             )

#         sx = image.width / float(width)
#         sy = image.height / float(height)

#     else:
#         raise ValueError(
#             f"Unsupported coordinate system: {space}."
#         )

#     if not all(
#         math.isfinite(v) and v > 0
#         for v in [sx, sy]
#     ):
#         raise ValueError(
#             "Source image dimensions are invalid."
#         )

#     x0, x1 = x0 * sx, x1 * sx
#     y0, y1 = y0 * sy, y1 * sy

#     if (
#         x1 <= 0
#         or y1 <= 0
#         or x0 >= image.width
#         or y0 >= image.height
#     ):
#         raise ValueError(
#             "The region lies outside the rendered page."
#         )

#     return (
#         max(0, x0),
#         max(0, y0),
#         min(image.width - 1, x1),
#         min(image.height - 1, y1),
#     )


# def _pdf_size(document_id, page):
#     if not document_id or page is None:
#         raise ValueError(
#             "Document and page metadata are required "
#             "for PDF coordinates."
#         )

#     candidates = [
#         (
#             Path("data/documents")
#             / document_id
#             / "original.pdf"
#         ),
#         (
#             Path("data/documents")
#             / f"{document_id}.pdf"
#         ),
#     ]

#     path = next(
#         (
#             candidate
#             for candidate in candidates
#             if candidate.is_file()
#         ),
#         None,
#     )

#     if path is None:
#         raise ValueError(
#             "The original PDF is unavailable "
#             "for coordinate conversion."
#         )

#     with pymupdf.open(path) as pdf:
#         source = pdf.load_page(page - 1)

#         if source.rotation:
#             raise ValueError(
#                 "PDF-point highlighting requires "
#                 "a rotation-aware box for this rotated page."
#             )

#         return (
#             source.rect.width,
#             source.rect.height,
#         )


# def _draw_highlight(image, box):
#     overlay = Image.new(
#         "RGBA",
#         image.size,
#         (0, 0, 0, 0),
#     )

#     ImageDraw.Draw(overlay).rectangle(
#         box,
#         fill=(56, 189, 248, 45),
#         outline=(14, 165, 233, 255),
#         width=max(3, image.width // 400),
#     )

#     return Image.alpha_composite(
#         image.convert("RGBA"),
#         overlay,
#     ).convert("RGB")


# def _evidence_page(evidence):
#     try:
#         return int(
#             evidence.get("page")
#             or evidence.get("page_number")
#         )
#     except (TypeError, ValueError):
#         return None


# def _clamp(page, count):
#     return max(
#         1,
#         min(int(page), int(count)),
#     )
# # from __future__ import annotations

# # from pathlib import Path
# # from typing import Any, Callable

# # import streamlit as st
# # from PIL import Image, ImageDraw


# # def render_document_viewer(
# #     document_id: str,
# #     page_count: int,
# #     initial_page: int = 1,
# #     evidence: dict[str, Any] | None = None,
# #     render_page_callback: Callable[..., dict[str, Any]] | None = None,
# # ) -> int:
# #     """
# #     Render a compact PDF page viewer.

# #     `render_page_callback` may be app.extraction.image_renderer.render_page.
# #     """

# #     if page_count < 1:
# #         st.info("No rendered document pages are available.")
# #         return 1

# #     state_key = f"viewer_page_{document_id}"

# #     if state_key not in st.session_state:
# #         st.session_state[state_key] = _clamp(
# #             initial_page,
# #             1,
# #             page_count,
# #         )

# #     st.subheader("Source document")

# #     nav1, nav2, nav3, nav4 = st.columns(
# #         [1, 1.4, 1, 1.2]
# #     )

# #     if nav1.button(
# #         "← Previous",
# #         key=f"previous_{document_id}",
# #         use_container_width=True,
# #         disabled=st.session_state[state_key] <= 1,
# #     ):
# #         st.session_state[state_key] -= 1

# #     requested_page = nav2.number_input(
# #         "Page",
# #         min_value=1,
# #         max_value=page_count,
# #         value=st.session_state[state_key],
# #         step=1,
# #         key=f"page_input_{document_id}",
# #     )

# #     st.session_state[state_key] = int(requested_page)

# #     if nav3.button(
# #         "Next →",
# #         key=f"next_{document_id}",
# #         use_container_width=True,
# #         disabled=st.session_state[state_key] >= page_count,
# #     ):
# #         st.session_state[state_key] += 1

# #     zoom = nav4.select_slider(
# #         "Zoom",
# #         options=[75, 100, 125, 150, 175, 200],
# #         value=100,
# #         key=f"zoom_{document_id}",
# #     )

# #     page_number = st.session_state[state_key]

# #     image_path = resolve_page_image(
# #         document_id=document_id,
# #         page_number=page_number,
# #         render_page_callback=render_page_callback,
# #     )

# #     if image_path is None:
# #         st.warning(
# #             f"Page {page_number} has not been rendered yet."
# #         )
# #         return page_number

# #     try:
# #         image = Image.open(image_path).convert("RGB")
# #     except Exception as exc:
# #         st.error(f"Could not open the rendered page: {exc}")
# #         return page_number

# #     if evidence and _evidence_page(evidence) == page_number:
# #         image = highlight_evidence(image, evidence)

# #     base_width = min(image.width, 950)
# #     display_width = int(base_width * (zoom / 100))

# #     st.image(
# #         image,
# #         width=display_width,
# #         caption=f"Page {page_number} of {page_count}",
# #     )

# #     return page_number


# # def go_to_page(
# #     document_id: str,
# #     page_number: int,
# # ) -> None:
# #     """Set the viewer page from an evidence-card action."""

# #     st.session_state[
# #         f"viewer_page_{document_id}"
# #     ] = max(1, int(page_number))


# # def resolve_page_image(
# #     document_id: str,
# #     page_number: int,
# #     render_page_callback: Callable[..., dict[str, Any]] | None,
# # ) -> Path | None:
# #     expected_path = (
# #         Path("data")
# #         / "pages"
# #         / document_id
# #         / f"page_{page_number:03d}.png"
# #     )

# #     if expected_path.exists():
# #         return expected_path

# #     if render_page_callback is None:
# #         return None

# #     result = render_page_callback(
# #         document_id=document_id,
# #         page_number=page_number,
# #     )

# #     if not result or not result.get("success"):
# #         return None

# #     returned_path = (
# #         result.get("path")
# #         or result.get("image_path")
# #         or result.get("output_path")
# #     )

# #     if returned_path and Path(returned_path).exists():
# #         return Path(returned_path)

# #     return expected_path if expected_path.exists() else None


# # def highlight_evidence(
# #     image: Image.Image,
# #     evidence: dict[str, Any],
# # ) -> Image.Image:
# #     """
# #     Draw an evidence highlight.

# #     Supported bbox formats:
# #     - Pixel coordinates: [x0, y0, x1, y1]
# #     - Normalized coordinates between 0 and 1
# #     """

# #     bbox = evidence.get("bbox")

# #     if not bbox or len(bbox) != 4:
# #         return image

# #     try:
# #         x0, y0, x1, y1 = map(float, bbox)
# #     except (TypeError, ValueError):
# #         return image

# #     if max(x0, y0, x1, y1) <= 1:
# #         x0 *= image.width
# #         x1 *= image.width
# #         y0 *= image.height
# #         y1 *= image.height

# #     highlighted = image.copy()
# #     overlay = Image.new(
# #         "RGBA",
# #         highlighted.size,
# #         (0, 0, 0, 0),
# #     )

# #     draw = ImageDraw.Draw(overlay)

# #     draw.rectangle(
# #         (x0, y0, x1, y1),
# #         fill=(56, 189, 248, 42),
# #         outline=(56, 189, 248, 255),
# #         width=max(3, image.width // 300),
# #     )

# #     return Image.alpha_composite(
# #         highlighted.convert("RGBA"),
# #         overlay,
# #     ).convert("RGB")


# # def _evidence_page(evidence: dict[str, Any]) -> int | None:
# #     value = (
# #         evidence.get("page")
# #         or evidence.get("page_number")
# #     )

# #     try:
# #         return int(value)
# #     except (TypeError, ValueError):
# #         return None


# # def _clamp(value: int, minimum: int, maximum: int) -> int:
# #     return max(minimum, min(int(value), maximum))
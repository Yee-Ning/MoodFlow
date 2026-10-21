import pytest

from app.rag.chunk.routing import ChunkRouter
from app.rag.parse_normalize.models import BlockType
from app.rag.parse_normalize.pdf.adapters import DoclingAdapter, DoclingRuntimeOutput
from tests.clean_helpers import normalized_document
from tests.test_pdf_optional_adapters import parse_input_for


@pytest.mark.parametrize("origin", ["TOPLEFT", "BOTTOMLEFT"])
def test_docling_interleaves_tables_and_pictures_before_later_headings(origin):
    def prov(y, page=1):
        top, bottom = (y, y + 10) if origin == "TOPLEFT" else (800 - y, 790 - y)
        return [{"page_no": page, "bbox": {"l": 10, "r": 100, "t": top, "b": bottom, "coord_origin": origin}}]

    structured = {
        "texts": [
            {"label": "section_header", "text": "First", "prov": prov(10)},
            {"label": "section_header", "text": "Second", "prov": prov(100)},
            {"label": "text", "text": "Later text", "prov": prov(120)},
            {"label": "section_header", "text": "Third", "prov": prov(10, 2)},
        ],
        "tables": [{"label": "table", "data": {"grid": [["Year"], ["2001"]]}, "prov": prov(40)}],
        "pictures": [{"caption": "First image", "prov": prov(60)}],
    }
    result = DoclingAdapter()._map_result(parse_input_for("scanned.pdf", "ordered"), DoclingRuntimeOutput(markdown="", structured=structured), True)
    assert [block.text for block in result.blocks] == ["First", "| Year |\n| --- |\n| 2001 |", "First image", "Second", "Later text", "Third"]
    assert [block.order_index for block in result.blocks] == list(range(6))
    assert result.blocks[1].parent_block_id == result.blocks[0].block_id
    assert result.blocks[2].parent_block_id == result.blocks[0].block_id
    assert result.blocks[4].parent_block_id == result.blocks[3].block_id
    assert result.tables[0].metadata["source_block_id"] == result.blocks[1].block_id
    document = normalized_document("ordered", "placeholder", title="Ordered").model_copy(update={"blocks": result.blocks, "tables": result.tables})
    routes = ChunkRouter().route(document, {b.block_id: b.text for b in result.blocks})
    assert routes.tables[0].section_path == ("First",)


def test_docling_table_without_position_does_not_inherit_last_heading():
    output = DoclingRuntimeOutput(markdown="", structured={
        "texts": [{"label": "section_header", "text": "Last", "prov": [{"page_no": 1, "bbox": [0, 100, 50, 90]}]}],
        "tables": [{"data": {"grid": [["Year"], ["2001"]]}}],
    })
    result = DoclingAdapter()._map_result(parse_input_for("scanned.pdf", "unplaced"), output, True)
    table = next(b for b in result.blocks if b.block_type is BlockType.TABLE)
    assert table.parent_block_id is None
    assert table.metadata["heading_assignment"] == "position_unavailable"
    doc = normalized_document("unplaced", "text", title="Unplaced").model_copy(update={"blocks": result.blocks, "tables": result.tables})
    assert ChunkRouter().route(doc, {b.block_id: b.text for b in result.blocks}).tables[0].section_path == ()

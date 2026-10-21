import pytest

from app.rag.chunk import ChunkEngine, TableHeaderState
from app.rag.chunk.models import TableRowFragment
from app.rag.chunk.table import TableChunker
from app.rag.parse_normalize.models import BlockType, DocumentBlock, DocumentTable
from tests.test_rag_chunk import _document


def table_document(rows, *, has_header=True):
    block = DocumentBlock(block_id="table-block", block_type=BlockType.TABLE,
                          text="", order_index=0,
                          metadata={"rows": rows, "has_header": has_header})
    table = DocumentTable(table_id="table-1", rows=rows, markdown="",
                          metadata={"source_block_id": block.block_id, "has_header": has_header})
    return _document([block], tables=[table])


def test_explicit_album_template_preserves_table_and_adds_auditable_context():
    rows = [["专辑", "艺人", "发行年份"], ["范特西", "周杰伦", "2001"]]
    cleaned, normalized = table_document(rows)
    result = ChunkEngine().chunk(cleaned, normalized)
    assert result.validation.valid, result.validation.issues
    child = result.children[0]
    assert "《范特西》是周杰伦于2001年发行的专辑。" in child.retrieval_content
    assert "专辑为范特西；艺人为周杰伦；发行年份为2001" in child.retrieval_content
    assert child.content in child.retrieval_content
    assert "表格记录" not in child.content
    assert result.parents[0].content == child.content
    assert child.table_data.rows == rows[1:]
    assert child.table_data.headers == rows[0]
    assert child.metadata["table_paraphrase_version"] == "v1"


@pytest.mark.parametrize("headers,row,state,expected", [
    (["", "年份"], ["范特西", ""], TableHeaderState.EXPLICIT, "第1列为范特西；年份为未提供值"),
    (["专辑", "年份"], ["范特西"], TableHeaderState.EXPLICIT, "专辑为范特西；年份为未提供值"),
    ([], ["范特西", "2001"], TableHeaderState.NONE, "第1列为范特西；第2列为2001"),
    (["专辑"], ["范特西", "2001"], TableHeaderState.EXPLICIT, "专辑为范特西；第2列为2001"),
    (["专辑", "艺人", "发行年份"], ["范特西", "周杰伦", "2001"], TableHeaderState.INFERRED, "按推定表头"),
])
def test_fallbacks_keep_values_without_inventing_column_meaning(headers, row, state, expected):
    description = TableChunker._paraphrase(headers, [row], state)
    assert expected in description
    assert "发行的专辑" not in description


def test_missing_release_year_or_ambiguous_year_never_emits_release_fact():
    for headers, row in [(["专辑", "艺人", "年份"], ["范特西", "周杰伦", "2001"]),
                         (["专辑", "艺人", "发行年份"], ["范特西", "周杰伦", ""]),
                         (["专辑", "艺人", "发行年份"], ["范特西", "周杰伦", "约2001"])]:
        assert "发行的专辑" not in TableChunker._paraphrase(headers, [row], TableHeaderState.EXPLICIT)


@pytest.mark.parametrize("headers,row", [
    (["专辑", "艺人", "发行年份", "发行年份"], ["范特西", "周杰伦", "2001", "2002"]),
    (["专辑", "album", "艺人", "发行年份"], ["范特西", "Other", "周杰伦", "2001"]),
])
def test_ambiguous_duplicate_or_alias_columns_fall_back_without_selecting_a_fact(headers, row):
    description = TableChunker._paraphrase(headers, [row], TableHeaderState.EXPLICIT)
    assert "发行的专辑" not in description
    assert all(value in description for value in row)


def test_headerless_and_compact_header_paths_retain_original_payload():
    for rows, has_header in [([["范特西", "2001"]], False),
                              ([["超长表头" * 100, "年份"], ["范特西", "2001"]], True)]:
        cleaned, normalized = table_document(rows, has_header=has_header)
        result = ChunkEngine().chunk(cleaned, normalized)
        assert result.validation.valid, result.validation.issues
        child = result.children[0]
        assert "第1列为范特西；第2列为2001" in child.retrieval_content
        assert child.table_data.rows == (rows[1:] if has_header else rows)
        if has_header:
            assert child.table_data.headers == rows[0]
            assert rows[0][0] not in child.retrieval_content


def test_fragments_describe_only_their_own_span_and_empty_rows_do_not_invent_facts():
    assert TableChunker._paraphrase([], [], TableHeaderState.NONE) == ""
    assert TableChunker._paraphrase([], [[]], TableHeaderState.NONE) == ""
    fragment = TableRowFragment(source_row_index=2, fragment_index=1, fragment_count=3,
                                content="本段值", owns_source_row=False)
    assert TableChunker._fragment_description(fragment) == "表格第3行的文本片段（第2/3段）：本段值"
    cleaned, normalized = table_document([["列"], ["长行文本" * 800]])
    result = ChunkEngine().chunk(cleaned, normalized)
    assert result.validation.valid, result.validation.issues
    assert len(result.children) > 1
    for child in result.children:
        piece = child.table_data.row_fragments[0]
        assert TableChunker._fragment_description(piece) in child.retrieval_content
        assert child.content in child.retrieval_content
        assert "长行文本" * 800 not in child.retrieval_content

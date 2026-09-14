"""Translate trusted query constraints into a whitelist-only filter tree."""

from app.rag.query_understanding.models import (
    FilterCondition,
    FilterGroup,
    FilterOperator,
    MetadataField,
    MetadataFilter,
    QueryAnalysis,
    QueryType,
)


def _match_any(field: MetadataField, values: list[str]) -> FilterCondition:
    return FilterCondition(
        field=field,
        operator=FilterOperator.MATCH_ANY,
        value=values,
    )


def _temporal_filter(start_year: int, end_year: int) -> FilterGroup:
    """Match point years or document intervals that overlap the requested range."""

    return FilterGroup(
        operator="any",
        children=[
            FilterCondition(
                field=MetadataField.YEAR,
                operator=FilterOperator.RANGE,
                gte=start_year,
                lte=end_year,
            ),
            FilterGroup(
                operator="all",
                children=[
                    FilterCondition(
                        field=MetadataField.START_YEAR,
                        operator=FilterOperator.RANGE,
                        lte=end_year,
                    ),
                    FilterCondition(
                        field=MetadataField.END_YEAR,
                        operator=FilterOperator.RANGE,
                        gte=start_year,
                    ),
                ],
            ),
        ],
    )


def build_metadata_filter(analysis: QueryAnalysis) -> MetadataFilter:
    constraints = analysis.constraints
    children: list[FilterCondition | FilterGroup] = []
    if constraints.entity_ids:
        if QueryType.RELATIONSHIP in analysis.query_types and len(
            constraints.entity_ids
        ) > 1:
            children.extend(
                FilterCondition(
                    field=MetadataField.ENTITY_IDS,
                    operator=FilterOperator.MATCH_VALUE,
                    value=entity_id,
                )
                for entity_id in constraints.entity_ids
            )
        else:
            children.append(
                _match_any(MetadataField.ENTITY_IDS, constraints.entity_ids)
            )
    if constraints.entity_types and not constraints.entity_ids:
        children.append(
            _match_any(
                MetadataField.ENTITY_TYPES,
                [item.value for item in constraints.entity_types],
            )
        )
    if constraints.start_year is not None and constraints.end_year is not None:
        children.append(_temporal_filter(constraints.start_year, constraints.end_year))
    if constraints.languages:
        children.append(_match_any(MetadataField.LANGUAGE, constraints.languages))
    if constraints.content_types:
        children.append(
            _match_any(
                MetadataField.CONTENT_TYPE,
                [item.value for item in constraints.content_types],
            )
        )
    return MetadataFilter(
        root=FilterGroup(operator="all", children=children) if children else None
    )

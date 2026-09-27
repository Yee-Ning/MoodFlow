import pytest

from app.rag.query_understanding.service import QueryUnderstandingService
from app.rag.query_understanding.models import IndexProfile


def profile(version):
    return IndexProfile(collection_name="independent", source_release_id="release",
                        index_schema_version="v2", projection_version="v4", registry_version=version)


def test_default_resolvers_use_the_bound_index_registry():
    service = QueryUnderstandingService(index_profile=profile("v5"))
    assert service.entity_resolver.registry.version == "v5"
    assert service.context_resolver.registry.version == "v5"
    assert service.entity_resolver.registry.get("artist:nirvana") is not None
    assert QueryUnderstandingService().entity_resolver.registry.version == "v3"


def test_unavailable_bound_registry_fails_instead_of_using_default():
    with pytest.raises(ValueError, match="entity_registry_invalid"):
        QueryUnderstandingService(index_profile=profile("v999999"))

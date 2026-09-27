"""Versioned production data additions preserve the extraction contract."""

from pathlib import Path

from app.rag.knowledge.config import load_knowledge_config
from app.rag.knowledge.entity_linking.registry import EntityRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_native_registry_preserves_existing_entities_and_resolves_new_names():
    path = ROOT / "app/rag/knowledge/entity_linking/registry"
    previous = EntityRegistry.load(path / "v3.yaml")
    current = EntityRegistry.load(path / "v4.yaml")
    for alias in previous.detectable_aliases():
        for entity in previous.candidates(alias):
            assert current.get(entity.entity_id) == entity
    assert current.resolve("Nirvana").entity.entity_id == "artist:nirvana"
    assert current.resolve("The Pixies").entity.entity_id == "artist:pixies"
    assert previous.resolve("Nirvana").entity is None
    assert current.resolve("Unknown new artist").entity is None


def test_new_knowledge_config_only_changes_registry_data_version():
    path = ROOT / "app/rag/knowledge/config"
    previous = load_knowledge_config(path / "v13.yaml").model_dump()
    current = load_knowledge_config(path / "v14.yaml").model_dump()
    assert current.pop("registry_version") == "v4"
    assert previous.pop("registry_version") == "v3"
    assert current == previous

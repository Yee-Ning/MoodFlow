"""Versioned production data additions preserve the extraction contract."""

from pathlib import Path

import pytest

from app.rag.knowledge.config import load_knowledge_config
from app.rag.knowledge.entity_linking.models import EntityType
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


@pytest.mark.parametrize("config,registry", [("v14", "v4"), ("v15", "v5")])
def test_new_knowledge_config_only_changes_registry_data_version(config, registry):
    path = ROOT / "app/rag/knowledge/config"
    previous = load_knowledge_config(path / "v13.yaml").model_dump()
    current = load_knowledge_config(path / (config + ".yaml")).model_dump()
    assert current.pop("registry_version") == registry
    assert previous.pop("registry_version") == "v3"
    assert current == previous


def test_original_named_subjects_preserve_prior_entities_and_surname_ambiguity():
    path = ROOT / "app/rag/knowledge/entity_linking/registry"
    previous = EntityRegistry.load(path / "v4.yaml")
    current = EntityRegistry.load(path / "v5.yaml")
    for alias in previous.detectable_aliases():
        for entity in previous.candidates(alias):
            assert current.get(entity.entity_id) == entity
    for name, entity_id in [("Kurt Cobain", "artist:kurt_cobain"),
                            ("NWA", "artist:nwa"), ("REM", "artist:rem"),
                            ("Metallica", "artist:metallica")]:
        assert current.resolve(name).entity.entity_id == entity_id
    assert current.resolve("Cobain").entity is None
    assert current.resolve("Cobain", context_type=EntityType.ARTIST).entity.entity_id == "artist:kurt_cobain"

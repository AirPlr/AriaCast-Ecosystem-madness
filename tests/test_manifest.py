"""Things hassfest and HACS check, caught before CI does."""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "ariacast"


def test_manifest_keys_are_in_hassfest_order() -> None:
    keys = list(json.loads((INTEGRATION / "manifest.json").read_text()))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_manifest_has_what_a_custom_integration_needs() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    for key in ("version", "documentation", "issue_tracker", "codeowners", "config_flow"):
        assert manifest[key]
    assert manifest["zeroconf"] == ["_audiocast._tcp.local."]
    # Home Assistant already ships everything we use; extra pins only cause install failures.
    assert manifest["requirements"] == []


def test_translations_cover_every_string_in_strings_json() -> None:
    def leaves(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from leaves(value, (*path, key))
        else:
            yield path

    strings = set(leaves(json.loads((INTEGRATION / "strings.json").read_text())))
    for language in (INTEGRATION / "translations").glob("*.json"):
        translated = set(leaves(json.loads(language.read_text())))
        assert strings == translated, f"{language.name} is out of step with strings.json"


def test_hacs_json_matches_the_layout() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"]
    assert (INTEGRATION / "manifest.json").exists()

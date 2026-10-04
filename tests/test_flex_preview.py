import json

from linebot.v3.messaging import FlexMessage

from scripts.build_flex_preview import OUTPUT, build_samples


async def test_committed_preview_matches_production_builders():
    content = OUTPUT.read_text(encoding="utf-8")
    nodes = json.loads(content.split("  const nodes = ", 1)[1].split(";\n", 1)[0])

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if value.keys() == {"$ref"}:
                return expand(nodes[value["$ref"]])
            return {key: expand(item) for key, item in value.items()}
        return value

    payload = json.loads(content.split("  return expand(", 1)[1].split(");", 1)[0])
    samples = await build_samples()
    assert expand(payload) == samples
    for sample in samples:
        assert sample["messages"], sample["id"]
        for message in sample["messages"]:
            assert FlexMessage.from_dict(message).to_dict() == message
            container = message["contents"]
            cards = container["contents"] if container["type"] == "carousel" else [container]
            assert 1 <= len(cards) <= 12
            assert len(json.dumps(container, ensure_ascii=False).encode("utf-8")) <= 50_000
            for card in cards:
                assert len(json.dumps(card, ensure_ascii=False).encode("utf-8")) <= 30_000

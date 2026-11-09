"""Явная offline подготовка LanceDB индексов встроенных справочников."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from atlas.reference_store import ReferenceStore
from atlas.reference_vectors import ReferenceVectors
from atlas.semantic_embeddings import LocalEmbedding


def main():
    """Индексирует только локальные корпуса/веса; существующее поколение можно заменить явно."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / "resources" / "language-references")
    parser.add_argument("--destination", type=Path, default=ROOT / "resources" / "reference-search")
    parser.add_argument("--languages", nargs="+")
    parser.add_argument("--packs", nargs="+", help="Build only these explicit pack IDs")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    embedder = LocalEmbedding(args.model_cache)
    vectors = ReferenceVectors(args.destination, embedder)
    store = ReferenceStore(Path("/tmp/unused-atlas-reference-state"), args.corpus)
    if not embedder.available():
        parser.error("Prepare the local model first; no model will be downloaded")
    packs = store.catalog()
    if args.packs and set(args.packs) - {pack["id"] for pack in packs}:
        parser.error("Unknown pack ID")
    packs.sort(key=lambda pack: pack["documentCount"])
    for pack in packs:
        if args.packs and pack["id"] not in args.packs:
            continue
        if args.languages and pack["language"] not in args.languages:
            continue
        if vectors.status(pack["id"]) and not args.force:
            continue
        print(json.dumps({"language": pack["language"], "packId": pack["id"], "phase": "index"}), flush=True)
        result = vectors.build(pack, lambda **value: print(json.dumps({"language": pack["language"], **value}), flush=True))
        print(json.dumps({"language": pack["language"], "phase": "ready", **result}), flush=True)


if __name__ == "__main__":
    main()

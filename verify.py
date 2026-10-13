"""Independently read and validate generated StarDict files using only stdlib."""

import argparse
import gzip
import hashlib
import json
import struct
from html.parser import HTMLParser
from pathlib import Path

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class DefinitionParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []
        self.links = []
        self.images = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        if tag == "a" and attrs.get("href", "").startswith("bword://"):
            self.links.append(attrs["href"][8:])
        if tag == "img":
            self.images.append(attrs.get("src", ""))

    def handle_endtag(self, tag):
        if tag in VOID or not self.stack or self.stack.pop() != tag:
            raise ValueError(f"Unbalanced HTML at </{tag}>")

    def handle_data(self, data):
        self.text.append(data)


def records(path, fields):
    data = path.read_bytes()
    position = 0
    while position < len(data):
        end = data.index(b"\0", position)
        word = data[position:end].decode("utf-8")
        if not word:
            raise ValueError(f"Empty key in {path}")
        numbers = struct.unpack_from(">" + "I" * fields, data, end + 1)
        position = end + 1 + fields * 4
        yield word, numbers
    if position != len(data):
        raise ValueError(f"Truncated records: {path}")


def text_digest_update(digest, text):
    data = text.encode("utf-8")
    digest.update(struct.pack(">Q", len(data)))
    digest.update(data)


def verify(folder):
    folder = Path(folder)
    ifos = list(folder.glob("*.ifo"))
    if len(ifos) != 1:
        raise ValueError("Expected one .ifo file")
    base = ifos[0].with_suffix("")
    info = dict(line.split("=", 1) for line in ifos[0].read_text().splitlines() if "=" in line)
    index = list(records(base.with_suffix(".idx"), 2))
    synonyms = list(records(base.with_suffix(".syn"), 1))
    if len(index) != int(info["wordcount"]) or len(synonyms) != int(info["synwordcount"]):
        raise ValueError("Entry counts don't match .ifo")
    if base.with_suffix(".idx").stat().st_size != int(info["idxfilesize"]):
        raise ValueError("Index size doesn't match .ifo")
    if info.get("sametypesequence") != "h":
        raise ValueError("Expected HTML dictionary")
    keys = {word for word, _ in index + synonyms}
    for table in (index, synonyms):
        previous = None
        for word, numbers in table:
            raw = word.encode("utf-8")
            current = (raw.lower(), raw)
            if previous is not None and current < previous:
                raise ValueError("Unsorted StarDict keys")
            previous = current
            if table is synonyms and numbers[0] >= len(index):
                raise ValueError(f"Out-of-range synonym target: {word}")
    compressed = base.with_suffix(".dict.dz")
    opener, path = (gzip.open, compressed) if compressed.exists() else (open, base.with_suffix(".dict"))
    digest = hashlib.sha256()
    links = images = 0
    resource_root = (folder / "res").resolve()
    with opener(path, "rb") as dictionary:
        for word, (offset, size) in index:
            if offset != dictionary.tell():
                raise ValueError(f"Invalid dictionary offset: {word}")
            data = dictionary.read(size)
            if len(data) != size:
                raise ValueError(f"Truncated definition: {word}")
            parser = DefinitionParser()
            parser.feed(data.decode("utf-8"))
            parser.close()
            if parser.stack:
                raise ValueError(f"Unclosed HTML: {word}")
            for target in parser.links:
                if target not in keys:
                    raise ValueError(f"Missing lookup target: {word} -> {target}")
            for image in parser.images:
                resource = (resource_root / image).resolve()
                if not resource.is_relative_to(resource_root) or not resource.is_file():
                    raise ValueError(f"Missing or unsafe image: {word} -> {image}")
            links += len(parser.links)
            images += len(parser.images)
            text_digest_update(digest, "".join(parser.text))
        if dictionary.read(1):
            raise ValueError("Unindexed dictionary bytes")
    if not base.with_suffix(".css").is_file():
        raise ValueError("Missing stylesheet")
    result = {"verified_entries": len(index), "verified_synonyms": len(synonyms),
              "verified_links": links, "verified_images": images, "text_sha256": digest.hexdigest()}
    report_path = folder / "validation.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if report.get("source_text_sha256") != result["text_sha256"]:
            raise ValueError("Definition text differs from source fingerprint")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    print(json.dumps(verify(parser.parse_args().folder), indent=2))

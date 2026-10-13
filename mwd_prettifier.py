"""Convert MW12's embedded publisher XHTML to StarDict; never rewrite wording."""

import argparse
import hashlib
import html
import json
import posixpath
import re
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

from verify import text_digest_update, verify

IDX = "{http://www.mobipocket.com/idx}"
XHTML = "{http://www.w3.org/1999/xhtml}"
BASENAME = "MW_Collegiate_12_Pretty"
SENSE = re.compile(r"(?:[1-9]\d*(?: [a-z])?(?: \(\d+\))?|[a-z](?: \(\d+\))?|\(\d+\))\Z")
BLOCKS = {"div", "p", "table", "ul", "ol", "h1", "h2", "h3", "h4", "br"}


@dataclass
class Entry:
    word: str
    aliases: set[str]
    source: str
    markup: bytes
    reference: bool = False


def local(tag):
    return tag.rsplit("}", 1)[-1]


def key(word):
    raw = word.encode("utf-8")
    if not raw or b"\0" in raw:
        raise ValueError(f"Invalid lookup key: {word!r}")
    # StarDict compares ASCII-case-insensitively, then by original UTF-8 bytes.
    return raw.lower(), raw


def remove_preserving_tail(parent, child):
    children = list(parent)
    i = children.index(child)
    if i:
        children[i - 1].tail = (children[i - 1].tail or "") + (child.tail or "")
    else:
        parent.text = (parent.text or "") + (child.tail or "")
    parent.remove(child)


def strip_index_tags(root):
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.startswith(IDX):
                remove_preserving_tail(parent, child)


def visible_text(root):
    return "".join(root.itertext())


def load_source(archive):
    entries = []
    targets = {}
    priorities = {}
    metadata = {"duplicate_source_ids": 0}
    with ZipFile(archive) as z:
        opf = ET.fromstring(z.read("OEBPS/content.opf"))
        for el in opf.iter():
            if local(el.tag) in {"title", "creator", "rights"}:
                metadata[local(el.tag)] = el.text or ""
        if "Twelfth Edition" not in metadata.get("title", ""):
            raise ValueError("Expected the MW Collegiate Twelfth Edition publisher source")
        for name in sorted(z.namelist()):
            if not name.startswith("OEBPS/") or not name.endswith(".xhtml"):
                continue
            root = ET.fromstring(z.read(name))
            records = list(root.iter(IDX + "entry"))
            reference = not records
            if reference:
                body = root.find(XHTML + "body")
                if body is None:
                    raise ValueError(f"No body: {name}")
                records = [body]
            first_word = None
            for record in records:
                if reference:
                    title = root.findtext(XHTML + "head/" + XHTML + "title")
                    word = "MW — " + (title or Path(name).stem)
                    aliases = set()
                else:
                    orths = [e.get("value", "").strip() for e in record.iter(IDX + "orth")]
                    if not orths or not orths[0]:
                        raise ValueError(f"Entry without headword: {name}")
                    word = orths[0]
                    aliases = set(orths)
                    aliases.update(e.get("value", "").strip() for e in record.iter(IDX + "iform"))
                    aliases.discard("")
                    aliases.discard(word)
                key(word)
                for alias in aliases:
                    key(alias)
                first_word = first_word or word
                for el in record.iter():
                    if el.get("id"):
                        target = (name, el.get("id"))
                        priority = 2 if el.get("class") == "entry" else 1
                        if target in targets and targets[target] != word:
                            metadata["duplicate_source_ids"] += 1
                        # Prefer a canonical entry ID over a variant's duplicate anchor.
                        # Otherwise retain the first target, like an HTML fragment lookup.
                        if priority > priorities.get(target, 0):
                            targets[target] = word
                            priorities[target] = priority
                strip_index_tags(record)
                record.tag = "div"
                record.set("class", "mw-reference" if reference else "mw-entry")
                record.tail = None
                entries.append(Entry(word, aliases, name, ET.tostring(record), reference))
            targets[name, ""] = first_word
    folded = {}
    for (path, fragment), word in targets.items():
        folded.setdefault((path, fragment.casefold()), set()).add(word)
    # MW's source contains lowercase references to mixed-case IDs.
    metadata["case_repaired_ids"] = 0
    for target, words in folded.items():
        if target not in targets and len(words) == 1:
            targets[target] = next(iter(words))
            metadata["case_repaired_ids"] += 1
    # Verified MW12 source erratum: zalcitabine links to #ddc, but means the
    # drug ddC, not the separate DDC (Dewey Decimal Classification) entry.
    if ("OEBPS/d1.xhtml", "ddC") in targets:
        targets["OEBPS/d1.xhtml", "ddc"] = targets["OEBPS/d1.xhtml", "ddC"]
    if not entries:
        raise ValueError("No source entries")
    return entries, targets, metadata


def mixed_content(parent):
    parts = [parent.text or ""]
    parent.text = None
    for child in list(parent):
        tail = child.tail or ""
        child.tail = None
        parent.remove(child)
        parts.extend([child, tail])
    return parts


def append_part(parent, part):
    if isinstance(part, str):
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + part
        else:
            parent.text = (parent.text or "") + part
    else:
        parent.append(part)


def wrap_delimited(parent, opening, closing, css_class, stats):
    """Wrap only balanced same-parent text ranges; retain ambiguous text verbatim."""
    parts = mixed_content(parent)
    pending = None
    for part in parts:
        if not isinstance(part, str):
            if pending is not None and local(part.tag) in BLOCKS:
                for item in pending:
                    append_part(parent, item)
                pending = None
            if pending is None:
                append_part(parent, part)
            else:
                pending.append(part)
            continue
        for token in re.split(f"([{re.escape(opening + closing)}])", part):
            if token == opening:
                if pending is not None:
                    for item in pending:
                        append_part(parent, item)
                pending = [token]
            elif token == closing and pending is not None:
                wrapper = ET.Element("span", {"class": css_class})
                for item in pending + [token]:
                    append_part(wrapper, item)
                parent.append(wrapper)
                stats[css_class] += 1
                pending = None
            elif pending is not None:
                pending.append(token)
            else:
                append_part(parent, token)
    if pending is not None:
        for item in pending:
            append_part(parent, item)


def structure_senses(parent, stats):
    parts = mixed_content(parent)
    group = None
    for part in parts:
        if not isinstance(part, str):
            label = visible_text(part).strip()
            if part.tag == "b" and SENSE.fullmatch(label):
                level = "sense" if label[0].isdigit() else "subsense"
                group = ET.SubElement(parent, "div", {"class": level})
                part.set("class", "sense-number")
                stats[level] += 1
            elif part.tag in BLOCKS or part.get("class") == "etymology":
                group = None
        append_part(group if group is not None else parent, part)


def archive_path(source, href):
    url = urlsplit(href)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(url.path))) if url.path else source
    if not path.startswith("OEBPS/"):
        raise ValueError(f"Resource outside OEBPS: {href}")
    return path, unquote(url.fragment)


def prettify(entry, targets, resources, stats):
    root = ET.fromstring(entry.markup)
    original = visible_text(root)
    for el in root.iter():
        el.tag = local(el.tag)
        # Retain meaningful publisher markup; discard Kindle build IDs and layout overrides.
        el.attrib = {k: v for k, v in el.attrib.items()
                     if k in {"id", "class", "href", "src", "alt", "title", "colspan", "rowspan"}}
        if el.tag == "a" and el.get("href"):
            href = el.get("href")
            if urlsplit(href).scheme in {"https", "http", "mailto"}:
                continue
            target = archive_path(entry.source, href)
            word = targets.get(target)
            if word is None:
                raise ValueError(f"Unresolved link in {entry.word!r}: {entry.source} -> {href}")
            # KOReader passes bword targets directly to lookup without URL-decoding.
            # ElementTree handles HTML attribute escaping (including quotes and &).
            el.set("href", "bword://" + word)
            stats["internal_links"] += 1
        if el.tag == "img":
            path, _ = archive_path(entry.source, el.get("src", ""))
            resources.add(path)
            el.set("src", path.removeprefix("OEBPS/"))
            stats["image_references"] += 1
    if not entry.reference:
        # Work on the original node list so newly wrapped ranges aren't wrapped twice.
        for el in reversed(list(root.iter())):
            if el.tag in {"div", "p"}:
                wrap_delimited(el, "<", ">", "example", stats)
                wrap_delimited(el, "{", "}", "etymology", stats)
        for el in list(root.iter()):
            if el.tag == "div" and set(el.get("class", "").split()) & {"entry", "entrysect"}:
                structure_senses(el, stats)
                # Source dates are final parenthetical text, not arbitrary parentheses.
                if len(el) and re.fullmatch(r"\s*\((?:before |ca\. )?\d[^()]*\)\s*", el[-1].tail or ""):
                    date = ET.Element("span", {"class": "first-use"})
                    date.text, el[-1].tail = el[-1].tail, None
                    el.append(date)
                    stats["first-use"] += 1
    if visible_text(root) != original:
        raise ValueError(f"Formatting changed text: {entry.word}")
    stats["text_preserved"] += 1
    return ET.tostring(root, encoding="unicode", method="html")


def write_stardict(entries, definitions, output, metadata):
    order = sorted(range(len(entries)), key=lambda i: key(entries[i].word))
    synonyms = set()
    base = output / BASENAME
    with base.with_suffix(".dict").open("wb") as dictionary, base.with_suffix(".idx").open("wb") as index:
        for number, i in enumerate(order):
            entry = entries[i]
            data = definitions[i].encode("utf-8")
            offset = dictionary.tell()
            if offset + len(data) >= 2**32:
                raise ValueError("Dictionary exceeds StarDict 32-bit offset capacity")
            index.write(entry.word.encode("utf-8") + b"\0" + struct.pack(">II", offset, len(data)))
            dictionary.write(data)
            synonyms.update((alias, number) for alias in entry.aliases)
    with base.with_suffix(".syn").open("wb") as syn:
        for alias, number in sorted(synonyms, key=lambda item: (key(item[0]), item[1])):
            syn.write(alias.encode("utf-8") + b"\0" + struct.pack(">I", number))
    info = {
        "version": "2.4.2", "wordcount": len(order), "synwordcount": len(synonyms),
        "idxfilesize": base.with_suffix(".idx").stat().st_size,
        "bookname": metadata["title"] + " — Prettified", "sametypesequence": "h",
        "author": metadata.get("creator", ""),
        "description": metadata.get("rights", "") + "; privately converted from publisher XHTML. MW reference pages use the prefix MW —.",
    }
    base.with_suffix(".ifo").write_text("StarDict's dict ifo file\n" + "".join(
        f"{k}={str(v).replace(chr(10), ' ')}\n" for k, v in info.items()), encoding="utf-8")
    return {"entries": len(order), "synonyms": len(synonyms)}


def write_preview(entries, definitions, output, css):
    samples = {"abacus", "dictionary", "color", "run", "hello", "abalone"}
    body = []
    for entry, definition in zip(entries, definitions):
        if entry.word in samples:
            body.append("<section><h2>" + html.escape(entry.word) + "</h2>" + definition + "</section>")
    preview = '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>MW12 review</title><style>'
    preview += css + "\nbody { max-width: 48em; margin: 2em auto; padding: 1em; } section { border-bottom: 1px solid #aaa; padding: 1em 0; }"
    preview += '</style></head><body><h1>MW12 formatting preview</h1><p>Browser preview only; check the dictionary in KOReader too.</p>' + "".join(body) + "</body></html>"
    # Resources in the StarDict res directory are resolved implicitly by readers, not browsers.
    preview = re.sub(r'src="([^"]+)"', r'src="res/\1"', preview)
    (output / "preview.html").write_text(preview, encoding="utf-8")


def convert(archive, destination, compress=False):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError(f"Output already exists; choose a new directory: {destination}")
    entries, targets, metadata = load_source(archive)
    print(f"Loaded {len(entries):,} dictionary entries and reference pages", flush=True)
    stats = Counter()
    resources = set()
    definitions = [prettify(entry, targets, resources, stats) for entry in entries]
    source_text_digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: key(e.word)):
        text_digest_update(source_text_digest, visible_text(ET.fromstring(entry.markup)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # No partial dictionary is published on failure, and existing output is never overwritten.
    with tempfile.TemporaryDirectory(prefix=".mw-build-", dir=destination.parent) as tmp:
        output = Path(tmp) / "dictionary"
        output.mkdir()
        stats.update(write_stardict(entries, definitions, output, metadata))
        with ZipFile(archive) as z:
            for name in sorted(resources):
                resource = output / "res" / name.removeprefix("OEBPS/")
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_bytes(z.read(name))
        css = Path(__file__).with_name("style.css").read_text(encoding="utf-8")
        (output / f"{BASENAME}.css").write_text(css, encoding="utf-8")
        write_preview(entries, definitions, output, css)
        if compress:
            subprocess.run(["dictzip", str(output / f"{BASENAME}.dict")], check=True)
        report = dict(stats)
        with Path(archive).open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        report.update(source_sha256=source_hash,
                      dictionary_entries=sum(not e.reference for e in entries),
                      reference_pages=sum(e.reference for e in entries), resources=len(resources),
                      duplicate_source_ids=metadata["duplicate_source_ids"],
                      case_repaired_ids=metadata["case_repaired_ids"],
                      source_text_sha256=source_text_digest.hexdigest())
        verified = verify(output)
        if verified["text_sha256"] != report["source_text_sha256"]:
            raise ValueError("Serialized definition text differs from publisher source")
        report.update(verified)
        (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        output.rename(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="MW12 MOBI, or KindleUnpack's kindlegensrc.zip")
    parser.add_argument("output", type=Path, help="New output directory (must not exist)")
    parser.add_argument("--compress", action="store_true", help="Use dictzip to produce .dict.dz")
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError(f"Output already exists: {args.output}")
        if args.input.suffix.lower() == ".zip":
            report = convert(args.input, args.output, args.compress)
        else:
            if not shutil.which("kindleunpack"):
                raise ValueError("kindleunpack is missing; run inside nix develop")
            with tempfile.TemporaryDirectory(prefix="mw-unpack-") as tmp:
                log = args.output.parent / (args.output.name + "-unpack.log")
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("w") as stream:
                    subprocess.run(["kindleunpack", str(args.input.resolve()), tmp], stdout=stream,
                                   stderr=subprocess.STDOUT, check=True)
                archive = Path(tmp) / "kindlegensrc.zip"
                if not archive.exists():
                    raise ValueError(f"No publisher source archive recovered. Unsupported or encrypted MOBI. See {log}")
                report = convert(archive, args.output, args.compress)
        print(json.dumps(report, indent=2))
        print(f"Ready for review: {args.output.resolve()}")
    except (ValueError, OSError, KeyError, ET.ParseError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()

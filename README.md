# MW Collegiate prettifier

Convert your own **Merriam-Webster's Collegiate Dictionary, Twelfth Edition** MOBI into a readable StarDict dictionary for KOReader. No .NET, GUI, or Python packages are required.

This edition contains the publisher's original XHTML in an embedded KindleGen source archive. KindleUnpack recovers that archive; this program works from its richer markup rather than reconstructing structure from flattened MOBI HTML. Files without this archive are **not supported**. It does not remove DRM.

## Convert a dictionary

From this directory:

```bash
nix-shell
uv run mwd_prettifier.py \
  "data/input/Merriam-Webster Collegiate Dictionary 12thEd.mobi" \
  data/output/MW_Collegiate_12 --compress
```

Or run `direnv allow` to enable the supplied `.envrc`. The shell provides Python, uv, a revision-pinned KindleUnpack command, and dictzip. The first Nix invocation needs network access; conversion itself is local. Nixpkgs comes from your `<nixpkgs>` channel and is not pinned.

The output directory must not exist. Use a different name for a second build; the tool does not overwrite an existing dictionary. KindleUnpack's log is written next to the output directory. Temporary unpacked files are removed after conversion.

To process an already-recovered source archive:

```bash
uv run mwd_prettifier.py /path/to/kindlegensrc.zip data/output/another-build
```

Omit `--compress` to produce an uncompressed `.dict` (also supported by KOReader). That path needs only Python 3.11+ once the archive has been recovered.

## Review in KOReader

Copy **the entire `data/output/MW_Collegiate_12/` directory** into KOReader's `data/dict/` directory. Keep the matching `.ifo`, `.idx`, `.syn`, `.dict.dz` (or `.dict`), `.css`, and `res/` together. Select the dictionary whose name ends in **Prettified** to distinguish it from the old conversion.

Suggested checks:

- **dictionary**, **hello**: pronunciation, dates, etymology, examples.
- **run**, **color**: separate homographs, long senses, subsense indentation.
- **went**, **children**: inflected-form lookup.
- **abalone**: illustration.
- **MW — Periodic Table**: publisher reference material.
- Tap a cross-reference, including one containing spaces.

`preview.html` in the output folder provides a browser preview of sample entries. Its `bword://` links are for dictionary readers, not browser navigation. Browser rendering is not a substitute for checking KOReader. The preview and `validation.json` can be omitted when copying to a device.

The stylesheet uses dark colour accents plus spacing, weight, and indentation. It works in colour or grayscale; it is not a separately tuned monochrome theme. Edit the generated `.css` to change the installed dictionary's appearance without rebuilding it. Edit `style.css` to change future builds.

## Preservation and validation

The converter retains every source dictionary entry, including repeated headwords for homographs. It preserves publisher inflections and additional orthographic lookup keys, illustrations, and 23 reference pages (under the `MW —` prefix). It does not merge definitions or discard duplicates.

Formatting adds sense/subsense containers and wraps recognisable examples, etymologies, and first-use dates. Original wording, punctuation, Unicode, and text order are retained. It removes Kindle build attributes and dictionary-index markup, not definition text.

Before publishing output, an independent reader verifies:

- StarDict sort order, offsets, sizes, counts, and synonym targets;
- balanced HTML, every internal link's lookup key, and every referenced image;
- a length-delimited SHA-256 fingerprint of all definition text against the publisher source.

```bash
uv run python -m unittest -v
uv run verify.py data/output/MW_Collegiate_12
```

Tests use invented text, not dictionary excerpts. `validation.json` records the checks and source-archive hash. Compressed output is read back through gzip, checking its CRC as part of validation.

### Link handling and limits

The publisher source contains duplicate IDs and case-mismatched references. Exact targets win; a canonical entry ID wins over a duplicate variant anchor; remaining duplicate anchors use the first target. Missing lowercase targets are resolved only when their case-folded destination is unique. The MW12 `zalcitabine → #ddc` source erratum is explicitly resolved to the drug **ddC**, not **DDC** (Dewey Decimal Classification). Unresolved links fail the build rather than silently disappearing.

Links open the containing headword or reference page. They do **not** jump to an exact sense or a particular homograph within that lookup. Displayed sense numbers remain intact. The large publisher A–Z browsing page is retained, but normal lookup is preferable on an e-reader.

This is deliberately specific to the recovered MW12 source, not a general MOBI converter. Visual grouping is conservative and should be reviewed on the target device.

## Local data and dependencies

`data/` is ignored by Git: source MOBI, publisher XHTML, generated dictionaries, logs, and previews stay local. Do not publish these copyrighted artifacts with the program.

Our converter and verifier use only Python's standard library. The external decoder is [KindleUnpack](https://github.com/kevinhendricks/KindleUnpack), GPLv3, pinned in `shell.nix` at `bf0ca6ece4e73494625e7950be3e259b6260774c`. Its code is fetched separately, not copied into this project. `dictzip` is optional; no global pip installs are needed.

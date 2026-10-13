import contextlib
import io
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from zipfile import ZipFile

import mwd_prettifier as mw
from verify import DefinitionParser, records, verify

# Invented definitions, not excerpts from the proprietary dictionary.
PAGE = '''<html xmlns="http://www.w3.org/1999/xhtml" xmlns:idx="http://www.mobipocket.com/idx">
<head><title>Test</title></head><body>
<idx:entry><div class="entry" id="Alpha"><idx:orth value="Alpha"><idx:infl>
<idx:iform value="Alphas"/><idx:iform value="Alpha"/></idx:infl></idx:orth>
<dfn>Alpha</dfn> <span class="phon">\\al-fa\\</span> <span class="pos">n</span><br/>
<b>1 a</b> : an invented test &lt;try <i>alpha</i>&gt; <b>b</b> : another meaning<br/>
{from <i>testing</i>}<br/>(2026)<div class="entrysect"><idx:orth value="alpha-ish"/>
— <dfn class="subhw">alpha-ish</dfn></div><img src="images/test.png" alt="test"/>
See <a href="#beta">BETA</a>, <a href="ref.xhtml#table">a table</a>.</div></idx:entry>
<idx:entry><div class="entry" id="beta"><idx:orth value="beta"/><dfn>beta</dfn>
See <a href="#alpha">ALPHA</a>.</div></idx:entry>
<idx:entry><div class="entry" id="beta_2"><idx:orth value="beta"><idx:infl>
<idx:iform value="betas"/></idx:infl></idx:orth><dfn><sup>2</sup>beta</dfn> a second homograph</div></idx:entry>
</body></html>'''
REF = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Table</title></head><body><div id="table">A &amp; B</div></body></html>'
OPF = '<package><metadata><title>Merriam-Webster Collegiate Twelfth Edition</title><creator>Test</creator></metadata></package>'


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / "source.zip"
        self.output = self.root / "output"

    def source(self, page=PAGE, image=True):
        with ZipFile(self.archive, "w") as z:
            z.writestr("OEBPS/content.opf", OPF)
            z.writestr("OEBPS/a.xhtml", page)
            z.writestr("OEBPS/ref.xhtml", REF)
            if image:
                z.writestr("OEBPS/images/test.png", b"test image payload")

    def convert(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return mw.convert(self.archive, self.output)

    def test_full_conversion_preserves_entries_aliases_resources_and_text(self):
        self.source()
        report = self.convert()
        self.assertEqual({k: report[k] for k in ["dictionary_entries", "reference_pages", "resources", "verified_entries", "verified_synonyms"]},
                         {"dictionary_entries": 3, "reference_pages": 1, "resources": 1, "verified_entries": 4, "verified_synonyms": 3})
        self.assertEqual(report["source_text_sha256"], verify(self.output)["text_sha256"])
        base = self.output / mw.BASENAME
        index = list(records(base.with_suffix(".idx"), 2))
        aliases = [(alias, index[number[0]][0]) for alias, number in records(base.with_suffix(".syn"), 1)]
        self.assertEqual(set(aliases), {("Alphas", "Alpha"), ("alpha-ish", "Alpha"), ("betas", "beta")})
        self.assertEqual([w for w, _ in index].count("beta"), 2)
        self.assertEqual((self.output / "res/images/test.png").read_bytes(), b"test image payload")
        data = base.with_suffix(".dict").read_text()
        self.assertIn('class="example"', data)
        self.assertIn('class="etymology"', data)
        self.assertIn('class="subsense"', data)
        self.assertIn('href="bword://Alpha"', data)
        self.assertNotIn("amzn-src-id", data)
        self.assertNotIn("idx:", data)

    def test_existing_output_is_never_changed(self):
        self.source()
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_text("original")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.convert()
        self.assertEqual(sentinel.read_text(), "original")

    def test_missing_link_fails_without_publishing_partial_output(self):
        self.source(PAGE.replace('href="#beta"', 'href="#absent"'))
        with self.assertRaisesRegex(ValueError, "Unresolved link"):
            self.convert()
        self.assertFalse(self.output.exists())

    def test_missing_image_fails_without_publishing_partial_output(self):
        self.source(image=False)
        with self.assertRaises(KeyError):
            self.convert()
        self.assertFalse(self.output.exists())

    def test_resource_traversal_is_rejected(self):
        self.source(PAGE.replace('src="images/test.png"', 'src="../../outside.png"'))
        with self.assertRaisesRegex(ValueError, "outside OEBPS"):
            self.convert()

    def test_verifier_rejects_corrupted_synonym(self):
        self.source()
        self.convert()
        syn = self.output / (mw.BASENAME + ".syn")
        data = bytearray(syn.read_bytes())
        end = data.index(0)
        data[end + 1:end + 5] = struct.pack(">I", 9999)
        syn.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Out-of-range"):
            verify(self.output)

    def test_verifier_rejects_changed_text(self):
        self.source()
        self.convert()
        dictionary = self.output / (mw.BASENAME + ".dict")
        dictionary.write_bytes(dictionary.read_bytes().replace(b"invented", b"modified"))
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            verify(self.output)

    def test_delimiters_preserve_mixed_and_unmatched_content(self):
        for markup in [
            '<div>&lt;use <i>this</i>&gt; remains</div>',
            '<div>unclosed &lt;thing <i>here</i></div>',
            '<div>&lt;before<div>block</div>after&gt;</div>',
            '<div>a &amp; b &lt; x &lt; y &gt;</div>',
        ]:
            with self.subTest(markup=markup):
                root = ET.fromstring(markup)
                expected = mw.visible_text(root)
                mw.wrap_delimited(root, "<", ">", "example", Counter())
                self.assertEqual(mw.visible_text(root), expected)
                parser = DefinitionParser()
                parser.feed(ET.tostring(root, encoding="unicode", method="html"))
                self.assertEqual((parser.stack, "".join(parser.text)), ([], expected))

    def test_koreader_links_use_literal_lookup_text(self):
        entry = mw.Entry("test", set(), "OEBPS/test.xhtml",
                         b'<div>See <a href="#target">target</a></div>')
        word = 'a 100% "quoted" & accented é word'
        result = mw.prettify(entry, {("OEBPS/test.xhtml", "target"): word}, set(), Counter())
        parser = DefinitionParser()
        parser.feed(result)
        self.assertEqual(parser.links, [word])

    def test_canonical_id_wins_over_variant_anchor(self):
        self.source(PAGE.replace('<dfn>Alpha</dfn>', '<a id="beta"/><dfn>Alpha</dfn>'))
        _, targets, metadata = mw.load_source(self.archive)
        self.assertEqual((targets["OEBPS/a.xhtml", "beta"], metadata["duplicate_source_ids"]), ("beta", 1))


if __name__ == "__main__":
    unittest.main()

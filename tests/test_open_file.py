"""The catalog's "Open file" opens the reader as coming from the catalog (so it shows "Catalog")."""

import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import make_server  # noqa: E402


def test_open_file_link_says_it_comes_from_the_catalog():
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(Path(tmp) / "Jane Doe - A Quiet Harbour.epub", "w") as zf:   # one book
            zf.writestr("META-INF/container.xml",
                        '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            zf.writestr("content.opf", '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
                        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>A Quiet Harbour</dc:title></metadata></package>')
        assert make_server.build_index(Path(tmp)) == 0
        html = (Path(tmp) / "index.html").read_text(encoding="utf-8")
    link = html[html.index('id="openFile"'):]
    link = link[:link.index(">")]
    assert 'href=".index/reader/reader.html#from=catalog"' in link, link

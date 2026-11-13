"""Apply only additive Black Friday entry points to an existing public release."""
from pathlib import Path
import re
import sys


PAGES = ("index.html", "catalogue.html", "clearance.html", "search.html", "alerts.html")
BANNER = ('<a href="/black-friday.html" data-black-friday-entry '
          'style="display:block;text-align:center;background:#f2ff5c;color:#19251d;'
          'padding:9px 18px;font-size:12px;font-weight:700;text-decoration:none">'
          'Black Friday 2026 &nbsp; · &nbsp; Explore early offers &amp; tracked deals ↗</a>')


def apply(root: Path) -> None:
    for name in PAGES:
        path = root / name
        text = path.read_text(encoding="utf-8")
        if 'data-black-friday-entry' in text:
            continue
        text, count = re.subn(r'(<body\b[^>]*>)', lambda m: m[1] + '\n' + BANNER,
                             text, count=1, flags=re.I)
        if count != 1:
            raise ValueError(f"Missing body: {name}")
        text, count = re.subn(r'(<div class="navlinks">)',
                             r'\1<a href="/black-friday.html">Black Friday</a>', text, count=1)
        if count != 1:
            raise ValueError(f"Missing navigation: {name}")
        path.write_text(text, encoding="utf-8")
    sitemap = root / "sitemap-pages.xml"
    text = sitemap.read_text(encoding="utf-8")
    if '/black-friday.html' not in text:
        text = text.replace('</urlset>', '<url><loc>https://dealwatch.com.au/black-friday.html</loc>'
                            '<changefreq>daily</changefreq><priority>0.9</priority></url>\n</urlset>')
        sitemap.write_text(text, encoding="utf-8")
    sw = root / "sw.js"
    text = sw.read_text(encoding="utf-8")
    text, count = re.subn(r"const CACHE = '[^']+';", "const CACHE = 'dealwatch-static-v21-black-friday';", text, count=1)
    if count != 1:
        raise ValueError("Missing service worker cache version")
    sw.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    apply(Path(sys.argv[1]))

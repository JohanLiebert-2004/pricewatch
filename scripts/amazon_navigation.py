"""Add Amazon navigation to an existing release while preserving unrelated HTML."""
from pathlib import Path
import re
import sys


def apply(root):
    root = Path(root)
    changed = []
    link = '<a href="/amazon.html" data-amazon-nav>Amazon</a>'
    for path in sorted(root.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        original = text
        pattern = r'(<div\b[^>]*class="navlinks"[^>]*>)(.*?)(</div>)'
        def add(match):
            return match[0] if '/amazon.html' in match[2] else match[1] + match[2] + link + match[3]
        text = re.sub(pattern, add, text, count=1, flags=re.S)
        mobile_pattern = r'(<nav\b[^>]*class="(?:bf-)?mobile-nav"[^>]*>)(.*?)(</nav>)'
        def add_mobile(match):
            if '/amazon.html' in match[2]:
                return match[0]
            count = len(re.findall(r'<a\b', match[2])) + 1
            opening = match[1]
            style = f'grid-template-columns:repeat({count},minmax(0,1fr))'
            if 'style="' in opening:
                opening = re.sub(r'style="([^"]*)"', lambda m: 'style="' + m[1] + ';' + style + '"', opening, count=1)
            else:
                opening = opening[:-1] + ' style="' + style + '">'
            mobile_link = '<a href="/amazon.html" data-amazon-nav><svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 3h2l3 12h10l3-8H6"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></svg>Amazon</a>'
            return opening + match[2] + mobile_link + match[3]
        text = re.sub(mobile_pattern, add_mobile, text, count=1, flags=re.S)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed.append(path.name)
    sitemap = root / "sitemap-pages.xml"
    if sitemap.exists():
        text = sitemap.read_text(encoding="utf-8")
        if "https://dealwatch.com.au/amazon.html" not in text:
            text = text.replace("</urlset>", '<url><loc>https://dealwatch.com.au/amazon.html</loc><changefreq>hourly</changefreq><priority>0.8</priority></url>\n</urlset>')
            sitemap.write_text(text, encoding="utf-8")
    sw = root / "sw.js"
    if sw.exists():
        text = sw.read_text(encoding="utf-8")
        text = re.sub(r"const CACHE = '[^']+';", "const CACHE = 'dealwatch-static-v22-amazon';", text, count=1)
        sw.write_text(text, encoding="utf-8")
    return changed


if __name__ == "__main__":
    print("Amazon navigation: " + ", ".join(apply(sys.argv[1])))

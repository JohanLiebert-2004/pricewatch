"""Patch existing public HTML/templates without replacing unrelated content."""
from pathlib import Path

TAG = '<script defer src="/analytics.js"></script>'


def patch_html(text, privacy=False):
    if TAG not in text:
        marker = '<!-- End Google Tag Manager -->'
        if marker not in text:
            raise ValueError('Existing Tag Manager marker missing')
        text = text.replace(marker, marker + '\n' + TAG, 1)
    if privacy:
        text = text.replace('Last updated 13 September 2026', 'Last updated 28 October 2026')
        text = text.replace('and uses Vercel to host the website and provide aggregated analytics.',
                            'hosts the website on Oracle Cloud Infrastructure (OCI), and uses Google Analytics to measure website usage.')
        start = text.index('<h2>Website measurement</h2>')
        end = text.index('<h2>Retention and choices</h2>', start)
        text = text[:start] + '''<h2>Website measurement</h2><p>We use Google Analytics 4 and Google Tag Manager to understand visits, sessions, popular pages, approximate countries, devices and traffic sources. Google Analytics uses browser cookies and receives technical information such as IP address and browser details. We disable Google signals and advertising personalization in our tag configuration. We do not send price-watch email addresses or alert tokens to Analytics. Our tag removes query strings and fragments from page and referrer URLs and skips URLs with sensitive parameters. Analytics can be blocked through browser privacy controls or Google's <a href="https://tools.google.com/dlpage/gaoptout">Analytics opt-out add-on</a>. See <a href="https://policies.google.com/privacy">Google's Privacy Policy</a> for information about Google's handling of data.</p>
''' + text[end:]
    return text


if __name__ == '__main__':
    import sys
    root = Path(sys.argv[1])
    for path in root.glob('*.html'):
        original = path.read_text(encoding='utf-8')
        updated = patch_html(original, path.name == 'privacy.html')
        if updated != original:
            path.write_text(updated, encoding='utf-8')

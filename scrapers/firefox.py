"""Standard Firefox WebDriver transport for opt-in local retail collectors.

Uses a disposable profile, default browser identity and ordinary headless
WebDriver. No browser cookies are imported or exported and refusals are not
retried. Requires installed Firefox and geckodriver; no Python extra packages.
"""
import atexit
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


@dataclass
class BrowserResponse:
    status_code: int
    text: str
    headers: dict = field(default_factory=dict)

    @property
    def content(self):
        return self.text.encode('utf-8')


class FirefoxSession:
    def __init__(self, host='www.bigw.com.au'):
        if host not in ('www.bigw.com.au', 'www.sephora.com.au'):
            raise ValueError('Unsupported Firefox collector host')
        self._host = host
        self._process = None
        self._session = None
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        driver = os.environ.get('GECKODRIVER') or shutil.which('geckodriver')
        if not driver:
            raise RuntimeError('Firefox collection requires installed geckodriver')
        options = {'args': ['-headless']}
        binary = os.environ.get('FIREFOX_BINARY')
        snap_binary = '/snap/firefox/current/usr/lib/firefox/firefox'
        if not binary and str(driver).startswith('/snap/bin/') and Path(snap_binary).exists():
            binary = snap_binary
        if binary:
            options['binary'] = binary
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self._base = f'http://127.0.0.1:{port}'
        try:
            self._process = subprocess.Popen(
                [driver, '--host', '127.0.0.1', '--port', str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(60):
                if self._process.poll() is not None:
                    raise RuntimeError('geckodriver exited during startup')
                try:
                    self._call('GET', '/status', timeout=1)
                    break
                except urllib.error.URLError:
                    time.sleep(0.25)
            else:
                raise RuntimeError('geckodriver startup timed out')
            result = self._call('POST', '/session', {'capabilities': {'alwaysMatch': {
                'browserName': 'firefox', 'pageLoadStrategy': 'eager',
                'moz:firefoxOptions': options}}})
            self._session = result['sessionId']
            self._command('POST', '/timeouts',
                          {'pageLoad': 45000, 'script': 30000, 'implicit': 0})
        except Exception:
            self.close()
            raise
        atexit.register(self.close)

    def _call(self, method, path, payload=None, timeout=55):
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Content-Type': 'application/json'}, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                value = json.load(response)['value']
        except urllib.error.HTTPError as exc:
            # Browser errors can contain page data; do not include their body.
            raise RuntimeError(f'Firefox WebDriver command failed (HTTP {exc.code})') from None
        return value

    def _command(self, method, path, payload=None, timeout=55):
        return self._call(method, '/session/' + self._session + path, payload, timeout)

    def _check_url(self, url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != self._host
                or parsed.port not in (None, 443) or parsed.username or parsed.password):
            raise ValueError('Firefox collector only supports its configured HTTPS host')

    @staticmethod
    def _response(status, text, headers=None):
        if not isinstance(status, int) or status < 100 or status > 599:
            raise RuntimeError('Firefox did not report a valid navigation HTTP status')
        return BrowserResponse(status, text, headers or {})

    def fetch(self, url, headers):
        """Read a same-origin API through the browser after normal warm-up."""
        self._check_url(url)
        if not self._session:
            raise RuntimeError('Firefox session is closed')
        if any(key not in ('X-Platform', 'X-Site-Country') for key in headers):
            raise ValueError('Unsupported browser API header')
        self._check_url(self._command('GET', '/url'))
        result = self._command('POST', '/execute/async', {
            'script': '''const done = arguments[arguments.length - 1];
                fetch(arguments[0], {headers: arguments[1], redirect: 'error'})
                .then(async r => done({status: r.status, text: await r.text(),
                    headers: {server: r.headers.get('server') || '',
                              'retry-after': r.headers.get('retry-after') || ''}}))
                .catch(() => done({error: true}));''',
            'args': [url, headers]})
        if result.get('error'):
            raise RuntimeError('Firefox same-origin API request failed')
        return self._response(result.get('status'), result.get('text', ''),
                              result.get('headers'))

    def get(self, url):
        self._check_url(url)
        if not self._session:
            raise RuntimeError('Firefox session is closed')
        # One navigation only. BaseScraper checks refusals before the caller
        # can parse data or issue its next request, preserving existing delays.
        self._command('POST', '/url', {'url': url})
        self._check_url(self._command('GET', '/url'))
        raw = self._command('GET', '/source')
        status = self._command('POST', '/execute/sync', {
            'script': 'return performance.getEntriesByType("navigation")[0]?.responseStatus ?? null;',
            'args': []})
        return self._response(status, raw)

    def close(self):
        if self._session:
            try:
                self._command('DELETE', '', timeout=10)
            except Exception:
                pass
            self._session = None
        if self._process:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
            self._process = None
        atexit.unregister(self.close)

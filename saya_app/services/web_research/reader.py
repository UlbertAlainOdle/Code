"""Safe HTTP reader for the web research pipeline.

Implements plan sections 12.3 (redirects), 12.4 (download), 12.5 (robots) and
13 (extraction & cleaning). The reader:

- manually walks redirects (``allow_redirects=False``), re-validating each
  target with :mod:`security` so public->internal redirects are blocked;
- streams responses with a hard decompressed byte cap;
- enforces a strict Content-Type allowlist;
- respects robots.txt, fetching it through the same SSRF-limited path;
- extracts main text with Trafilatura from already-downloaded HTML only
  (Trafilatura never fetches the URL itself);
- never lets a network error escape to the chat top level: failures are
  returned as :class:`PageDocument` with an ``error_code``.
"""

import re
from io import BytesIO
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests

from saya_app.config import (
    WEB_READER_ALLOWED_CONTENT_TYPES,
    WEB_READER_MAX_BYTES,
    WEB_READER_MAX_PDF_BYTES,
    WEB_READER_MAX_REDIRECTS,
    WEB_READER_MAX_TEXT_CHARS,
    WEB_READER_USER_AGENT,
)
from saya_app.services.web_research.models import PageDocument
from saya_app.services.web_research.security import is_url_safe


# Short-lived per-origin robots cache. Values: (expires_at_ts, RobotFileParser).
ROBOTS_CACHE_TTL_SECONDS = 300

# Redirect status codes that require re-validation of the Location target.
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# The product token advertised to robots.txt (substring of the full UA).
ROBOTS_USER_AGENT_TOKEN = "Saya-Reader"

# Error codes returned by ``_safe_download`` that represent a security block
# (e.g. a redirect target pointing at an internal host) rather than a plain
# network failure. These map a PageDocument to status "blocked".
SECURITY_BLOCK_CODES = frozenset({
    "unsafe_url",
    "unsafe_address",
    "invalid_scheme",
    "missing_host",
    "userinfo_present",
    "invalid_port",
    "loopback_host",
    "empty_url",
    "unparseable_address",
})

# NUL and other C0 control chars except tab/newline/cr to strip from text.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Collapse any run of blank lines (including space/tab-only lines) into a
# single blank line. Needs at least two newlines, so single line breaks in
# flowing text are preserved.
_BLANK_RE = re.compile(r"\n[ \t\n]*\n")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_FALLBACK_NOISE_RE = re.compile(
    r"(?:sign\s*up|subscribe|newsletter|in\s+your\s+inbox|latest\s+news.*inbox|"
    r"accept\s+cookies?|privacy\s+policy|log\s*in|register|订阅|注册|登录|隐私政策)",
    re.I,
)


class WebReader:
    """Fetches and extracts a single webpage safely."""

    def __init__(self, session=None, resolver=None, robots_cache=None,
                 timeout=(5, 15)):
        self.session = session if session is not None else requests.Session()
        self.resolver = resolver  # injectable DNS resolver for tests.
        # robots cache shared across reads; origin -> (expiry_ts, parser).
        self._robots_cache = robots_cache if robots_cache is not None else {}
        self.timeout = timeout

    # ------------------------------------------------------------------ robots
    def _robots_key(self, info: dict) -> str:
        return f"{info['scheme']}://{info['host']}:{info['port'] or 443 if info['scheme']=='https' else info['port'] or 80}"

    def _origin_of(self, url: str):
        parts = urlsplit_safe(url)
        return parts

    def is_allowed_by_robots(self, url: str) -> str:
        """Check robots.txt for ``url``.

        Returns one of: ``"allow"``, ``"disallow"``, ``"unavailable"``.
        The robots.txt fetch itself is SSRF + size limited. On any fetch error
        we treat robots as unavailable and allow the read (plan 12.5).
        """
        safe, code, info = is_url_safe(url, resolver=self.resolver)
        if not safe:
            # Cannot fetch robots for an unsafe URL; treat as unavailable.
            return "unavailable"

        key = self._robots_key(info)
        cached = self._robots_cache.get(key)
        import time
        now = time.time()
        if cached and cached[0] > now:
            parser = cached[1]
        else:
            robots_url = f"{info['scheme']}://{info['host']}"
            if info['port']:
                robots_url += f":{info['port']}"
            robots_url += "/robots.txt"
            content, _final, err = self._safe_download(
                robots_url, max_bytes=64 * 1024, expect_any_text=True
            )
            if err or content is None:
                # Unavailable -> allow (plan 12.5).
                return "unavailable"
            parser = RobotFileParser()
            try:
                parser.parse(content.splitlines())
            except Exception:
                return "unavailable"
            self._robots_cache[key] = (now + ROBOTS_CACHE_TTL_SECONDS, parser)

        try:
            if parser.can_fetch(ROBOTS_USER_AGENT_TOKEN, url):
                return "allow"
            return "disallow"
        except Exception:
            return "unavailable"

    # --------------------------------------------------------------- download
    def _safe_download(self, url: str, max_bytes=WEB_READER_MAX_BYTES,
                       expect_any_text=False):
        """Download ``url`` safely, following redirects manually.

        Returns ``(text_content, final_url, error_code)``. On success
        ``error_code`` is ``""``; otherwise ``text_content`` is ``None``.
        Never raises for network/HTTP problems.
        """
        current = url
        redirects = 0
        while True:
            safe, code, info = is_url_safe(current, resolver=self.resolver)
            if not safe:
                return None, current, code or "unsafe_url"

            try:
                resp = self.session.get(
                    current,
                    allow_redirects=False,
                    stream=True,
                    timeout=self.timeout,
                    headers={"User-Agent": WEB_READER_USER_AGENT},
                )
            except requests.RequestException:
                return None, current, "network_error"

            if resp.status_code in REDIRECT_STATUSES:
                location = resp.headers.get("Location") or ""
                resp.close()
                if not location:
                    return None, current, "redirect_without_location"
                next_url = urljoin(current, location)
                redirects += 1
                if redirects > WEB_READER_MAX_REDIRECTS:
                    return None, next_url, "too_many_redirects"
                current = next_url
                continue

            break

        if resp.status_code != 200:
            resp.close()
            return None, current, f"http_{resp.status_code}"

        # Content-Type allowlist (text content only).
        content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        pdf_octet_stream = (
            content_type == "application/octet-stream"
            and urlsplit_safe(current).path.lower().endswith(".pdf")
        )
        if content_type not in WEB_READER_ALLOWED_CONTENT_TYPES and not pdf_octet_stream:
            resp.close()
            return None, current, "unsupported_content_type"
        effective_max_bytes = (
            WEB_READER_MAX_PDF_BYTES
            if content_type == "application/pdf" or pdf_octet_stream
            else max_bytes
        )

        # Content-Length pre-check; still enforce the stream cap below.
        try:
            declared = resp.headers.get("Content-Length")
            if declared is not None and int(declared) > effective_max_bytes:
                resp.close()
                return None, current, "oversized_response"
        except (TypeError, ValueError):
            pass

        # Streamed accumulation with a hard decompressed byte cap.
        chunks = []
        total = 0
        try:
            for chunk in resp.iter_content(chunk_size=8192, decode_unicode=False):
                if not chunk:
                    continue
                total += len(chunk)
                if total > effective_max_bytes:
                    resp.close()
                    return None, current, "oversized_response"
                chunks.append(chunk)
        except requests.RequestException:
            resp.close()
            return None, current, "network_error"
        finally:
            try:
                resp.close()
            except Exception:
                pass

        raw = b"".join(chunks)
        if content_type == "application/pdf" or pdf_octet_stream:
            if not raw.lstrip().startswith(b"%PDF-"):
                return None, current, "invalid_pdf_content"
            return raw, current, ""
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            text = raw.decode("latin-1", errors="replace")
        return text, current, ""

    # ------------------------------------------------------------- extraction
    def extract_text(self, html_content: str, fallback_title: str = ""):
        """Extract main text from already-downloaded HTML.

        Trafilatura is imported lazily so the app still starts if the optional
        dependency is missing. Returns ``(title, text)``. On any failure
        ``text`` is ``""`` so the caller can degrade to a search snippet.
        Trafilatura never fetches the URL itself; it only parses the content
        passed in.
        """
        title = ""
        text = ""
        try:
            trafilatura = _import_trafilatura()
        except ImportError:
            trafilatura = None

        if trafilatura is not None:
            try:
                # ``bare_extraction`` returns both title and text in one pass.
                data = trafilatura.bare_extraction(
                    html_content,
                    include_comments=False,
                    include_tables=False,
                    no_fallback=True,
                    as_dict=True,
                )
            except Exception:
                data = None
            if data:
                title = data.get("title") or ""
                text = data.get("text") or ""

        # Trafilatura is optional in this project. Falling straight to an
        # empty document when it is not installed made every normal HTML page
        # degrade to a search snippet. BeautifulSoup is already available in
        # the application environment and provides a conservative fallback.
        if not text:
            fallback_title_value, fallback_text = self._extract_text_fallback(html_content)
            title = title or fallback_title_value
            text = fallback_text

        text = clean_text(text)
        if title:
            title = clean_text(title)
        if not title:
            title = clean_text(fallback_title)
        return title, text

    @staticmethod
    def _extract_text_fallback(html_content: str):
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return "", ""
        try:
            soup = BeautifulSoup(html_content, "lxml")
            for node in soup.select(
                "script, style, noscript, template, svg, canvas, iframe, nav, footer, "
                "form, button, input, select, textarea, [aria-hidden='true']"
            ):
                node.decompose()
            title = ""
            if soup.title:
                title = soup.title.get_text(" ", strip=True)
            candidates = soup.find_all(["article", "main"])
            candidates = [
                node for node in candidates
                if len(clean_text(node.get_text(" ", strip=True))) >= 120
            ]
            root = max(
                candidates,
                key=lambda node: len(clean_text(node.get_text(" ", strip=True))),
                default=None,
            )
            if root is None:
                root = soup.body or soup
            blocks = []
            for node in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "td"]):
                if node.find_parent(["nav", "footer", "aside", "header"]):
                    continue
                value = clean_text(node.get_text(" ", strip=True))
                # Short UI fragments and repeated navigation labels add noise
                # but carry no article evidence.
                if len(value) < 25:
                    continue
                if _FALLBACK_NOISE_RE.search(value):
                    continue
                if value not in blocks:
                    blocks.append(value)
            text = clean_text("\n\n".join(blocks))
            if len(text) < 120:
                text = ""
            return clean_text(title), text
        except Exception:
            return "", ""

    # --------------------------------------------------------------- public
    def fetch(self, url: str, fallback_title: str = "", check_robots: bool = True) -> PageDocument:
        """Fetch and extract one page. Never raises to the chat top level."""
        doc = PageDocument(requested_url=url)

        safe, code, info = is_url_safe(url, resolver=self.resolver)
        if not safe:
            doc.status = "blocked"
            doc.error_code = code or "unsafe_url"
            return doc

        if check_robots:
            verdict = self.is_allowed_by_robots(url)
            if verdict == "disallow":
                doc.final_url = url
                doc.status = "blocked"
                doc.error_code = "robots_disallow"
                return doc

        content, final_url, err = self._safe_download(url)
        if err or content is None:
            doc.final_url = final_url or url
            doc.error_code = err or "download_failed"
            # Security blocks (e.g. redirect to an internal host) are distinct
            # from network failures so callers can mark sources "blocked".
            doc.status = "blocked" if doc.error_code in SECURITY_BLOCK_CODES else "failed"
            return doc

        doc.final_url = final_url
        if isinstance(content, bytes):
            title, text, pdf_error = self.extract_pdf_text(content, fallback_title=fallback_title)
            if pdf_error:
                doc.status = "failed"
                doc.error_code = pdf_error
                doc.title = title
                doc.content_type = "application/pdf"
                return doc
            content_type_guess = "application/pdf"
        else:
            content_type_guess = ""
            title, text = self.extract_text(content, fallback_title=fallback_title)
        doc.title = title
        doc.text = text[:WEB_READER_MAX_TEXT_CHARS]
        doc.content_type = content_type_guess
        if doc.text:
            doc.status = "ok"
        else:
            # Extraction failed or empty; signal caller to use snippet.
            doc.status = "extraction_failed"
            doc.error_code = "extraction_empty"
        return doc

    def extract_pdf_text(self, pdf_content: bytes, fallback_title: str = ""):
        """Extract bounded text from an already-downloaded PDF.

        The PDF is never allowed to fetch external resources.  Downloads still
        pass through the same SSRF, redirect, robots and 2 MB byte limits.
        """
        try:
            from pypdf import PdfReader
        except ImportError:
            return clean_text(fallback_title), "", "pdf_parser_unavailable"
        try:
            reader = PdfReader(BytesIO(pdf_content), strict=False)
            if reader.is_encrypted:
                try:
                    if reader.decrypt("") == 0:
                        return clean_text(fallback_title), "", "pdf_encrypted"
                except Exception:
                    return clean_text(fallback_title), "", "pdf_encrypted"
            metadata = reader.metadata or {}
            title = clean_text(getattr(metadata, "title", "") or fallback_title)
            pages = []
            total = 0
            for page in reader.pages[:80]:
                value = clean_text(page.extract_text() or "")
                if not value:
                    continue
                remaining = WEB_READER_MAX_TEXT_CHARS - total
                if remaining <= 0:
                    break
                value = value[:remaining]
                pages.append(value)
                total += len(value)
            text = clean_text("\n\n".join(pages))
            if not text:
                return title, "", "pdf_extraction_empty"
            return title, text, ""
        except Exception:
            return clean_text(fallback_title), "", "pdf_parse_failed"


def _import_trafilatura():
    """Lazy import of Trafilatura (optional dependency).

    Isolated as a function so tests can monkeypatch it without installing the
    real package. Raises :class:`ImportError` when unavailable.
    """
    import trafilatura
    return trafilatura


def urlsplit_safe(url: str):
    """Local import to avoid a circular import at module top."""
    from urllib.parse import urlsplit
    return urlsplit(url)


def clean_text(text: str) -> str:
    """Normalize extracted text: strip control chars, collapse blank/whitespace."""
    if not text:
        return ""
    text = _CONTROL_CHAR_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()

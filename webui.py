#{{{ imports
import os
import re
import bottle
import time
import sys
import datetime
import glob
import hashlib
import collections
import csv
import io
import mimetypes
import string
import subprocess
import tempfile
import threading
import shlex
import posixpath
import zipfile
import sqlite3
from types import SimpleNamespace
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from html import escape as html_escape
from urllib.parse import parse_qsl, urlencode, quote as urlquote, unquote as urlunquote, urlsplit
from recoll import recoll, rclextract, rclconfig
import account_web
from accounts import Store, NotFound, ValidationError, INTEGER_PREFERENCES
from webui_data import ApplicationData

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
bottle.TEMPLATE_PATH.insert(0, os.path.join(APP_ROOT, 'views'))
account_store = Store()

def msg(s):
    print("%s" % s, file=sys.stderr)

# use ujson if avalaible (faster than built in json)
try:
    import ujson as json
except ImportError:
    import json
    #msg("ujson module not found, using (slower) built-in json module instead")

g_fscharset=sys.getfilesystemencoding()

g_tmpdir = os.getenv("RECOLL_TMPDIR")
if not g_tmpdir:
    g_tmpdir = os.getenv("TMPDIR")
    
#}}}
#{{{ settings
# settings defaults
DEFAULTS = {
    'context': 30,
    'stem': 1,
    'timefmt': '%c',
    'dirdepth': 2,
    'maxchars': 500,
    'maxresults': 500,
    'perpage': 500,
    'csvfields': 'filename title author size time mtype url',
    'title_link': 'download',
    'collapsedups': 0,
    'synonyms': "",
    'mounts': {
        # Override default links for directories.
        # Useful for rewriting links to access the files on a server.
        # If not specified, the url will be dir path prefixed with 'file://'.
        #
        # Path in recoll.conf   : Remote url
        # '/media/data/docs'    : 'https://media.server.com/docs',
        # '/var/www/data'       : 'file:///192.168.1.2/data',
    },
    "noresultlinks":  0,
    "logquery": 0,
    "shortenpaths": 1,
    "permlinks": 0,
    "res_permlink": 0,
}

READER_SUPPORTED_FORMATS = {
    "txt",
    "epub",
    "mobi",
    "azw",
    "azw3",
    "fb2",
    "chm",
    "docx",
    "md",
    "html",
    "htm",
    "xhtml",
    "xml",
    "mhtml",
    "pdf",
    "cbz",
    "cbr",
    "cbt",
    "cb7",
}

READER_FOLDER_SCAN_LIMIT = 1000
READER_TXT_CACHE_VERSION = 3
READER_TXT_CACHE_ROOT = os.path.join(g_tmpdir or tempfile.gettempdir(), "recoll-webui-reader", "txt")
READER_CHM_CACHE_ROOT = os.path.join(g_tmpdir or tempfile.gettempdir(), "recoll-webui-reader", "chm")
READER_EPUB_CACHE_ROOT = os.path.join(g_tmpdir or tempfile.gettempdir(), "recoll-webui-reader", "epub")

# Number of "keyword in context" snippets kept in the in-process cache. Building a
# snippet re-reads and re-splits the whole source document, which dominates the cost
# of a search on large files. The exact same snippets are asked for again on page
# reloads, history navigation, and for duplicated results.
SNIPPET_CACHE_SIZE = 2048

# sort fields/labels
SORTS = [
    ("relevancyrating", "Relevancy"),
    ("mtime", "Date",),
    ("url", "Path"),
    ("filename", "Filename"),
    ("fbytes", "Size"),
    ("author", "Author"),
]

# doc fields
FIELDS = [
    # exposed by python api
    'abstract',
    'author',
    'collapsecount',
    'dbytes',
    'dmtime',
    'fbytes',
    'filename',
    'fmtime',
    'ipath',
    'keywords',
    'mtime',
    'mtype',
    'origcharset',
    'relevancyrating',
    'sig',
    'size',
    'title',
    'url',
    # calculated
    'label',
    'snippet',
    'time',
]
#}}}
#{{{  functions
#{{{  helpers
def select(ls, invalid=[None]):
    for value in ls:
        if value not in invalid:
            return value

def select_int(ls, default=0, invalid=[None], minimum=None):
    value = select(ls, invalid)
    if value in invalid:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value

def timestr(secs, fmt):
    # Just in case: we had a bug at some point inserting commas in the dmtime field.
    secs = secs.strip(',')
    if secs == '' or secs is None:
        secs = '0'
    t = time.localtime(int(secs))
    return time.strftime(fmt, t)

# Compute a file name used for an attachment 'filename' attribute. We don't know what the remote
# system would accept, so play it safe
_g_valid_filename_chars = "_-%s%s" % (string.ascii_letters, string.digits)
def normalise_filename(fn):
    out = ''.join(c if c in _g_valid_filename_chars else "_" for c in fn)
    return out

# We may need to get the "topdirs" value from other directories than our main one.
def get_topdirs(confdir):
    rclconf = rclconfig.RclConfig(confdir)
    return rclconf.getConfParam('topdirs')

# Environment fetch for the cases where we don't care if unset or null
def safe_envget(varnm):
    try:
        return os.environ[varnm]
    except Exception as ex:
        return None

def normalize_file_path(path):
    if not path:
        return ""
    path = urlunquote(path)
    if path.startswith("file://"):
        path = path[len("file://") :]
    return os.path.realpath(path)

def normalize_reader_relpath(path):
    if not path:
        return ""
    parsed = urlsplit(str(path).strip().replace("\\", "/"))
    relpath = urlunquote(parsed.path or "").lstrip("/")
    parts = [part for part in relpath.split("/") if part not in ("", ".")]
    normalized = []
    for part in parts:
        if part == "..":
            if normalized:
                normalized.pop()
            continue
        normalized.append(part)
    relpath = "/".join(normalized)
    if parsed.query:
        relpath = f"{relpath}?{parsed.query}"
    if parsed.fragment:
        relpath = f"{relpath}#{parsed.fragment}"
    return relpath

def split_reader_relpath(path):
    normalized = normalize_reader_relpath(path)
    parsed = urlsplit(normalized)
    relpath = (parsed.path or "").lstrip("/")
    return relpath, parsed.query or "", parsed.fragment or ""

def build_reader_content_url(prefix, relpath=""):
    clean_relpath, query, fragment = split_reader_relpath(relpath)
    url = prefix.rstrip("/")
    if clean_relpath:
        url = f"{url}/{urlquote(clean_relpath, safe='/')}"
    else:
        url = f"{url}/"
    if query:
        url = f"{url}?{query}"
    if fragment:
        fragment_safe = "-._~!$&'()*+,;=:@/?"
        url = f"{url}#{urlquote(fragment, safe=fragment_safe)}"
    return url

class ChmTocParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.current = None
        self.items = []

    def handle_starttag(self, tag, attrs):
        attrs = {key.lower(): value for key, value in attrs}
        tag = tag.lower()
        if tag == "ul":
            self.depth += 1
            return
        if tag == "object" and attrs.get("type", "").lower() == "text/sitemap":
            self.current = {}
            return
        if tag == "param" and self.current is not None:
            name = (attrs.get("name") or "").strip().lower()
            value = (attrs.get("value") or "").strip()
            if name and value:
                self.current[name] = value

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "object" and self.current is not None:
            label = self.current.get("name", "").strip()
            href = normalize_reader_relpath(self.current.get("local", ""))
            if label or href:
                if not label:
                    label = os.path.splitext(os.path.basename(urlsplit(href).path or ""))[0] or "Untitled"
                self.items.append({
                    "label": label,
                    "href": href,
                    "depth": max(0, self.depth - 1),
                })
            self.current = None
            return
        if tag == "ul" and self.depth > 0:
            self.depth -= 1

def parse_chm_hhp(path):
    metadata = {}
    in_options = False
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith(";"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    in_options = line.strip().lower() == "[options]"
                    continue
                if not in_options or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().lower()
                value = value.strip()
                if key == "default topic":
                    metadata["default_topic"] = normalize_reader_relpath(value)
                elif key == "contents file":
                    metadata["contents_file"] = normalize_reader_relpath(value)
                elif key == "title":
                    metadata["title"] = value
    except OSError:
        return metadata
    return metadata

def parse_chm_toc(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            data = handle.read()
    except OSError:
        return []
    parser = ChmTocParser()
    parser.feed(data)
    return [item for item in parser.items if item.get("href")]

def build_epub_cache_key(path):
    st = os.stat(path)
    payload = json.dumps(
        {
            "path": os.path.realpath(path),
            "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)),
            "size": st.st_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def get_epub_cache_paths(path):
    cache_key = build_epub_cache_key(path)
    root = os.path.join(READER_EPUB_CACHE_ROOT, cache_key)
    return {
        "key": cache_key,
        "root": root,
        "manifest_path": os.path.join(root, "manifest.json"),
    }

def build_txt_cache_key(path):
    st = os.stat(path)
    payload = json.dumps(
        {
            "version": READER_TXT_CACHE_VERSION,
            "path": os.path.realpath(path),
            "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)),
            "size": st.st_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def get_txt_cache_paths(path):
    cache_key = build_txt_cache_key(path)
    root = os.path.join(READER_TXT_CACHE_ROOT, cache_key)
    return {
        "key": cache_key,
        "root": root,
        "manifest_path": os.path.join(root, "manifest.json"),
        "content_path": os.path.join(root, "content.bin"),
    }

def detect_txt_charset_bytes(raw):
    if raw.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"
    if raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"

def normalize_txt_bytes(raw):
    charset = detect_txt_charset_bytes(raw)
    text = raw.decode(charset, errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.encode("utf-8"), "utf-8"

def split_txt_chunks(raw_bytes):
    target = 64 * 1024
    soft = 48 * 1024
    hard = 96 * 1024
    chunks = []
    start = 0
    total = len(raw_bytes)
    while start < total:
        if start + target >= total:
            end = total
        else:
            search_start = min(total, start + soft)
            search_end = min(total, start + hard)
            cut = -1
            for idx in range(search_end - 1, search_start - 1, -1):
                current = raw_bytes[idx:idx + 1]
                if current not in (b"\n", b"\r"):
                    continue
                cut = idx + 1
                if current == b"\r" and idx + 1 < total and raw_bytes[idx + 1:idx + 2] == b"\n":
                    cut = idx + 2
                break
            end = cut if cut > start else min(total, start + hard)
        while end < total and end > start and raw_bytes[end] & 0xC0 == 0x80:
            end -= 1
        if end <= start:
            end = min(total, start + hard)
        chunks.append({
            "index": len(chunks),
            "start": start,
            "end": end,
            "byteLength": max(0, end - start),
        })
        start = end
    return chunks

def build_txt_outline(raw_bytes, chunks):
    """Index headings while building the cached text, without a second browser download."""
    pattern = re.compile(r'^[ \t\u3000]*(第[〇零一二三四五六七八九十百千万两\d]+[章节卷部回篇][^\n]{0,70}|(?:chapter|part|book)\s+[\divxlcdm]+[^\n]{0,70}|序章|序言|前言|楔子|尾声|后记|附录)[ \t]*$', re.IGNORECASE | re.MULTILINE)
    toc = []
    for chunk in chunks:
        text = raw_bytes[chunk['start']:chunk['end']].decode('utf-8', errors='replace')
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        for match in pattern.finditer(text):
            label = match.group(1).strip()
            # DOM ranges use UTF-16 code units, including for astral characters.
            offset = len(text[:match.start(1)].encode('utf-16-le')) // 2
            toc.append({'index': chunk['index'], 'label': label, 'depth': 0,
                        'textOffset': offset, 'query': label,
                        'matchLength': len(label.encode('utf-16-le')) // 2})
    return toc

def ensure_txt_cache(path):
    paths = get_txt_cache_paths(path)
    manifest_path = paths["manifest_path"]
    content_path = paths["content_path"]
    if os.path.isfile(manifest_path) and os.path.isfile(content_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("path") == os.path.realpath(path):
            return manifest

    os.makedirs(READER_TXT_CACHE_ROOT, exist_ok=True)
    os.makedirs(paths["root"], exist_ok=True)
    with open(path, "rb") as handle:
        raw = handle.read()
    raw, charset = normalize_txt_bytes(raw)
    chunks = split_txt_chunks(raw)
    manifest = {
        "path": os.path.realpath(path),
        "size": os.path.getsize(path),
        "mtime": int(os.path.getmtime(path)),
        "charset": charset,
        "chunkSizeTarget": 64 * 1024,
        "chunkCount": len(chunks),
        "estimatedCharsPerChunk": 64 * 1024,
        "chunks": chunks,
        "toc": build_txt_outline(raw, chunks),
    }
    with open(paths["content_path"], "wb") as handle:
        handle.write(raw)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))
    return manifest

def xml_local_name(tag):
    if not tag:
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag

def epub_href_to_path(base_dir, href):
    href = (href or "").strip()
    if not href:
        return ""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return ""
    relpath = parts.path or ""
    if not relpath:
        return ""
    joined = posixpath.normpath(posixpath.join(base_dir or "", relpath))
    if joined.startswith("../") or joined == "..":
        return ""
    if joined.startswith("./"):
        joined = joined[2:]
    if parts.query:
        joined = f"{joined}?{parts.query}"
    if parts.fragment:
        joined = f"{joined}#{parts.fragment}"
    return joined.lstrip("/")

def epub_href_to_zip_member(base_dir, href):
    href = (href or "").strip()
    if not href:
        return ""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return ""
    relpath = parts.path or ""
    if not relpath:
        return ""
    joined = posixpath.normpath(posixpath.join(base_dir or "", relpath))
    if joined.startswith("../") or joined == "..":
        return ""
    return joined.lstrip("/")

def parse_epub_nav_items(container):
    items = []
    if container is None:
        return items
    for li in [child for child in list(container) if xml_local_name(child.tag).lower() == "li"]:
        label = ""
        href = ""
        for child in list(li):
            tag = xml_local_name(child.tag).lower()
            if tag == "a":
                label = " ".join("".join(child.itertext()).split())
                href = (child.get("href") or "").strip()
                if href:
                    break
        subitems = []
        for child in list(li):
            if xml_local_name(child.tag).lower() == "ol":
                subitems = parse_epub_nav_items(child)
                break
        if label or href:
            items.append({
                "label": label or os.path.splitext(os.path.basename(urlsplit(href).path or ""))[0] or "Untitled",
                "href": href,
                "subitems": subitems,
            })
    return items

def parse_epub_nav_toc(opf_dir, nav_member_name, zipf):
    if not nav_member_name:
        return []
    try:
        with zipf.open(nav_member_name) as handle:
            root = ET.parse(handle).getroot()
    except Exception:
        return []
    nav_candidates = []
    for elem in root.iter():
        if xml_local_name(elem.tag).lower() != "nav":
            continue
        epub_type = (elem.get("{http://www.idpf.org/2007/ops}type") or elem.get("epub:type") or elem.get("type") or "").lower()
        role = (elem.get("role") or "").lower()
        nav_candidates.append((("toc" in epub_type) or ("doc-toc" in epub_type) or (role == "doc-toc"), elem))
    selected = None
    for is_toc, elem in nav_candidates:
        if is_toc:
            selected = elem
            break
    if selected is None and nav_candidates:
        selected = nav_candidates[0][1]
    if selected is None:
        return []
    ol = None
    for child in list(selected):
        if xml_local_name(child.tag).lower() == "ol":
            ol = child
            break
    if ol is None:
        for child in selected.iter():
            if xml_local_name(child.tag).lower() == "ol":
                ol = child
                break
    if ol is None:
        return []
    items = parse_epub_nav_items(ol)
    normalized = []
    for item in items:
        href = epub_href_to_path(opf_dir, item.get("href", ""))
        if not href:
            continue
        normalized.append({
            "label": item.get("label", "Untitled"),
            "href": href,
            "subitems": item.get("subitems", []),
        })
    return normalized

def parse_epub_ncx_toc(opf_dir, ncx_member_name, zipf):
    if not ncx_member_name:
        return []
    try:
        with zipf.open(ncx_member_name) as handle:
            root = ET.parse(handle).getroot()
    except Exception:
        return []
    nav_map = None
    for elem in root.iter():
        if xml_local_name(elem.tag).lower() == "navmap":
            nav_map = elem
            break
    if nav_map is None:
        return []
    def parse_navpoints(container):
        out = []
        for navpoint in [child for child in list(container) if xml_local_name(child.tag).lower() == "navpoint"]:
            label = ""
            content = None
            for child in list(navpoint):
                tag = xml_local_name(child.tag).lower()
                if tag == "navlabel":
                    label = " ".join("".join(child.itertext()).split())
                elif tag == "content":
                    content = child
            href = epub_href_to_path(opf_dir, content.get("src", "") if content is not None else "")
            if href:
                out.append({
                    "label": label or os.path.splitext(os.path.basename(urlsplit(href).path or ""))[0] or "Untitled",
                    "href": href,
                    "subitems": parse_navpoints(navpoint),
                })
        return out
    return parse_navpoints(nav_map)

def ensure_epub_cache(path):
    paths = get_epub_cache_paths(path)
    manifest_path = paths["manifest_path"]
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    os.makedirs(READER_EPUB_CACHE_ROOT, exist_ok=True)
    os.makedirs(paths["root"], exist_ok=True)
    try:
        with zipfile.ZipFile(path) as zipf:
            namelist = zipf.namelist()
            lower_names = {name.lower(): name for name in namelist}
            container_name = lower_names.get("meta-inf/container.xml")
            opf_name = ""
            opf_dir = ""
            if container_name:
                with zipf.open(container_name) as handle:
                    container_root = ET.parse(handle).getroot()
                ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
                rootfile = container_root.find(".//c:rootfile", ns)
                if rootfile is not None:
                    opf_name = (rootfile.get("full-path") or "").strip()
            if not opf_name:
                opf_candidates = [name for name in namelist if name.lower().endswith(".opf")]
                opf_candidates.sort(key=lambda item: item.lower())
                if opf_candidates:
                    opf_name = opf_candidates[0]
            if not opf_name:
                raise RuntimeError("EPUB package document not found")
            opf_name = epub_href_to_zip_member("", opf_name)
            opf_dir = posixpath.dirname(opf_name)
            with zipf.open(opf_name) as handle:
                opf_root = ET.parse(handle).getroot()
            package_ns = opf_root.tag.split("}", 1)[0].strip("{") if "}" in opf_root.tag else ""
            ns = {
                "opf": package_ns or "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }
            manifest_items = {}
            nav_member = ""
            ncx_member = ""
            for item in opf_root.findall(".//opf:manifest/opf:item", ns):
                item_id = (item.get("id") or "").strip()
                href = (item.get("href") or "").strip()
                media_type = (item.get("media-type") or "").strip()
                properties = (item.get("properties") or "").strip()
                resolved = epub_href_to_zip_member(opf_dir, href)
                manifest_items[item_id] = {
                    "id": item_id,
                    "href": resolved,
                    "mediaType": media_type,
                    "properties": properties,
                }
                if "nav" in properties.split():
                    nav_member = resolved
                if media_type == "application/x-dtbncx+xml":
                    ncx_member = resolved
            spine = opf_root.find(".//opf:spine", ns)
            spine_items = []
            if spine is not None:
                toc_id = (spine.get("toc") or "").strip()
                if toc_id and toc_id in manifest_items and not ncx_member:
                    ncx_member = manifest_items[toc_id]["href"]
                for itemref in spine.findall("opf:itemref", ns):
                    idref = (itemref.get("idref") or "").strip()
                    manifest_item = manifest_items.get(idref)
                    if not manifest_item:
                        continue
                    href = manifest_item.get("href", "")
                    if not href:
                        continue
                    spine_items.append(manifest_item)
            toc_items = parse_epub_nav_toc(opf_dir, nav_member, zipf)
            if not toc_items:
                toc_items = parse_epub_ncx_toc(opf_dir, ncx_member, zipf)
            toc_index_by_href = {}
            def flatten_toc(items):
                out = []
                for item in items or []:
                    href = normalize_reader_relpath(item.get("href", ""))
                    if href and href not in toc_index_by_href:
                        toc_index_by_href[href] = item.get("label", "")
                    out.append(item)
                    out.extend(flatten_toc(item.get("subitems", [])))
                return out
            flatten_toc(toc_items)
            chapters = []
            for index, manifest_item in enumerate(spine_items):
                href = normalize_reader_relpath(manifest_item.get("href", ""))
                if not href:
                    continue
                label = toc_index_by_href.get(href) or os.path.splitext(os.path.basename(urlsplit(href).path or ""))[0] or f"第 {index + 1} 章"
                zip_member = epub_href_to_zip_member("", href)
                size = 0
                try:
                    size = zipf.getinfo(zip_member).file_size
                except Exception:
                    size = 0
                chapters.append({
                    "label": label,
                    "href": href,
                    "size": size,
                    "mediaType": manifest_item.get("mediaType", ""),
                })
            title = ""
            for child in opf_root.iter():
                if xml_local_name(child.tag).lower() == "title":
                    title = " ".join("".join(child.itertext()).split())
                    if title:
                        break
            if not title:
                title = os.path.splitext(os.path.basename(path))[0]
            manifest = {
                "cacheKey": paths["key"],
                "sourcePath": os.path.realpath(path),
                "title": title,
                "start_path": chapters[0]["href"] if chapters else "",
                "toc": toc_items,
                "chapters": chapters,
                "count": len(chapters),
            }
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
            return manifest
    except Exception:
        raise

def build_chm_cache_key(path):
    st = os.stat(path)
    payload = json.dumps(
        {
            "path": os.path.realpath(path),
            "mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000)),
            "size": st.st_size,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def get_chm_cache_paths(path):
    cache_key = build_chm_cache_key(path)
    root = os.path.join(READER_CHM_CACHE_ROOT, cache_key)
    return {
        "key": cache_key,
        "root": root,
        "files_root": os.path.join(root, "files"),
        "manifest_path": os.path.join(root, "manifest.json"),
    }

def remove_tree(path):
    if not os.path.isdir(path):
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False):
        for filename in filenames:
            os.unlink(os.path.join(dirpath, filename))
        for dirname in dirnames:
            os.rmdir(os.path.join(dirpath, dirname))
    os.rmdir(path)

def resolve_existing_reader_path(root, relpath):
    clean_path, _, _ = split_reader_relpath(relpath)
    if not clean_path:
        return ""
    exact_path = os.path.join(root, clean_path.replace("/", os.sep))
    if os.path.exists(exact_path):
        return clean_path
    target = clean_path.lower()
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            rel = os.path.relpath(os.path.join(dirpath, filename), root).replace(os.sep, "/")
            if rel.lower() == target:
                return rel
    return ""

def find_chm_start_path(files_root, manifest):
    default_topic = resolve_existing_reader_path(files_root, manifest.get("default_topic", ""))
    if default_topic:
        return default_topic

    toc = manifest.get("toc", [])
    if toc:
        toc_path = resolve_existing_reader_path(files_root, toc[0].get("href", ""))
        if toc_path:
            return toc_path

    preferred = ("index.html", "index.htm", "default.html", "default.htm", "start.html", "start.htm")
    for candidate in preferred:
        resolved = resolve_existing_reader_path(files_root, candidate)
        if resolved:
            return resolved

    html_candidates = []
    for dirpath, _, filenames in os.walk(files_root):
        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext in (".html", ".htm", ".xhtml"):
                rel = os.path.relpath(os.path.join(dirpath, filename), files_root).replace(os.sep, "/")
                html_candidates.append(rel)
    html_candidates.sort(key=lambda item: item.lower())
    return html_candidates[0] if html_candidates else ""

def ensure_chm_cache(path):
    paths = get_chm_cache_paths(path)
    manifest_path = paths["manifest_path"]
    if os.path.isfile(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    os.makedirs(READER_CHM_CACHE_ROOT, exist_ok=True)
    tmp_root = f"{paths['root']}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
    files_root = os.path.join(tmp_root, "files")
    os.makedirs(files_root, exist_ok=True)
    try:
        result = subprocess.run(
            ["7z", "x", "-y", f"-o{files_root}", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stdout.strip() or "7z extraction failed")

        manifest = {
            "cacheKey": paths["key"],
            "sourcePath": os.path.realpath(path),
            "title": "",
            "default_topic": "",
            "contents_file": "",
            "toc": [],
            "start_path": "",
        }
        hhp_files = []
        hhc_files = []
        for dirpath, _, filenames in os.walk(files_root):
            for filename in filenames:
                lower = filename.lower()
                rel = os.path.relpath(os.path.join(dirpath, filename), files_root).replace(os.sep, "/")
                if lower.endswith(".hhp"):
                    hhp_files.append(rel)
                elif lower.endswith(".hhc"):
                    hhc_files.append(rel)

        hhp_files.sort(key=lambda item: item.lower())
        hhc_files.sort(key=lambda item: item.lower())

        for rel in hhp_files:
            metadata = parse_chm_hhp(os.path.join(files_root, rel.replace("/", os.sep)))
            if metadata.get("title") and not manifest["title"]:
                manifest["title"] = metadata["title"]
            if metadata.get("default_topic") and not manifest["default_topic"]:
                manifest["default_topic"] = metadata["default_topic"]
            if metadata.get("contents_file") and not manifest["contents_file"]:
                manifest["contents_file"] = metadata["contents_file"]
            if manifest["default_topic"] and manifest["contents_file"]:
                break

        toc_candidates = []
        if manifest["contents_file"]:
            toc_candidates.append(manifest["contents_file"])
        toc_candidates.extend(hhc_files)
        seen = set()
        for rel in toc_candidates:
            resolved = resolve_existing_reader_path(files_root, rel)
            if not resolved or resolved in seen:
                continue
            seen.add(resolved)
            toc = parse_chm_toc(os.path.join(files_root, resolved.replace("/", os.sep)))
            if toc:
                manifest["toc"] = toc
                break

        manifest["start_path"] = find_chm_start_path(files_root, manifest)
        if not manifest["title"]:
            manifest["title"] = os.path.splitext(os.path.basename(path))[0]
        if not manifest["start_path"]:
            raise RuntimeError("No HTML entry point found in CHM archive")

        if os.path.isdir(paths["root"]):
            if os.path.isfile(manifest_path):
                with open(manifest_path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            remove_tree(paths["root"])
        os.replace(tmp_root, paths["root"])
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
        return manifest
    except Exception:
        try:
            remove_tree(tmp_root)
        except OSError:
            pass
        raise

def get_reader_chm_content_root(path):
    cache_paths = get_chm_cache_paths(path)
    manifest = ensure_chm_cache(path)
    return cache_paths["files_root"], manifest

def resolve_chm_content_file(root, manifest, relpath=""):
    requested = relpath or manifest.get("start_path", "")
    clean_path = resolve_existing_reader_path(root, requested) if requested else manifest.get("start_path", "")
    if not clean_path:
        return "", ""
    real_path = os.path.realpath(os.path.join(root, clean_path.replace("/", os.sep)))
    if not real_path.startswith(os.path.realpath(root) + os.sep) and real_path != os.path.realpath(root):
        return "", ""
    if os.path.isdir(real_path):
        for candidate in ("index.html", "index.htm", "default.html", "default.htm"):
            nested = resolve_existing_reader_path(root, f"{clean_path.rstrip('/')}/{candidate}")
            if nested:
                clean_path = nested
                real_path = os.path.realpath(os.path.join(root, clean_path.replace("/", os.sep)))
                break
    if not os.path.isfile(real_path):
        return "", ""
    return real_path, clean_path

def get_reader_format_from_name(name, mtype=""):
    ext = os.path.splitext(name or "")[1].lower().lstrip(".")
    if ext in READER_SUPPORTED_FORMATS:
        return ext
    mtype = (mtype or "").lower()
    mimetype_map = {
        "text/plain": "txt",
        "text/markdown": "md",
        "text/html": "html",
        "application/xhtml+xml": "xhtml",
        "application/xml": "xml",
        "application/pdf": "pdf",
        "application/epub+zip": "epub",
        "application/x-fictionbook+xml": "fb2",
        "application/fb2+xml": "fb2",
        "application/vnd.ms-htmlhelp": "chm",
        "application/x-chm": "chm",
        "application/vnd.amazon.ebook": "azw3",
        "application/x-mobipocket-ebook": "mobi",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "multipart/related": "mhtml",
        "application/x-cbz": "cbz",
        "application/x-cbr": "cbr",
        "application/x-cbt": "cbt",
        "application/x-cb7": "cb7",
    }
    return mimetype_map.get(mtype, "")

def get_doc_display_name(doc):
    name = ""
    try:
        name = getattr(doc, "filename", "") or ""
    except Exception:
        name = ""
    if not name:
        try:
            name = os.path.basename(normalize_file_path(getattr(doc, "url", "") or ""))
        except Exception:
            name = ""
    return name

def is_reader_supported_doc(doc):
    name = get_doc_display_name(doc)
    return get_reader_format_from_name(name, getattr(doc, "mtype", "")) in READER_SUPPORTED_FORMATS

def get_reader_payload_path(doc):
    url = normalize_file_path(getattr(doc, "url", "") or "")
    if url and os.path.exists(url):
        return url
    xt = rclextract.Extractor(doc)
    return normalize_file_path(xt.idoctofile(doc.ipath, doc.mimetype))

def is_allowed_reader_path(path, config):
    if not path:
        return False
    real_path = os.path.realpath(path)
    for d in config["dirs"]:
        try:
            root = os.path.realpath(d)
            if os.path.commonpath([real_path, root]) == root:
                return True
        except Exception:
            continue
    return False

def make_reader_token(path, mimetype="", name=""):
    return appdata.token_for_path(path, mimetype, name)

def read_reader_token(token):
    return appdata.token_payload(token)

def resolve_doc_from_result_query(resnum, query=None):
    config = get_config()
    q = query or get_query(config)
    appdata.search_event(config, q)
    rclq, db, _ = recoll_initsearch(q)
    if "rcludi" in q and q["rcludi"]:
        doc = db.getDoc(q["rcludi"])
    else:
        if resnum < 0 or resnum > rclq.rowcount - 1:
            bottle.abort(404, "Bad result index %d" % resnum)
        rclq.scroll(resnum)
        doc = rclq.fetchone()
    if not doc:
        bottle.abort(404, "Document not found")
    source = normalize_file_path(getattr(doc, 'url', '') or '')
    if not is_allowed_reader_path(source, config):
        bottle.abort(403, 'Document access denied')
    return config, q, rclq, db, doc

def get_reader_path_for_doc(doc, config):
    path = get_reader_payload_path(doc)
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    return path

def build_reader_item(doc, config, path):
    name = get_doc_display_name(doc)
    reader_format = get_reader_format_from_name(name, getattr(doc, "mtype", ""))
    token = make_reader_token(path, getattr(doc, "mtype", ""), name)
    item = {
        "name": name,
        "title": select([getattr(doc, "title", ""), name, "?"], [None, ""]),
        "author": getattr(doc, "author", "") or "",
        "path": path,
        "format": reader_format,
        "size": int(getattr(doc, "size", 0) or getattr(doc, "fbytes", 0) or 0),
        "mtime": int(getattr(doc, "mtime", 0) or 0),
        "token": token,
        "url": account_web.app_url(f"/api/reader/file/{urlquote(token)}"),
        "supported": reader_format in READER_SUPPORTED_FORMATS,
    }
    if reader_format == "txt":
        item["manifestUrl"] = account_web.app_url(f"/api/reader/txt/{urlquote(token)}/manifest")
    elif reader_format == "chm":
        item["manifestUrl"] = account_web.app_url(f"/api/reader/chm/{urlquote(token)}/manifest")
    elif reader_format == "epub":
        item["manifestUrl"] = account_web.app_url(f"/api/reader/epub/{urlquote(token)}/manifest")
    return appdata.reader_metadata(item, doc=doc, config=config)

def scan_reader_folder(current_doc, config):
    current_path = get_reader_path_for_doc(current_doc, config)
    root_dir = os.path.dirname(current_path)
    items = []
    seen = set()
    truncated = False
    current_item = None

    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".")])
        for filename in sorted([f for f in filenames if not f.startswith(".")]):
            path = os.path.realpath(os.path.join(dirpath, filename))
            if path in seen:
                continue
            if not os.path.isfile(path):
                continue
            reader_format = get_reader_format_from_name(filename)
            if reader_format not in READER_SUPPORTED_FORMATS:
                continue
            if not is_allowed_reader_path(path, config):
                continue
            seen.add(path)
            if len(items) >= READER_FOLDER_SCAN_LIMIT:
                truncated = True
                continue
            item = {
                "name": filename,
                "title": os.path.splitext(filename)[0],
                "author": "",
                "path": path,
                "root": root_dir,
                "format": reader_format,
                "size": os.path.getsize(path),
                "mtime": int(os.path.getmtime(path)),
                "token": make_reader_token(path, "", filename),
                "url": "",
                "supported": True,
            }
            item["url"] = account_web.app_url(f"/api/reader/file/{urlquote(item['token'])}")
            if reader_format == "txt":
                item["manifestUrl"] = account_web.app_url(f"/api/reader/txt/{urlquote(item['token'])}/manifest")
            elif reader_format == "chm":
                item["manifestUrl"] = account_web.app_url(f"/api/reader/chm/{urlquote(item['token'])}/manifest")
            elif reader_format == "epub":
                item["manifestUrl"] = account_web.app_url(f"/api/reader/epub/{urlquote(item['token'])}/manifest")
            if path == current_path:
                current_item = item
            items.append(item)

    items.sort(key=lambda item: os.path.relpath(item["path"], root_dir).lower())
    current_index = 0
    for idx, item in enumerate(items):
        if item["path"] == current_path:
            current_index = idx
            current_item = item
            break

    if current_item is None and os.path.isfile(current_path):
        current_name = os.path.basename(current_path)
        current_format = get_reader_format_from_name(current_name)
        if current_format in READER_SUPPORTED_FORMATS:
            current_item = {
                "name": current_name,
                "title": os.path.splitext(current_name)[0],
                "author": "",
                "path": current_path,
                "root": root_dir,
                "format": current_format,
                "size": os.path.getsize(current_path),
                "mtime": int(os.path.getmtime(current_path)),
                "token": make_reader_token(current_path, "", current_name),
                "url": "",
                "supported": True,
            }
            current_item["url"] = account_web.app_url(f"/api/reader/file/{urlquote(current_item['token'])}")
            if current_format == "txt":
                current_item["manifestUrl"] = account_web.app_url(f"/api/reader/txt/{urlquote(current_item['token'])}/manifest")
            elif current_format == "chm":
                current_item["manifestUrl"] = account_web.app_url(f"/api/reader/chm/{urlquote(current_item['token'])}/manifest")
            elif current_format == "epub":
                current_item["manifestUrl"] = account_web.app_url(f"/api/reader/epub/{urlquote(current_item['token'])}/manifest")
            items.insert(0, current_item)
            current_index = 0
            if len(items) > READER_FOLDER_SCAN_LIMIT:
                items.pop()
                truncated = True

    folder_key = appdata.folder_key(root_dir)
    for item in items:
        appdata.reader_metadata(item, config=config, folder_key=folder_key)
    resume = account_store.get_folder_resume(account_web.current_user()['id'], folder_key)
    resume_warning = ''
    if resume and bottle.request.query.get('resume') != '0':
        for index, item in enumerate(items):
            if item['documentId'] == resume['last_document_id']:
                current_index = index
                break
        else:
            try:
                resumed = appdata.document(resume['last_document_id'])
                if os.path.commonpath([resumed['source_path'], root_dir]) == root_dir:
                    resumed_doc = appdata.as_recoll_doc(resumed['id'])
                    resumed_item = build_reader_item(resumed_doc, config, resumed['source_path'])
                    appdata.reader_metadata(resumed_item, config=config, folder_key=folder_key)
                    items.insert(0, resumed_item)
                    current_index = 0
                    if len(items) > READER_FOLDER_SCAN_LIMIT:
                        items.pop()
                        truncated = True
                else:
                    resume_warning = '上次阅读的文件已不在该目录中，已打开本次选择的文件。'
            except (NotFound, ValidationError, OSError):
                resume_warning = '上次阅读的文件已不可用，已打开本次选择的文件。'
    return {
        "root": root_dir,
        "folderKey": folder_key,
        "resumeWarning": resume_warning,
        "currentIndex": current_index,
        "items": items,
        "truncated": truncated,
        "count": len(items),
    }

# Get the database directory from recoll.conf, defaults to confdir/xapiandb. Note
# that this is available as getDbDir() from recoll 1.27 (2020)
def get_dbdir(confdir):
    confdir = os.path.expanduser(confdir)
    rclconf = rclconfig.RclConfig(confdir)
    try:
        dbdir = rclconf.getDbDir()
    except:
        dbdir = rclconf.getConfParam('dbdir')
        if not dbdir:
            dbdir = 'xapiandb'
        if not os.path.isabs(dbdir):
            cachedir = rclconf.getConfParam('cachedir')
            if not cachedir:
                cachedir = confdir
            dbdir = os.path.join(cachedir, dbdir)
    # recoll API expects bytes, not strings
    return os.path.normpath(dbdir).encode(g_fscharset)

#}}}
def commonpathprefix(paths):
    if len(paths) == 0:
        return ""
    common = [p for p in paths[0].split("/") if len(p)]
    for path in paths[1:]:
        # Keep only the paths elements at the start which are common with the currently
        # calculated common part
        np = [p for p in path.split("/") if len(p)]
        nc = []
        for i in range(len(np)):
            if i >= len(common) or np[i] != common[i]:
                break
            nc.append(np[i])
        if len(nc) == 0:
            return ""
        common = nc
    return "/" + "/".join(common) + "/"

#{{{ get_config
def get_config():
    if 'recoll.config' in bottle.request.environ:
        return bottle.request.environ['recoll.config']
    config = {}
    envdir = bottle.request.environ.get('RECOLL_CONFDIR') or safe_envget('RECOLL_CONFDIR')
    # get useful things from recoll.conf
    rclconf = rclconfig.RclConfig(envdir)
    config['confdir'] = rclconf.getConfDir()
    topdirs = [os.path.expanduser(d) for d in shlex.split(rclconf.getConfParam('topdirs'))]
    config['dirs'] = dict.fromkeys(topdirs, config['confdir'])
    config['commonprefix'] = commonpathprefix(topdirs)
    # add topdirs from extra config dirs
    extraconfdirs = bottle.request.environ.get('RECOLL_EXTRACONFDIRS') or safe_envget('RECOLL_EXTRACONFDIRS')
    if extraconfdirs:
        config['extraconfdirs'] = shlex.split(extraconfdirs)
        for e in config['extraconfdirs']:
            config['dirs'].update(dict.fromkeys([os.path.expanduser(d) for d in
                shlex.split(get_topdirs(e))],e))
        config['extradbs'] = list(map(get_dbdir, config['extraconfdirs']))
    else:
        config['extraconfdirs'] = None
        config['extradbs'] = None
    config['stemlang'] = rclconf.getConfParam('indexstemminglanguages')

    # Possibly adjust user config defaults with data from recoll.conf. Some defaults which are
    # generally suitable like dirdepth=2 can be unworkable on big data sets (causing init errors so
    # that they can't even be adjusted from the UI). The 2nd parameter asks for an int conversion
    fetches = [("context", 1), ("stem", 1),("timefmt", 0),("dirdepth", 1),("maxchars", 1),
               ("maxresults", 1), ("perpage", 1), ("csvfields", 0), ("title_link", 0),
               ("collapsedups", 1), ("synonyms", 0), ("noresultlinks", 1), ("logquery", 1),
               ("shortenpaths", 1), ("permlinks", 1), ("res_permlink", 1), ("queryfrag", 0),
               ]
    defaults = dict(DEFAULTS)
    for k, isint in fetches:
        value = rclconf.getConfParam("webui_" + k)
        if value is not None:
            defaults[k] = int(value) if isint else value
    config.update(defaults)
    config['locked_search_options'] = (
        {k: defaults[k] for k in ('stem', 'collapsedups')}
        if int(rclconf.getConfParam('webui_nosettings') or 0) else {})
    # Only validated account preferences can override administrator defaults.
    preferences = account_web.current_settings().get('preferences', {})
    editable = {'stem', 'collapsedups', 'maxresults', 'perpage', 'context', 'maxchars',
                'timefmt', 'dirdepth', 'title_link', 'csvfields', 'shortenpaths'}
    if not int(rclconf.getConfParam('webui_nosettings') or 0):
        config.update({k: v for k, v in preferences.items() if k in editable})
    # Fix csvfields: get rid of invalid ones to avoid needing tests in the dump function
    cf = config['csvfields'].split()
    ncf = [f for f in cf if f in FIELDS]
    config['csvfields'] = ' '.join(ncf)
    config['fields'] = ' '.join(FIELDS)
    # get mountpoints
    config['mounts'] = {}
    for d in config['dirs']:
        name = 'mount_%s_%s' % (account_web.current_user()['id'], urlquote(d,''))
        cookie_value = bottle.request.get_cookie(name)
        mount_value = account_web.read_payload(cookie_value, 'device-mount') if cookie_value else None
        config['mounts'][d] = select([mount_value.get('url') if mount_value else None,
                                      rclconf.getConfParam(f"webui_mount_{d}"),
                                      f"file://{d}"],
                                     [None, ''])

    # Parameters set by the admin in the recoll configuration
    # file. These override anything else, so read them last
    val = rclconf.getConfParam('webui_nojsoncsv')
    val = 0 if val is None else int(val)
    config['rclc_nojsoncsv'] = val

    val = rclconf.getConfParam('webui_maxperpage')
    val = 0 if val is None else int(val)
    config['rclc_maxperpage'] = val
    if val:
        if config['perpage'] == 0 or config['perpage'] > val:
            config['perpage'] = val

    val = rclconf.getConfParam('webui_nosettings')
    val = 0 if val is None else int(val)
    config['rclc_nosettings'] = val

    val = preferences.get('defaultsort') if not config['rclc_nosettings'] else None
    val = val or str(rclconf.getConfParam('webui_defaultsort'))
    config['defsortidx'] = 0
    for i in range(len(SORTS)):
        if SORTS[i][0] == val or SORTS[i][1] == val:
            config['defsortidx'] = i
            break
    bottle.request.environ['recoll.config'] = config
    return config
#}}}
#{{{ get_dirs
def get_dirs(tops, depth):
    v = []
    for top in tops:
        # We do the conversion to bytes here, because Python versions
        # before 3.7 won't do the right thing if the locale is C,
        # which would be the case with a default apache install
        top = top.encode('utf-8', 'surrogateescape')
        dirs = [top]
        for d in range(1, depth+1):
            dirs = dirs + glob.glob(top + b'/*' * d)
        dirs = filter(lambda f: os.path.isdir(f), dirs)
        top_path = top.rsplit(b'/', 1)[0]
        dirs = [w.replace(top_path+b'/', b'', 1) for w in dirs]
        v = v + dirs
    for i in range(len(v)):
        v[i] = v[i].decode('utf-8', 'surrogateescape')
    return ['<all>'] + v
#}}}
#{{{ get_query
def get_query(config=None, params=None):
    defsortidx = config['defsortidx'] if config and 'defsortidx' in config else 0
    params = bottle.request.query if params is None else params
    if hasattr(params, 'decode'):
        try:
            params = params.decode('utf-8')
        except UnicodeError:
            bottle.abort(400, '检索条件必须使用 UTF-8 编码')
    query = {
        'query': select([params.get('query'), '']),
        'before': select([params.get('before'), '']),
        'after': select([params.get('after'), '']),
        'dir': select([params.get('dir'), '', '<all>'], [None, '']),
        'sort': select([params.get('sort'), SORTS[defsortidx][0]], [None, '']),
        'ascending': select_int([params.get('ascending'), 0], default=0),
        # Reader runtimes may write non-numeric positions like "121:06" into the
        # browser URL. Treat invalid search page values as "first page" instead
        # of failing the whole request.
        'page': select_int([params.get('page'), 0], default=0, minimum=0),
        'highlight': select_int([params.get('highlight'), 1], default=1),
        'snippets': select_int([params.get('snippets'), 1], default=1),
    }
    if params.get('rcludi'):
        query['rcludi'] = params.get('rcludi')
    if params.get('search_id'):
        query['search_id'] = params.get('search_id')
    #msg("query['query'] : %s" % query['query'])
    return query
#}}}
#{{{ query_to_recoll_string
def query_to_recoll_string(q):
    qs = q['query']
    if len(q['after']) > 0 or len(q['before']) > 0:
        qs += " date:%s/%s" % (q['after'], q['before'])
    qdir = q['dir']
    if qdir != '<all>':
        qs += " dir:\"%s\" " % qdir
    return qs
#}}}
#{{{ recoll_initsearch
def recoll_initsearch(q):
    config = get_config()
    try:
        appdata.validate_query(q)
    except ValidationError as exc:
        bottle.abort(400, str(exc))
    confdir = config['confdir']
    dbs = []
    """ The reason for this somewhat elaborate scheme is to keep the
    set size as small as possible by searching only those databases
    with matching topdirs """
    if q['dir'] == '<all>':
        if config['extraconfdirs']:
            dbs.extend(map(get_dbdir,config['extraconfdirs']))
    else:
        confdirs = []
        for d,conf in config['dirs'].items():
            tdbasename = os.path.basename(d)
            if os.path.commonprefix([tdbasename, q['dir']]) == tdbasename:
                confdirs.append(conf)
        if len(confdirs) == 0:
            # should not happen, using non-existing q['dir']?
            bottle.abort(400, 'no matching database for search directory ' + q['dir'])
        elif len(confdirs) == 1:
            # only one config (most common situation)
            confdir = confdirs[0]
        else:
            # more than one config with matching topdir, use 'm all
            confdir = confdirs[0]
            dbs.extend(map(get_dbdir, confdirs[1:]))

    if config['extradbs']:
        dbs.extend(config['extradbs'])

    db = recoll.connect(confdir, extra_dbs=dbs)

    # Compare to "None" because of the conv. to str done while setting from cookies
    if config["synonyms"] and config["synonyms"] != "None":
        try:
            db.setSynonymsFile(config["synonyms"])
        except:
            # Only supported from recoll 1.40.3, just ignore the error for now
            msg(f"Setting synonyms to [{config['synonyms']}] failed")
            pass

    db.setAbstractParams(config['maxchars'], config['context'])
    query = db.query()
    query.sortby(q['sort'], q['ascending'])
    qs = ""
    try:
        qs = query_to_recoll_string(q)
        if "queryfrag" in config and config["queryfrag"]:
            qs += " " + config["queryfrag"]
        if "logquery" in config and config["logquery"]:
            msg(f"Query: {qs}")
        query.execute(qs, config['stem'], config['stemlang'],
                      collapseduplicates=config['collapsedups'])
    except Exception as ex:
        # Do not turn execution errors into successful zero-result history.
        bottle.abort(400, '查询执行失败，请检查检索表达式')
    return query, db, qs
#}}}
#{{{ HlMeths
class HlMeths:
    def startMatch(self, idx):
        return '<span class="search-result-highlight">'
    def endMatch(self):
        return '</span>'


def reader_match_terms(snippet, expression):
    """Carry literal matched words into the reader, never a Recoll expression.

    A highlighted excerpt includes stemming-expanded words as they occur in the
    document. When snippets are disabled, use positive literal query terms and
    leave metadata-only searches without a misleading content-hit action.
    """
    class Matches(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.parts = []
            self.terms = []

        def handle_starttag(self, tag, attrs):
            if self.depth:
                self.depth += 1
            elif 'search-result-highlight' in dict(attrs).get('class', '').split():
                self.depth = 1
                self.parts = []

        def handle_endtag(self, tag):
            if self.depth:
                self.depth -= 1
                if not self.depth:
                    self.terms.append(''.join(self.parts))

        def handle_data(self, data):
            if self.depth:
                self.parts.append(data)

    parser = Matches()
    parser.feed(snippet or '')
    terms = parser.terms
    if not terms:
        try:
            tokens = shlex.split(expression or '')
        except ValueError:
            tokens = []
        negate = False
        for token in tokens:
            token = token.strip('()')
            if token.upper() in ('AND', 'OR'):
                continue
            if token.upper() == 'NOT':
                negate = True
                continue
            if negate or token.startswith('-'):
                negate = False
                continue
            if ':' in token or '*' in token or '?' in token:
                continue
            terms.append(token)
    return list(dict.fromkeys(term.strip()[:200] for term in terms if term.strip()))[:16]
#}}}
#{{{ snippet cache
_g_snippet_cache = collections.OrderedDict()
_g_snippet_cache_lock = threading.Lock()

def get_cached_snippet(key):
    with _g_snippet_cache_lock:
        snippet = _g_snippet_cache.pop(key, None)
        if snippet is not None:
            _g_snippet_cache[key] = snippet
        return snippet

def put_cached_snippet(key, snippet):
    with _g_snippet_cache_lock:
        _g_snippet_cache[key] = snippet
        while len(_g_snippet_cache) > SNIPPET_CACHE_SIZE:
            _g_snippet_cache.popitem(last=False)
#}}}
#{{{ recoll_search
def recoll_search(q):
    config = get_config()
    tstart = datetime.datetime.now()
    results = []
    query, _, qs = recoll_initsearch(q)
    nres = query.rowcount
    if "rcludi" in q and q["rcludi"]:
        rcludi = q["rcludi"]
        nres = 1
        q['page'] = 1
    else:
        rcludi = None
    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if nres > config['maxresults']:
        nres = config['maxresults']
    if config['perpage'] == 0 or q['page'] == 0:
        config['perpage'] = nres
        q['page'] = 1
    # "Show all" is still bounded by the administrator's HTML page limit.
    # Explicit JSON/CSV exports retain their existing full-result semantics.
    if bottle.request.path not in ('/json', '/csv') and config['rclc_maxperpage']:
        config['perpage'] = min(config['perpage'], config['rclc_maxperpage'])
    offset = (q['page'] - 1) * config['perpage']

    if query.rowcount > 0 and offset < nres:
        if type(query.next) == int:
            query.next = offset
        else:
            query.scroll(offset, mode='absolute')

    if 'highlight' in q and q['highlight']:
        highlighter = HlMeths()
    else:
        highlighter = None

    udibreak = False
    while len(results) < min(config['perpage'], max(0, nres - offset)):
        try:
            doc = query.fetchone()
            # Later Recoll versions return None at EOL instead of
            # exception This change restores conformance to PEP 249
            # Python Database API Specification
            if not doc:
                break
            if rcludi:
                if doc['rcludi'] == rcludi:
                    udibreak = True
                else:
                    continue
        except:
            break
        d = {}
        for f in FIELDS:
            v = getattr(doc, f)
            if v is not None:
                d[f] = v
            else:
                d[f] = ''
        d['source_url'] = d['url']
        if doc['mtype'] == "application/pdf" and doc['url'].startswith("file://"):
            try:
                # Note: getfirstmatchpage is only available from recoll 1.44
                pagenum, term = query.getfirstmatchpage(doc)
                d['url'] += f"#page={pagenum}&search={urlquote(term)}"
                if int(pagenum) > 0:
                    d['reader_match_page'] = int(pagenum)
                d['reader_match_term'] = str(term or '')[:200]
            except:
                pass
        d['label'] = select([d['title'], d['filename'], '?'], [None, ''])
        d['sha'] = hashlib.sha1((d['url']+d['ipath']).encode('utf-8')).hexdigest()
        d['time'] = timestr(d['mtime'], config['timefmt'])
        d['rcludi'] = doc['rcludi']
        if 'snippets' in q and q['snippets']:
            snippet_key = (qs, config['stem'], config['stemlang'], config['maxchars'],
                           config['context'], bool(highlighter), d['url'], d['ipath'],
                           d['mtime'])
            snippet = get_cached_snippet(snippet_key)
            if snippet is None:
                if highlighter:
                    snippet = query.makedocabstract(doc, methods=highlighter)
                else:
                    snippet = query.makedocabstract(doc)
                if not snippet:
                    try:
                        snippet = html_escape(doc['abstract'] or '')
                    except:
                        snippet = ''
                if snippet:
                    put_cached_snippet(snippet_key, snippet)
            d['snippet'] = snippet or ''
        d['reader_match_terms'] = reader_match_terms(d.get('snippet', ''), q.get('query', ''))
        if not d.get('reader_match_term'):
            d['reader_match_term'] = next(iter(d['reader_match_terms']), '')
        #for n,v in d.items():
        #    print("type(%s) is %s" % (n,type(v)))
        results.append(d)
        if udibreak:
            break
    tend = datetime.datetime.now()
    return results, nres, tend - tstart
#}}}
#}}}
#{{{ routes
#{{{ static
@bottle.route('/static/:path#.+#')
def server_static(path):
    return bottle.static_file(path, root=os.path.join(APP_ROOT, 'static'))
#}}}
@bottle.route('/staticdoc/:path#.+#')
def server_staticdoc(path):
    # Legacy URLs never expose an entire process-wide temporary directory.
    payload = read_reader_token(path)
    if not payload:
        bottle.abort(404, 'Document resource not found')
    return bottle.static_file(os.path.basename(payload['path']),
                              root=os.path.dirname(payload['path']), etag=False,
                              headers={'Cache-Control': 'private, no-store'})
#}}}
#{{{ main
@bottle.route('/')
@bottle.view('main')
def main():
    config = get_config()
    bottle.response.headers['Vary'] = 'Cookie'
    return { 'dirs': get_dirs(config['dirs'], config['dirdepth']),
            'query': get_query(config), 'sorts': SORTS, 'config': config,
            'recent_readings': account_store.list_readings(account_web.current_user()['id'], limit=5)['items']}
#}}}
#{{{ results
@bottle.route('/results')
@bottle.view('results')
def results():
    config = get_config()
    query = get_query(config)
    event = appdata.search_event(config, query)
    if not bottle.request.query.page:
        # A missing page parameter means "first page" for the html interface. Left
        # at 0 it would make recoll_search ignore webui_perpage and render the whole
        # result set (up to maxresults), which takes seconds of cpu on large
        # collections. An explicit page=0 keeps its "all results" meaning.
        query['page'] = 1
    qs = query_to_recoll_string(query)
    try:
        res, nres, timer = recoll_search(query)
    except (ValidationError, bottle.HTTPError):
        if event:
            account_store.fail_search(account_web.current_user()['id'], event['id'])
        bottle.abort(400, '查询执行失败，请检查检索表达式和筛选条件')
    appdata.finish_search(event, query, config, nres)
    for result in res:
        try:
            # Use the source URL before PDF search anchors were appended.
            source = result.get('source_url') or result['url'].split('#', 1)[0]
            ref = appdata.register_doc(SimpleNamespace(**dict(result, url=source)), config)
            result['document_id'] = ref['id']
            result['reader_supported'] = ref['format'] in READER_SUPPORTED_FORMATS
            result['shelf_handle'] = account_web.sign_payload({'document_id': ref['id']}, 'shelf-add')
            result['on_shelf'] = account_store.is_on_shelf(account_web.current_user()['id'], ref['id'])
        except (NotFound, ValidationError, OSError, sqlite3.Error):
            result['document_id'] = None
            bottle.request.environ['recoll.notice'] = '搜索已完成，部分书架操作暂不可用，请稍后重试。'
    if config['maxresults'] == 0:
        config['maxresults'] = nres
    if config['perpage'] == 0:
        config['perpage'] = nres
    bottle.response.headers['Vary'] = 'Cookie'
    bottle.response.headers['No-Vary-Search'] = 'key-order'
    return { 'res': res, 'time': timer, 'query': query, 'dirs':
             get_dirs(config['dirs'], config['dirdepth']),
             'qs': qs, 'sorts': SORTS, 'config': config,
             'query_string': bottle.request.query_string, 'nres': nres,
             'notice': bottle.request.environ.get('recoll.notice', '') or
                       ('本次搜索历史尚未保存。' if bottle.request.query.get('history_unsaved') else '')}
#}}}
#{{{ reader page
@bottle.route('/reader')
@bottle.view('reader')
def reader():
    mode = select([bottle.request.query.mode, "book"], [None, ""])
    if mode not in ("book", "folder"):
        bottle.abort(400, "Invalid reader mode")
    search_query = [
        (key, value)
        for key, value in parse_qsl(
            bottle.request.query_string,
            keep_blank_values=True,
            encoding="utf-8",
            errors="strict",
        )
        if key not in ("mode", "resnum", "document_id", "return_to", "reader_search", "reader_term", "reader_page")
    ]
    results_query_string = urlencode(search_query, doseq=True, encoding='utf-8')
    results_url = account_web.app_url('/results') + '?' + results_query_string
    return_to = bottle.request.query.getunicode('return_to', '')
    if return_to:
        results_url = account_web.safe_next(return_to)
    session = account_web.current_session()
    account = {
        'userId': account_web.current_user()['id'], 'sessionId': session['id'],
        'csrf': account_web.csrf_token(), 'settings': account_web.current_settings(),
        'baseUrl': account_web.app_url('/').rstrip('/'),
    }
    reader_config = {
        'mode': mode, 'queryString': bottle.request.query_string,
        'resultsQueryString': results_query_string,
        'apiBase': account_web.app_url('/api/reader'), 'account': account,
        'initialSearch': {
            'query': bottle.request.query.getunicode('reader_search', '')[:200],
            'terms': [str(term)[:200] for term in bottle.request.query.decode('utf-8').getall('reader_term')][:16],
            'pageNumber': select_int([bottle.request.query.get('reader_page')], default=0, minimum=0),
        },
    }
    return {
        "mode": mode,
        "query_string": bottle.request.query_string,
        "results_query_string": results_query_string,
        "results_url": results_url,
        'reader_config_json': json.dumps(reader_config, ensure_ascii=True).replace('<', '\\u003c'),
    }
#}}}
#{{{ reader book api
@bottle.route('/api/reader/book')
def reader_book():
    resnum = select_int([bottle.request.query.resnum, -1], default=-1)
    if bottle.request.query.get('document_id'):
        try:
            config = get_config()
            doc = appdata.as_recoll_doc(bottle.request.query.document_id)
        except (NotFound, ValidationError) as exc:
            bottle.abort(404, str(exc))
    else:
        config, query, rclq, db, doc = resolve_doc_from_result_query(resnum)
    if not is_reader_supported_doc(doc):
        bottle.abort(400, "Unsupported format for reader")
    path = get_reader_path_for_doc(doc, config)
    item = build_reader_item(doc, config, path)
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps({
        "mode": "book",
        "book": item,
    })
#}}}
#{{{ reader folder api
@bottle.route('/api/reader/folder')
def reader_folder():
    resnum = select_int([bottle.request.query.resnum, -1], default=-1)
    if bottle.request.query.get('document_id'):
        try:
            config = get_config()
            doc = appdata.as_recoll_doc(bottle.request.query.document_id)
        except (NotFound, ValidationError) as exc:
            bottle.abort(404, str(exc))
    else:
        config, query, rclq, db, doc = resolve_doc_from_result_query(resnum)
    if not is_reader_supported_doc(doc):
        bottle.abort(400, "Unsupported format for reader")
    folder_data = scan_reader_folder(doc, config)
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps({
        "mode": "folder",
        "root": folder_data["root"],
        "folderKey": folder_data["folderKey"],
        "resumeWarning": folder_data["resumeWarning"],
        "currentIndex": folder_data["currentIndex"],
        "count": folder_data["count"],
        "truncated": folder_data["truncated"],
        "items": folder_data["items"],
    })
#}}}
#{{{ reader file api
@bottle.route('/api/reader/file/<token>')
def reader_file(token):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    name = payload.get("name") or os.path.basename(path)
    mimetype = payload.get("mimetype") or ""
    if not mimetype:
        ext = get_reader_format_from_name(name)
        mimetype_map = {
            "txt": "text/plain",
            "md": "text/markdown",
            "html": "text/html",
            "htm": "text/html",
            "xhtml": "application/xhtml+xml",
            "xml": "application/xml",
            "mhtml": "multipart/related",
            "epub": "application/epub+zip",
            "pdf": "application/pdf",
            "fb2": "application/x-fictionbook+xml",
            "chm": "application/vnd.ms-htmlhelp",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "mobi": "application/x-mobipocket-ebook",
            "azw": "application/vnd.amazon.ebook",
            "azw3": "application/vnd.amazon.ebook",
            "cbz": "application/x-cbz",
            "cbr": "application/x-cbr",
            "cbt": "application/x-cbt",
            "cb7": "application/x-cb7",
        }
        mimetype = mimetype_map.get(ext, "application/octet-stream")
    return bottle.static_file(os.path.basename(path), root=os.path.dirname(path),
                              mimetype=mimetype, etag=False,
                              headers={'Cache-Control': 'private, no-store'})
#}}}
#{{{ reader txt manifest api
@bottle.route('/api/reader/txt/<token>/manifest')
def reader_txt_manifest(token):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "txt":
        bottle.abort(400, "Not a TXT document")

    manifest = ensure_txt_cache(path)
    paths = get_txt_cache_paths(path)
    title = payload.get("name") or os.path.splitext(os.path.basename(path))[0]
    chunks = [
        {
            "index": item["index"],
            "start": item["start"],
            "end": item["end"],
            "byteLength": item["byteLength"],
        }
        for item in manifest.get("chunks", [])
    ]
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps(
        {
            "format": "txt",
            "title": title,
            "path": path,
            "size": manifest.get("size", 0),
            "charset": manifest.get("charset", "utf-8"),
            "chunkCount": manifest.get("chunkCount", 0),
            "chunkWindowMounted": 2,
            "chunkWindowPrefetch": 4,
            "estimatedCharsPerChunk": manifest.get("estimatedCharsPerChunk", 0),
            "chunks": chunks,
            "toc": manifest.get("toc", []),
            "contentUrl": account_web.app_url(f"/api/reader/txt/{urlquote(token)}/chunk"),
            "cacheKey": paths["key"],
        },
        ensure_ascii=False,
    )
#}}}
#{{{ reader txt chunk api
@bottle.route('/api/reader/txt/<token>/chunk/<index:int>')
def reader_txt_chunk(token, index):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "txt":
        bottle.abort(400, "Not a TXT document")

    manifest = ensure_txt_cache(path)
    chunks = manifest.get("chunks", [])
    if index < 0 or index >= len(chunks):
        bottle.abort(404, "TXT chunk not found")
    chunk = chunks[index]
    content_path = get_txt_cache_paths(path)["content_path"]
    with open(content_path, "rb") as handle:
      handle.seek(int(chunk["start"]))
      raw = handle.read(int(chunk["end"]) - int(chunk["start"]))
    text = raw.decode(manifest.get("charset", "utf-8"), errors="replace")
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps(
        {
            "index": int(chunk["index"]),
            "start": int(chunk["start"]),
            "end": int(chunk["end"]),
            "text": text,
            "hasPrev": index > 0,
            "hasNext": index < len(chunks) - 1,
        },
        ensure_ascii=False,
    )
#}}}
#{{{ reader epub manifest api
@bottle.route('/api/reader/epub/<token>/manifest')
def reader_epub_manifest(token):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "epub":
        bottle.abort(400, "Not an EPUB document")

    manifest = ensure_epub_cache(path)
    content_prefix = account_web.app_url(f"/api/reader/epub/{urlquote(token)}/content")
    chapters = [
        {
            "label": item.get("label", "Untitled"),
            "href": item.get("href", ""),
            "url": build_reader_content_url(content_prefix, item.get("href", "")),
            "size": int(item.get("size", 0) or 0),
            "mediaType": item.get("mediaType", ""),
        }
        for item in manifest.get("chapters", [])
        if item.get("href")
    ]
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps(
        {
            "title": manifest.get("title") or os.path.splitext(os.path.basename(path))[0],
            "startPath": manifest.get("start_path", ""),
            "startUrl": build_reader_content_url(content_prefix, manifest.get("start_path", "")),
            "contentBaseUrl": f"{content_prefix}/",
            "toc": manifest.get("toc", []),
            "chapters": chapters,
            "count": len(chapters),
        },
        ensure_ascii=False,
    )
#}}}
#{{{ reader epub content api
@bottle.route('/api/reader/epub/<token>/content')
@bottle.route('/api/reader/epub/<token>/content/<relpath:path>')
def reader_epub_content(token, relpath=""):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "epub":
        bottle.abort(400, "Not an EPUB document")

    manifest = ensure_epub_cache(path)
    requested = normalize_reader_relpath(relpath) or normalize_reader_relpath(manifest.get("start_path", ""))
    member_name = epub_href_to_zip_member("", requested)
    if not member_name:
        bottle.abort(404, "EPUB entry not found")
    try:
        with zipfile.ZipFile(path) as zipf:
            if member_name not in zipf.namelist():
                lower_map = {name.lower(): name for name in zipf.namelist()}
                member_name = lower_map.get(member_name.lower(), "")
            if not member_name:
                bottle.abort(404, "EPUB entry not found")
            raw = zipf.read(member_name)
    except KeyError:
        bottle.abort(404, "EPUB entry not found")

    mimetype, encoding = mimetypes.guess_type(member_name)
    ext = os.path.splitext(member_name)[1].lower()
    if ext in (".html", ".htm", ".xhtml"):
        mimetype = "text/html"
    elif ext == ".css":
        mimetype = "text/css"
    elif ext == ".js":
        mimetype = "application/javascript"
    elif ext == ".svg":
        mimetype = "image/svg+xml"
    bottle.response.content_type = mimetype or "application/octet-stream"
    if encoding:
        bottle.response.content_type = f"{mimetype}; charset={encoding}"
    bottle.response.headers['Cache-Control'] = 'private, no-store'
    bottle.response.headers['Content-Length'] = len(raw)
    return raw
#}}}
#{{{ reader chm manifest api
@bottle.route('/api/reader/chm/<token>/manifest')
def reader_chm_manifest(token):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "chm":
        bottle.abort(400, "Not a CHM document")

    files_root, manifest = get_reader_chm_content_root(path)
    content_prefix = account_web.app_url(f"/api/reader/chm/{urlquote(token)}/content")
    toc = [
        {
            "label": item.get("label", "Untitled"),
            "href": item.get("href", ""),
            "depth": int(item.get("depth", 0) or 0),
            "url": build_reader_content_url(content_prefix, item.get("href", "")),
        }
        for item in manifest.get("toc", [])
        if item.get("href")
    ]
    title = manifest.get("title") or os.path.splitext(os.path.basename(path))[0]
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps(
        {
            "title": title,
            "startPath": manifest.get("start_path", ""),
            "startUrl": build_reader_content_url(content_prefix, manifest.get("start_path", "")),
            "contentBaseUrl": f"{content_prefix}/",
            "toc": toc,
            "count": len(toc),
        },
        ensure_ascii=False,
    )
#}}}
def rewrite_chm_html_urls(raw_bytes, token):
    """Rewrite absolute paths in CHM HTML content to route through the API.
    Operates at byte level to preserve the original encoding of the file."""
    prefix_bytes = account_web.app_url(f'/api/reader/chm/{urlquote(token)}/content').encode('utf-8')

    def replace_url(match):
        return match.group(1) + b'=' + match.group(2) + prefix_bytes + match.group(3) + match.group(2)

    return re.sub(
        rb'(src|href|action)=("|\')(/[^"\'#][^"\']*?)\2',
        replace_url,
        raw_bytes,
        flags=re.IGNORECASE,
    )

CHARSET_RE = re.compile(
    rb'<meta[^>]+(?:charset=["\']?([^"\';>\s]+)|content=["\'][^"\']*charset=([^"\';>\s]+))',
    re.IGNORECASE,
)

def detect_chm_html_mimetype(raw, fallback="text/html"):
    """Detect charset from HTML meta tag and return Content-Type with charset."""
    match = CHARSET_RE.search(raw)
    charset = None
    if match:
        charset = (match.group(1) or match.group(2) or b"").decode('ascii', errors='ignore').strip()
    if charset and charset.lower() in CHARSET_ALIASES:
        charset = CHARSET_ALIASES[charset.lower()]
    if charset and charset.isascii() and len(charset) < 32:
        return f"{fallback}; charset={charset.lower()}"
    return fallback

CHARSET_ALIASES = {
    "gb2312": "gbk",
    "gb_2312": "gbk",
    "gb-2312": "gbk",
    "gb2312-80": "gbk",
    "gbk": "gbk",
    "gb18030": "gb18030",
    "big5": "big5",
    "big-5": "big5",
    "shift_jis": "shift_jis",
    "shift-jis": "shift_jis",
    "euc-jp": "euc-jp",
    "euc_kr": "euc-kr",
    "euc-kr": "euc-kr",
}

#{{{ reader chm content api
@bottle.route('/api/reader/chm/<token>/content')
@bottle.route('/api/reader/chm/<token>/content/<relpath:path>')
def reader_chm_content(token, relpath=""):
    payload = read_reader_token(token)
    if not payload:
        bottle.abort(403, "Invalid or expired reader token")
    config = get_config()
    path = payload["path"]
    if not is_allowed_reader_path(path, config):
        bottle.abort(403, "Reader access denied")
    if not os.path.isfile(path):
        bottle.abort(404, "Reader file not found")
    if get_reader_format_from_name(payload.get("name") or path, payload.get("mimetype", "")) != "chm":
        bottle.abort(400, "Not a CHM document")

    files_root, manifest = get_reader_chm_content_root(path)
    file_path, resolved_relpath = resolve_chm_content_file(files_root, manifest, relpath)
    if not file_path:
        bottle.abort(404, "CHM entry not found")

    mimetype, encoding = mimetypes.guess_type(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".html", ".htm", ".hhc"):
        mimetype = "text/html"
    elif ext == ".css":
        mimetype = "text/css"
    elif ext == ".js":
        mimetype = "application/javascript"
    elif ext == ".svg":
        mimetype = "image/svg+xml"
    elif ext == ".xhtml":
        mimetype = "application/xhtml+xml"
    bottle.response.content_type = mimetype or "application/octet-stream"
    bottle.response.headers['Cache-Control'] = 'private, no-store'
    bottle.response.headers['X-Reader-Path'] = resolved_relpath
    with open(file_path, 'rb') as handle:
        raw = handle.read()
    if ext in (".html", ".htm", ".hhc", ".xhtml"):
        raw = rewrite_chm_html_urls(raw, token)
        mimetype = detect_chm_html_mimetype(raw, mimetype)
        bottle.response.content_type = mimetype
    bottle.response.headers['Content-Length'] = len(raw)
    return raw
#}}}
#{{{ preview
@bottle.route('/preview/<resnum:int>')
def preview(resnum):
    config, query, rclq, db, doc = resolve_doc_from_result_query(resnum)
    xt = rclextract.Extractor(doc)
    tdoc = xt.textextract(doc.ipath)
    if tdoc.mimetype == 'text/html':
        ishtml = 1
        bottle.response.content_type = 'text/html; charset=utf-8'
    else:
        ishtml = 0
        bottle.response.content_type = 'text/plain; charset=utf-8'
    if 'highlight' in query and query['highlight']:
        hl = HlMeths()
        txt = rclq.highlight(tdoc.text, ishtml=ishtml, methods=hl)
        pos = txt.find('<head>')
        ssref = '<link rel="stylesheet" type="text/css" href="../static/style.css">'
        if pos >= 0:
            txt = txt[0:pos+6] + ssref + txt[pos+6:]
        else:
            txt = '<html><head>' + ssref + \
                '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8"></head><body>'+ \
                txt
        bottle.response.content_type = 'text/html; charset=utf-8'
        return txt
    bottle.response.headers['Vary'] = 'Cookie'
    bottle.response.headers['No-Vary-Search'] = 'key-order'
    return tdoc.text
#}}}
#{{{ download
@bottle.route('/download/<resnum:int>')
def edit(resnum):
    config, query, rclq, db, doc = resolve_doc_from_result_query(resnum)
    source = normalize_file_path(doc.url)
    filename = doc.filename if "filename" in doc.keys() else os.path.basename(source)
    pagenum = -1
    try:
        # Note: getfirstmatchpage is only available from recoll 1.44
        pagenum, term = rclq.getfirstmatchpage(doc)
    except:
        pass
    if pagenum != -1 and doc.mimetype == 'application/pdf' and not doc.ipath:
        token = make_reader_token(source, doc.mimetype, filename)
        bottle.redirect(account_web.app_url('/staticdoc/' + token) +
                        '#page=%s&search=%s' % (pagenum, urlquote(term)), 303)
    xt = rclextract.Extractor(doc)
    path = os.fsdecode(xt.idoctofile(doc.ipath, doc.mimetype))
    # The extractor may return a temporary file; it is never exposed as a directory.
    # Only the document authorized above can reach this response.
    result = bottle.static_file(os.path.basename(path), root=os.path.dirname(path),
                                mimetype=doc.mimetype, download=filename, etag=False,
                                headers={'Cache-Control': 'private, no-store'})
    # static_file has opened its response body already. On this Unix service an
    # extracted temporary file can be unlinked while that descriptor streams it.
    temporary_root = os.path.realpath(g_tmpdir or tempfile.gettempdir())
    extracted_path = os.path.realpath(path)
    if (extracted_path != os.path.realpath(source) and
            os.path.commonpath([extracted_path, temporary_root]) == temporary_root):
        try:
            os.unlink(path)
        except OSError:
            pass
    return result

        
#}}}
#{{{ json
@bottle.route('/json')
def get_json():
    config = get_config()
    if config['rclc_nojsoncsv']:
        bottle.abort(403, '结果导出已被管理员禁用')
    query = get_query(config)
    qs = query_to_recoll_string(query)
    bottle.response.headers['Vary'] = 'Cookie'
    bottle.response.headers['No-Vary-Search'] = 'key-order'
    bottle.response.headers['Content-Type'] = 'application/json'
    bottle.response.headers['Content-Disposition'] = \
      'attachment; filename=recoll-%s.json' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)
    ures = []
    for d in res:
        ud={}
        for f,v in d.items():
            ud[f] = v
        ures.append(ud)
    res = ures
    return json.dumps({ 'query': query, 'results': res })
#}}}
#{{{ csv
@bottle.route('/csv')
def get_csv():
    config = get_config()
    if config['rclc_nojsoncsv']:
        bottle.abort(403, '结果导出已被管理员禁用')
    query = get_query(config)
    query['page'] = 0
    query['snippets'] = 0
    qs = query_to_recoll_string(query)
    bottle.response.headers['Vary'] = 'Cookie'
    bottle.response.headers['No-Vary-Search'] = 'key-order'
    bottle.response.headers['Content-Type'] = 'text/csv'
    bottle.response.headers['Content-Disposition'] = \
      'attachment; filename=recoll-%s.csv' % normalise_filename(qs)
    res, nres, timer = recoll_search(query)
    si = io.StringIO()
    cw = csv.writer(si)
    fields = config['csvfields'].split()
    cw.writerow(fields)
    for doc in res:
        row = []
        for f in fields:
            if f in doc:
                row.append(doc[f])
            else:
                row.append('')
        cw.writerow(row)
    return si.getvalue().strip("\r\n")
#}}}
#{{{ settings/set
@bottle.route('/settings')
@bottle.view('settings')
def settings():
    config = get_config()
    if config['rclc_nosettings']:
        bottle.abort(403, '搜索设置已被管理员锁定')
    return config

@bottle.route('/set', method='POST')
def set_settings():
    config = get_config()
    try:
        forms = bottle.request.forms.decode('utf-8')
    except UnicodeError:
        bottle.abort(400, '设置必须使用 UTF-8 编码')
    if config['rclc_nosettings']:
        bottle.abort(403, '搜索设置已被管理员锁定')
    editable = ('stem', 'collapsedups', 'maxresults', 'perpage', 'context', 'maxchars',
                'timefmt', 'dirdepth', 'title_link', 'csvfields')
    preferences = {key: forms.get(key) for key in editable if key in forms}
    for key in preferences.keys() & INTEGER_PREFERENCES.keys():
        value = preferences[key]
        if not re.fullmatch(r'\d+', value):
            bottle.abort(400, '设置 %s 必须是整数' % key)
        preferences[key] = int(value)
    if 'csvfields' in preferences:
        preferences['csvfields'] = ' '.join(x for x in preferences['csvfields'].split() if x in FIELDS)
    mounts = {}
    for d in config['dirs']:
        value = forms.get('mount_%s' % d, '')
        if value and (len(value) > 4096 or urlsplit(value).scheme not in ('file', 'http', 'https', 'ftp', 'local-file')):
            bottle.abort(400, '不支持的路径映射协议')
        mounts[d] = value
    try:
        account_store.save_settings(account_web.current_user()['id'], preferences=preferences)
    except ValidationError as exc:
        bottle.abort(400, str(exc))
    for d, value in mounts.items():
        cookie_name = 'mount_%s_%s' % (account_web.current_user()['id'], urlquote(d, ''))
        bottle.response.set_cookie(cookie_name, account_web.sign_payload({'url': value}, 'device-mount', ttl=86400*30),
                                   path=account_web.app_url('/'), max_age=86400*30,
                                   httponly=True, samesite='Lax', secure=bottle.request.urlparts.scheme == 'https')
    bottle.redirect(account_web.app_url('/'), 303)
#}}}
#{{{ osd
@bottle.route('/osd.xml')
@bottle.view('osd')
def main():
    #config = get_config()
    url = bottle.request.urlparts
    url = '%s://%s%s' % (url.scheme, url.netloc, bottle.request.environ.get('SCRIPT_NAME', '').rstrip('/'))
    return {'url': url}
#}}}


account_web.install(bottle.default_app(), account_store, callbacks={
    'settings_locked': lambda: bool(get_config()['rclc_nosettings']),
    'legacy_import': lambda data: appdata.import_legacy(data),
})
appdata = ApplicationData(bottle.default_app(), account_store, sys.modules[__name__])


@bottle.hook('after_request')
def protect_document_responses():
    path = bottle.request.path
    if path == '/reader':
        # Generated about:blank/blob reader frames inherit this policy, unlike
        # the CSP on the source file fetched as an ArrayBuffer by the engine.
        bottle.response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self' blob:; "
            "worker-src 'self' blob:; frame-src 'self' blob:; object-src 'none'; "
            "base-uri 'self'; form-action 'self'")
    if (path.startswith(('/preview/', '/download/', '/staticdoc/', '/api/reader/file/')) or
            (path.startswith(('/api/reader/epub/', '/api/reader/chm/')) and '/content' in path)):
        bottle.response.headers['Content-Security-Policy'] = (
            "sandbox allow-same-origin; default-src 'none'; script-src 'none'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; media-src 'self' blob:; connect-src 'none'; "
            "object-src 'none'; frame-src 'none'; base-uri 'self'; form-action 'none'")
        bottle.response.headers['Cache-Control'] = 'private, no-store'
        bottle.response.headers['X-Content-Type-Options'] = 'nosniff'

# vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et

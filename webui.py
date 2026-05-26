#{{{ imports
import os
import re
import bottle
import time
import sys
import datetime
import glob
import hashlib
import hmac
import base64
import csv
import io
import mimetypes
import string
import subprocess
import tempfile
import shlex
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, quote as urlquote, unquote as urlunquote, urlsplit
from recoll import recoll, rclextract, rclconfig

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
    'maxresults': 0,
    'perpage': 25,
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
READER_TOKEN_TTL = 60 * 60 * 12
READER_TOKEN_SECRET = os.environ.get("RECOLL_READER_TOKEN_SECRET")
if not READER_TOKEN_SECRET:
    READER_TOKEN_SECRET = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
READER_CHM_CACHE_ROOT = os.path.join(g_tmpdir or tempfile.gettempdir(), "recoll-webui-reader", "chm")
READER_EPUB_CACHE_ROOT = os.path.join(g_tmpdir or tempfile.gettempdir(), "recoll-webui-reader", "epub")

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
    payload = {
        "path": os.path.realpath(path),
        "mimetype": mimetype or "",
        "name": name or "",
        "exp": int(time.time()) + READER_TOKEN_TTL,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(
        READER_TOKEN_SECRET.encode("utf-8"),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"

def read_reader_token(token):
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(
                READER_TOKEN_SECRET.encode("utf-8"),
                payload_b64.encode("ascii"),
                hashlib.sha256,
            ).digest()
        ).decode("ascii").rstrip("=")
        if not hmac.compare_digest(expected, sig_b64):
            return None
        raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        path = normalize_file_path(payload.get("path", ""))
        if not path:
            return None
        payload["path"] = path
        return payload
    except Exception:
        return None

def resolve_doc_from_result_query(resnum, query=None):
    config = get_config()
    q = query or get_query(config)
    rclq, db = recoll_initsearch(q)
    if "rcludi" in q and q["rcludi"]:
        doc = db.getDoc(q["rcludi"])
    else:
        if resnum > rclq.rowcount - 1:
            bottle.abort(404, "Bad result index %d" % resnum)
        rclq.scroll(resnum)
        doc = rclq.fetchone()
    if not doc:
        bottle.abort(404, "Document not found")
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
        "url": f"/api/reader/file/{urlquote(token)}",
        "supported": reader_format in READER_SUPPORTED_FORMATS,
    }
    if reader_format == "chm":
        item["manifestUrl"] = f"/api/reader/chm/{urlquote(token)}/manifest"
    elif reader_format == "epub":
        item["manifestUrl"] = f"/api/reader/epub/{urlquote(token)}/manifest"
    return item

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
            item["url"] = f"/api/reader/file/{urlquote(item['token'])}"
            if reader_format == "chm":
                item["manifestUrl"] = f"/api/reader/chm/{urlquote(item['token'])}/manifest"
            elif reader_format == "epub":
                item["manifestUrl"] = f"/api/reader/epub/{urlquote(item['token'])}/manifest"
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
            current_item["url"] = f"/api/reader/file/{urlquote(current_item['token'])}"
            if current_format == "chm":
                current_item["manifestUrl"] = f"/api/reader/chm/{urlquote(current_item['token'])}/manifest"
            elif current_format == "epub":
                current_item["manifestUrl"] = f"/api/reader/epub/{urlquote(current_item['token'])}/manifest"
            items.insert(0, current_item)
            current_index = 0
            if len(items) > READER_FOLDER_SCAN_LIMIT:
                items.pop()
                truncated = True

    return {
        "root": root_dir,
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
    # Arrange for apache wsgi SetEnv values to be reflected in the os environment.
    # This allows people to use either method
    for k in ("RECOLL_CONFDIR", "RECOLL_EXTRACONFDIRS"):
        if  k in bottle.request.environ:
            os.environ[k] = bottle.request.environ[k]
    config = {}
    envdir = safe_envget('RECOLL_CONFDIR')
    # get useful things from recoll.conf
    rclconf = rclconfig.RclConfig(envdir)
    config['confdir'] = rclconf.getConfDir()
    topdirs = [os.path.expanduser(d) for d in shlex.split(rclconf.getConfParam('topdirs'))]
    config['dirs'] = dict.fromkeys(topdirs, config['confdir'])
    config['commonprefix'] = commonpathprefix(topdirs)
    # add topdirs from extra config dirs
    extraconfdirs = safe_envget('RECOLL_EXTRACONFDIRS')
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
    for k, isint in fetches:
        value = rclconf.getConfParam("webui_" + k)
        if value is not None:
            DEFAULTS[k] = int(value) if isint else value
    # get config from cookies or defaults
    for k, v in DEFAULTS.items():
        value = select([bottle.request.get_cookie(k), v], invalid=["None", None])
        config[k] = type(v)(value)
    # Fix csvfields: get rid of invalid ones to avoid needing tests in the dump function
    cf = config['csvfields'].split()
    ncf = [f for f in cf if f in FIELDS]
    config['csvfields'] = ' '.join(ncf)
    config['fields'] = ' '.join(FIELDS)
    # get mountpoints
    config['mounts'] = {}
    for d in config['dirs']:
        name = 'mount_%s' % urlquote(d,'')
        config['mounts'][d] = select([bottle.request.get_cookie(name),
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
    if val:
        if config['perpage'] == 0 or config['perpage'] > val:
            config['perpage'] = val

    val = rclconf.getConfParam('webui_nosettings')
    val = 0 if val is None else int(val)
    config['rclc_nosettings'] = val

    val = str(rclconf.getConfParam('webui_defaultsort'))
    config['defsortidx'] = 0
    for i in range(len(SORTS)):
        if SORTS[i][0] == val or SORTS[i][1] == val:
            config['defsortidx'] = i
            break
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
def get_query(config=None):
    defsortidx = config['defsortidx'] if config and 'defsortidx' in config else 0
    query = {
        'query': select([bottle.request.query.query, '']),
        'before': select([bottle.request.query.before, '']),
        'after': select([bottle.request.query.after, '']),
        'dir': select([bottle.request.query.dir, '', '<all>'], [None, '']),
        'sort': select([bottle.request.query.sort, SORTS[defsortidx][0]], [None, '']),
        'ascending': select_int([bottle.request.query.ascending, 0], default=0),
        # Reader runtimes may write non-numeric positions like "121:06" into the
        # browser URL. Treat invalid search page values as "first page" instead
        # of failing the whole request.
        'page': select_int([bottle.request.query.page, 0], default=0, minimum=0),
        'highlight': select_int([bottle.request.query.highlight, 1], default=1),
        'snippets': select_int([bottle.request.query.snippets, 1], default=1),
    }
    if bottle.request.query.rcludi:
        query['rcludi'] = bottle.request.query.rcludi
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
    try:
        qs = query_to_recoll_string(q)
        if "queryfrag" in config and config["queryfrag"]:
            qs += " " + config["queryfrag"]
        if "logquery" in config and config["logquery"]:
            msg(f"Query: {qs}")
        query.execute(qs, config['stem'], config['stemlang'],
                      collapseduplicates=config['collapsedups'])
    except Exception as ex:
        msg("Query execute failed: %s" % ex)
        pass
    return query, db
#}}}
#{{{ HlMeths
class HlMeths:
    def startMatch(self, idx):
        return '<span class="search-result-highlight">'
    def endMatch(self):
        return '</span>'
#}}}
#{{{ recoll_search
def recoll_search(q):
    config = get_config()
    tstart = datetime.datetime.now()
    results = []
    query,_ = recoll_initsearch(q)
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
    offset = (q['page'] - 1) * config['perpage']

    if query.rowcount > 0:
        if type(query.next) == int:
            query.next = offset
        else:
            query.scroll(offset, mode='absolute')

    if 'highlight' in q and q['highlight']:
        highlighter = HlMeths()
    else:
        highlighter = None

    udibreak = False
    while len(results) < config['perpage']:
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
        if doc['mtype'] == "application/pdf" and doc['url'].startswith("file://"):
            try:
                # Note: getfirstmatchpage is only available from recoll 1.44
                pagenum, term = query.getfirstmatchpage(doc)
                d['url'] += f"#page={pagenum}&search={urlquote(term)}"
            except:
                pass
        d['label'] = select([d['title'], d['filename'], '?'], [None, ''])
        d['sha'] = hashlib.sha1((d['url']+d['ipath']).encode('utf-8')).hexdigest()
        d['time'] = timestr(d['mtime'], config['timefmt'])
        d['rcludi'] = doc['rcludi']
        if 'snippets' in q and q['snippets']:
            if highlighter:
                d['snippet'] = query.makedocabstract(doc, methods=highlighter)
            else:
                d['snippet'] = query.makedocabstract(doc)
            if not d['snippet']:
                try:
                    d['snippet'] = doc['abstract']
                except:
                    pass
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
    return bottle.static_file(path, root='./static')
#}}}
@bottle.route('/staticdoc/:path#.+#')
def server_staticdoc(path):
    if not g_tmpdir:
        return ""
    return bottle.static_file(path, root=g_tmpdir)
#}}}
#{{{ main
@bottle.route('/')
@bottle.view('main')
def main():
    config = get_config()
    bottle.response.headers['Vary'] = 'Cookie'
    return { 'dirs': get_dirs(config['dirs'], config['dirdepth']),
            'query': get_query(config), 'sorts': SORTS, 'config': config}
#}}}
#{{{ results
@bottle.route('/results')
@bottle.view('results')
def results():
    config = get_config()
    query = get_query(config)
    qs = query_to_recoll_string(query)
    res, nres, timer = recoll_search(query)
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
             'config': config}
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
        if key not in ("mode", "resnum")
    ]
    return {
        "mode": mode,
        "query_string": bottle.request.query_string,
        "results_query_string": urlencode(search_query, doseq=True, encoding="utf-8", errors="strict"),
    }
#}}}
#{{{ reader book api
@bottle.route('/api/reader/book')
def reader_book():
    resnum = select_int([bottle.request.query.resnum, -1], default=-1)
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
    config, query, rclq, db, doc = resolve_doc_from_result_query(resnum)
    if not is_reader_supported_doc(doc):
        bottle.abort(400, "Unsupported format for reader")
    folder_data = scan_reader_folder(doc, config)
    bottle.response.content_type = 'application/json; charset=utf-8'
    return json.dumps({
        "mode": "folder",
        "root": folder_data["root"],
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
    bottle.response.content_type = mimetype
    bottle.response.headers['Cache-Control'] = 'private, max-age=3600'
    bottle.response.headers['Content-Length'] = os.stat(path).st_size
    with open(path, 'rb') as f:
        return f.read()
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
    content_prefix = f"/api/reader/epub/{urlquote(token)}/content"
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
    bottle.response.headers['Cache-Control'] = 'private, max-age=3600'
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
    content_prefix = f"/api/reader/chm/{urlquote(token)}/content"
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
    prefix_bytes = f'/api/reader/chm/{urlquote(token)}/content'.encode('utf-8')

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
    bottle.response.headers['Cache-Control'] = 'private, max-age=3600'
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
    xt = rclextract.Extractor(doc)
    path = xt.idoctofile(doc.ipath, doc.mimetype)
    if "filename" in doc.keys():
        filename = doc.filename
    else:
        filename = os.path.basename(path)
    pagenum = -1
    try:
        # Note: getfirstmatchpage is only available from recoll 1.44
        pagenum, term = rclq.getfirstmatchpage(doc)
    except:
        pass
    if pagenum != -1 and doc.mimetype == "application/pdf":
        return \
            "<html><head></head><body><script>" \
            f"window.location.replace(\"/staticdoc/{filename}#page={pagenum}&search={term}\");" \
            "</script></body></html>"
    else:
        bottle.response.content_type = doc.mimetype
        bottle.response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        bottle.response.headers['Content-Length'] = os.stat(path).st_size
        f = open(path, 'rb')
        try:
            os.unlink(path)
        except:
            pass
        bottle.response.headers['Vary'] = 'Cookie'
        bottle.response.headers['No-Vary-Search'] = 'key-order'
        return f

        
#}}}
#{{{ json
@bottle.route('/json')
def get_json():
    config = get_config()
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
    return get_config()

@bottle.route('/set')
def set_settings():
    config = get_config()
    for k, v in DEFAULTS.items():
        bottle.response.set_cookie(k, str(bottle.request.query.get(k)),
                                   max_age=3153600000, expires=315360000)
    for d in config['dirs']:
        # We should not set the cookie if the value is the default (identical path). This would
        # allow the server configuration setting to be used if set. This would also show a wrong
        # value in the settings screen (default instead of server config value), so not too sure
        # what the right thing would be here.
        cookie_name = 'mount_%s' % urlquote(d, '')
        bottle.response.set_cookie(cookie_name, str(bottle.request.query.get('mount_%s' % d)),
                                   max_age=3153600000, expires=315360000)
    bottle.redirect('./')
#}}}
#{{{ osd
@bottle.route('/osd.xml')
@bottle.view('osd')
def main():
    #config = get_config()
    url = bottle.request.urlparts
    url = '%s://%s' % (url.scheme, url.netloc)
    return {'url': url}
#}}}
# vim: fdm=marker:tw=80:ts=4:sw=4:sts=4:et

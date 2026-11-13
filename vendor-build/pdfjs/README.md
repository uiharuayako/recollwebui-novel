# Local PDF.js runtime

The reader uses Mozilla `pdfjs-dist` **6.5.438**, released 2026-11-01. The dependency is pinned exactly and copied from the npm distribution without modifying its JavaScript. Versioned asset paths keep the API, viewer components and parsing worker together. The `legacy` build supports Chrome 125+, Firefox ESR+, and Safari 18+ within the upstream compatibility table; it avoids requiring the newest browser syntax.

To reproduce the checked-in static files, with Node 22.22.3 or another supported Node release:

```sh
cd vendor-build/pdfjs
npm install --ignore-scripts --no-audit --no-fund
npm run build
```

The output is `static/reader/vendor/pdfjs/6.5.438/`. It includes the main module, parsing worker, viewer component module/CSS/images, CMaps, standard fonts, image decoding WASM, ICC profiles, and their licenses. `SHA256SUMS.json` records every copied file. Node and npm are build tools; deployed Python servers serve the checked-in assets and do not contact npm or a CDN.

`static/reader/pdf-adapter.js` loads the runtime only when a PDF is opened. It uses the official `PDFViewer`, `PDFLinkService`, `PDFFindController` and text/annotation layers, independently of Kookit's PDF viewer. Pages form one continuous vertical scroll surface. PDF.js prioritizes visible pages and nearby pages and evicts old canvases through its bounded page-view buffer; page placeholders remain so scrolling is stable. Position is stored as page number and proportional offset, with zoom and rotation. Width fitting and layout changes preserve that anchor.

Selectable text, copy, document outline, internal/external links, full-document search with context and highlighted results, zoom, and rotation use the matching viewer components. Search populates the result list without navigating automatically; selecting a result scrolls its text into view. Search strings and result excerpts stay in the current reader instance and are cleared when the document closes. Scanned PDFs without a text layer need OCR before text search can find their contents.

The adapter has two small integrations with the pinned viewer's exposed state: selecting a sidebar result updates `PDFFindController.selected` and dispatches `updatetextlayermatches`; documents above the viewer's 10,000-page auto-pagination threshold keep `_scrollMode` vertical before page views are created. Review both when changing versions. Long documents may still take time to initialize page metadata. The authenticated same-origin document URL is passed to PDF.js so it can stream bytes and make Range requests; the first page does not wait for an explicit full-file arrayBuffer download. ArrayBuffer input remains available for generated documents and tests.

The adapter does not create a scripting manager, run PDF OpenAction/JavaScript actions, or load `pdf.sandbox`. XFA and annotation editing are disabled; form fields are rendered without editing. Password-protected files ask for the password for that open only; it is never stored. PDF.js fetches the authenticated same-origin document URL with credentials; each file request remains subject to the server's session and document authorization checks. Viewer resources are local and derived from `import.meta.url`, including when mounted under a URL prefix. The optional second rendering worker is disabled. Document close destroys the viewer, its listeners and text layers, the parsing worker, and cached document/search references.

Upstream references checked 2026-11-13:

- [Mozilla release 6.5.438](https://github.com/mozilla/pdf.js/releases/tag/v6.5.438)
- [PDF.js rendering walkthrough](https://mozilla.github.io/pdf.js/examples/)
- [6.5.438 core API source](https://github.com/mozilla/pdf.js/blob/v6.5.438/src/display/api.js)
- [6.5.438 viewer](https://github.com/mozilla/pdf.js/blob/v6.5.438/web/pdf_viewer.js)
- [6.5.438 find controller](https://github.com/mozilla/pdf.js/blob/v6.5.438/web/pdf_find_controller.js)
- [Supported browsers](https://github.com/mozilla/pdf.js/wiki/Frequently-Asked-Questions#which-browsersenvironments-are-supported)
- [Viewer scripting manager source](https://github.com/mozilla/pdf.js/blob/v6.5.438/web/pdf_scripting_manager.js)

When upgrading, update the exact package version, the build version check, and the adapter's asset path together; rebuild and rerun the PDF unit/browser and account-sync tests. Review upstream security advisories at each upgrade.

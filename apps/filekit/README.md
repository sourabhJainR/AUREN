# FileKit

FileKit is a static browser SPA for everyday PDF, image, spreadsheet and text-file tasks. Files are processed in the browser; there is no FileKit upload backend.

## Launch

Open `index.html` from a static server or deploy the directory to GitHub Pages or another static host. CDN-loaded libraries require an internet connection to load. Once loaded, processing uses browser-side APIs.

## Implemented operations

| Tool | Operation | Limitations |
|---|---|---|
| PDF merge | Merge selected PDFs | PDF encryption/unsupported features may fail |
| PDF extract | Extract page ranges | 1-based ranges; output order follows ranges |
| PDF rotate | Rotate every page | Uses page rotation metadata |
| PDF compress | Rasterize and rebuild | Lossy; text search, vectors, links and metadata are lost; DPI/quality control |
| PDF compare | Extracted text comparison | Not a visual or OCR comparison |
| PDF to text | Text extraction | No OCR for scanned pages |
| PDF to PNG | Render pages | ZIP output |
| Images to PDF | JPEG/PNG images to PDF | Other formats may need conversion first |
| Image resize/convert | Resize and encode PNG/JPEG/WebP | Fit within bounds; does not upscale |
| Image compare | Pixel-level difference | Same dimensions only for pixel diff |
| Spreadsheet export | First worksheet CSV/JSON/HTML | No workbook editing; first worksheet only |
| Spreadsheet compare | Compare first sheet rows | Basic row-level comparison |
| DOCX text | Extract raw text | Formatting not preserved |
| Text/HTML | Save text or escaped HTML | Simple conversion |
| ZIP | Pack selected files | Extraction workflow produces archive of extracted entries |
| File info | Name, MIME, size, modified time | Basic metadata only |

## Privacy and safety

- Files are read in the browser and not sent to FileKit servers.
- Library scripts are loaded from pinned CDN URLs. This is not an audited, offline, or supply-chain-verified release.
- Avoid processing sensitive documents in this prototype until dependencies and deployment are independently audited.
- Converted files may lose formatting, metadata, signatures, formulas, OCR content or other fidelity.
- No password recovery, OCR, document-signature validation or advanced Office conversion is offered.

## Validation status

This is an initial prototype delivered via GitHub file API. It has not yet been executed in a browser, run through automated tests, or validated against real-world sample files. Treat those gates as pending.

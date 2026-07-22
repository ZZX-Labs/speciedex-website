# Speciedex Utilities

This directory contains archive verification, repair, export, packaging, and
provider-credential utilities used by Speciedex.org.

## Export formats

| Utility | Output |
|---|---|
| `export_json.py` | JSON or JSONL |
| `export_csv.py` | CSV |
| `export_txt.py` | Plain text |
| `export_pdf.py` | PDF through ReportLab |
| `export_docx.py` | Microsoft Word DOCX through python-docx |
| `export_word.py` | DOCX compatibility entry point |
| `export_doc.py` | Legacy Word DOC through LibreOffice conversion |
| `export_epub.py` | EPUB 3 through EbookLib |
| `export_sqlite.py` | Portable SQLite database |
| `export_mariadb.py` | MariaDB database migration |
| `export_archive.py` | ZIP, 7z, RAR, TAR, TAR.GZ, TAR.XZ |
| `export_zip.py` | ZIP wrapper |
| `export_7z.py` | 7z wrapper |
| `export_rar.py` | RAR wrapper |

## Optional dependencies

```bash
python -m pip install \
  EbookLib \
  PyMySQL \
  python-docx \
  reportlab
```

Legacy DOC conversion additionally requires LibreOffice. Seven-Zip archives
require `7z` or `7zz`. RAR creation requires the proprietary `rar` executable.

## Provider authentication

Store one GitHub Actions secret named:

```text
SPECIEDEX_PROVIDER_CREDENTIALS
```

Its value is a JSON object:

```json
{
  "EOL_API_KEY": "...",
  "IUCN_API_TOKEN": "...",
  "NATURESERVE_API_KEY": "...",
  "NCBI_API_KEY": "...",
  "BACDIVE_USERNAME": "...",
  "BACDIVE_PASSWORD": "...",
  "BHL_API_KEY": "...",
  "GEONAMES_USERNAME": "...",
  "YOUTUBE_API_KEY": "...",
  "GOOGLE_API_KEY": "..."
}
```

Load it in a workflow:

```yaml
- name: Load provider credentials
  env:
    SPECIEDEX_PROVIDER_CREDENTIALS: >-
      ${{ secrets.SPECIEDEX_PROVIDER_CREDENTIALS }}
  run: |
    python static/tools/utilities/provider_authenticate.py \
      --require-bundle \
      --scan-repository .
```

The utility masks values, validates names against `providers.json`, scans for
exact-value leakage, and writes credentials only to the ephemeral `GITHUB_ENV`
file. Never print `env`, `printenv`, `$GITHUB_ENV`, or the secret JSON.

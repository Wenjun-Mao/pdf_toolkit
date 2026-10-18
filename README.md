# PDF Kit

Single-user PDF utility app built with FastAPI, HTMX, Redis/RQ, SQLite, and PyMuPDF.

## Features

- Merge 2+ PDFs into one output
- Combine PDFs and images into one ordered PDF
- Combine multiple images into one PDF
- Build one PDF page from top half of image 1 and bottom half of image 2
- Split one PDF by explicit ranges or every N pages
- Extract selected pages into a new PDF
- Visually organize a PDF: reorder page thumbnails, delete selected pages, and save a new PDF
- Export embedded images directly from PDFs
- Analyze and clean scanned-page backgrounds while preserving existing OCR/text layers
- Run through the web UI, the CLI, or a Jupyter notebook

## Local Development

Python 3.12+ and [uv](https://docs.astral.sh/uv/) are required.

### macOS and Linux

```bash
uv sync
PDFKIT_RUN_JOBS_INLINE=true uv run pdfkit-web
```

### Windows PowerShell

```powershell
uv sync
$env:PDFKIT_RUN_JOBS_INLINE = "true"
uv run pdfkit-web
```

`PDFKIT_RUN_JOBS_INLINE=true` is useful when you want to test locally without Redis.

Install the locked test extra and run the supported test suite with:

```bash
uv run --extra test pytest -q -m "not slow"
```

## Docker Compose

Docker Engine and Docker Compose are required. On macOS, OrbStack can provide both; confirm that its Docker context is active with `docker context show`.

Create the local Docker secrets. These files are ignored by Git:

```bash
mkdir -p secrets
openssl rand -base64 24 > secrets/admin_password.txt
openssl rand -hex 32 > secrets/session_secret.txt
printf 'false\n' > secrets/require_login.txt
chmod 700 secrets
chmod 600 secrets/*.txt
```

Set `require_login.txt` to `true` and replace `admin_password.txt` with a known password when login protection is needed.

Build and start the stack:

```bash
docker compose up -d --build
```

On OrbStack, open [https://web.pdf-toolkit.orb.local](https://web.pdf-toolkit.orb.local). OrbStack supplies the local domain, HTTPS certificate, and reverse proxy. The portable fallback is [http://127.0.0.1:8100](http://127.0.0.1:8100).

Services:

- `web` serves the FastAPI app internally on port `8000` and on the Mac-only fallback port `8100`
- `worker` runs RQ background jobs
- `redis` backs the queue and is not published to the host
- The Compose project name is `pdf-toolkit`
- The `pdfkit_data` named volume persists jobs, uploads, previews, and results

Useful lifecycle commands:

```bash
docker compose ps
docker compose logs -f web worker redis
docker compose restart
docker compose down
docker compose up -d --build
```

To deliberately discard all PDF Kit data and recreate a fresh stack:

```bash
docker compose down -v
docker compose up -d --build
```

`docker compose down -v` permanently deletes the named data volume.

## VPS nginx Note

Proxy a host-installed nginx to `127.0.0.1:8100` and forward:

- `Host`
- `X-Real-IP`
- `X-Forwarded-For`
- `X-Forwarded-Proto`

Also set `client_max_body_size` high enough for large PDF uploads.

## CLI Usage

```bash
uv run pdfkit merge merged.pdf file1.pdf file2.pdf
uv run pdfkit mixed-to-pdf mixed.pdf file1.pdf photo.jpg file2.pdf --page-size letter --margin-mm 12.7 --placement fit
uv run pdfkit images-to-pdf output.pdf page1.png page2.jpg page3.webp --page-size letter --margin-mm 12.7 --placement fit
uv run pdfkit id-halves-to-pdf id-front.jpg id-back.jpg id-card.pdf
uv run pdfkit split input.pdf out_dir --ranges "1-4;5-8"
uv run pdfkit split input.pdf out_dir --every 10
uv run pdfkit extract-pages input.pdf output.pdf --pages "1,3-5,8-10"
uv run pdfkit extract-images input.pdf output_dir
uv run pdfkit scan-analyze input.pdf preview_dir
uv run pdfkit scan-cleanup input.pdf cleaned.pdf --strength 0.7 --white-point 244 --contrast 1.1 --dpi-cap 300 --jpeg-quality 92
```

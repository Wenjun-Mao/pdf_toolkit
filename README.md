# PDF Kit

Single-user PDF utility app built with FastAPI, HTMX, Redis/RQ, SQLite, and PyMuPDF.

## Features

- Merge 2+ PDFs into one output
- Combine PDFs and images into one ordered PDF
- Combine multiple images into one PDF
- Build one PDF page from top half of image 1 and bottom half of image 2
- Split one PDF by explicit ranges or every N pages
- Extract selected pages into a new PDF
- Export embedded images directly from PDFs
- Analyze and clean scanned-page backgrounds while preserving existing OCR/text layers
- Run through the web UI, the CLI, or a Jupyter notebook

## Local Development

```powershell
uv sync
$env:PDFKIT_RUN_JOBS_INLINE = "true"
uv run pdfkit-web
```

`PDFKIT_RUN_JOBS_INLINE=true` is useful when you want to test locally without Redis.

## Docker Compose

1. Create `secrets/admin_password.txt`
2. Create `secrets/session_secret.txt`
3. Create `secrets/require_login.txt` with `false` to leave the app open or `true` to require login
4. Start the stack:

```powershell
docker compose up -d --build
```

Services:

- `web` serves the FastAPI app on port `8100`
- `worker` runs RQ background jobs
- `redis` backs the queue
- The Compose project name is `pdf-toolkit`

## VPS nginx Note

Proxy nginx to the app container on port `8100` and forward:

- `Host`
- `X-Real-IP`
- `X-Forwarded-For`
- `X-Forwarded-Proto`

Also set `client_max_body_size` high enough for large PDF uploads.

## CLI Usage

```powershell
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

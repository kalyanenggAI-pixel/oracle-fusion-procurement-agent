# Oracle Fusion Procurement Agent

External AI agent that reads a supplier quote PDF, extracts line items with OpenAI, maps them to Oracle Fusion purchasing data, and prepares a purchase requisition through Oracle Fusion REST APIs.

This repo is designed to be shared publicly without hosting your own app.

## What this repo supports

- Local dry-run testing with a sample quote PDF
- GitHub Codespaces browser-based runs
- GitHub Actions manual dry-run workflow
- Real Oracle Fusion execution when you provide your own credentials

## What this repo does not require

- No Oracle-hosted AI tooling
- No always-on server
- No public deployment

## Quick demo

You only need an OpenAI API key for the dry-run demo.

### Option 1: Run locally

1. Copy `.env.example` to `.env`, or use the included `.env` for demo mode.
2. Set `OPENAI_API_KEY` in `.env`.
3. Run:

```bash
python -m pip install -r requirements.txt
python main.py --pdf quotes/sample_supplier_quote.pdf
```

On Windows, you can also double-click `start_demo.bat`.

### Option 2: Run in GitHub Codespaces

1. Open the repository on GitHub.
2. Click `Code` -> `Codespaces` -> `Create codespace on main`.
3. In the Codespaces terminal, run:

```bash
cp .env.example .env
```

4. Edit `.env` and set:

```env
OPENAI_API_KEY=your_key_here
FUSION_BASE_URL=https://dummy.fa.us2.oraclecloud.com
FUSION_USERNAME=your.email@company.com
FUSION_PASSWORD=testpassword
FUSION_REST_VERSION=11.13.18.05
FUSION_CURRENCY=USD
DRY_RUN=true
```

5. Start the demo:

```bash
./start_demo.sh
```

### Option 3: Run with GitHub Actions

This repo includes a manual workflow at `.github/workflows/dry-run-demo.yml`.

For your own repo:

1. Go to `Settings` -> `Secrets and variables` -> `Actions`.
2. Add a repository secret named `OPENAI_API_KEY`.
3. Open the `Actions` tab.
4. Run the `Dry Run Demo` workflow manually.

For public users:

- Fork the repo
- Add their own `OPENAI_API_KEY` secret
- Run the same workflow in their fork

## Real Oracle Fusion usage

To call a real Fusion environment, update `.env`:

```env
OPENAI_API_KEY=your_key_here
FUSION_BASE_URL=https://your-instance.fa.us2.oraclecloud.com
FUSION_USERNAME=your.username@company.com
FUSION_PASSWORD=your_password
FUSION_REST_VERSION=11.13.18.05
FUSION_CURRENCY=USD
DRY_RUN=false
```

Optional overrides:

```env
FUSION_BU_NAME=Vision Operations
FUSION_REQUESTER_EMAIL=requester@company.com
```

If `FUSION_BU_NAME` and `FUSION_REQUESTER_EMAIL` are not set, the app will try to discover them dynamically from the connected Fusion environment.

## Included sample assets

- `quotes/sample_supplier_quote.pdf`
- `start_demo.bat`
- `start_demo.sh`

## Security notes

- Never commit a real `.env` file with secrets
- Never store your OpenAI API key or Fusion password in the repo
- For public usage, prefer GitHub repository secrets or Codespaces secrets

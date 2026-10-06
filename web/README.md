# Pump Callout Evidence Atlas

A static, read-only dashboard generated from the local Pump.fun callout analysis. It contains no API keys and makes no third-party API calls from the browser.

## Preview locally

```powershell
npm run build
npm run dev
```

Open `http://127.0.0.1:4173`.

## Deploy to Vercel

Upload this `web` directory as the project root, or set the Vercel Root Directory to `web` when importing the full repository. Vercel will use `vercel.json`, run `npm run build`, and publish `dist`.

## Refresh the snapshot

After rerunning the Python analyzer, replace `public/data/analysis.json` and the CSV files in `public/downloads`, then rebuild. Keep credentials in the repository root `.env`; never add them to this directory.

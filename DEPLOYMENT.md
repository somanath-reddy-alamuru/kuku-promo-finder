# Deploy Promo Finder

This repository is prepared for **Streamlit Community Cloud**. The public UI supports a keyword baseline without credentials. AI mode requires each visitor to provide their own provider, model ID and API key for that session.

Official guide: https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy

## 1. Keep private material out of GitHub

Never commit or upload:

- Kuku's 50-episode screenplay pack or any other confidential screenplay.
- ZIP files containing source screenplays.
- Run folders such as `outputs/`, `outputs_ai/`, `outputs_baseline/` or `raw_outputs/`.
- `llm_calls/`, model caches, temporary files or generated comparison results.
- `gold.csv` containing manually selected reference timestamps.
- `.env`, `.streamlit/secrets.toml`, API keys or credentials.

The included `.gitignore` excludes common private paths, but it is not a substitute for checking the files you stage.

## 2. Prepare the GitHub repository

1. Create a repository named `promo-finder` on GitHub.
2. For the simplest free Streamlit Community Cloud deployment, make it public after confirming it contains only the intended source files and the invented demo episode.
3. Extract this ZIP and open the `kuku_promos` folder.
4. Upload the **contents** of `kuku_promos` to the root of the GitHub repository. The repository root should contain `app.py`, `requirements.txt`, `promo_finder/`, `examples/` and `.streamlit/`.

Alternatively, push from PowerShell opened in that folder:

```powershell
git init
git add .
git status --short
```

Inspect the staged list. Confirm that no private screenplay, ZIP, raw output, cache, `.env` or secrets file is staged. Then:

```powershell
git commit -m "Prepare Promo Finder deployment"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/promo-finder.git
git push -u origin main
```

If `origin` already exists, inspect `git remote -v` rather than adding another remote.

## 3. Create the Streamlit app

1. Open https://share.streamlit.io and sign in with GitHub.
2. Authorise access to the repository.
3. Choose **Create app**.
4. Select the repository, `main` branch and `app.py` as the main file path.
5. Select Python 3.12 in the advanced settings if the option is available.
6. Leave the Secrets field empty for this version. The application does not require a shared owner API key.
7. Deploy and wait for the build to complete.

## 4. Verify the deployed app

Open the actual Streamlit URL in an incognito/private browser window. Check each item:

- Landing page loads.
- Invented demo screenplay parses and can be analysed in keyword mode.
- A non-confidential sample ZIP uploads and parses.
- Keyword mode completes and displays the Overview, Ranked promos, Promo inspector, Quality and Downloads tabs.
- CSV, JSON and raw-output downloads work.
- AI mode remains disabled until the visitor provides their own provider, model ID and key.
- A small non-confidential input can complete an AI-mode run with the visitor's own key.
- Clear session removes the current session's working files and results.

Do not claim deployment is complete until the real URL has been opened and these checks have been performed.

## 5. Public-app security and privacy

- AI mode uses a key entered by the visitor in the current browser session. Do not add an owner API key to Streamlit Secrets or source code.
- Only preset hosted providers are exposed in the public UI; custom endpoints remain available through the local CLI.
- Screenplay dialogue, actions, camera notes and timestamps are sent to the selected provider in AI mode. The visitor is responsible for confirming they are allowed to send that material.
- Uploads are parsed from memory; ZIP contents are not extracted to disk. Each browser session has a separate temporary directory for model-call caching. Use **Clear session** when finished.
- This is an assignment prototype, not a hardened multi-tenant production service. Do not upload sensitive personal data or confidential scripts without an appropriate security and data-processing review.

## 6. Update the deployment

Commit and push changes to the selected GitHub branch. Streamlit Community Cloud detects the new commit and rebuilds the application. Changes to `requirements.txt` can trigger dependency installation.

## 7. Before sharing with Kuku

- Add the verified public URL to the solution PDF and assignment form.
- Submit the two-page solution PDF and raw-output ZIP privately through the assignment form.
- Keep the screenplay archive and raw prompts/responses out of public GitHub.
- Complete a real AI-mode run and a human-reference evaluation before claiming comparative AI quality.

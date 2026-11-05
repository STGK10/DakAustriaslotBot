# Free cloud setup: Dakar appointment Telegram monitor

This version uses **GitHub Actions** instead of a VPS. Your computer does not need to stay on.

It checks the Dakar appointment portal for:
- Residence permit / Aufenthaltstitel
- Legalization / Beglaubigungen

Configured for one person. It sends possible availability alerts to your Telegram chat. It does not book appointments or bypass CAPTCHA/security controls.

## What you need

- A free GitHub account: https://github.com/signup
- A Telegram account
- A Telegram bot token from `@BotFather`

## Step 1 — Create your Telegram bot

1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`.
3. Follow the instructions and copy the bot token. Keep it private.
4. Open your new bot using the link BotFather gives you and press **Start**.

## Step 2 — Find your Telegram chat ID

For a quick setup, open this URL in your browser, replacing `YOUR_TOKEN` with your bot token:

`https://api.telegram.org/botYOUR_TOKEN/getUpdates`

Find the number after `"chat":{"id":` in the message you sent to your bot. That is your chat ID. Do not share the URL or token.

If `result` is empty, send `/start` to your bot and refresh the URL.

## Step 3 — Create a GitHub repository

1. Sign in at https://github.com.
2. Click the **+** sign, then **New repository**.
3. Name it `dakar-appointment-monitor`.
4. Choose **Public** if you want scheduled runs to be free without a monthly minutes allowance. Important: a public repository makes the source code visible to everyone. The bot token and chat ID must ONLY be added as GitHub Secrets, never written into code.
5. Tick **Add a README file**, then click **Create repository**.

## Step 4 — Upload this project

1. Open the new repository.
2. Click **Add file** → **Upload files**.
3. Extract this ZIP on your computer first.
4. Upload all files and folders inside `dakar_free_github_monitor` (including the hidden `.github` folder and its `workflows/monitor.yml` file).
5. Click **Commit changes**.

If the upload page does not show hidden `.github` files, create `.github/workflows/monitor.yml` directly on GitHub and paste the contents from the ZIP's workflow file.

## Step 5 — Add your private Telegram credentials

In your repository:
1. Open **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Add a secret named `TELEGRAM_BOT_TOKEN` and paste the BotFather token.
4. Add another secret named `TELEGRAM_CHAT_ID` and paste your numeric chat ID.

Never put either value in a normal file or public message.

## Step 6 — Enable and test

1. Open the repository's **Actions** tab.
2. If GitHub asks to enable workflows, click **I understand my workflows, go ahead and enable them**.
3. Open **Dakar appointment monitor**.
4. Click **Run workflow** to test it immediately.
5. Open the run and inspect its logs. If it fails to reach the calendar, download the `dakar-monitor-debug` artifact.

The scheduled workflow is configured for every 15 minutes. GitHub says scheduled runs can be delayed during high load, so this is not an exact-time or instant guarantee.

## Privacy and cost notes

- GitHub's standard Actions runners are free for public repositories. GitHub Free currently includes 2,000 Actions minutes/month for private repositories; this browser workflow may consume that allowance, so a private repository at 15-minute intervals may not fit.
- Public means anyone can view the source code. Secrets added under repository Actions Secrets are not exposed in the source files, but do not put personal details in logs or screenshots.
- You can keep the repository private and change the schedule to every 30 minutes (`*/30 * * * *`), but watch your usage in GitHub Settings → Billing.
- No payment method should be needed for standard public-repository Actions usage, but review GitHub's current billing settings before enabling any paid add-ons.

## Very important: verify the calendar selector before depending on alerts

The supplied screenshot confirms the Dakar portal and the category names, but it does not show the calendar's HTML structure. `app/portal.py` currently uses a conservative generic candidate detector and saves screenshots/HTML/text into `data/debug/`.

After the first manual run:
1. Open the workflow run in Actions.
2. Download the debug artifact if present.
3. Send the calendar screenshot here (hide personal data).
4. I can then help tune the selector so it detects actual bookable appointment choices, not ordinary calendar controls.

A candidate notification must always be verified on the official booking page. Availability can disappear before you open it.

# Control the bot from Telegram (free, no server, no card)

You need ~20 minutes once. Do the steps in order.

## 1. Find your chat id
Message your bot once, then open (replace TOKEN, no `<>` brackets):
`https://api.telegram.org/botTOKEN/getUpdates`
Your id is the number after `"chat":{"id":`. (It is the same value as your
`TELEGRAM_CHAT_ID` GitHub secret.)

## 2. Make a GitHub token (only for this repo)
GitHub → Settings → Developer settings → Fine-grained tokens → Generate.
- Repository access: **Only select repositories → your private instance repo**
- Permissions: **Actions: Read and write**, **Contents: Read and write**
- Expiry: 1 year (put a reminder to renew it)

Copy it once. Never paste it in chat or commit it.

## 3. Create the Worker
dash.cloudflare.com → sign up (free) → Workers & Pages → Create → Worker →
name it `job-bot` → Deploy → **Edit code** → delete the sample, paste all of
`worker/worker.js` → Deploy.

## 4. Add the secrets
Worker → Settings → Variables and Secrets → add (type **Secret**):

| Name | Value |
|---|---|
| TELEGRAM_BOT_TOKEN | your bot token |
| WEBHOOK_SECRET | a long random string you make up (letters/digits only) |
| OWNER_CHAT_ID | your chat id from step 1 |
| GITHUB_TOKEN | the token from step 2 |
| GITHUB_REPO | `your-user/your-private-repo`: the repo with your `config.yaml`, `prefs.json` and data files (see `docs/private-instance.md`) |

Then **Deploy** again.

## 4b. Tell Telegram where the Worker lives
Open this once in your browser (fill in the three parts, no brackets):
`https://api.telegram.org/botTOKEN/setWebhook?url=https://job-bot.YOURNAME.workers.dev&secret_token=YOUR_WEBHOOK_SECRET`
You should see `"ok":true`. (Your Worker URL is shown on its overview page.)

## 5. Try it
Send `/help` to the bot. Then `/run`, wait 1–2 minutes, and expect the 📊 summary.

## Notes
- Only your chat id is obeyed; everyone else is ignored.
- Settings you change are saved in `prefs.json` in the repo and merged over
  `config.yaml` / `sources.yaml`, so the files you edit by hand are never overwritten.
- `/addsource` takes JSON, e.g.
  `/addsource {"name":"My API","type":"json","url":"https://example.com/jobs","items_path":"data","id":"id","title":"title","company":"company","link":"url","text":["title","description"]}`
- Keyed APIs (`${MY_KEY}`) can only be added by editing `sources.yaml` yourself.
- Free-plan limits (100k requests/day) are far above what one person uses.

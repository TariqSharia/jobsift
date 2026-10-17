// Cloudflare Worker — controls the job-alert bot from Telegram.
// Single file, no dependencies. Paste it into the Cloudflare dashboard editor.
//
// Secrets / variables (Worker -> Settings -> Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  bot token from @BotFather
//   WEBHOOK_SECRET      any random string (also given to Telegram's setWebhook)
//   OWNER_CHAT_ID       your Telegram chat id; everyone else is ignored
//   GITHUB_TOKEN        fine-grained token, ONE repo, Actions:write + Contents:write
//   GITHUB_REPO         your-user/your-private-repo (the repo that holds config.yaml, prefs.json and the data files)
//   WORKFLOW_FILE       optional, default job_alert.yml
//   BRANCH              optional, default main

const LIST_NAMES = { include: "include_keywords", exclude: "exclude_keywords", strong: "strong_signal_keywords" };
const PREFS_PATH = "prefs.json";
const ATS_TYPES = ["greenhouse", "ashby", "lever"];
const SAFE_TOKEN = /^[A-Za-z0-9_.-]+$/;

export const HELP = `<b>Job bot commands</b>
/run — fetch jobs now
/status — last run result
/show — current keywords and sources
/include word, word — must match one of these
/exclude word, word — reject jobs containing these
/strong word — mark as ✅ strong signal
/remove include|exclude|strong word
/inbox — latest matches with their ref codes
/job ref — full text of one job (copy it into the chat)
/appian on|off
/maxyears N|off — skip postings asking more years of experience
/sources — list sources
/addrss Name https://feed.url
/addcompany greenhouse|ashby|lever name — follow a company's jobs
/addsource {json} — advanced (see README)
/removesource Name`;

// ---------- small helpers ----------
export const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function splitWords(s) {
  return s.split(",").map((w) => w.trim()).filter(Boolean);
}

const hasWord = (arr, w) => arr.some((x) => x.toLowerCase() === w.toLowerCase());
const without = (arr, w) => arr.filter((x) => x.toLowerCase() !== w.toLowerCase());

// Reads top-level `key:` followed by `- item` lines out of config.yaml (flat file, no YAML parser needed).
export function parseYamlLists(text) {
  const out = {};
  let key = null;
  for (const line of text.split("\n")) {
    const k = line.match(/^([A-Za-z_]+):/);
    if (k) { key = k[1]; out[key] = []; continue; }
    const item = line.match(/^\s+-\s+"?([^"#]+?)"?\s*(?:#.*)?$/);
    if (item && key) out[key].push(item[1].trim());
  }
  return out;
}

export function parseSourceNames(text) {
  return [...text.matchAll(/^- name:\s*(.+)$/gm)].map((m) => m[1].trim());
}

export function emptyPrefs() {
  return { include: { add: [], remove: [] }, exclude: { add: [], remove: [] }, strong: { add: [], remove: [] }, sources: { add: [], remove: [] } };
}

function normalize(p) {
  const base = emptyPrefs();
  for (const k of Object.keys(LIST_NAMES)) base[k] = { add: p?.[k]?.add ?? [], remove: p?.[k]?.remove ?? [] };
  base.sources = { add: p?.sources?.add ?? [], remove: p?.sources?.remove ?? [] };
  if (typeof p?.include_appian === "boolean") base.include_appian = p.include_appian;
  if (Number.isInteger(p?.max_years) && p.max_years >= 0 && p.max_years <= 30) base.max_years = p.max_years;
  return base;
}

// ---------- pure prefs mutations ----------
export function addWords(prefs, list, words) {
  for (const w of words) {
    prefs[list].remove = without(prefs[list].remove, w);
    if (!hasWord(prefs[list].add, w)) prefs[list].add.push(w);
  }
  return prefs;
}

export function removeWord(prefs, list, word) {
  prefs[list].add = without(prefs[list].add, word);
  if (!hasWord(prefs[list].remove, word)) prefs[list].remove.push(word);
  return prefs;
}

export function validateSource(src) {
  if (!src || typeof src !== "object") return "Source must be a JSON object.";
  if (!src.name || typeof src.name !== "string") return "Source needs a name.";
  if (!["json", "rss", "hn_hiring", ...ATS_TYPES].includes(src.type)) return "type must be json, rss, hn_hiring, greenhouse, ashby or lever.";
  if (JSON.stringify(src).includes("${")) return "${…} secrets are not allowed from chat — put keyed sources in sources.yaml.";
  if (ATS_TYPES.includes(src.type)) {
    const ok = Array.isArray(src.companies) && src.companies.length > 0 && src.companies.every((c) => typeof c === "string" && SAFE_TOKEN.test(c));
    return ok ? null : "companies must be a list of board names (letters, digits, - _ .).";
  }
  if (src.type !== "hn_hiring" && !/^https:\/\//.test(src.url || "")) return "url must start with https://";
  return null;
}

export function addSource(prefs, src) {
  prefs.sources.remove = without(prefs.sources.remove, src.name);
  prefs.sources.add = prefs.sources.add.filter((s) => s.name.toLowerCase() !== src.name.toLowerCase());
  prefs.sources.add.push(src);
  return prefs;
}

export function removeSource(prefs, name) {
  prefs.sources.add = prefs.sources.add.filter((s) => s.name.toLowerCase() !== name.toLowerCase());
  if (!hasWord(prefs.sources.remove, name)) prefs.sources.remove.push(name);
  return prefs;
}

// ---------- GitHub client (injectable for tests) ----------
const b64 = (s) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const unb64 = (s) => new TextDecoder().decode(Uint8Array.from(atob(s.replace(/\n/g, "")), (c) => c.charCodeAt(0)));

export function makeGithub(env, fetchFn = fetch) {
  const repo = env.GITHUB_REPO;
  const branch = env.BRANCH || "main";
  const wf = env.WORKFLOW_FILE || "job_alert.yml";
  const call = (path, init = {}) =>
    fetchFn(`https://api.github.com/repos/${repo}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "job-alert-worker",
        ...(init.headers || {}),
      },
    });
  return {
    async getFile(path) {
      const r = await call(`/contents/${path}?ref=${branch}`);
      if (r.status === 404) return null;
      if (!r.ok) throw new Error(`GitHub ${r.status} reading ${path}`);
      const j = await r.json();
      return { text: unb64(j.content), sha: j.sha };
    },
    async putFile(path, text, sha, message) {
      const body = { message, content: b64(text), branch };
      if (sha) body.sha = sha;
      const r = await call(`/contents/${path}`, { method: "PUT", body: JSON.stringify(body) });
      if (r.status === 409 || r.status === 422) return { conflict: true };
      if (!r.ok) throw new Error(`GitHub ${r.status} writing ${path}`);
      return { conflict: false };
    },
    async dispatch() {
      const r = await call(`/actions/workflows/${wf}/dispatches`, { method: "POST", body: JSON.stringify({ ref: branch }) });
      if (r.status !== 204) throw new Error(`GitHub ${r.status} starting the run`);
    },
    async lastRun() {
      const r = await call(`/actions/workflows/${wf}/runs?per_page=1`);
      if (!r.ok) throw new Error(`GitHub ${r.status} reading runs`);
      return (await r.json()).workflow_runs?.[0] ?? null;
    },
  };
}

async function updatePrefs(gh, mutate, message) {
  for (let i = 0; i < 3; i++) {
    const file = await gh.getFile(PREFS_PATH);
    let prefs;
    try { prefs = normalize(file ? JSON.parse(file.text) : {}); } catch { prefs = emptyPrefs(); }
    const err = mutate(prefs);
    if (err) return err;
    const res = await gh.putFile(PREFS_PATH, JSON.stringify(prefs, null, 2) + "\n", file?.sha, message);
    if (!res.conflict) return null;
  }
  return "Could not save (the repo changed twice in a row). Try again.";
}

// ---------- command handling ----------
export async function handleCommand(text, gh) {
  const m = text.trim().match(/^\/(\w+)(?:@\w+)?\s*([\s\S]*)$/);
  if (!m) return "Send /help to see what I can do.";
  const cmd = m[1].toLowerCase();
  const arg = m[2].trim();

  try {
    switch (cmd) {
      case "start":
      case "help":
        return HELP;

      case "run":
        await gh.dispatch();
        return "▶️ Run started. Results arrive in about 1–2 minutes.";

      case "status": {
        const r = await gh.lastRun();
        if (!r) return "No runs yet.";
        const icon = r.status !== "completed" ? "⏳" : r.conclusion === "success" ? "✅" : "❌";
        return `${icon} Last run: ${r.status}${r.conclusion ? " / " + r.conclusion : ""}\nStarted: ${r.run_started_at || r.created_at}`;
      }

      case "show": {
        const cfgFile = await gh.getFile("config.yaml");
        const prefsFile = await gh.getFile(PREFS_PATH);
        const base = cfgFile ? parseYamlLists(cfgFile.text) : {};
        const prefs = normalize(prefsFile ? JSON.parse(prefsFile.text) : {});
        const lines = [];
        for (const [short, key] of Object.entries(LIST_NAMES)) {
          const words = [...(base[key] || []).filter((w) => !hasWord(prefs[short].remove, w))];
          for (const w of prefs[short].add) if (!hasWord(words, w)) words.push(w);
          lines.push(`<b>${short}</b> (${words.length}): ${esc(words.join(", ") || "—")}`);
        }
        lines.push(`<b>max years</b>: ${prefs.max_years === undefined ? "from config.yaml" : prefs.max_years || "off"}`);
        lines.push(`<b>appian</b>: ${(prefs.include_appian ?? true) ? "on" : "off"}`);
        return lines.join("\n\n");
      }

      case "include":
      case "exclude":
      case "strong": {
        const words = splitWords(arg);
        if (!words.length) return `Usage: /${cmd} word, another word`;
        const err = await updatePrefs(gh, (p) => addWords(p, cmd, words) && null, `bot: ${cmd} ${words.join(", ")}`);
        return err || `✅ ${cmd}: added ${esc(words.join(", "))}. Applies from the next run.`;
      }

      case "remove": {
        const [list, ...rest] = arg.split(/\s+/);
        const word = rest.join(" ").trim();
        if (!LIST_NAMES[list] || !word) return "Usage: /remove include|exclude|strong word";
        const err = await updatePrefs(gh, (p) => removeWord(p, list, word) && null, `bot: remove ${list} ${word}`);
        return err || `✅ Removed “${esc(word)}” from ${list}.`;
      }

      case "inbox": {
        const f = await gh.getFile("inbox.json");
        let list = [];
        try { list = f ? JSON.parse(f.text) : []; } catch { list = []; }
        if (!list.length) return "Inbox is empty. New matches appear after the next run.";
        return "<b>Latest matches</b>\n" + list.slice(0, 12).map((j) =>
          `<code>${esc(j.ref)}</code> ${j.strong_signal ? "✅ " : ""}${esc(j.title.slice(0, 70))}${j.company ? " — " + esc(j.company) : ""}`).join("\n");
      }

      case "job": {
        const ref = arg.trim().toLowerCase();
        if (!ref) return "Usage: /job ref (the code shown under each job)";
        const f = await gh.getFile("inbox.json");
        let list = [];
        try { list = f ? JSON.parse(f.text) : []; } catch { list = []; }
        const j = list.find((x) => String(x.ref).toLowerCase() === ref);
        if (!j) return `No job with ref ${esc(ref)} in the inbox (it keeps the latest 150).`;
        const head = `${j.title}\n${j.company ? j.company + " · " : ""}${j.source}\n${j.url}\n\n`;
        return esc(head + j.text).slice(0, 3900);
      }

      case "appian": {
        if (!["on", "off"].includes(arg.toLowerCase())) return "Usage: /appian on|off";
        const on = arg.toLowerCase() === "on";
        const err = await updatePrefs(gh, (p) => { p.include_appian = on; return null; }, `bot: appian ${arg}`);
        return err || `✅ Appian jobs ${on ? "on" : "off"}.`;
      }

      case "maxyears": {
        const a = arg.toLowerCase();
        const n = a === "off" ? 0 : /^\d{1,2}$/.test(a) ? Number(a) : -1;
        if (n < 0 || n > 30) return "Usage: /maxyears 5  (or /maxyears off)";
        const err = await updatePrefs(gh, (p) => { p.max_years = n; return null; }, `bot: maxyears ${a}`);
        return err || (n ? `✅ Skipping postings that ask for more than ${n} years of experience. Applies from the next run.` : "✅ Years-of-experience check is off.");
      }

      case "sources": {
        const f = await gh.getFile("sources.yaml");
        const prefsFile = await gh.getFile(PREFS_PATH);
        const prefs = normalize(prefsFile ? JSON.parse(prefsFile.text) : {});
        const names = (f ? parseSourceNames(f.text) : []).filter((n) => !hasWord(prefs.sources.remove, n));
        for (const s of prefs.sources.add) names.push(`${s.name} (added)`);
        return `<b>Sources</b>\n${esc(names.join("\n") || "none")}`;
      }

      case "addrss": {
        const parts = arg.split(/\s+/);
        const url = parts.pop();
        const name = parts.join(" ");
        if (!name || !url) return "Usage: /addrss Name https://example.com/feed.xml";
        const src = { name, type: "rss", url };
        const bad = validateSource(src);
        if (bad) return `❌ ${bad}`;
        const err = await updatePrefs(gh, (p) => addSource(p, src) && null, `bot: add source ${name}`);
        return err || `✅ Added source “${esc(name)}”. It runs from the next run — use /run to try it now.`;
      }

      case "addcompany": {
        const [ats, token] = arg.split(/\s+/);
        if (!ATS_TYPES.includes(ats) || !token) return "Usage: /addcompany ashby checkly\n(greenhouse | ashby | lever, then the board name from the company's jobs URL)";
        const src = { name: `${ats}:${token}`, type: ats, companies: [token] };
        const bad = validateSource(src);
        if (bad) return `❌ ${bad}`;
        const err = await updatePrefs(gh, (p) => addSource(p, src) && null, `bot: follow ${ats}:${token}`);
        return err || `✅ Following ${esc(token)} on ${ats}. Use /run to test; if the summary shows “${esc(token)}: HTTP 404”, the board name is wrong.`;
      }

      case "addsource": {
        let src;
        try { src = JSON.parse(arg); } catch { return "❌ That is not valid JSON."; }
        const bad = validateSource(src);
        if (bad) return `❌ ${bad}`;
        const err = await updatePrefs(gh, (p) => addSource(p, src) && null, `bot: add source ${src.name}`);
        return err || `✅ Added source “${esc(src.name)}”. Use /run to test it.`;
      }

      case "removesource": {
        if (!arg) return "Usage: /removesource Name";
        const err = await updatePrefs(gh, (p) => removeSource(p, arg) && null, `bot: remove source ${arg}`);
        return err || `✅ Removed source “${esc(arg)}”.`;
      }

      default:
        return "Unknown command. Send /help.";
    }
  } catch (e) {
    return `❌ ${esc(e.message)}`;
  }
}

async function reply(env, chatId, text, fetchFn = fetch) {
  await fetchFn(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text: text.slice(0, 4000), parse_mode: "HTML", disable_web_page_preview: true }),
  });
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("job-alert worker is running");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try { update = await request.json(); } catch { return new Response("ok"); }
    const msg = update.message;
    // Owner-only: anyone else is silently ignored.
    if (!msg?.text || String(msg.chat?.id) !== String(env.OWNER_CHAT_ID)) return new Response("ok");
    const answer = await handleCommand(msg.text, makeGithub(env));
    await reply(env, msg.chat.id, answer);
    return new Response("ok");
  },
};
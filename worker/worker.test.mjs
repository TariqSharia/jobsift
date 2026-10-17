import assert from "node:assert/strict";
import test from "node:test";
import worker, { addWords, emptyPrefs, handleCommand, parseSourceNames, parseYamlLists, removeWord, validateSource } from "./worker.js";

function fakeGh(files = {}) {
  const store = { ...files };
  const calls = [];
  return {
    store, calls,
    async getFile(p) { return p in store ? { text: store[p], sha: "sha-" + store[p].length } : null; },
    async putFile(p, text) { store[p] = text; calls.push(["put", p]); return { conflict: false }; },
    async dispatch() { calls.push(["dispatch"]); },
    async lastRun() { return { status: "completed", conclusion: "success", run_started_at: "t" }; },
  };
}

test("parseYamlLists reads flat config", () => {
  const out = parseYamlLists('include_keywords:\n  - "node.js"\n  - python  # c\nexclude_keywords:\n  - "casino"\nother: 5\n');
  assert.deepEqual(out.include_keywords, ["node.js", "python"]);
  assert.deepEqual(out.exclude_keywords, ["casino"]);
});

test("parseSourceNames", () => {
  assert.deepEqual(parseSourceNames("- name: A\n  type: rss\n- name: HN Who Is Hiring\n"), ["A", "HN Who Is Hiring"]);
});

test("add then remove word", () => {
  const p = emptyPrefs();
  addWords(p, "exclude", ["hybrid"]);
  addWords(p, "exclude", ["Hybrid"]);
  assert.deepEqual(p.exclude.add, ["hybrid"]);
  removeWord(p, "exclude", "hybrid");
  assert.deepEqual(p.exclude, { add: [], remove: ["hybrid"] });
  addWords(p, "exclude", ["hybrid"]);
  assert.deepEqual(p.exclude.remove, []);
});

test("validateSource blocks secrets, http and bad types", () => {
  assert.ok(validateSource({ name: "x", type: "rss", url: "http://a" }));
  assert.ok(validateSource({ name: "x", type: "json", url: "https://a/${TELEGRAM_BOT_TOKEN}" }));
  assert.ok(validateSource({ name: "x", type: "bash", url: "https://a" }));
  assert.equal(validateSource({ name: "x", type: "rss", url: "https://a" }), null);
});

test("/exclude saves prefs.json", async () => {
  const gh = fakeGh();
  const out = await handleCommand("/exclude on-site, hybrid", gh);
  assert.match(out, /added/);
  assert.deepEqual(JSON.parse(gh.store["prefs.json"]).exclude.add, ["on-site", "hybrid"]);
});

test("/run dispatches, /status reports", async () => {
  const gh = fakeGh();
  assert.match(await handleCommand("/run", gh), /Run started/);
  assert.deepEqual(gh.calls[0], ["dispatch"]);
  assert.match(await handleCommand("/status@MyBot", gh), /success/);
});

test("/show merges base and prefs, escapes html", async () => {
  const gh = fakeGh({ "config.yaml": 'include_keywords:\n  - "python"\n  - "go"\nexclude_keywords:\n  - "casino"\n' });
  await handleCommand("/remove include go", gh);
  await handleCommand("/include R&D", gh);
  const out = await handleCommand("/show", gh);
  assert.match(out, /python, R&amp;D/);
  assert.doesNotMatch(out, /\bgo\b/);
});

test("/addrss validates and stores", async () => {
  const gh = fakeGh();
  assert.match(await handleCommand("/addrss My Board http://x", gh), /❌/);
  assert.match(await handleCommand("/addrss My Board https://x/feed", gh), /Added/);
  assert.equal(JSON.parse(gh.store["prefs.json"]).sources.add[0].name, "My Board");
  assert.match(await handleCommand("/removesource My Board", gh), /Removed/);
  assert.deepEqual(JSON.parse(gh.store["prefs.json"]).sources.add, []);
});

test("webhook rejects wrong secret and ignores strangers", async () => {
  const env = { WEBHOOK_SECRET: "s", OWNER_CHAT_ID: "1", TELEGRAM_BOT_TOKEN: "t", GITHUB_REPO: "a/b", GITHUB_TOKEN: "g" };
  const bad = await worker.fetch(new Request("https://w/", { method: "POST", body: "{}" }), env);
  assert.equal(bad.status, 403);
  let called = false;
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => { called = true; return new Response("{}"); };
  const body = JSON.stringify({ message: { text: "/run", chat: { id: 999 } } });
  const res = await worker.fetch(new Request("https://w/", { method: "POST", body, headers: { "X-Telegram-Bot-Api-Secret-Token": "s" } }), env);
  globalThis.fetch = realFetch;
  assert.equal(res.status, 200);
  assert.equal(called, false);
});

test("/addcompany validates and stores company sources", async () => {
  const gh = fakeGh();
  assert.match(await handleCommand("/addcompany ashby", gh), /Usage/);
  assert.match(await handleCommand("/addcompany ftp x", gh), /Usage/);
  assert.match(await handleCommand("/addcompany ashby ../evil", gh), /❌/);
  assert.match(await handleCommand("/addcompany ashby checkly", gh), /Following/);
  const src = JSON.parse(gh.store["prefs.json"]).sources.add[0];
  assert.deepEqual(src, { name: "ashby:checkly", type: "ashby", companies: ["checkly"] });
});

test("/inbox lists and /job returns full text", async () => {
  const inbox = [{ ref: "ab12cd", title: "Backend & Co", company: "Acme", source: "S", url: "https://u", strong_signal: true, text: "Remote <worldwide> role" }];
  const gh = fakeGh({ "inbox.json": JSON.stringify(inbox) });
  const list = await handleCommand("/inbox", gh);
  assert.match(list, /ab12cd/);
  assert.match(list, /Backend &amp; Co/);
  const job = await handleCommand("/job AB12CD", gh);
  assert.match(job, /Remote &lt;worldwide&gt; role/);
  assert.match(await handleCommand("/job nope00", gh), /No job/);
  assert.match(await handleCommand("/inbox", fakeGh()), /empty/);
});
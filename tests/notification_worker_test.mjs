import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { test } from "node:test";
import worker from "../notifications/worker.mjs";

function database() {
  const sql = new DatabaseSync(":memory:");
  sql.exec(readFileSync(new URL("../notifications/schema.sql", import.meta.url), "utf8"));
  return {
    prepare(query) {
      return { bind(...args) {
        const stmt = sql.prepare(query);
        return { run: () => ({meta: {changes: stmt.run(...args).changes}}), first: () => stmt.get(...args) };
      }};
    },
    batch(stmts) {
      sql.exec("BEGIN");
      try {
        const result = stmts.map(s => s.run());
        sql.exec("COMMIT");
        return result;
      } catch (err) {
        sql.exec("ROLLBACK");
        throw err;
      }
    }
  };
}

test("Private device pairing, event delivery, expiration and revocation", async t => {
  const env = {DB:database(), TELEGRAM_TOKEN:"fake", MAX_TOKEN:"fake", TELEGRAM_WEBHOOK_SECRET:"tg-secret", MAX_WEBHOOK_SECRET:"max-secret"};
  const out = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    const body = JSON.parse(options.body);
    out.push({target:body.chat_id || new URL(url).searchParams.get("user_id"), text:body.text, host:new URL(url).hostname});
    return Response.json({ok:true});
  });
  const post = (path, body, headers) => worker.fetch(new Request("https://example.org" + path, {
    method:"POST", body:JSON.stringify(body), headers
  }), env);
  const tg = (user, text, type = "private", secret = "tg-secret") => post("/hook/telegram",
    {message:{chat:{type,id:user}, from:{id:user}, text}}, {"X-Telegram-Bot-Api-Secret-Token":secret});
  const max = event => post("/hook/max", event, {"X-Max-Bot-Api-Secret":"max-secret"});
  const device = (key,path,body={}) => post("/" + path, body, {Authorization:"Bearer " + key.repeat(43)});
  const code = () => out.at(-1).text.match(/#([a-f0-9]{32})/)[1];

  assert.equal((await tg(101, "/start","private","wrong")).status,403);
  await tg(101,"/start","group");
  await max({update_type:"message_created", message:{recipient:{chat_type:"chat"},sender:{user_id:202},body:{text:"/start"}}});
  assert.equal(out.length,0);

  await tg(101,"/start");
  const first = code();
  await max({update_type:"bot_started",user:{user_id:202}});
  const second = code();
  assert.deepEqual(out.map(m=>m.target),["101","202"]);
  assert.equal((await device("a","bind",{code:first})).status,200);
  assert.equal((await device("x","bind",{code:first})).status,410);
  assert.equal((await device("b","bind",{code:second})).status,200);
  out.length=0;
  assert.equal((await device("a","alert",{recipient:"202"})).status,200);
  assert.equal((await device("a","alert")).status,429);
  assert.equal((await device("b","alert")).status,200);
  assert.equal((await device("x","alert")).status,401);
  assert.deepEqual(out.map(m=>[m.host,m.target]),[["api.telegram.org","101"],["platform-api2.max.ru","202"]]);

  await tg(101,"/stop");
  assert.equal((await device("a","test")).status,401);
  assert.equal((await device("b","unlink")).status,200);
  assert.equal((await device("b","test")).status,401);
  await tg(303,"/start");
  const expired = code();
  await worker.scheduled({scheduledTime:Date.now()+601000},env);
  assert.equal((await device("x","bind",{code:expired})).status,410);
});

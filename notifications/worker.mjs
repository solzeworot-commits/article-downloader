// One code per private chat. Only the verified bot webhook supplies the recipient.
const providers = new Set(["telegram", "max"]);
const alertText = "Загрузчик статей ждёт вас. Откройте окно издателя на компьютере и пройдите капчу.";
const testText = "Проверка связи. На этот личный чат подключены уведомления загрузчика статей.";
const ok = () => Response.json({ ok: true });
const fail = status => Response.json({ ok: false }, { status });

async function hash(value) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(bytes), n => n.toString(16).padStart(2, "0")).join("");
}

function secret() {
  return crypto.randomUUID().replaceAll("-", "");
}

async function send(env, provider, recipient, text) {
  const tg = provider === "telegram";
  const target = tg ? "https://api.telegram.org/bot" + env.TELEGRAM_TOKEN + "/sendMessage"
                    : "https://platform-api2.max.ru/messages?user_id=" + encodeURIComponent(recipient);
  const headers = { "Content-Type": "application/json" };
  if (!tg) headers.Authorization = env.MAX_TOKEN;
  const res = await fetch(target, {
    method: "POST", redirect: "error", headers,
    body: JSON.stringify(tg ? { chat_id: recipient, text, link_preview_options: { is_disabled: true } } : { text }),
  });
  if (!res.ok) throw new Error("Messenger unavailable");
}

function privateCommand(provider, event) {
  if (provider === "telegram") {
    const msg = event.message;
    if (!msg || msg.chat?.type !== "private" || msg.from?.is_bot || msg.from?.id !== msg.chat.id) return null;
    return { id: msg.chat.id, text: msg.text };
  }
  if (event.update_type === "bot_started" && !event.user?.is_bot) {
    return { id: event.user?.user_id, text: "/start" };
  }
  const msg = event.message;
  if (event.update_type !== "message_created" || msg?.recipient?.chat_type !== "dialog" || msg.sender?.is_bot) return null;
  return { id: msg.sender?.user_id, text: msg.body?.text };
}

async function webhook(req, env, provider, now) {
  const tg = provider === "telegram";
  const expected = tg ? env.TELEGRAM_WEBHOOK_SECRET : env.MAX_WEBHOOK_SECRET;
  const actual = req.headers.get(tg ? "X-Telegram-Bot-Api-Secret-Token" : "X-Max-Bot-Api-Secret");
  if (!expected || !actual || await hash(actual) !== await hash(expected)) return fail(403);
  const cmd = privateCommand(provider, await req.json());
  if (!cmd || !Number.isSafeInteger(cmd.id) || cmd.id <= 0) return ok();
  const user = String(cmd.id);
  if (cmd.text === "/stop") {
    await env.DB.batch(["codes", "devices"].map(table => env.DB.prepare(
      "DELETE FROM " + table + " WHERE provider=? AND recipient=?").bind(provider, user)));
    return ok();
  }
  if (cmd.text !== "/start") return ok();
  const code = secret();
  const result = await env.DB.prepare(
    "INSERT INTO codes (provider,recipient,digest,issued) VALUES(?,?,?,?) ON CONFLICT(provider,recipient) DO UPDATE SET digest=excluded.digest, issued=excluded.issued WHERE codes.issued<=?"
  ).bind(provider, user, await hash(code), now, now-10).run();
  if (!result.meta.changes) return ok();
  const url = new URL(req.url).origin + "/pair#" + code;
  try {
    await send(env, provider, user, "В загрузчике откройте настройки личных уведомлений и вставьте эту ссылку в течение 10 минут. Она подключает ваш личный чат.\n\n" + url + "\n\nЧтобы отключить все ваши компьютеры, отправьте /stop.");
  } catch (e) {
    await env.DB.prepare("DELETE FROM codes WHERE digest=?").bind(await hash(code)).run();
    throw e;
  }
  return ok();
}

async function handle(req, env) {
  const path = new URL(req.url).pathname;
  if (path === "/pair" && req.method === "GET") {
    return new Response("Скопируйте полную ссылку из сообщения бота в настройки загрузчика.", {headers:{"Content-Type":"text/plain; charset=utf-8","Referrer-Policy":"no-referrer"}});
  }
  if (req.method !== "POST") return fail(405);
  const now = Math.floor(Date.now()/1000);
  const provider = path.split("/")[2];
  if (path === "/hook/" + provider && providers.has(provider)) return webhook(req, env, provider, now);
  const token = req.headers.get("Authorization") || "";
  if (!/^Bearer [A-Za-z0-9_-]{43}$/.test(token)) return fail(401);
  const device = await hash(token.slice(7));
  if (path === "/bind") {
    const { code } = await req.json();
    if (typeof code !== "string" || !/^[a-f0-9]{32}$/.test(code)) return fail(400);
    const digest = await hash(code);
    const [insert] = await env.DB.batch([
      env.DB.prepare("INSERT INTO devices(device,provider,recipient) SELECT ?,provider,recipient FROM codes WHERE digest=? AND issued>?").bind(device,digest,now-600),
      env.DB.prepare("DELETE FROM codes WHERE digest=?").bind(digest)
    ]);
    if (!insert.meta.changes) return fail(410);
    const row = await env.DB.prepare("SELECT provider FROM devices WHERE device=?").bind(device).first();
    return Response.json({provider:row.provider});
  }
  if (path === "/unlink") {
    await env.DB.prepare("DELETE FROM devices WHERE device=?").bind(device).run();
    return ok();
  }
  if (path !== "/alert" && path !== "/test") return fail(404);
  const row = await env.DB.prepare("UPDATE devices SET sent=? WHERE device=? AND sent<=? RETURNING provider,recipient").bind(now,device,now-5).first();
  if (!row) {
    const exists = await env.DB.prepare("SELECT 1 FROM devices WHERE device=?").bind(device).first();
    return fail(exists ? 429 : 401);
  }
  await send(env, row.provider, row.recipient, path === "/alert" ? alertText : testText);
  return ok();
}

export default {
  async fetch(req, env) {
    try { return await handle(req, env); }
    catch { return fail(502); }
  },
  async scheduled(event, env) {
    await env.DB.prepare("DELETE FROM codes WHERE issued<=?").bind(Math.floor(event.scheduledTime/1000)-600).run();
  },
};

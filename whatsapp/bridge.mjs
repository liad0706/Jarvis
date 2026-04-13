import {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
} from "@whiskeysockets/baileys";
import pino from "pino";
import qrcode from "qrcode-terminal";

import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const JARVIS_API = process.env.JARVIS_API || "http://127.0.0.1:8585";
const __dirname = dirname(fileURLToPath(import.meta.url));
const AUTH_DIR = join(__dirname, "auth");
const DEBOUNCE_MS = 1500;

const logger = pino({ level: "silent" });

const pending = new Map();
const sentByBot = new Set();
const messageStore = new Map();
const MESSAGE_STORE_TTL = 5 * 60 * 1000;
let activeSock = null;
let reconnectTimeout = null;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;

function debounce(sender, text, sock, msg, replyJid) {
  const existing = pending.get(sender);
  if (existing) {
    existing.texts.push(text);
    clearTimeout(existing.timer);
  } else {
    pending.set(sender, { texts: [text], sock, msg, replyJid });
  }

  const entry = pending.get(sender);
  entry.timer = setTimeout(() => {
    const merged = entry.texts.join("\n");
    pending.delete(sender);
    handleMessage(sender, merged, entry.sock, entry.msg, entry.replyJid);
  }, DEBOUNCE_MS);
}

async function handleMessage(sender, text, sock, msg, replyJid) {
  const name = msg.pushName || "";
  console.log(`[${ts()}] << ${name} (${sender}): ${text}`);

  try {
    const res = await fetch(`${JARVIS_API}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender, message: text, name }),
    });

    if (!res.ok) {
      console.error(`[${ts()}] API error: ${res.status}`);
      return;
    }

    const data = await res.json();
    const reply = data.reply || "";
    const imagePaths = Array.isArray(data.image_paths) ? data.image_paths : [];

    if (reply) {
      try {
        const sentMsg = await sock.sendMessage(replyJid, { text: reply });
        if (sentMsg?.key?.id) {
          sentByBot.add(sentMsg.key.id);
          setTimeout(() => sentByBot.delete(sentMsg.key.id), 30000);
        }
        console.log(`[${ts()}] >> Jarvis -> ${replyJid}: ${reply.slice(0, 120)}${reply.length > 120 ? "..." : ""}`);
      } catch (sendErr) {
        console.error(`[${ts()}] sendMessage failed to ${replyJid}:`, sendErr.message);
      }
    }

    for (const imgPath of imagePaths) {
      if (!imgPath || typeof imgPath !== "string") continue;
      try {
        if (!existsSync(imgPath)) {
          console.error(`[${ts()}] image missing: ${imgPath}`);
          continue;
        }
        const buf = readFileSync(imgPath);
        const sentImg = await sock.sendMessage(replyJid, { image: buf });
        if (sentImg?.key?.id) {
          sentByBot.add(sentImg.key.id);
          setTimeout(() => sentByBot.delete(sentImg.key.id), 30000);
        }
        console.log(`[${ts()}] >> Jarvis -> ${replyJid}: [image] ${imgPath}`);
      } catch (imgErr) {
        console.error(`[${ts()}] image send failed (${imgPath}):`, imgErr.message);
      }
    }
  } catch (err) {
    console.error(`[${ts()}] Failed to reach Jarvis API:`, err.message);
  }
}

function ts() {
  return new Date().toLocaleTimeString();
}

function scheduleReconnect() {
  if (reconnectTimeout) {
    clearTimeout(reconnectTimeout);
  }
  reconnectAttempts++;
  if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
    console.error(`[${ts()}] Max reconnect attempts reached (${MAX_RECONNECT_ATTEMPTS}). Giving up.`);
    process.exit(1);
  }
  const delay = Math.min(2000 * Math.pow(2, reconnectAttempts - 1), 30000);
  console.log(`[${ts()}] Reconnecting in ${(delay / 1000).toFixed(0)}s (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);
  reconnectTimeout = setTimeout(() => startBridge(), delay);
}

async function startBridge() {
  if (activeSock) {
    try {
      activeSock.ev.removeAllListeners("connection.update");
      activeSock.ev.removeAllListeners("messages.upsert");
      activeSock.ev.removeAllListeners("creds.update");
      activeSock.end(undefined);
    } catch (_) {}
    activeSock = null;
  }

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: {
      creds: state.creds,
      keys: makeCacheableSignalKeyStore(state.keys, logger),
    },
    logger,
    browser: ["Jarvis", "Desktop", "1.0.0"],
    printQRInTerminal: false,
    getMessage: async (key) => {
      const stored = messageStore.get(key.id);
      return stored?.message || undefined;
    },
  });
  activeSock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      console.log("\n  Scan this QR code with WhatsApp:\n");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "open") {
      reconnectAttempts = 0;
      const myJid = sock.user?.id || "unknown";
      console.log(`[${ts()}] WhatsApp connected as ${myJid} — Jarvis bridge is live`);
    }

    if (connection === "close") {
      if (sock !== activeSock) return;

      const code = lastDisconnect?.error?.output?.statusCode;

      if (code === DisconnectReason.loggedOut) {
        console.log(`[${ts()}] Logged out of WhatsApp. Delete whatsapp/auth/ and restart to re-pair.`);
        process.exit(0);
      }

      console.log(`[${ts()}] Connection closed (code ${code}).`);
      scheduleReconnect();
    }
  });

  sock.ev.on("messages.upsert", ({ messages: msgs, type }) => {
    console.log(`[${ts()}] messages.upsert — type=${type} count=${msgs.length}`);

    for (const msg of msgs) {
      // Store message for retry decryption
      if (msg.key?.id && msg.message) {
        messageStore.set(msg.key.id, msg);
        setTimeout(() => messageStore.delete(msg.key.id), MESSAGE_STORE_TTL);
      }
      const jid = msg.key.remoteJid || "";
      const fromMe = msg.key.fromMe;
      const msgId = msg.key.id;
      const msgKeys = msg.message ? Object.keys(msg.message) : [];
      console.log(`[${ts()}]   jid=${jid} fromMe=${fromMe} id=${msgId} keys=[${msgKeys}]`);

      if (!jid) continue;

      // Skip bot's own replies
      if (fromMe && sentByBot.has(msgId)) {
        sentByBot.delete(msgId);
        console.log(`[${ts()}]   -> skipped (bot reply)`);
        continue;
      }

      // Skip groups, newsletters, broadcasts, status
      if (jid.endsWith("@g.us") || jid.endsWith("@newsletter") || jid.endsWith("@broadcast") || jid === "status@broadcast") {
        console.log(`[${ts()}]   -> skipped (group/newsletter/broadcast)`);
        continue;
      }

      // Skip non-notify for old messages
      if (type !== "notify") {
        console.log(`[${ts()}]   -> skipped (type=${type})`);
        continue;
      }

      // --- Extract text from various message types ---
      let text = "";
      let mediaCaption = "";

      if (msg.message?.conversation) {
        text = msg.message.conversation;
      } else if (msg.message?.extendedTextMessage?.text) {
        text = msg.message.extendedTextMessage.text;
      } else if (msg.message?.imageMessage?.caption) {
        text = "[תמונה] " + msg.message.imageMessage.caption;
        mediaCaption = msg.message.imageMessage.caption;
      } else if (msg.message?.imageMessage) {
        text = "[תמונה נשלחה]";
      } else if (msg.message?.videoMessage?.caption) {
        text = "[וידאו] " + msg.message.videoMessage.caption;
      } else if (msg.message?.videoMessage) {
        text = "[וידאו נשלח]";
      } else if (msg.message?.documentMessage) {
        const fname = msg.message.documentMessage.fileName || "document";
        text = `[מסמך: ${fname}]`;
      } else if (msg.message?.audioMessage) {
        text = msg.message.audioMessage.ptt ? "[הודעה קולית]" : "[קובץ אודיו]";
      } else if (msg.message?.stickerMessage) {
        text = "[סטיקר]";
      } else if (msg.message?.locationMessage) {
        const loc = msg.message.locationMessage;
        text = `[מיקום: ${loc.degreesLatitude}, ${loc.degreesLongitude}]`;
      } else if (msg.message?.contactMessage) {
        text = `[איש קשר: ${msg.message.contactMessage.displayName || ""}]`;
      }

      if (!text.trim()) {
        console.log(`[${ts()}]   -> skipped (no text content)`);
        continue;
      }

      // --- Resolve reply JID ---
      // Baileys supports sending to @lid directly, so just use the jid as-is.
      // Only for self-messages (fromMe + @lid), map to @s.whatsapp.net for "Message Yourself" chat.
      let replyJid = jid;
      if (jid.endsWith("@lid") && fromMe && sock.user?.id) {
        // Self-message: reply to own @s.whatsapp.net (Message Yourself chat)
        const myNumber = sock.user.id.split(":")[0].split("@")[0];
        replyJid = myNumber + "@s.whatsapp.net";
        console.log(`[${ts()}]   LID self-msg -> replying to ${replyJid}`);
      } else if (jid.endsWith("@lid")) {
        // Message from someone else via LID — reply to their LID directly
        // Baileys handles @lid routing internally
        console.log(`[${ts()}]   LID from other -> replying to ${jid}`);
      }

      const sender = jid.replace("@s.whatsapp.net", "").replace("@lid", "");
      console.log(`[${ts()}] Processing: ${sender} -> "${text.trim().slice(0, 80)}"`);
      debounce(sender, text.trim(), sock, msg, replyJid);
    }
  });
}

console.log("╔══════════════════════════════════════════╗");
console.log("║     Jarvis WhatsApp Bridge (Baileys)     ║");
console.log("╠══════════════════════════════════════════╣");
console.log(`║  API: ${JARVIS_API.padEnd(34)}║`);
console.log("╚══════════════════════════════════════════╝\n");

startBridge();

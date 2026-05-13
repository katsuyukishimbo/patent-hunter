// PatentHunter Hono Edge API: wraps the Python scoring pipeline as HTTP endpoints.
//
// Design rationale:
//   - Hono on Node (via @hono/node-server) for local dev. Cloudflare Workers
//     compatible: routes use Web standard Request/Response only.
//   - Python pipeline (P1 CLI + Eval Harness) is invoked via child_process.
//     This is the simplest contract: Hono never reimplements scoring logic.
//   - SSE endpoint exposes the run.log file as a live tail for the dashboard.
//   - Discord interactions endpoint validates Ed25519 signatures (per Discord
//     docs). Body is read raw before parsing because signature is over raw.
//
// Endpoints:
//   GET   /                            health probe
//   GET   /api/patents/top             latest week scores.jsonl (top-N adopted)
//   GET   /api/eval/latest             latest evals/out/eval_*/metrics.json
//   POST  /api/eval/run                kick off run_eval.py (fire and forget)
//   POST  /api/scoring/run             kick off scripts/dryrun.py (fire and forget)
//   GET   /api/scoring/log/:week       read out/<week>/run.log
//   GET   /api/scoring/stream/:week    SSE tail run.log
//   POST  /api/discord/interactions    Discord webhook (Ed25519 verified)

import { Hono } from "hono";
import { serve } from "@hono/node-server";
import { spawn } from "node:child_process";
import { readFile, readdir, stat } from "node:fs/promises";
import { createReadStream } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { streamSSE } from "hono/streaming";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, "..", "..");
const PY_BIN = process.env.PATENT_HUNTER_PY ?? "python3";

const app = new Hono();

// Health
app.get("/", (c) => {
  return c.json({
    name: "patent-hunter-web",
    version: "0.1.0",
    repo_root: REPO_ROOT,
    endpoints: [
      "GET /api/patents/top",
      "GET /api/eval/latest",
      "POST /api/eval/run",
      "POST /api/scoring/run",
      "GET /api/scoring/log/:week",
      "GET /api/scoring/stream/:week",
      "POST /api/discord/interactions",
    ],
  });
});

// Latest week's adopted patents (top-N).
app.get("/api/patents/top", async (c) => {
  const topN = Number(c.req.query("n") ?? 10);
  const week = await latestWeek();
  if (!week) return c.json({ error: "no run yet" }, 404);
  const scoresPath = resolve(REPO_ROOT, "out", week, "scores.jsonl");
  const text = await readFile(scoresPath, "utf-8");
  const records = text
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as ScoredRecord)
    .filter((r) => r.adopted)
    .sort((a, b) => b.consensus_score - a.consensus_score)
    .slice(0, topN);
  return c.json({ week, count: records.length, patents: records });
});

// Latest eval metrics.
app.get("/api/eval/latest", async (c) => {
  const evalRoot = resolve(REPO_ROOT, "evals", "out");
  const dirs = await safeReaddir(evalRoot);
  const evalDirs = dirs.filter((d) => d.startsWith("eval_")).sort();
  const last = evalDirs[evalDirs.length - 1];
  if (!last) return c.json({ error: "no eval run yet" }, 404);
  const metricsPath = resolve(evalRoot, last, "metrics.json");
  const text = await readFile(metricsPath, "utf-8");
  return c.json({ run_id: last, metrics: JSON.parse(text) });
});

// Fire and forget: evals/run_eval.py
app.post("/api/eval/run", async (c) => {
  const job = spawn(PY_BIN, ["evals/run_eval.py"], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
  });
  job.unref();
  return c.json({ status: "started", pid: job.pid });
});

// Fire and forget: scripts/dryrun.py
app.post("/api/scoring/run", async (c) => {
  const job = spawn(PY_BIN, ["scripts/dryrun.py"], {
    cwd: REPO_ROOT,
    detached: true,
    stdio: "ignore",
  });
  job.unref();
  return c.json({ status: "started", pid: job.pid });
});

// Read run.log for a given week.
app.get("/api/scoring/log/:week", async (c) => {
  const week = c.req.param("week");
  const logPath = resolve(REPO_ROOT, "out", week, "run.log");
  try {
    const text = await readFile(logPath, "utf-8");
    return c.text(text);
  } catch {
    return c.json({ error: `log not found for week=${week}` }, 404);
  }
});

// SSE tail of run.log.
app.get("/api/scoring/stream/:week", (c) => {
  const week = c.req.param("week");
  const logPath = resolve(REPO_ROOT, "out", week, "run.log");
  return streamSSE(c, async (stream) => {
    const rs = createReadStream(logPath, { encoding: "utf-8" });
    let buffer = "";
    for await (const chunk of rs) {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        await stream.writeSSE({ data: line });
      }
    }
    if (buffer.length > 0) {
      await stream.writeSSE({ data: buffer });
    }
  });
});

// Discord interaction webhook (Ed25519 verified).
// Per Discord docs: PING (type=1) responds with PONG (type=1).
app.post("/api/discord/interactions", async (c) => {
  const signature = c.req.header("x-signature-ed25519");
  const timestamp = c.req.header("x-signature-timestamp");
  const publicKey = process.env.DISCORD_PUBLIC_KEY;
  const rawBody = await c.req.text();

  if (!signature || !timestamp || !publicKey) {
    return c.json(
      { error: "missing signature headers or DISCORD_PUBLIC_KEY" },
      401,
    );
  }

  const valid = await verifyDiscordSignature(
    rawBody,
    signature,
    timestamp,
    publicKey,
  );
  if (!valid) {
    return c.json({ error: "invalid signature" }, 401);
  }

  const body = JSON.parse(rawBody) as { type: number };
  if (body.type === 1) {
    return c.json({ type: 1 });
  }
  return c.json({ type: 4, data: { content: "received (not yet implemented)" } });
});

// Helpers

type ScoredRecord = {
  patent: {
    patent_id: string;
    title: string;
    cpc_code: string;
    category: string;
    google_patents_url: string;
  };
  sonnet: { score: number };
  codex: { score: number };
  consensus_score: number;
  adopted: boolean;
};

async function latestWeek(): Promise<string | undefined> {
  const outDir = resolve(REPO_ROOT, "out");
  const entries = await safeReaddir(outDir);
  const weeks = entries.filter((e) => /^\d{4}-W\d{2}$/.test(e)).sort();
  return weeks[weeks.length - 1];
}

async function safeReaddir(path: string): Promise<string[]> {
  try {
    const s = await stat(path);
    if (!s.isDirectory()) return [];
    return await readdir(path);
  } catch {
    return [];
  }
}

// Verify Discord Ed25519 signature using Web Crypto (Node 20+).
async function verifyDiscordSignature(
  body: string,
  signatureHex: string,
  timestamp: string,
  publicKeyHex: string,
): Promise<boolean> {
  const enc = new TextEncoder();
  const message = enc.encode(timestamp + body);
  const sig = hexToBytes(signatureHex);
  const pub = hexToBytes(publicKeyHex);
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      pub,
      { name: "Ed25519" },
      false,
      ["verify"],
    );
    return await crypto.subtle.verify("Ed25519", key, sig, message);
  } catch {
    return false;
  }
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

// Server bootstrap (skipped if imported as a module: useful for Workers later).

const PORT = Number(process.env.PORT ?? 8787);

const isDirectRun =
  import.meta.url === `file://${process.argv[1]}` ||
  process.argv[1]?.endsWith("src/index.ts") === true;

if (isDirectRun) {
  serve({ fetch: app.fetch, port: PORT }, (info) => {
    console.log(`[patent-hunter-web] listening on http://localhost:${info.port}`);
  });
}

export default app;

/**
 * server.js
 *
 * Local backend for the P2P energy market dashboard.
 *
 * Responsibilities:
 *   1. GET  /api/config          -> public contract addresses + households from config.json
 *   2. GET  /abi/:name.json      -> static ABI files (from python/abi/)
 *   3. POST /api/scripts/:name/start  -> spawn oracle_writer.py or settlement_trigger.py
 *   4. POST /api/scripts/:name/stop   -> kill that process (and its children)
 *   5. GET  /api/scripts/status  -> which scripts are currently running
 *   6. Serves the dashboard itself from ./public
 *
 * Deliberately never reads or exposes python/.env — private keys stay out of
 * this process entirely. MetaMask does all signing on the frontend side.
 */

const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const express = require("express");
const treeKill = require("tree-kill");
require("dotenv").config();

const PORT = process.env.PORT || 3000;
const PYTHON_DIR = path.resolve(__dirname, process.env.PYTHON_DIR || "../python");
const CONFIG_PATH = path.join(PYTHON_DIR, "node.json");
const ABI_DIR = path.join(PYTHON_DIR, "abi");
const LOG_DIR = path.join(PYTHON_DIR, "logs");

fs.mkdirSync(LOG_DIR, { recursive: true });

// Whitelist of scripts that are allowed to be started this way — never build
// the command from unvalidated user input, or any string becomes an RCE.
const SCRIPTS = {
  oracle_writer: "oracle_writer.py",
  settlement_trigger: "settlement_trigger.py",
};

/** @type {Map<string, import('child_process').ChildProcess>} */
const runningProcesses = new Map();

const app = express();
app.use(express.json());

// ─────────────────────────────────────────────────────────────
//  Config + ABIs
// ─────────────────────────────────────────────────────────────

app.get("/api/config", (req, res) => {
  try {
    const config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    res.json({
      blockchain: config.blockchain,
      households: config.households.map((h) => ({
        id: h.id,
        name: h.name,
        address: h.address,
      })),
    });
  } catch (err) {
    console.error("Failed to read config.json:", err);
    res.status(500).json({ error: "Could not read config.json", detail: err.message });
  }
});

app.use("/abi", express.static(ABI_DIR));

// ─────────────────────────────────────────────────────────────
//  Script control
// ─────────────────────────────────────────────────────────────

// app.get("/api/scripts/status", (req, res) => {
//   const status = {};
//   for (const name of Object.keys(SCRIPTS)) {
//     status[name] = runningProcesses.has(name);
//   }
//   res.json(status);
// });

app.post("/api/scripts/:name/start", (req, res) => {
  const { name } = req.params;
  const scriptFile = SCRIPTS[name];
  if (!scriptFile) {
    return res.status(400).json({ error: `Unknown script "${name}"` });
  }
  if (runningProcesses.has(name)) {
    return res.status(409).json({ error: `${name} is already running` });
  }

  const scriptPath = path.join(PYTHON_DIR, scriptFile);
  if (!fs.existsSync(scriptPath)) {
    return res.status(404).json({ error: `${scriptFile} not found in ${PYTHON_DIR}` });
  }

  const outLog = fs.createWriteStream(path.join(LOG_DIR, `${name}.stdout.log`), { flags: "a" });
  const errLog = fs.createWriteStream(path.join(LOG_DIR, `${name}.stderr.log`), { flags: "a" });

  // "python" vs "python3": adjust if your setup needs a specific interpreter
  // (e.g. a venv's python.exe) — could also come from an env var per script.
  const child = spawn("python", [scriptFile], { cwd: PYTHON_DIR });

  child.stdout.pipe(outLog);
  child.stderr.pipe(errLog);

  child.on("exit", (code, signal) => {
    console.log(`[${name}] exited (code=${code}, signal=${signal})`);
    runningProcesses.delete(name);
  });

  child.on("error", (err) => {
    console.error(`[${name}] failed to start:`, err);
    runningProcesses.delete(name);
  });

  runningProcesses.set(name, child);
  console.log(`[${name}] started, pid=${child.pid}`);
  res.json({ started: true, pid: child.pid });
});

app.post("/api/scripts/:name/stop", (req, res) => {
  const { name } = req.params;
  const child = runningProcesses.get(name);
  if (!child) {
    return res.status(409).json({ error: `${name} is not running` });
  }

  // Plain child.kill() often fails to actually stop the process on Windows
  // when spawn() went through a shell — tree-kill uses taskkill /T /F there
  // (and kills the whole process group on Linux/Mac), so it's used
  // unconditionally rather than branching on process.platform.
  treeKill(child.pid, "SIGTERM", (err) => {
    if (err) {
      console.error(`[${name}] failed to stop:`, err);
      return res.status(500).json({ error: err.message });
    }
    runningProcesses.delete(name);
    console.log(`[${name}] stopped`);
    res.json({ stopped: true });
  });
});

// ─────────────────────────────────────────────────────────────
//  Static dashboard
// ─────────────────────────────────────────────────────────────

app.use(express.static(path.join(__dirname, "public")));

app.listen(PORT, () => {
  console.log(`Dashboard running at http://localhost:${PORT}`);
  console.log(`Reading contracts from: ${CONFIG_PATH}`);
  console.log(`Serving ABIs from:      ${ABI_DIR}`);
});

// Make sure spawned scripts don't outlive this server on Ctrl+C
process.on("SIGINT", () => {
  for (const [name, child] of runningProcesses) {
    console.log(`Stopping ${name} (pid=${child.pid})...`);
    treeKill(child.pid, "SIGTERM");
  }
  process.exit(0);
});
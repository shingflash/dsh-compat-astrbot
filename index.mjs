#!/usr/bin/env node
/**
 * index.mjs - host side for dsh: load AstrBot (Python) plugins over stdio
 * JSON-lines.
 *
 * Usage:
 *   node index.mjs --plugin <plugin dir> --probe
 *   node index.mjs --plugin <dir> --config <cfg.json> --rpc
 *
 * Protocol matches py/star_host.py; stdout carries frames only, logs go to stderr.
 */
import { spawn, spawnSync } from 'node:child_process'
import { createInterface } from 'node:readline'
import { existsSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const HOST_PY = join(HERE, 'py', 'star_host.py')

const argv = process.argv.slice(2)
const arg = (k) => {
  const i = argv.indexOf(k)
  return i >= 0 ? argv[i + 1] : undefined
}
const has = (k) => argv.includes(k)

const pluginDir = arg('--plugin')
const configPath = arg('--config') || ''
const probe = has('--probe')
const rpc = has('--rpc')
const debug = has('--debug')

if (!pluginDir) {
  console.error('missing --plugin <plugin directory>')
  process.exit(2)
}
if (!existsSync(pluginDir) || !statSync(pluginDir).isDirectory()) {
  console.error('plugin directory not found: ' + pluginDir)
  process.exit(2)
}
if (!existsSync(HOST_PY)) {
  console.error('host script missing: ' + HOST_PY)
  process.exit(2)
}

/** Pick a usable Python interpreter: explicit flag, PYTHON env, py -3, python3, python. */
function pickPython() {
  const explicit = arg('--python')
  if (explicit) return { cmd: explicit, args: [] }
  if (process.env.PYTHON) return { cmd: process.env.PYTHON, args: [] }
  const candidates = process.platform === 'win32'
    ? [['py', ['-3']], ['python', []], ['python3', []]]
    : [['python3', []], ['python', []]]
  for (const [cmd, args] of candidates) {
    const r = spawnSync(cmd, [...args, '--version'], { encoding: 'utf8', timeout: 8000 })
    if (r.status === 0 && /^Python 3\./m.test((r.stdout || '') + (r.stderr || ''))) {
      return { cmd, args }
    }
  }
  return null
}

const py = pickPython()
if (!py) {
  console.error('no usable Python 3 found; pass --python <path> or set PYTHON')
  process.exit(2)
}
if (debug) process.stderr.write(`[index] python = ${py.cmd} ${py.args.join(' ')}\n`)

const pyArgs = [...py.args, HOST_PY, '--plugin', resolve(pluginDir)]
if (configPath) pyArgs.push('--config', resolve(configPath))

const child = spawn(py.cmd, pyArgs, { stdio: ['pipe', 'pipe', 'pipe'] })

child.stderr.on('data', (b) => {
  if (debug) process.stderr.write(b.toString())
})

const rl = createInterface({ input: child.stdout })
const waiters = new Map()
let seq = 0
let ready = null

const writeFrame = (obj) => {
  process.stdout.write(JSON.stringify(obj) + '\n')
}

function fail(msg) {
  writeFrame({ event: 'fatal', error: msg })
  try { child.kill() } catch {}
  process.exit(1)
}

rl.on('line', (line) => {
  let msg
  try { msg = JSON.parse(line) } catch { return }
  if (msg.event === 'ready') {
    ready = msg
    if (probe) {
      console.log(`plugin: ${msg.plugin}`)
      console.log(`commands: ${(msg.commands || []).join(', ') || '-'}`)
      console.log(`tools: ${(msg.tools || []).join(', ') || '-'}`)
      console.log(`hooks: ${(msg.hooks || []).join(', ') || '-'}`)
      child.stdin.write(JSON.stringify({ id: 0, method: 'shutdown' }) + '\n')
      return
    }
    if (!rpc) {
      console.log(JSON.stringify(msg))
      child.stdin.write(JSON.stringify({ id: 0, method: 'shutdown' }) + '\n')
    } else {
      writeFrame(msg)
    }
    return
  }
  if (msg.event === 'fatal') return fail(msg.error || 'unknown fatal error')
  const id = msg.id
  const w = waiters.get(id)
  if (!w) return
  waiters.delete(id)
  if ('error' in msg) w.reject(new Error(msg.error))
  else w.resolve(msg.result)
})

child.on('exit', (code) => {
  if (rpc && !ready) fail(`star_host exited with code ${code}`)
  for (const [, w] of waiters) w.reject(new Error(`star_host exited with code ${code}`))
  waiters.clear()
  process.exit(code ?? 0)
})

function call(method, params = {}, timeout = 120000) {
  return new Promise((resolveP, rejectP) => {
    const id = ++seq
    waiters.set(id, { resolve: resolveP, reject: rejectP })
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n')
    setTimeout(() => {
      if (waiters.has(id)) {
        waiters.delete(id)
        rejectP(new Error(`call timed out: ${method}`))
      }
    }, timeout).unref()
  })
}

if (rpc) {
  const inRl = createInterface({ input: process.stdin })
  inRl.on('line', async (line) => {
    line = line.trim()
    if (!line) return
    let req
    try { req = JSON.parse(line) } catch { return }
    if (req.method === 'ready') return writeFrame({ id: req.id, result: ready })
    try {
      const result = await call(req.method, req.params || {})
      writeFrame({ id: req.id, result })
    } catch (e) {
      writeFrame({ id: req.id, error: String(e.message || e) })
    }
    if (req.method === 'shutdown') {
      inRl.close()
      process.exit(0)
    }
  })
}

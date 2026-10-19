import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { identifier, atomic } from './runtime.mjs';
import path from 'node:path';
import crypto from 'node:crypto';

const exec = promisify(execFile);
// Runtime counters are not configuration. A new registration, even with identical
// values, gets a new revision; legacy revision-less records use their actual fields.
const identity = c => c && JSON.stringify([c.revision ?? null, c.kind, c.probe, c.repair,
  c.intervalSeconds, c.timeoutSeconds, Boolean(c.cancelled)]);

export class ChannelGuards {
  constructor(fleet, codex, { isStopped = () => false, signal } = {}) {
    this.fleet = fleet; this.codex = codex; this.running = new Set(); this.stopped = false;
    // A closed plugin must stop this observer even while a probe is awaiting its child process.
    this.isStopped = () => this.stopped || isStopped() || signal?.aborted === true;
  }

  stop() { this.stopped = true; }

  async configure(sessionID, { action, name, kind = 'command', probe, repair, intervalSeconds = 30, timeoutSeconds = 15 }) {
    const f = this.fleet;
    return f.lock(async () => {
      const s = f.requireCaptain(sessionID, true);
      s.channels ??= {};
      if (action === 'list') return s.channels;
      identifier(name);
      if (action === 'cancel') {
        if (!s.channels[name]) throw new Error('Unknown channel');
        s.channels[name].cancelled = true;
        s.channels[name].revision = crypto.randomUUID();
      } else {
        if (!['command', 'codex'].includes(kind)) throw new Error('Unknown channel kind');
        if (this.running.has(name)) throw new Error('Channel check is still running');
        if (kind === 'codex' && !this.codex) throw new Error('Codex transport unavailable');
        if (kind === 'command') for (const cmd of [probe, repair]) {
          if (!Array.isArray(cmd) || !cmd.length || cmd.some(v => typeof v !== 'string' || v.includes('\0'))) throw new Error('Probe and repair need executable + argument arrays');
        }
        if (![intervalSeconds, timeoutSeconds].every(v => Number.isFinite(v) && v > 0)) throw new Error('Invalid probe interval or timeout');
        const repairUnconfirmed = s.channels[name]?.repairUnconfirmed === true;
        s.channels[name] = { kind, probe, repair, intervalSeconds, timeoutSeconds, failures: 0,
          state: 'unobserved', nextAt: 0, revision: crypto.randomUUID(), repairUnconfirmed };
      }
      f.save(s);
      atomic(path.join(f.home, 'channels-latest.json'), JSON.stringify({ backend: 'OpenCode host timer', channels: s.channels }, null, 2));
      return s.channels[name];
    });
  }

  async probe(config) {
    if (config.kind === 'codex') { await this.codex.rpc('model/list'); return; }
    await exec(config.probe[0], config.probe.slice(1), { cwd: this.fleet.directory, windowsHide: true,
      timeout: config.timeoutSeconds * 1000, maxBuffer: Infinity });
  }

  async repair(config, stillCurrent) {
    if (config.kind === 'codex') {
      await this.codex.close();
      if (!stillCurrent()) throw Object.assign(new Error('Channel stopped before restart'), { code: 'REPAIR_STOPPED' });
      await this.codex.start();
    } else await exec(config.repair[0], config.repair.slice(1), { cwd: this.fleet.directory, windowsHide: true,
      timeout: config.timeoutSeconds * 1000, maxBuffer: Infinity });
  }

  async scan(force = false) {
    const f = this.fleet;
    for (const [name, initial] of Object.entries(f.state()?.channels ?? {})) {
      if (this.isStopped()) return;
      if (initial.cancelled || this.running.has(name) || (!force && Date.now() < initial.nextAt)) continue;
      const expected = identity(initial);
      const current = s => !this.isStopped() && s?.channels?.[name] && !s.channels[name].cancelled && identity(s.channels[name]) === expected;
      const stillCurrent = () => current(f.state());
      this.running.add(name);
      try {
        let healthy = false;
        try { await this.probe(initial); healthy = true; } catch { /* Two consecutive failures, not one transient. */ }
        if (!stillCurrent()) continue;
        let rebuild = false;
        await f.lock(async () => {
          const s = f.state();
          if (!current(s)) return;
          const c = s.channels[name];
          c.nextAt = Date.now() + c.intervalSeconds * 1000;
          if (healthy) { c.failures = 0; c.state = 'healthy'; c.repairUnconfirmed = false; delete c.repairAttempt; }
          else if (!c.repairUnconfirmed) {
            c.failures += 1; c.state = 'probe_failed';
            if (c.failures >= 2) rebuild = true;
          } else {
            c.state = 'repair_unconfirmed';
            f.addEvent(s, 'channel_guard', `${name}: previous repair outcome unknown; probe still fails, no blind duplicate repair`);
          }
          f.save(s);
        });
        if (!rebuild) continue;
        let operation, attempt;
        await f.lock(async () => {
          const s = f.state();
          if (!current(s) || s.channels[name].repairUnconfirmed) return;
          const c = s.channels[name];
          attempt = crypto.randomUUID();
          c.repairUnconfirmed = true; c.repairAttempt = attempt; c.state = 'repairing';
          f.save(s);
          // Persist intent and invoke the repair in the same synchronous lock turn.
          // Once invoked, stop/reconfiguration cannot relabel it as never submitted.
          operation = this.repair(initial, stillCurrent).then(() => ({ ok: true }), error => ({ ok: false, error }));
        });
        if (!operation) continue;
        let repairOK = false, recovered = false, uncertain = false;
        const result = await operation;
        repairOK = result.ok;
        if (!result.ok) uncertain = initial.kind === 'codex' || Boolean(result.error.killed ||
          ['ETIMEDOUT', 'REPAIR_STOPPED'].includes(result.error.code));
        if (!stillCurrent()) continue; // Leave submitted repair intent unresolved.
        try { await this.probe(initial); recovered = true; } catch { /* Exit zero alone is not recovery. */ }
        if (!stillCurrent()) continue;
        await f.lock(async () => {
          const s = f.state();
          if (!current(s) || s.channels[name].repairAttempt !== attempt) return;
          const c = s.channels[name];
          c.repairUnconfirmed = uncertain && !recovered;
          c.failures = 0;
          c.state = recovered ? 'healthy' : c.repairUnconfirmed ? 'repair_unconfirmed' : 'repair_failed';
          c.lastRepair = { at: new Date().toISOString(), commandSucceeded: repairOK, recovered };
          if (!c.repairUnconfirmed) delete c.repairAttempt;
          // Record outcome only, not stdout/stderr which may contain tunnel credentials.
          f.addEvent(s, 'channel_guard', `${name}: rebuild command=${repairOK ? 'OK' : 'FAIL'}; post-probe=${recovered ? 'OK' : 'FAIL'}`);
          f.save(s);
        });
      } finally { this.running.delete(name); }
    }
  }
}

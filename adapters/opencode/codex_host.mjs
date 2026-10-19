// A project-owned native-stdio host. No model logic or approval policy lives here.
import fs from 'node:fs';
import net from 'node:net';
import { createInterface } from 'node:readline';
import { CodexConnection, codexErrorWire } from './codex.mjs';
import { atomic, readJSON } from './runtime.mjs';

const file = process.argv[2];
const config = readJSON(file);
const clients = new Set();
const send = (socket, value) => { if (!socket.destroyed) socket.write(JSON.stringify(value) + '\n'); };
const native = new CodexConnection({ binary: config.binary, directory: config.directory,
  onEvent: message => { for (const socket of clients) send(socket, message); } });
let stopping = false;

try {
  await native.start();
  const server = net.createServer(socket => {
    let authenticated = false;
    socket.setTimeout(10000, () => socket.destroy());
    socket.on('error', () => {});
    socket.on('close', () => clients.delete(socket));
    // Readline re-emits socket errors. A lost UI must close only its transport,
    // not crash the durable host that owns workers and pending approvals.
    createInterface({ input: socket }).on('error', () => socket.destroy()).on('line', async line => {
      let message;
      try { message = JSON.parse(line); } catch { socket.destroy(); return; }
      if (!authenticated) {
        if (message.method !== 'starfix/auth' || message.token !== config.token) { socket.destroy(); return; }
        authenticated = true; socket.setTimeout(0); clients.add(socket);
        send(socket, { method: 'starfix/loaded', params: { threads: [...native.threads] } });
        // Requests remain native and pending while the UI is absent. Reconnect
        // replays them, never synthesizes approval or drops the request.
        for (const request of native.approvals.values()) send(socket, request);
        return;
      }
      try {
        if (!message.method) {
          native.answer(String(message.id), message.result);
          for (const client of clients) send(client, { method: 'starfix/requestResolved', params: { id: message.id } });
          return;
        }
        if (message.method === 'initialized') return;
        if (message.method === 'starfix/stop') {
          stopping = true;
          let failure;
          try { await native.close(); } catch (error) { failure = error; }
          if (native.child) { stopping = false; throw failure; }
          send(socket, failure ? { id: message.id, error: codexErrorWire(failure) } :
            { id: message.id, result: { stopped: true, shutdown: native.lastDisconnect } });
          for (const client of clients) client.end();
          server.close(() => process.exit(0));
          if (readJSON(file, null)?.pid === process.pid) fs.unlinkSync(file);
          return;
        }
        const result = message.method === 'initialize' ? native.initialization : await native.rpc(message.method, message.params);
        if (['thread/start', 'thread/resume'].includes(message.method)) native.threads.set(result.thread.id, { ...result, persisted: message.method === 'thread/resume' });
        // Per-turn overrides become native thread defaults. Preserve the same
        // metadata for a reconnecting UI; never reconstruct it from captain settings.
        if (message.method === 'turn/start') {
          const selected = native.threads.get(message.params.threadId);
          if (selected && message.params.effort != null) selected.reasoningEffort = message.params.effort;
        }
        if (['thread/archive', 'thread/unsubscribe'].includes(message.method)) native.threads.delete(message.params.threadId);
        send(socket, { id: message.id, result });
      } catch (error) {
        send(socket, { id: message.id, error: codexErrorWire(error) });
      }
    });
  });
  native.child.once('close', () => {
    if (stopping) return;
    for (const socket of clients) socket.destroy();
    server.close();
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  atomic(file, JSON.stringify({ ...config, pid: process.pid, port: server.address().port }));
} catch {
  await native.close().catch(() => {});
  process.exitCode = 1;
}

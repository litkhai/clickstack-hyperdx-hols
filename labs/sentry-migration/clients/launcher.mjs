// Game-launcher stand-in on the real Sentry Node SDK (@sentry/electron's main
// process builds on it). Simulates launcher runs: patch download, integrity
// check, game start. Each run is a Sentry session; some runs hit handled
// errors, a share crash with an unhandled exception.
//
//   node launcher.mjs --dsn http://<key>@localhost:9800/101 --release 3.2.0 --runs 40 --crash-rate 0.02
import * as Sentry from '@sentry/node';

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, arr) =>
  (v.startsWith('--') ? [...acc, [v.slice(2), arr[i + 1]]] : acc), []));
const runs = Number(args.runs ?? 40), crashRate = Number(args['crash-rate'] ?? 0.02);
const release = args.release ?? '3.2.0';
let seed = [...(args.seed ?? `7-${release}`)].reduce((a, c) => a * 31 + c.charCodeAt(0) >>> 0, 7);
const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2 ** 32);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

Sentry.init({
  dsn: args.dsn,
  release: `game-launcher@${release}`,
  environment: 'production',
  defaultIntegrations: false,
  integrations: [],
  initialScope: { tags: { service: 'game-launcher' } },
});
Sentry.setContext('os', { name: 'Windows', version: '10.0.26100' });

class PatchDownloader {
  fetchChunk(n) {
    if (n === 3) throw new Error('ECONNRESET while downloading patch chunk 3/12');
  }
}
function verifyIntegrity(file) {
  throw new Error(`integrity check failed: ${file} sha256 mismatch`);
}
function launchGame(cfg) {
  return cfg.graphics.api.toUpperCase(); // TypeError when cfg.graphics is missing
}

let crashed = 0;
for (let i = 0; i < runs; i++) {
  Sentry.setUser({ id: `player-${String(1 + Math.floor(rnd() * 5000)).padStart(5, '0')}` });
  Sentry.startSession();
  try {
    if (rnd() < 0.12) {
      try { new PatchDownloader().fetchChunk(3); } catch (e) { Sentry.captureException(e); }
    }
    if (rnd() < 0.04) {
      try { verifyIntegrity('data/pak/core_02.pak'); } catch (e) { Sentry.captureException(e); }
    }
    const cfg = rnd() < crashRate ? {} : { graphics: { api: 'dx12' } };
    launchGame(cfg);
    Sentry.endSession();
  } catch (e) {
    crashed++;
    // What the SDK's uncaught-exception handler reports: level fatal,
    // mechanism handled=false. Set explicitly because the run loop catches it.
    Sentry.withScope((scope) => {
      scope.setLevel('fatal');
      scope.addEventProcessor((ev) => {
        for (const v of ev.exception?.values ?? []) v.mechanism = { type: 'onuncaughtexception', handled: false };
        return ev;
      });
      Sentry.captureException(e);
    });
    const s = Sentry.getIsolationScope().getSession();
    if (s) s.status = 'crashed';
    Sentry.endSession();
  }
  await sleep(Number(args.pause ?? 200));
}
await Sentry.flush(10000);
console.error(`game-launcher ${release}: ${runs} runs, ${crashed} crashed`);

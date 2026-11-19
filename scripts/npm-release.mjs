// Stamps a release version into the npm package, and refuses a publish that wasn't stamped.
//
//   node scripts/npm-release.mjs <version>    set package.json and .claude-plugin/plugin.json to <version> (semver)
//   node scripts/npm-release.mjs --check      exit 1 unless both carry the same release version (npm runs this before
//                                            every publish, as prepublishOnly)
//
// On main, plugin.json has no version on purpose: Claude Code then versions the GitHub install by commit, so every
// merge reaches users. An npm-installed plugin with no version reads as "unknown" and never sees an update, so the
// published copy carries the release's version. The publish workflow stamps it from the release tag in its own checkout
// and nothing is committed; to publish by hand, stamp, publish, then `git checkout -- package.json .claude-plugin/plugin.json`
// (the README, "Releasing Changes", has the first-release steps).
import { readFileSync, writeFileSync } from "node:fs";

const PKG = "package.json";
const PLUGIN = ".claude-plugin/plugin.json";
const SEMVER = /^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/;
const read = (p) => JSON.parse(readFileSync(p, "utf8"));
const fail = (msg) => { console.error(`npm-release: ${msg}`); process.exit(1); };

const arg = process.argv[2];
if (!arg) fail("usage: node scripts/npm-release.mjs <version> | --check");

if (arg === "--check") {
  const pkg = read(PKG).version, plugin = read(PLUGIN).version;
  if (!pkg || pkg.startsWith("0.0.0-development")) fail(`package.json is ${pkg}: stamp a release first (node scripts/npm-release.mjs <version>)`);
  if (plugin !== pkg) fail(`plugin.json version (${plugin ?? "none"}) must equal package.json (${pkg}), or npm installs never update`);
  console.log(`npm-release: publishing ${read(PKG).name}@${pkg}`);
} else {
  const v = arg.replace(/^v/, "");
  if (!SEMVER.test(v)) fail(`"${arg}" is not a semver version`);
  for (const p of [PKG, PLUGIN]) {
    const d = read(p);
    // keep the field order: version goes right after name in both files
    const out = {};
    for (const [k, val] of Object.entries(d)) {
      if (k === "version") continue;
      out[k] = val;
      if (k === "name") out.version = v;
    }
    writeFileSync(p, JSON.stringify(out, null, 2) + "\n");
  }
  console.log(`npm-release: package.json and plugin.json set to ${v}`);
}

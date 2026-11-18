import fs from "node:fs";

const auditPath = process.argv[2] ?? "audit-report.json";
const allowedAdvisory = "GHSA-fc5q-39xc-vjpf";
const severityRank = { info: 0, low: 1, moderate: 2, high: 3, critical: 4 };

function fail(message) {
  console.error(`Dependency audit policy failed: ${message}`);
  process.exit(1);
}

let report;
try {
  report = JSON.parse(fs.readFileSync(auditPath, "utf8"));
} catch (error) {
  fail(`could not parse npm audit JSON (${error.message})`);
}

const vulnerabilities = report.vulnerabilities ?? {};
const relevant = Object.entries(vulnerabilities).filter(
  ([, vulnerability]) => (severityRank[vulnerability.severity] ?? 0) >= severityRank.moderate,
);

if (relevant.length === 0) {
  console.log("Full dependency audit: no moderate, high, or critical findings.");
  process.exit(0);
}

function collectAdvisories(packageName, visited = new Set()) {
  if (visited.has(packageName)) return [];
  const vulnerability = vulnerabilities[packageName];
  if (!vulnerability) return [];

  const nextVisited = new Set(visited);
  nextVisited.add(packageName);
  return (vulnerability.via ?? []).flatMap((item) => {
    if (typeof item === "string") {
      return collectAdvisories(item, nextVisited);
    }
    if (item && typeof item === "object") {
      return [item.url ?? item.source ?? item.title ?? "unknown advisory"];
    }
    return [];
  });
}

const unexpected = [];
for (const [packageName, vulnerability] of relevant) {
  if (vulnerability.isDirect) {
    unexpected.push(`${packageName} is a direct dependency`);
    continue;
  }
  const advisories = collectAdvisories(packageName);
  if (
    advisories.length === 0 ||
    advisories.some((reference) => !String(reference).includes(allowedAdvisory))
  ) {
    unexpected.push(
      `${packageName} has an unapproved advisory path: ${advisories.join(", ") || "none"}`,
    );
  }
}

const lock = JSON.parse(fs.readFileSync("package-lock.json", "utf8"));
const picomatchEntries = Object.entries(lock.packages ?? {}).filter(
  ([packagePath]) => packagePath.endsWith("/picomatch"),
);
if (picomatchEntries.length === 0) {
  unexpected.push("the lockfile no longer contains the expected picomatch package");
}
for (const [packagePath, packageInfo] of picomatchEntries) {
  if (packageInfo.version !== "4.0.7" || packageInfo.dev !== true) {
    unexpected.push(
      `${packagePath} is ${packageInfo.version} (dev=${packageInfo.dev}); reassess and remove/update the temporary exception`,
    );
  }
}

if (unexpected.length > 0) {
  fail(unexpected.join("; "));
}

console.warn(
  `Accepted temporary dev-only exception ${allowedAdvisory}: ${relevant.length} transitive audit entries resolve only to picomatch 4.0.7. The separate production-only npm audit must pass before this step.`,
);

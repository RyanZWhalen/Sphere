// Pure rendering helpers for requirement evidence. They consume only the selected
// authoritative requires-edge record; no package or edge lookup happens here.

function shellQuote(value) {
  const text = String(value);
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) return text;
  return `'${text.replaceAll("'", `'"'"'`)}'`;
}

export function shellCommand(command = []) {
  return command.map(shellQuote).join(' ');
}

export function proofSentence(item = {}, queryEvidence = null) {
  if (queryEvidence?.available !== true) {
    return queryEvidence?.reason
      ? `Query evidence unavailable: ${queryEvidence.reason}`
      : 'Query evidence was not retained for this verdict.';
  }
  const reported = item.evidence?.reported_distribution;
  const required = item.specifier || 'any version';
  if (item.status === 'missing') {
    if (reported) return `A raw distribution record exists, but Sphere retained a missing verdict; no proof claim can be made.`;
    return `${item.name || 'This package'} was absent from the returned distribution list · requires ${required} · fails`;
  }
  if (!reported?.version) {
    return `Sphere retained the verdict, but not the matching raw distribution record.`;
  }
  const outcome = item.status === 'satisfied' ? 'passes' : 'fails';
  return `reported ${reported.version} · requires ${required} · ${outcome}`;
}

export function freshnessText(generatedAt) {
  return generatedAt
    ? `Live reading captured ${generatedAt}`
    : 'Freshness was not recorded for this topology.';
}

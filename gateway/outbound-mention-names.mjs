function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizedEntries(entries) {
  return (entries || []).map((entry) => {
    const aliases = [...new Set(
      (entry?.aliases || [])
        .map((value) => String(value || "").trim().replace(/^@+/, ""))
        .filter(Boolean),
    )];
    const name = String(entry?.name || "").trim().replace(/^@+/, "");
    return { aliases, name };
  }).filter((entry) => entry.aliases.length);
}

function identityMentionPattern(alias) {
  return new RegExp(
    `(^|[^\\p{L}\\p{N}_@])@${escapeRegExp(alias)}(?=$|[^\\p{L}\\p{N}_@])`,
    "giu",
  );
}

export function hasIdentityMentionLabels(text, entries) {
  const value = String(text || "");
  return normalizedEntries(entries).some((entry) => (
    entry.aliases.some((alias) => identityMentionPattern(alias).test(value))
  ));
}

export function replaceIdentityMentionLabels(text, entries) {
  let rendered = String(text || "");
  for (const entry of normalizedEntries(entries)) {
    if (!entry.name || entry.aliases.some(
      (alias) => alias.toLowerCase() === entry.name.toLowerCase()
    )) {
      continue;
    }
    for (const alias of [...entry.aliases].sort((left, right) => right.length - left.length)) {
      rendered = rendered.replace(
        identityMentionPattern(alias),
        (_match, prefix) => `${prefix}@${entry.name}`,
      );
    }
  }
  return rendered;
}

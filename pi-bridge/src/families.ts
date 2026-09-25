/**
 * Task families, read from the backend's canonical task_families.yaml.
 *
 * Reading the same file the backend validates against is what keeps Pi and the
 * router speaking the same vocabulary. A hardcoded copy here would drift the
 * first time a family is added, and the failure would look like the analyzer
 * being wrong rather than the bridge being stale.
 *
 * A minimal parser is used rather than a YAML dependency: the shape needed is
 * exactly two fields per family, and the file is ours.
 */

import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

export type TaskFamily = { key: string; definition: string };

function candidatePaths(): string[] {
  const configured = process.env.TASK_FAMILIES_PATH;
  const paths = configured ? [resolve(configured)] : [];
  paths.push(
    join(HERE, '..', '..', 'backend', 'config', 'task_families.yaml'),
    join(HERE, '..', 'config', 'task_families.yaml'),
  );
  return paths;
}

/**
 * Extract `key:` entries and their `definition:` blocks from the families map.
 *
 * Handles the two forms the file uses: a folded `>` block and a single-line
 * string. Anything else is treated as an empty definition rather than crashing,
 * because a missing sentence in a prompt is recoverable and a dead bridge is not.
 */
export function parseTaskFamilies(yaml: string): TaskFamily[] {
  const lines = yaml.split(/\r?\n/);
  const start = lines.findIndex((line) => /^families:\s*$/.test(line));
  if (start === -1) return [];

  const families: TaskFamily[] = [];
  let current: { key: string; parts: string[] } | null = null;
  let inDefinition = false;

  const flush = () => {
    if (current) {
      families.push({ key: current.key, definition: current.parts.join(' ').trim() });
      current = null;
    }
    inDefinition = false;
  };

  for (const line of lines.slice(start + 1)) {
    if (/^\S/.test(line) && line.trim() !== '') {
      flush();
      break; // left the `families:` block entirely
    }

    const familyMatch = line.match(/^ {2}([a-z0-9_]+):\s*$/);
    if (familyMatch) {
      flush();
      current = { key: familyMatch[1], parts: [] };
      continue;
    }
    if (!current) continue;

    const folded = line.match(/^ {4}definition:\s*>\s*$/);
    if (folded) {
      inDefinition = true;
      continue;
    }
    const inline = line.match(/^ {4}definition:\s*(.+)$/);
    if (inline) {
      current.parts.push(inline[1].replace(/^["']|["']$/g, '').trim());
      inDefinition = false;
      continue;
    }
    if (/^ {4}\w+:/.test(line)) {
      inDefinition = false;
      continue;
    }
    if (inDefinition && line.trim()) current.parts.push(line.trim());
  }
  flush();

  return families;
}

export function loadTaskFamilies(): TaskFamily[] {
  for (const path of candidatePaths()) {
    if (!existsSync(path)) continue;
    try {
      const families = parseTaskFamilies(readFileSync(path, 'utf8'));
      if (families.length > 0) return families;
    } catch {
      // Try the next candidate rather than failing to start.
    }
  }
  return [];
}

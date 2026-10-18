import type { InputType } from './types.ts';

/** Mirrors TASK_MAX_CHARS / DOCUMENT_MAX_CHARS in backend/app/schemas/api.py. */
export const TASK_MAX_CHARS = 200_000;
export const DOCUMENT_MAX_CHARS = 5_000_000;

export const INPUT_TYPE_OPTIONS: Array<{ value: InputType; label: string; hint: string }> = [
  { value: 'task', label: 'Task / prompt', hint: 'A request in your own words.' },
  { value: 'spec', label: 'Spec', hint: 'Goals, requirements, edge cases and acceptance criteria.' },
  { value: 'plan', label: 'Implementation plan', hint: 'Ordered steps, files to change, tests.' },
];

export const FILE_ACCEPT = '.md,.markdown,.mdx,.txt,text/markdown,text/plain';

/**
 * Where the picked file probably lives in the repository.
 *
 * Only a guess: a web page is told a picked file's name, never its folder.
 * The user confirms or corrects it, because the executing agent opens exactly
 * this path.
 */
export function suggestedPath(inputType: InputType, fileName: string): string {
  const folder = inputType === 'spec' ? 'docs/specs' : 'docs/plans';
  return `${folder}/${fileName}`;
}

/** Lower-case hex SHA-256 of the file's bytes, as `sha256sum` prints it. */
export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export type PickedFile = {
  name: string;
  text: string;
  sha256: string;
};

/** Read a picked file once: its text for analysis, and the hash of its exact bytes. */
export async function readPickedFile(file: File): Promise<PickedFile> {
  const bytes = await readBytes(file);
  // fatal: a binary or non-UTF-8 file is refused rather than analysed as garbage.
  const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  return { name: file.name, text, sha256: await sha256Hex(bytes) };
}

/** FileReader rather than Blob.arrayBuffer(): same bytes, wider support. */
function readBytes(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error('could not read the file'));
    reader.readAsArrayBuffer(file);
  });
}

/** A problem with the text that the backend would reject anyway, said up front. */
export function inputProblem(inputType: InputType, text: string): string | null {
  if (inputType === 'task' && text.length > TASK_MAX_CHARS) {
    return (
      `This is ${text.length.toLocaleString()} characters; a task can be at most ` +
      `${TASK_MAX_CHARS.toLocaleString()}. If it is a spec or implementation plan, choose that ` +
      'type above: long documents are condensed for analysis.'
    );
  }
  if (text.length > DOCUMENT_MAX_CHARS) {
    return (
      `This is ${text.length.toLocaleString()} characters; the limit is ` +
      `${DOCUMENT_MAX_CHARS.toLocaleString()}.`
    );
  }
  return null;
}

/** The same characters the backend refuses in a path (see CreateTaskRequest). */
export function pathProblem(path: string): string | null {
  if (!path.trim()) return 'Enter the path of the file in your repository.';
  // eslint-disable-next-line no-control-regex
  if (/[`\u0000-\u001f]/.test(path)) return 'The path cannot contain backticks, tabs or line breaks.';
  return null;
}

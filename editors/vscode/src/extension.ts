// Ronin VS Code extension.
//
// Drives a locally-running `ronin serve` HTTP API from the editor. Two commands:
//   - ronin.code : "Ronin: Run a coding task"
//   - ronin.ask  : "Ronin: Ask about the codebase"
//
// Both prompt for input and POST it to `<serveUrl>/ask` with the body shape
// that `ronin serve` expects: { "question": "..." }. The JSON reply
// ({ success, output, iterations, trace, usage, error, demo_mode }) is rendered
// in a dedicated output channel.
//
// `ronin serve` is unauthenticated on localhost; `ronin.token` is sent as a
// bearer header only when set, for users who front it with an authenticating
// reverse proxy.

import * as vscode from 'vscode';

// ---- Wire shape, mirrored from packages/cli/src/ronin_cli/server.py ----

interface AskRequest {
  question: string;
}

interface AskResponse {
  success: boolean;
  output: string;
  iterations: number;
  trace: Array<{ kind?: string; content?: string; [k: string]: unknown }>;
  usage: Record<string, number>;
  error?: string | null;
  demo_mode?: boolean;
}

let output: vscode.OutputChannel | undefined;
let extContext: vscode.ExtensionContext | undefined;

function channel(): vscode.OutputChannel {
  if (!output) {
    output = vscode.window.createOutputChannel('Ronin');
    // Ensure the channel is disposed when the extension deactivates.
    extContext?.subscriptions.push(output);
  }
  return output;
}

function config() {
  return vscode.workspace.getConfiguration('ronin');
}

function serveAskUrl(): string {
  const base = (config().get<string>('serveUrl') ?? 'http://127.0.0.1:8000').replace(/\/+$/, '');
  return `${base}/ask`;
}

/**
 * Ask `ronin serve` a question. Throws on transport/HTTP errors so callers can
 * surface a friendly "is `ronin serve` running?" message.
 */
async function askRonin(question: string): Promise<AskResponse> {
  const url = serveAskUrl();
  const token = (config().get<string>('token') ?? '').trim();
  const timeoutMs = config().get<number>('requestTimeoutMs') ?? 600000;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const body: AskRequest = { question };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    // Connection refused, DNS failure, abort/timeout, etc.
    const reason = err instanceof Error ? err.message : String(err);
    throw new ServeUnreachableError(reason);
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    // Try to extract FastAPI's { "detail": "..." } error body.
    let detail = `${res.status} ${res.statusText}`;
    try {
      const errJson = (await res.json()) as { detail?: unknown };
      if (errJson && typeof errJson.detail === 'string') {
        detail = `${res.status}: ${errJson.detail}`;
      }
    } catch {
      // non-JSON error body — keep the status line
    }
    throw new Error(`ronin serve returned an error (${detail})`);
  }

  return (await res.json()) as AskResponse;
}

/** Raised when we cannot reach `ronin serve` at all. */
class ServeUnreachableError extends Error {
  constructor(public readonly reason: string) {
    super(reason);
    this.name = 'ServeUnreachableError';
  }
}

function renderReply(kind: 'task' | 'ask', question: string, reply: AskResponse): void {
  const out = channel();
  const stamp = new Date().toLocaleTimeString();
  const label = kind === 'task' ? 'CODING TASK' : 'ASK';

  out.appendLine('');
  out.appendLine(`──────────── ${label} · ${stamp} ────────────`);
  out.appendLine(`> ${question}`);
  out.appendLine('');

  if (reply.demo_mode) {
    out.appendLine('[demo mode] ronin has no provider key configured — this is the offline demo brain.');
    out.appendLine('');
  }

  if (reply.success === false && reply.error) {
    out.appendLine(`[error] ${reply.error}`);
    out.appendLine('');
  }

  out.appendLine(reply.output ?? '(no output)');

  const usageParts = Object.entries(reply.usage ?? {}).map(([k, v]) => `${k}=${v}`);
  const meta: string[] = [];
  if (typeof reply.iterations === 'number') {
    meta.push(`iterations=${reply.iterations}`);
  }
  if (usageParts.length) {
    meta.push(`usage(${usageParts.join(', ')})`);
  }
  if (meta.length) {
    out.appendLine('');
    out.appendLine(`[${meta.join(' · ')}]`);
  }
  out.show(true);
}

async function showServeHelp(err: ServeUnreachableError): Promise<void> {
  const url = config().get<string>('serveUrl') ?? 'http://127.0.0.1:8000';
  const pick = await vscode.window.showErrorMessage(
    `Ronin couldn't reach \`ronin serve\` at ${url}. Start it with \`ronin serve\` in a terminal, then try again.`,
    'Run `ronin serve`',
    'Open Settings',
  );
  if (pick === 'Run `ronin serve`') {
    const term = vscode.window.createTerminal('ronin serve');
    term.show(true);
    term.sendText('ronin serve');
  } else if (pick === 'Open Settings') {
    await vscode.commands.executeCommand('workbench.action.openSettings', 'ronin');
  }
}

/** Shared command body for both `ronin.code` and `ronin.ask`. */
async function runCommand(
  kind: 'task' | 'ask',
  opts: { prompt: string; placeHolder: string; progress: string },
): Promise<void> {
  const question = await vscode.window.showInputBox({
    prompt: opts.prompt,
    placeHolder: opts.placeHolder,
    ignoreFocusOut: true,
  });

  // User cancelled (Esc) or submitted empty.
  if (question === undefined || question.trim() === '') {
    return;
  }

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: opts.progress,
      cancellable: false,
    },
    async () => {
      try {
        const reply = await askRonin(question.trim());
        renderReply(kind, question.trim(), reply);
      } catch (err) {
        if (err instanceof ServeUnreachableError) {
          await showServeHelp(err);
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          await vscode.window.showErrorMessage(`Ronin: ${msg}`);
        }
      }
    },
  );
}

export function activate(context: vscode.ExtensionContext): void {
  extContext = context;
  context.subscriptions.push(
    vscode.commands.registerCommand('ronin.code', () =>
      runCommand('task', {
        prompt: 'Describe the coding task for Ronin',
        placeHolder: 'e.g. add retry + tests to the http client',
        progress: 'Ronin is working on your task…',
      }),
    ),
    vscode.commands.registerCommand('ronin.ask', () =>
      runCommand('ask', {
        prompt: 'Ask Ronin about this codebase',
        placeHolder: 'e.g. where is the HTTP serve endpoint defined?',
        progress: 'Ronin is thinking…',
      }),
    ),
  );
}

export function deactivate(): void {
  output?.dispose();
  output = undefined;
  extContext = undefined;
}

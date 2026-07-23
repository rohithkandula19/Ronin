"use client";

import { useMemo, useState } from "react";
import { Icon, RiskChip, Badge, cn, type IconName } from "@ronin/design-system";
import {
  FILE_TREE,
  OPEN_FILES,
  REPO_NAME,
  TEST_RUN,
  PROBLEMS,
  AUTONOMY_LEVELS,
  type FileNode,
  type Autonomy,
  type OpenFile,
} from "./_data";
import { tokenize, TOKEN_CLASS } from "./_highlight";
import { diffLines, diffStat } from "./_diff";

const STATUS_COLOR: Record<string, string> = {
  M: "text-warn",
  A: "text-success",
  U: "text-info",
};

export default function RoninCode() {
  const [activePath, setActivePath] = useState(OPEN_FILES[0].path);
  const [openPaths, setOpenPaths] = useState<string[]>(OPEN_FILES.map((f) => f.path));
  const [showDiff, setShowDiff] = useState(true);
  const [bottomTab, setBottomTab] = useState<"terminal" | "problems">("terminal");
  const [autonomy, setAutonomy] = useState<Autonomy>("ask");
  const [autonomyOpen, setAutonomyOpen] = useState(false);

  const active = OPEN_FILES.find((f) => f.path === activePath) ?? OPEN_FILES[0];
  const openFiles = openPaths
    .map((p) => OPEN_FILES.find((f) => f.path === p))
    .filter(Boolean) as OpenFile[];

  return (
    <div className="flex h-full min-h-0 flex-col bg-bg">
      {/* Ronin Code header */}
      <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
        <div className="flex items-center gap-2">
          <span className="grid size-6 place-items-center rounded-md bg-ink text-[0.7rem] text-bg">‹›</span>
          <span className="text-[0.875rem] font-semibold text-text">Ronin Code</span>
        </div>
        <Badge tone="neutral">
          <Icon name="worlds" size={12} /> {REPO_NAME}
        </Badge>
        <span className="text-[0.75rem] text-text-faint">main · sandboxed working copy</span>

        <div className="relative ml-auto">
          <button
            onClick={() => setAutonomyOpen((o) => !o)}
            className="flex items-center gap-2 rounded-lg border border-border bg-surface px-3 py-1.5 text-[0.8125rem] text-text hover:border-border-strong"
          >
            <Icon name="shield" size={15} className="text-accent" />
            <span className="font-medium">{AUTONOMY_LEVELS.find((a) => a.id === autonomy)!.label}</span>
            <Icon name="chevronDown" size={14} className="text-text-dim" />
          </button>
          {autonomyOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setAutonomyOpen(false)} />
              <div className="fadeup absolute right-0 top-full z-20 mt-1.5 w-72 overflow-hidden rounded-xl border border-border bg-surface shadow-[0_12px_40px_rgba(26,24,22,0.12)]">
                {AUTONOMY_LEVELS.map((lvl) => (
                  <button
                    key={lvl.id}
                    onClick={() => {
                      setAutonomy(lvl.id);
                      setAutonomyOpen(false);
                    }}
                    className={cn(
                      "flex w-full flex-col gap-0.5 border-b border-border px-3 py-2.5 text-left last:border-0 hover:bg-accent/8",
                      autonomy === lvl.id && "bg-accent/8",
                    )}
                  >
                    <span className="flex items-center gap-2 text-[0.8125rem] font-medium text-text">
                      {autonomy === lvl.id && <Icon name="check" size={14} className="text-accent" />}
                      {lvl.label}
                    </span>
                    <span className="text-[0.75rem] text-text-dim">{lvl.detail}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Explorer */}
        <div className="hidden w-60 shrink-0 flex-col border-r border-border bg-surface md:flex">
          <div className="flex h-9 items-center px-3 text-[0.6875rem] font-semibold uppercase tracking-wider text-text-faint">
            Explorer
          </div>
          <div className="flex-1 overflow-y-auto px-2 pb-3">
            <Tree
              nodes={FILE_TREE}
              activePath={activePath}
              onOpen={(p) => {
                setActivePath(p);
                setOpenPaths((prev) => (prev.includes(p) ? prev : [...prev, p]));
              }}
            />
          </div>
        </div>

        {/* Editor column */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Tabs */}
          <div className="flex h-9 shrink-0 items-stretch border-b border-border bg-surface">
            {openFiles.map((f) => (
              <button
                key={f.path}
                onClick={() => setActivePath(f.path)}
                className={cn(
                  "flex items-center gap-2 border-r border-border px-3 text-[0.8125rem] transition-colors",
                  f.path === activePath
                    ? "bg-bg text-text"
                    : "text-text-dim hover:bg-bg/50 hover:text-text",
                )}
              >
                <Icon name="code" size={13} className="text-text-faint" />
                {f.path.split("/").pop()}
                {f.diff && <span className="size-1.5 rounded-full bg-warn" />}
              </button>
            ))}
          </div>

          {/* Breadcrumb + actions */}
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg px-4 text-[0.75rem] text-text-dim">
            <Icon name="chevronRight" size={12} className="text-text-faint" />
            <span>{active.path.replace(/\//g, "  ›  ")}</span>
            {active.diff && (
              <div className="ml-auto flex items-center gap-2">
                <DiffBadge file={active} />
                <div className="flex overflow-hidden rounded-md border border-border">
                  <button
                    onClick={() => setShowDiff(false)}
                    className={cn("px-2.5 py-1 text-[0.75rem]", !showDiff ? "bg-accent/12 text-text" : "text-text-dim hover:text-text")}
                  >
                    File
                  </button>
                  <button
                    onClick={() => setShowDiff(true)}
                    className={cn("px-2.5 py-1 text-[0.75rem]", showDiff ? "bg-accent/12 text-text" : "text-text-dim hover:text-text")}
                  >
                    Proposed diff
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Code */}
          <div className="min-h-0 flex-1 overflow-auto bg-bg font-mono text-[0.8125rem] leading-6">
            {active.diff && showDiff ? (
              <DiffView before={active.diff.before} after={active.diff.after} />
            ) : (
              <CodeView content={active.content} />
            )}
          </div>

          {/* Agent command bar */}
          <div className="shrink-0 border-t border-border bg-surface px-4 py-3">
            <div className="flex items-center gap-2 rounded-xl border border-border bg-bg px-3 py-2">
              <Icon name="spark" size={16} className="text-accent" />
              <input
                placeholder="Ask Ronin to change something — it plans first, then asks before applying…"
                className="flex-1 bg-transparent text-[0.8125rem] text-text placeholder:text-text-faint focus:outline-none"
              />
              <span className="hidden text-[0.6875rem] text-text-faint sm:inline">
                {AUTONOMY_LEVELS.find((a) => a.id === autonomy)!.label}
              </span>
              <button className="grid size-7 place-items-center rounded-lg bg-accent text-on-accent hover:bg-accent-deep">
                <Icon name="arrowRight" size={15} />
              </button>
            </div>
          </div>

          {/* Bottom dock */}
          <div className="h-44 shrink-0 border-t border-border bg-surface">
            <div className="flex h-9 items-center gap-1 border-b border-border px-3">
              <DockTab active={bottomTab === "terminal"} onClick={() => setBottomTab("terminal")} icon="command">
                Terminal
              </DockTab>
              <DockTab active={bottomTab === "problems"} onClick={() => setBottomTab("problems")} icon="alert">
                Problems
                <span className="ml-1 rounded-full bg-info-soft px-1.5 text-[0.625rem] font-semibold text-info">
                  {PROBLEMS.length}
                </span>
              </DockTab>
              <span className="ml-auto flex items-center gap-1.5 text-[0.6875rem] text-text-faint">
                <Icon name="shield" size={12} /> runs in an isolated sandbox
              </span>
            </div>
            <div className="h-[calc(100%-2.25rem)] overflow-auto px-4 py-2 font-mono text-[0.75rem] leading-5">
              {bottomTab === "terminal" ? <Terminal /> : <Problems />}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── file tree ── */
function Tree({
  nodes,
  activePath,
  onOpen,
  depth = 0,
}: {
  nodes: FileNode[];
  activePath: string;
  onOpen: (p: string) => void;
  depth?: number;
}) {
  return (
    <ul>
      {nodes.map((n) => (
        <TreeNode key={n.path} node={n} activePath={activePath} onOpen={onOpen} depth={depth} />
      ))}
    </ul>
  );
}

function TreeNode({
  node,
  activePath,
  onOpen,
  depth,
}: {
  node: FileNode;
  activePath: string;
  onOpen: (p: string) => void;
  depth: number;
}) {
  const [open, setOpen] = useState(true);
  const pad = { paddingLeft: `${depth * 12 + 8}px` };

  if (node.kind === "dir") {
    return (
      <li>
        <button
          onClick={() => setOpen((o) => !o)}
          style={pad}
          className="flex h-7 w-full items-center gap-1.5 rounded-md pr-2 text-[0.8125rem] text-text-dim hover:bg-accent/8 hover:text-text"
        >
          <Icon name={open ? "chevronDown" : "chevronRight"} size={13} className="text-text-faint" />
          {node.name}
        </button>
        {open && node.children && (
          <Tree nodes={node.children} activePath={activePath} onOpen={onOpen} depth={depth + 1} />
        )}
      </li>
    );
  }

  const isActive = node.path === activePath;
  return (
    <li>
      <button
        onClick={() => onOpen(node.path)}
        style={pad}
        className={cn(
          "flex h-7 w-full items-center gap-1.5 rounded-md pr-2 text-[0.8125rem]",
          isActive ? "bg-accent/12 text-text" : "text-text-dim hover:bg-accent/8 hover:text-text",
        )}
      >
        <Icon name="code" size={13} className="shrink-0 text-text-faint" />
        <span className="truncate">{node.name}</span>
        {node.status && (
          <span className={cn("ml-auto text-[0.6875rem] font-bold", STATUS_COLOR[node.status])}>
            {node.status}
          </span>
        )}
      </button>
    </li>
  );
}

/* ── code + diff ── */
function CodeView({ content }: { content: string }) {
  const lines = content.replace(/\n$/, "").split("\n");
  return (
    <table className="w-full border-collapse">
      <tbody>
        {lines.map((line, i) => (
          <tr key={i} className="hover:bg-accent/[0.04]">
            <td className="w-10 select-none border-r border-border px-2 text-right align-top text-text-faint">
              {i + 1}
            </td>
            <td className="whitespace-pre px-4">
              <Line text={line} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function DiffView({ before, after }: { before: string; after: string }) {
  const rows = useMemo(() => diffLines(before, after), [before, after]);
  return (
    <table className="w-full border-collapse">
      <tbody>
        {rows.map((r, i) => (
          <tr
            key={i}
            className={cn(
              r.type === "add" && "bg-success-soft",
              r.type === "del" && "bg-danger-soft",
            )}
          >
            <td
              className={cn(
                "w-8 select-none px-1 text-center align-top",
                r.type === "add" ? "text-success" : r.type === "del" ? "text-danger" : "text-text-faint",
              )}
            >
              {r.type === "add" ? "+" : r.type === "del" ? "−" : ""}
            </td>
            <td className="w-10 select-none border-r border-border px-2 text-right align-top text-text-faint">
              {r.ln ?? ""}
            </td>
            <td className="whitespace-pre px-4">
              <Line text={r.text} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Line({ text }: { text: string }) {
  if (text === "") return <span>&nbsp;</span>;
  return (
    <>
      {tokenize(text).map((t, i) => (
        <span key={i} className={TOKEN_CLASS[t.cls]}>
          {t.text}
        </span>
      ))}
    </>
  );
}

function DiffBadge({ file }: { file: OpenFile }) {
  const stat = useMemo(() => (file.diff ? diffStat(diffLines(file.diff.before, file.diff.after)) : { add: 0, del: 0 }), [file]);
  return (
    <span className="flex items-center gap-1.5 font-mono text-[0.6875rem]">
      <span className="text-success">+{stat.add}</span>
      <span className="text-danger">−{stat.del}</span>
    </span>
  );
}

/* ── bottom dock ── */
function DockTab({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: IconName;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[0.75rem] font-medium transition-colors",
        active ? "bg-accent/12 text-text" : "text-text-dim hover:text-text",
      )}
    >
      <Icon name={icon} size={13} />
      {children}
    </button>
  );
}

const TERM_COLOR: Record<string, string> = {
  cmd: "text-text",
  dim: "text-text-faint",
  ok: "text-success",
  pass: "text-success font-semibold",
  err: "text-danger",
};

function Terminal() {
  return (
    <div className="space-y-0.5">
      {TEST_RUN.lines.map((l, i) => (
        <div key={i} className={cn("whitespace-pre", TERM_COLOR[l.t] ?? "text-text")}>
          {l.text}
        </div>
      ))}
      <div className="mt-1 flex items-center gap-2 text-text-faint">
        <RiskChip tone="safe" label="Sample run" />
        <span>No command was executed in your environment.</span>
      </div>
    </div>
  );
}

function Problems() {
  return (
    <ul className="space-y-1">
      {PROBLEMS.map((p, i) => (
        <li key={i} className="flex items-start gap-2 text-text-dim">
          <Icon name="alert" size={13} className="mt-0.5 shrink-0 text-info" />
          <span>
            <span className="text-text">{p.file}</span>
            <span className="text-text-faint">:{p.line}</span> — {p.msg}
          </span>
        </li>
      ))}
    </ul>
  );
}

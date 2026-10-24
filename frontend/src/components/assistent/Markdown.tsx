"use client";

/**
 * Kleiner, sicherer Markdown-Renderer für Antworten des Assistenten (keine Abhängigkeit, kein dangerouslySetInnerHTML).
 * Unterstützt: Überschriften, Absätze, Listen (- * 1.), Tabellen, Codeblöcke, Zitate, Trennlinien und
 * **fett**, *kursiv*, `code`, [Text](https://…).
 */

import { Fragment, ReactNode } from "react";

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (m[1]) out.push(<code key={key} className="rounded bg-gray-100 px-1 py-0.5 font-mono text-[0.85em] text-gray-800">{tok.slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    else if (m[3]) out.push(<em key={key}>{tok.slice(1, -1)}</em>);
    else {
      const mm = /^\[([^\]]+)\]\((.+)\)$/.exec(tok)!;
      out.push(<a key={key} href={mm[2]} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline">{mm[1]}</a>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isTableSep = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export default function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) code.push(lines[i++]);
      i++;
      blocks.push(<pre key={k++} className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 font-mono text-[12.5px] leading-relaxed text-slate-100"><code>{code.join("\n")}</code></pre>);
      continue;
    }

    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const size = ["text-xl", "text-lg", "text-base", "text-sm"][h[1].length - 1];
      blocks.push(<p key={k++} className={`mb-1 mt-3 font-semibold text-gray-900 ${size}`}>{inline(h[2], `h${k}`)}</p>);
      i++;
      continue;
    }

    if (/^\s*(---|\*\*\*)\s*$/.test(line)) { blocks.push(<hr key={k++} className="my-3 border-gray-200" />); i++; continue; }

    if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) rows.push(cells(lines[i++]));
      blocks.push(
        <div key={k++} className="my-2 overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <thead><tr>{head.map((c, ci) => <th key={ci} className="border border-gray-200 bg-gray-50 px-2 py-1 text-left font-medium">{inline(c, `th${k}-${ci}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci} className="border border-gray-200 px-2 py-1">{inline(c, `td${k}-${ri}-${ci}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }

    const li = /^(\s*)([-*]|\d+[.)])\s+(.*)$/.exec(line);
    if (li) {
      const ordered = /\d/.test(li[2]);
      const items: ReactNode[] = [];
      while (i < lines.length) {
        const m = /^(\s*)([-*]|\d+[.)])\s+(.*)$/.exec(lines[i]);
        if (!m) break;
        items.push(<li key={items.length} style={{ marginLeft: Math.min(m[1].length, 8) * 4 }}>{inline(m[3], `li${k}-${items.length}`)}</li>);
        i++;
      }
      blocks.push(ordered
        ? <ol key={k++} className="my-1.5 list-decimal space-y-0.5 pl-6">{items}</ol>
        : <ul key={k++} className="my-1.5 list-disc space-y-0.5 pl-6">{items}</ul>);
      continue;
    }

    if (line.startsWith(">")) {
      const q: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) q.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={k++} className="my-2 border-l-4 border-gray-300 pl-3 text-gray-600">{inline(q.join(" "), `q${k}`)}</blockquote>);
      continue;
    }

    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(```|#{1,4}\s|\s*([-*]|\d+[.)])\s|>)/.test(lines[i]) && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) para.push(lines[i++]);
    blocks.push(
      <p key={k++} className="my-1.5">
        {para.map((p, pi) => <Fragment key={pi}>{pi > 0 && <br />}{inline(p, `p${k}-${pi}`)}</Fragment>)}
      </p>,
    );
  }
  return <div className="break-words text-[15px] leading-relaxed text-gray-800">{blocks}</div>;
}

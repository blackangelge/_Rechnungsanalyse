"use client";

/**
 * KI-Assistent im Vollbild (Aufbau wie bei OpenWebUI): Unterhaltung in der Mitte, rechts die letzten Chats.
 * Bilder per Büroklammer, Einfügen (Strg+V) oder Ziehen; Token je Chat oben; „Neu starten“ leert den Chat.
 * Änderungen der KI sind Vorschläge — sie werden erst durch Klick auf „Freigeben“ ausgeführt.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "@/components/assistent/Markdown";
import { ProposalCard, StepsView } from "@/components/assistent/Parts";
import { Icon } from "@/components/ui";
import {
  ChatInfo, ChatMessage, WorkflowListItem, assistantApi, extractApiError, workflowsApi,
} from "@/lib/api";

const EXAMPLES = [
  { title: "Einheiten prüfen", text: "Zeige mir alle Einheiten-Gruppen und sag, was für Rechnungen im Agrarbereich noch fehlt." },
  { title: "Feld ergänzen", text: "Füge dem Schema bei der Position ein Feld „Chargennummer“ (Text) hinzu." },
  { title: "Workflow bauen", text: "Erstelle einen Workflow: Dokumententyp erkennen, bei Eingangsrechnung extrahieren und prüfen, sonst beenden." },
  { title: "Import starten", text: "Welche Ordner liegen im Import-Verzeichnis? Starte für den größten einen Import ohne Analyse." },
];

const MAX_SIDE = 1600;

/** Bild einlesen und verkleinern (spart Token und Übertragung); Ergebnis ist eine Data-URL. */
async function fileToDataUrl(file: File): Promise<string> {
  const raw: string = await new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result));
    r.onerror = () => rej(new Error("Datei nicht lesbar"));
    r.readAsDataURL(file);
  });
  const img: HTMLImageElement = await new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = () => rej(new Error("Kein gültiges Bild"));
    i.src = raw;
  });
  const scale = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
  if (scale === 1 && raw.length < 1_500_000) return raw;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(img.width * scale);
  canvas.height = Math.round(img.height * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) return raw;
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.85);
}

const fmtNum = (n: number) => n.toLocaleString("de-DE");

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const diff = Math.floor((new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86_400_000);
  if (diff <= 0) return "Heute";
  if (diff === 1) return "Gestern";
  if (diff < 7) return "Letzte 7 Tage";
  return "Älter";
}

export default function AssistentPage() {
  const [chats, setChats] = useState<ChatInfo[]>([]);
  const [chat, setChat] = useState<ChatInfo | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [models, setModels] = useState<{ id: number; name: string; model_name: string; default: boolean }[]>([]);
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [modelId, setModelId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [busyProposal, setBusyProposal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [renaming, setRenaming] = useState<number | null>(null);
  const [renameText, setRenameText] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const chatIdRef = useRef<number | null>(null);

  const loadChats = useCallback(async () => {
    try { setChats(await assistantApi.chats()); } catch (e) { setError(extractApiError(e, "Chats konnten nicht geladen werden")); }
  }, []);

  const openChat = useCallback(async (id: number) => {
    try {
      const c = await assistantApi.getChat(id);
      chatIdRef.current = c.id;
      setChat(c);
      setMessages(c.messages ?? []);
      if (c.ai_config_id) setModelId(c.ai_config_id);
      setError(null);
      setPanelOpen(false);
      try { localStorage.setItem("ra_assistant_chat", String(id)); } catch { /* optional */ }
    } catch (e) {
      setError(extractApiError(e, "Chat konnte nicht geladen werden"));
    }
  }, []);

  useEffect(() => {
    (async () => {
      const [ms, wf, list] = await Promise.all([
        assistantApi.models().catch(() => []), workflowsApi.list().catch(() => []), assistantApi.chats().catch(() => [] as ChatInfo[]),
      ]);
      setModels(ms);
      setWorkflows(wf);
      setChats(list);
      setModelId(ms.find((m) => m.default)?.id ?? ms[0]?.id ?? null);
      let last: number | null = null;
      try { last = Number(localStorage.getItem("ra_assistant_chat")) || null; } catch { /* optional */ }
      const target = list.find((c) => c.id === last) ?? list[0];
      if (target) await openChat(target.id);
    })();
  }, [openChat]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  useEffect(() => {
    const t = textRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 220)}px`;
  }, [input]);

  async function newChat() {
    try {
      const c = await assistantApi.createChat(modelId);
      chatIdRef.current = c.id;
      setChat(c);
      setMessages([]);
      setInput("");
      setImages([]);
      setError(null);
      setPanelOpen(false);
      await loadChats();
      textRef.current?.focus();
    } catch (e) {
      setError(extractApiError(e, "Chat konnte nicht angelegt werden"));
    }
  }

  async function addFiles(files: FileList | File[]) {
    const list = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (list.length === 0) { setError("Bitte Bild-Dateien (PNG, JPEG, WebP …) wählen."); return; }
    try {
      const urls = await Promise.all(list.map(fileToDataUrl));
      setImages((cur) => [...cur, ...urls].slice(0, 6));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Bild konnte nicht gelesen werden");
    }
  }

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if ((!content && images.length === 0) || sending) return;
    let current = chat;
    if (!current) {
      try {
        current = await assistantApi.createChat(modelId);
        chatIdRef.current = current.id;
        setChat(current);
      } catch (e) { setError(extractApiError(e, "Chat konnte nicht angelegt werden")); return; }
    }
    const sentImages = images;
    const optimistic: ChatMessage = {
      id: -Date.now(), role: "user", content, images: sentImages, steps: [], proposals: [], usage: null, created_at: new Date().toISOString(),
    };
    setMessages((m) => [...m, optimistic]);
    setInput("");
    setImages([]);
    setSending(true);
    setError(null);
    const chatId = current.id;
    try {
      const res = await assistantApi.send(chatId, content, sentImages, modelId);
      if (chatIdRef.current === chatId) {
        setChat(res.chat);
        setMessages((m) => [...m.filter((x) => x.id !== optimistic.id), { ...optimistic, id: res.user_message_id }, res.message]);
      }
      await loadChats();
    } catch (e) {
      setError(extractApiError(e, "Die KI hat nicht geantwortet"));
      if (chatIdRef.current === chatId) {
        try { const c = await assistantApi.getChat(chatId); setMessages(c.messages ?? []); setChat(c); } catch { /* Anzeige bleibt */ }
      }
    } finally {
      setSending(false);
    }
  }

  async function restart() {
    if (!chat || !confirm("Diesen Chat neu starten? Alle Nachrichten und der Token-Zähler dieses Chats werden gelöscht.")) return;
    try {
      const c = await assistantApi.restartChat(chat.id);
      setChat(c);
      setMessages([]);
      setError(null);
      await loadChats();
    } catch (e) { setError(extractApiError(e, "Neustart fehlgeschlagen")); }
  }

  async function removeChat(c: ChatInfo) {
    if (!confirm(`Chat „${c.title}“ löschen?`)) return;
    try {
      await assistantApi.deleteChat(c.id);
      const rest = chats.filter((x) => x.id !== c.id);
      setChats(rest);
      if (chat?.id === c.id) {
        if (rest[0]) await openChat(rest[0].id);
        else { chatIdRef.current = null; setChat(null); setMessages([]); }
      }
    } catch (e) { setError(extractApiError(e, "Löschen fehlgeschlagen")); }
  }

  async function saveRename(c: ChatInfo) {
    const title = renameText.trim();
    setRenaming(null);
    if (!title || title === c.title) return;
    try {
      const upd = await assistantApi.patchChat(c.id, { title });
      setChats((l) => l.map((x) => (x.id === c.id ? { ...x, title: upd.title } : x)));
      if (chat?.id === c.id) setChat({ ...chat, title: upd.title });
    } catch (e) { setError(extractApiError(e, "Umbenennen fehlgeschlagen")); }
  }

  function replaceMessage(m: ChatMessage) {
    setMessages((list) => list.map((x) => (x.id === m.id ? { ...x, proposals: m.proposals } : x)));
  }

  async function apply(m: ChatMessage, pid: string, workflowId?: number) {
    if (!chat) return;
    setBusyProposal(pid);
    setError(null);
    try {
      replaceMessage(await assistantApi.applyProposal(chat.id, m.id, pid, workflowId));
    } catch (e) {
      setError(extractApiError(e, "Vorschlag konnte nicht ausgeführt werden"));
      try { const c = await assistantApi.getChat(chat.id); setMessages(c.messages ?? []); } catch { /* Anzeige bleibt */ }
    } finally { setBusyProposal(null); }
  }

  async function dismiss(m: ChatMessage, pid: string) {
    if (!chat) return;
    setBusyProposal(pid);
    try { replaceMessage(await assistantApi.dismissProposal(chat.id, m.id, pid)); }
    catch (e) { setError(extractApiError(e, "Verwerfen fehlgeschlagen")); }
    finally { setBusyProposal(null); }
  }

  /** Alle offenen Vorschläge einer Antwort der Reihe nach freigeben; bei einem Fehler anhalten. */
  async function applyAll(m: ChatMessage) {
    if (!chat) return;
    for (const p of m.proposals.filter((x) => x.status === "pending")) {
      if (p.tool === "propose_reanalysis") continue;     // braucht die Workflow-Auswahl → einzeln
      setBusyProposal(p.id);
      try { replaceMessage(await assistantApi.applyProposal(chat.id, m.id, p.id)); }
      catch (e) {
        setError(extractApiError(e, "Vorschlag konnte nicht ausgeführt werden"));
        try { const c = await assistantApi.getChat(chat.id); setMessages(c.messages ?? []); } catch { /* ignore */ }
        break;
      }
    }
    setBusyProposal(null);
  }

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const allDecided = !!lastAssistant && lastAssistant.proposals.length > 0 && lastAssistant.proposals.every((p) => p.status !== "pending")
    && lastAssistant.proposals.some((p) => p.status === "applied");

  const groups: [string, ChatInfo[]][] = [];
  for (const c of chats) {
    const g = dayLabel(c.updated_at);
    const last = groups[groups.length - 1];
    if (last && last[0] === g) last[1].push(c); else groups.push([g, [c]]);
  }

  const chatList = (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <button type="button" onClick={newChat} className="flex w-full items-center justify-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-800 shadow-sm hover:bg-gray-50">
          <Icon name="plus" className="h-4 w-4" /> Neuer Chat
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {chats.length === 0 && <p className="px-3 py-6 text-center text-xs text-gray-400">Noch keine Chats.</p>}
        {groups.map(([label, list]) => (
          <div key={label} className="mb-3">
            <p className="px-3 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">{label}</p>
            {list.map((c) => (
              <div key={c.id} className={`group flex items-center gap-1 rounded-lg px-2 py-1.5 ${chat?.id === c.id ? "bg-gray-200/70" : "hover:bg-gray-100"}`}>
                {renaming === c.id ? (
                  <input autoFocus aria-label="Chat umbenennen" className="input min-w-0 flex-1 py-0.5 text-sm" value={renameText}
                    onChange={(e) => setRenameText(e.target.value)} onBlur={() => saveRename(c)}
                    onKeyDown={(e) => { if (e.key === "Enter") saveRename(c); if (e.key === "Escape") setRenaming(null); }} />
                ) : (
                  <button type="button" onClick={() => openChat(c.id)} className="min-w-0 flex-1 text-left" title={c.title}>
                    <div className="truncate text-sm text-gray-800">{c.title}</div>
                    <div className="text-[11px] text-gray-400">{fmtNum(c.input_tokens + c.output_tokens)} Token</div>
                  </button>
                )}
                {renaming !== c.id && (
                  <span className="flex shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <button type="button" aria-label={`Chat ${c.title} umbenennen`} className="rounded p-1 text-gray-400 hover:bg-white hover:text-gray-700"
                      onClick={() => { setRenaming(c.id); setRenameText(c.title); }}><Icon name="edit" className="h-3.5 w-3.5" /></button>
                    <button type="button" aria-label={`Chat ${c.title} löschen`} className="rounded p-1 text-gray-400 hover:bg-white hover:text-red-600"
                      onClick={() => removeChat(c)}><Icon name="trash" className="h-3.5 w-3.5" /></button>
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div className="flex h-full bg-white" onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }}>
      {/* ─── Unterhaltung ─── */}
      <section className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-gray-200 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold text-gray-900">{chat?.title ?? "KI-Assistent"}</h1>
            <p className="hidden text-[11px] text-gray-400 md:block">Liest und ändert Einheiten, Schema, Export-Vorlagen, Workflows und Imports — Änderungen nur nach deiner Freigabe.</p>
          </div>
          <select aria-label="KI-Modell" className="input w-auto max-w-[14rem] py-1 text-sm" value={modelId ?? ""} onChange={(e) => {
            const id = Number(e.target.value) || null;
            setModelId(id);
            if (chat) assistantApi.patchChat(chat.id, { ai_config_id: id }).catch(() => undefined);
          }}>
            {models.length === 0 && <option value="">Keine KI-Konfiguration</option>}
            {models.map((m) => <option key={m.id} value={m.id}>{m.name} ({m.model_name})</option>)}
          </select>
          {chat && (
            <span className="hidden whitespace-nowrap rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-600 sm:inline" title="Verbrauchte Token in diesem Chat" data-testid="chat-tokens">
              {fmtNum(chat.input_tokens)} ein · {fmtNum(chat.output_tokens)} aus · {fmtNum(chat.input_tokens + chat.output_tokens)} gesamt
            </span>
          )}
          <button type="button" onClick={restart} disabled={!chat || messages.length === 0 || sending} className="btn btn-secondary btn-sm" title="Nachrichten und Token dieses Chats löschen">
            <Icon name="refresh" /> <span className="hidden sm:inline">Neu starten</span>
          </button>
          <button type="button" onClick={() => setPanelOpen(true)} className="btn btn-secondary btn-sm lg:hidden" aria-label="Chats anzeigen"><Icon name="chat" /></button>
        </header>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl px-4 py-6">
            {messages.length === 0 && !sending && (
              <div className="pt-[8vh] text-center">
                <span className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-blue-600 text-white"><Icon name="sparkle" className="h-6 w-6" /></span>
                <h2 className="text-2xl font-semibold text-gray-900">Wie kann ich helfen?</h2>
                <p className="mx-auto mt-2 max-w-lg text-sm text-gray-500">Frag nach Daten, lass dir Einheiten, Schema, Vorlagen und Workflows anlegen oder ändern, oder starte einen Import. Du kannst auch Bilder anhängen.</p>
                <div className="mx-auto mt-8 grid max-w-2xl gap-2 sm:grid-cols-2">
                  {EXAMPLES.map((ex) => (
                    <button key={ex.title} type="button" onClick={() => send(ex.text)} className="rounded-xl border border-gray-200 p-3 text-left hover:bg-gray-50">
                      <div className="text-sm font-medium text-gray-800">{ex.title}</div>
                      <div className="mt-0.5 line-clamp-2 text-xs text-gray-500">{ex.text}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) => m.role === "user" ? (
              <div key={m.id} className="mb-6 flex justify-end">
                <div className="max-w-[85%] rounded-3xl bg-gray-100 px-4 py-2.5 text-[15px] text-gray-900">
                  {m.images.length > 0 && (
                    <div className="mb-2 flex flex-wrap gap-2">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      {m.images.map((src, i) => <img key={i} src={src} alt={`Anhang ${i + 1}`} className="max-h-48 max-w-full rounded-xl object-contain" />)}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap break-words">{m.content}</div>
                </div>
              </div>
            ) : (
              <div key={m.id} className="mb-8 flex gap-3">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white"><Icon name="sparkle" className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <StepsView steps={m.steps} />
                  <Markdown text={m.content} />
                  {m.proposals.map((p) => (
                    <ProposalCard key={p.id} p={p} busy={busyProposal !== null} workflows={workflows}
                      onApply={(wf) => apply(m, p.id, wf)} onDismiss={() => dismiss(m, p.id)} />
                  ))}
                  {m.proposals.filter((p) => p.status === "pending" && p.tool !== "propose_reanalysis").length > 1 && (
                    <button type="button" disabled={busyProposal !== null} onClick={() => applyAll(m)}
                      className="mt-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50" data-testid="apply-all">
                      Alle offenen der Reihe nach freigeben
                    </button>
                  )}
                  {m.usage && (
                    <p className="mt-1 text-[11px] text-gray-400">{fmtNum(m.usage.input_tokens)} ein · {fmtNum(m.usage.output_tokens)} aus · {m.usage.calls} KI-Aufruf{m.usage.calls === 1 ? "" : "e"} · {m.usage.seconds.toLocaleString("de-DE")} s</p>
                  )}
                </div>
              </div>
            ))}

            {allDecided && !sending && (
              <div className="mb-6 pl-10">
                <button type="button" onClick={() => send("Die Vorschläge sind bearbeitet. Bitte mach weiter.")} className="rounded-full border border-gray-300 px-3 py-1 text-xs text-gray-600 hover:bg-gray-50">
                  Dem Assistenten Bescheid geben und weitermachen
                </button>
              </div>
            )}

            {sending && (
              <div className="mb-8 flex items-center gap-3" role="status" aria-live="polite">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white"><Icon name="sparkle" className="h-4 w-4" /></span>
                <span className="flex items-center gap-1.5 text-sm text-gray-500">
                  <span className="inline-flex gap-1">{[0, 1, 2].map((i) => <span key={i} className="h-1.5 w-1.5 animate-bounce rounded-full bg-gray-400" style={{ animationDelay: `${i * 0.15}s` }} />)}</span>
                  denkt nach … (lokale Modelle können mehrere Minuten brauchen)
                </span>
              </div>
            )}
          </div>
        </div>

        {/* ─── Eingabe ─── */}
        <div className="px-4 pb-4">
          <div className="mx-auto w-full max-w-3xl">
            {error && <div role="alert" className="mb-2 flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              <span className="min-w-0 break-words">{error}</span>
              <button type="button" aria-label="Meldung schließen" onClick={() => setError(null)}><Icon name="x" className="h-4 w-4" /></button>
            </div>}
            <div className="rounded-3xl border border-gray-300 bg-white p-2 shadow-sm focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100">
              {images.length > 0 && (
                <div className="flex flex-wrap gap-2 px-2 pb-2 pt-1">
                  {images.map((src, i) => (
                    <div key={i} className="relative">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={src} alt={`Vorschau ${i + 1}`} className="h-16 w-16 rounded-xl object-cover" />
                      <button type="button" aria-label={`Bild ${i + 1} entfernen`} onClick={() => setImages((l) => l.filter((_, j) => j !== i))}
                        className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-gray-800 text-white"><Icon name="x" className="h-3 w-3" /></button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex items-end gap-1">
                <input ref={fileRef} type="file" accept="image/*" multiple hidden onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
                <button type="button" aria-label="Bild anhängen" title="Bild anhängen (auch per Einfügen oder Ziehen)" onClick={() => fileRef.current?.click()}
                  className="rounded-full p-2 text-gray-500 hover:bg-gray-100 hover:text-gray-800"><Icon name="image" className="h-5 w-5" /></button>
                <textarea ref={textRef} rows={1} value={input} aria-label="Nachricht an den Assistenten" placeholder="Nachricht eingeben …"
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
                  onPaste={(e) => { const files = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/")); if (files.length) { e.preventDefault(); addFiles(files); } }}
                  className="max-h-[220px] min-h-[40px] flex-1 resize-none bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-gray-400" />
                <button type="button" aria-label="Senden" onClick={() => send()} disabled={sending || (!input.trim() && images.length === 0)}
                  className="rounded-full bg-gray-900 p-2 text-white hover:bg-black disabled:bg-gray-200 disabled:text-gray-400"><Icon name="send" className="h-5 w-5" /></button>
              </div>
            </div>
            <p className="mt-1.5 text-center text-[11px] text-gray-400">Enter sendet, Umschalt+Enter macht eine neue Zeile. Der Assistent kann sich irren — prüfe Vorschläge vor der Freigabe.</p>
          </div>
        </div>

        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-blue-50/90 text-lg font-medium text-blue-700">
            Bild hier ablegen
          </div>
        )}
      </section>

      {/* ─── Letzte Chats (rechts) ─── */}
      <aside aria-label="Letzte Chats" className="hidden w-72 shrink-0 border-l border-gray-200 bg-gray-50 lg:block">{chatList}</aside>
      {panelOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setPanelOpen(false)} />
          <div className="absolute inset-y-0 right-0 w-72 bg-gray-50 shadow-xl">{chatList}</div>
        </div>
      )}
    </div>
  );
}

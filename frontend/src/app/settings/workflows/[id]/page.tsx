"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import WorkflowEditor from "@/components/workflows/WorkflowEditor";
import {
  AIConfig, DocumentType, SystemPrompt, Workflow,
  aiConfigsApi, documentTypesApi, extractApiError, systemPromptsApi, workflowsApi,
} from "@/lib/api";

export default function WorkflowEditPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [prompts, setPrompts] = useState<SystemPrompt[]>([]);
  const [aiConfigs, setAiConfigs] = useState<AIConfig[]>([]);
  const [docTypes, setDocTypes] = useState<DocumentType[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [wf, ps, cs, dts] = await Promise.all([
          workflowsApi.get(id), systemPromptsApi.list(), aiConfigsApi.list(), documentTypesApi.list(),
        ]);
        if (cancelled) return;
        setWorkflow(wf); setPrompts(ps); setAiConfigs(cs); setDocTypes(dts);
      } catch (err) {
        if (!cancelled) setError(extractApiError(err, "Workflow konnte nicht geladen werden"));
      }
    })();
    return () => { cancelled = true; };
  }, [id]);

  return (
    <div className="w-full">
      <div className="mb-3 flex items-center gap-3 text-sm">
        <Link href="/settings/workflows" className="text-blue-700 hover:underline">← Alle Workflows</Link>
      </div>
      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
      {!workflow && !error && <p className="py-10 text-center text-sm text-gray-400">Lade…</p>}
      {workflow && (
        <WorkflowEditor
          key={workflow.id}
          workflow={workflow} prompts={prompts} aiConfigs={aiConfigs} docTypes={docTypes}
          onSaved={(wf) => setWorkflow((prev) => (prev ? { ...prev, ...wf } : wf))}
        />
      )}
    </div>
  );
}

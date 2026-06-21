import { FormEvent, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ExternalLink, Loader2, Send, UploadCloud } from "lucide-react";

import ConceptGraphCanvas, {
  GraphCanvasEdge,
  GraphCanvasNode,
} from "../components/ConceptGraphCanvas";
import PdfPreviewModal from "../components/PdfPreviewModal";
import UploadModal from "../components/UploadModal";
import {
  API_BASE_URL,
  GraphContextItem,
  IngestResponse,
  QueryResponse,
  UploadStatusResponse,
  getUploadStatus,
  sendQuery,
} from "../services/api";

const DEFAULT_COURSE_ID = "default-course";

export default function Dashboard(): JSX.Element {
  const [question, setQuestion] = useState("");
  const [courseId, setCourseId] = useState(DEFAULT_COURSE_ID);
  const [weekNumber, setWeekNumber] = useState(1);
  const [response, setResponse] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [uploadJobs, setUploadJobs] = useState<UploadStatusResponse[]>([]);
  const [selectedPreview, setSelectedPreview] = useState<{
    title: string;
    previewUrl: string;
  } | null>(null);

  const graphElements = useMemo(
    () => buildGraphElements(response?.graph_context ?? []),
    [response],
  );

  useEffect(() => {
    const pending = uploadJobs.filter(
      (job) => job.status === "queued" || job.status === "running",
    );
    if (pending.length === 0) {
      return undefined;
    }

    const intervalId = window.setInterval(async () => {
      const results = await Promise.allSettled(
        pending.map(async (job) => getUploadStatus(job.task_id)),
      );

      setUploadJobs((current) =>
        current.map((job) => {
          const match = results.find(
            (result) => result.status === "fulfilled" && result.value.task_id === job.task_id,
          );
          if (!match || match.status !== "fulfilled") {
            return job;
          }
          return match.value;
        }),
      );
    }, 2500);

    return () => window.clearInterval(intervalId);
  }, [uploadJobs]);

  function handleUploadCreated(upload: IngestResponse): void {
    const now = new Date().toISOString();
    setUploadJobs((current) => [
      {
        upload_id: upload.upload_id,
        task_id: upload.task_id,
        course_id: upload.course_id,
        week_number: upload.week_number,
        original_filename: upload.original_filename,
        status: "queued",
        error_message: null,
        result_json: null,
        created_at: now,
        updated_at: now,
        started_at: null,
        completed_at: null,
        preview_url: upload.preview_url,
      },
      ...current.filter((job) => job.task_id !== upload.task_id),
    ]);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!question.trim() || !courseId.trim() || weekNumber < 1) {
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const result = await sendQuery(question.trim(), courseId.trim(), weekNumber);
      setResponse(result);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "Unable to resolve the query.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="grid min-h-[calc(100vh-64px)] grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(360px,440px)_1fr] lg:p-6">
      <PdfPreviewModal
        isOpen={selectedPreview !== null}
        onClose={() => setSelectedPreview(null)}
        previewUrl={selectedPreview?.previewUrl ?? ""}
        title={selectedPreview?.title ?? "PDF Preview"}
      />
      <UploadModal
        isOpen={isUploadModalOpen}
        onClose={() => setIsUploadModalOpen(false)}
        onUploaded={handleUploadCreated}
      />
      <section className="flex min-h-[calc(100vh-96px)] flex-col rounded-md border border-slate-200 bg-white shadow-panel">
        <form className="border-b border-slate-200 p-4" onSubmit={handleSubmit}>
          <div className="space-y-3">
            <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
              Course ID
              <input
                className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm font-medium text-ink outline-none transition focus:border-signal focus:ring-2 focus:ring-teal-100"
                value={courseId}
                onChange={(event) => setCourseId(event.target.value)}
              />
            </label>
            <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
              Week Number
              <input
                className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm font-medium text-ink outline-none transition focus:border-signal focus:ring-2 focus:ring-teal-100"
                type="number"
                min={1}
                value={weekNumber}
                onChange={(event) => setWeekNumber(Number(event.target.value))}
              />
            </label>
            <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500">
              Student Question
              <textarea
                className="mt-1 min-h-28 w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm leading-6 text-ink outline-none transition focus:border-signal focus:ring-2 focus:ring-teal-100"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
              />
            </label>
            <button
              className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-md bg-ink px-4 text-sm font-semibold text-white transition hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-400"
              type="submit"
              disabled={isLoading || !question.trim() || !courseId.trim() || weekNumber < 1}
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Send className="h-4 w-4" aria-hidden="true" />
              )}
              Query ConceptGraph
            </button>
          </div>
        </form>

        <div className="border-b border-slate-200 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Processing Queue
            </h2>
            <span className="text-xs text-slate-400">{uploadJobs.length} uploads</span>
          </div>
          {uploadJobs.length ? (
            <div className="space-y-2">
              {uploadJobs.map((job) => (
                <div
                  className="flex items-center justify-between gap-3 rounded-md border border-slate-200 bg-panel px-3 py-2"
                  key={job.task_id}
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">
                      {job.original_filename}
                    </p>
                    <p className="text-xs text-slate-500">
                      Course {job.course_id} · Week {job.week_number}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-semibold uppercase tracking-wide text-slate-600">
                      {job.status}
                    </span>
                    {job.status === "completed" ? (
                      <button
                        className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                        onClick={() =>
                          setSelectedPreview({
                            title: job.original_filename,
                            previewUrl: `${API_BASE_URL}/ingest/uploads/${job.upload_id}/preview`,
                          })
                        }
                        type="button"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        Preview
                      </button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              Upload a syllabus to see live processing progress here.
            </p>
          )}
        </div>

        <div className="relative flex-1 overflow-y-auto p-4">
          {isLoading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/60 backdrop-blur-sm dark:bg-[#0B0B0F]/60">
              <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
            </div>
          )}

          {error ? (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              {error}
            </div>
          ) : null}

          <article className="prose prose-slate max-w-none text-sm dark:prose-invert">
            {response?.answer ? (
              <ReactMarkdown>{response.answer}</ReactMarkdown>
            ) : (
              <p className="text-slate-500">
                The answer and syllabus citations will appear here after a query.
              </p>
            )}
          </article>

          {response?.sources.length ? (
            <div className="mt-6 space-y-3">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                Source Citations
              </h2>
              {response.sources.map((source, index) => (
                <div
                  className="rounded-md border border-slate-200 bg-panel p-3"
                  key={source.id}
                >
                  <div className="mb-2 flex items-center justify-between gap-3 text-xs font-semibold text-slate-500">
                    <span>Chunk {index + 1}</span>
                    <span>{formatScore(source.rerank_score ?? source.score)}</span>
                  </div>
                  <button
                    className="mb-2 inline-flex items-center gap-2 text-xs font-semibold text-teal-700 hover:text-teal-800"
                    onClick={() => {
                      const uploadId =
                        typeof source.metadata.upload_id === "string"
                          ? source.metadata.upload_id
                          : "";
                      if (!uploadId) {
                        return;
                      }

                      const pageNumber =
                        typeof source.metadata.page_number === "number"
                          ? source.metadata.page_number
                          : undefined;
                      const pageSuffix =
                        typeof pageNumber === "number" ? `#page=${pageNumber}` : "";
                      setSelectedPreview({
                        title:
                          typeof pageNumber === "number"
                            ? `Chunk ${index + 1} · Page ${pageNumber}`
                            : `Chunk ${index + 1}`,
                        previewUrl: `${API_BASE_URL}/ingest/uploads/${uploadId}/preview${pageSuffix}`,
                      });
                    }}
                    type="button"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open PDF preview
                  </button>
                  <p className="line-clamp-5 text-sm leading-6 text-slate-700">
                    {source.text}
                  </p>
                  <p className="mt-2 text-xs text-slate-500">
                    {typeof source.metadata.page_number === "number"
                      ? `Page ${source.metadata.page_number}`
                      : "Page unavailable"}
                  </p>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="min-h-[calc(100vh-96px)] rounded-md border border-slate-200 bg-white p-4 shadow-panel">
        <div className="mb-3 flex items-center justify-between gap-4">
          <div>
            <h1 className="text-base font-semibold text-ink">Concept Map</h1>
            <p className="text-sm text-slate-500">
              {graphElements.nodes.length} concepts, {graphElements.edges.length} links
            </p>
          </div>
          <button
            onClick={() => setIsUploadModalOpen(true)}
            className="inline-flex items-center gap-2 rounded-md bg-teal-50 px-3 py-1.5 text-sm font-medium text-teal-700 hover:bg-teal-100 dark:bg-teal-900/30 dark:text-teal-300 dark:hover:bg-teal-900/50"
          >
            <UploadCloud className="h-4 w-4" />
            Upload Syllabus
          </button>
        </div>
        <div className="relative h-[calc(100%-56px)]">
          {isLoading && (
            <div className="absolute inset-0 z-10 flex items-center justify-center rounded-md bg-white/60 backdrop-blur-sm dark:bg-[#0B0B0F]/60">
              <Loader2 className="h-8 w-8 animate-spin text-teal-500" />
            </div>
          )}
          <ConceptGraphCanvas
            nodes={graphElements.nodes}
            edges={graphElements.edges}
          />
        </div>
      </section>
    </main>
  );
}

function buildGraphElements(graphContext: GraphContextItem[]): {
  nodes: GraphCanvasNode[];
  edges: GraphCanvasEdge[];
} {
  const nodes = new Map<string, GraphCanvasNode>();
  const edges = new Map<string, GraphCanvasEdge>();

  graphContext.forEach((item, itemIndex) => {
    const conceptId = item.concept.id ?? `concept-${itemIndex}`;
    nodes.set(conceptId, {
      id: conceptId,
      label: item.concept.name ?? conceptId,
      type: item.concept.type,
      description: item.concept.description,
    });

    item.prerequisites.forEach((prerequisite, prereqIndex) => {
      const prerequisiteId =
        prerequisite.id ?? `${conceptId}-prerequisite-${prereqIndex}`;
      nodes.set(prerequisiteId, {
        id: prerequisiteId,
        label: prerequisite.name ?? prerequisiteId,
        type: prerequisite.type,
        description: prerequisite.description,
      });

      const edgeId = `${prerequisiteId}->${conceptId}`;
      edges.set(edgeId, {
        id: edgeId,
        source: prerequisiteId,
        target: conceptId,
        label: "PREREQUISITE_OF",
      });
    });
  });

  return {
    nodes: Array.from(nodes.values()),
    edges: Array.from(edges.values()),
  };
}

function formatScore(score: number | undefined): string {
  if (typeof score !== "number") {
    return "score unavailable";
  }
  return score.toFixed(3);
}

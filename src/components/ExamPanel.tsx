import { useState } from "react";
import { BookOpenCheck, ChevronDown, ChevronUp, Loader2 } from "lucide-react";

import { ExamResponse, generateExam } from "../services/api";

interface ExamPanelProps {
  courseId: string;
}

export default function ExamPanel({ courseId }: ExamPanelProps): JSX.Element {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [exam, setExam] = useState<ExamResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<number[]>([]);

  async function handleGenerate(): Promise<void> {
    if (!courseId) return;
    setIsLoading(true);
    setError(null);
    setRevealed([]);
    try {
      const result = await generateExam(courseId, 5);
      setExam(result);
      setIsOpen(true);
      if (result.questions.length === 0) {
        setError("No processed content was found for this course.");
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to generate exam.");
      setIsOpen(true);
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="border-b border-slate-200 bg-teal-50/40 p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink">
            <BookOpenCheck className="h-4 w-4 text-teal-700" />
            Practice exam
          </h2>
          <p className="mt-0.5 text-xs text-slate-500">Create 5 questions from the whole course.</p>
        </div>
        <button
          className="inline-flex items-center gap-2 rounded-md border border-teal-200 bg-white px-3 py-2 text-xs font-semibold text-teal-800 hover:bg-teal-50 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!courseId || isLoading}
          onClick={() => void handleGenerate()}
          type="button"
        >
          {isLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Generate
        </button>
      </div>

      {(exam || error) && isOpen ? (
        <div className="mt-4 max-h-96 space-y-3 overflow-y-auto pr-1">
          {error ? <p className="rounded-md bg-red-50 p-3 text-xs text-red-700">{error}</p> : null}
          {exam?.coverage.topics?.length ? (
            <p className="text-xs text-slate-500">
              Covers {exam.coverage.topics.length} topics across {exam.coverage.documents?.length ?? 0} documents using {exam.source_count} cited passages.
            </p>
          ) : null}
          {exam?.questions.map((item, index) => {
            const isRevealed = revealed.includes(index);
            return (
              <article className="rounded-md border border-slate-200 bg-white p-3" key={`${index}-${item.question_text}`}>
                <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-teal-700">{item.topic}</p>
                <p className="text-sm font-semibold text-ink">{index + 1}. {item.question_text}</p>
                <ol className="mt-2 space-y-1 text-xs text-slate-600">
                  {item.options.map((option) => <li key={option}>{option}</li>)}
                </ol>
                <button
                  className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-teal-700"
                  onClick={() => setRevealed((current) => isRevealed ? current.filter((value) => value !== index) : [...current, index])}
                  type="button"
                >
                  {isRevealed ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                  {isRevealed ? "Hide answer" : "Reveal answer"}
                </button>
                {isRevealed ? (
                  <div className="mt-2 space-y-2 rounded bg-teal-50 p-2 text-xs leading-5 text-teal-950">
                    <p><strong>{item.correct_answer}</strong><br />{item.explanation}</p>
                    {item.sources.map((source) => (
                      <details className="rounded border border-teal-200 bg-white/70 p-2" key={source.source_id}>
                        <summary className="cursor-pointer font-semibold">
                          {source.document_name}{source.page_number ? ` · Page ${source.page_number}` : ""}{source.section_heading ? ` · ${source.section_heading}` : ""}
                        </summary>
                        <p className="mt-1 text-slate-700">{source.supporting_passage}</p>
                      </details>
                    ))}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

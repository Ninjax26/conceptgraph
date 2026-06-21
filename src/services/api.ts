export interface ConceptNode {
  id: string;
  name: string;
  type: string;
  description: string;
}

export interface ConceptRelationship {
  source_node_id: string;
  target_node_id: string;
  relation_type: string;
}

export interface SourceChunk {
  id: string;
  score?: number;
  rerank_score?: number;
  text: string;
  metadata: Record<string, string | number | boolean | null>;
}

export interface GraphContextItem {
  concept: Partial<ConceptNode>;
  prerequisites: Array<Partial<ConceptNode>>;
}

export interface QueryResponse {
  answer: string;
  sources: SourceChunk[];
  graph_context: GraphContextItem[];
}

export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

async function fetchWithTimeout(url: string, options: RequestInit & { timeout?: number } = {}) {
  const { timeout = 30000 } = options;
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeout);
  
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(id);
    if (!response.ok) {
      let message = response.statusText;
      try {
        const errJson = await response.json();
        message = errJson.detail || message;
      } catch (e) {
        const errText = await response.text();
        message = errText || message;
      }
      throw new Error(`API request failed: ${message}`);
    }
    return response;
  } catch (error) {
    clearTimeout(id);
    if (error instanceof Error && error.name === "AbortError") {
      throw new Error("Request timed out. The server took too long to respond.");
    }
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      throw new Error("Network error: Could not connect to the server. Is it running?");
    }
    throw error;
  }
}

export async function sendQuery(
  question: string,
  courseId: string,
  weekNumber: number,
): Promise<QueryResponse> {
  const response = await fetchWithTimeout(`${API_BASE_URL}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      course_id: courseId,
      week_number: weekNumber,
    }),
    timeout: 60000, // 60s timeout for LLM synthesis
  });

  return response.json() as Promise<QueryResponse>;
}

export interface MockQuestion {
  question_text: string;
  options: string[];
  correct_answer: string;
  explanation: string;
}

export interface ExamResponse {
  course_id: string;
  week_number: number;
  questions: MockQuestion[];
}

export async function generateExam(
  courseId: string,
  weekNumber: number,
  numQuestions: number = 5
): Promise<ExamResponse> {
  const response = await fetchWithTimeout(`${API_BASE_URL}/exam/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      course_id: courseId,
      week_number: weekNumber,
      num_questions: numQuestions,
    }),
    timeout: 60000, // 60s timeout for LLM exam generation
  });

  return response.json() as Promise<ExamResponse>;
}

export interface IngestResponse {
  message: string;
  task_id: string;
  upload_id: string;
  course_id: string;
  week_number: number;
  original_filename: string;
  status: "queued";
  preview_url: string;
}

export async function uploadDocument(
  file: File,
  courseId: string,
  weekNumber = 1,
): Promise<IngestResponse> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("course_id", courseId);
  formData.append("week_number", String(weekNumber));

  const response = await fetchWithTimeout(`${API_BASE_URL}/ingest/upload`, {
    method: "POST",
    body: formData,
    timeout: 30000,
  });

  return response.json() as Promise<IngestResponse>;
}

export interface UploadStatusResponse {
  upload_id: string;
  task_id: string;
  course_id: string;
  week_number: number;
  original_filename: string;
  status: "queued" | "running" | "completed" | "failed";
  error_message?: string | null;
  result_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  preview_url: string;
}

export async function getUploadStatus(taskId: string): Promise<UploadStatusResponse> {
  const response = await fetchWithTimeout(`${API_BASE_URL}/ingest/status/${taskId}`, {
    method: "GET",
    timeout: 15000,
  });

  return response.json() as Promise<UploadStatusResponse>;
}

export interface ChathySession {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  n_messages: number;
}

export interface StoredMessage {
  role: string;
  content: string | null;
  tool_calls?: unknown;
  tool_call_id?: string | null;
  name?: string | null;
  usage?: unknown;
  created_at: string;
}

export interface ToolActivity {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  preview?: string;
  done: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  tools: ToolActivity[];
  streaming?: boolean;
  error?: boolean;
}

export type ChathyEvent =
  | { type: "delta"; text: string }
  | { type: "tool_start"; id: string; name: string; args?: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; preview?: string }
  // `capped`: the turn ran out of tool rounds and landed on a wrap-up answer (still a real
  // answer — the model was told to flag whatever it could not finish verifying).
  | { type: "done"; usage?: unknown; capped?: boolean }
  | { type: "error"; message: string };

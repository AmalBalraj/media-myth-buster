export type Verdict =
  | "true"
  | "mostly_true"
  | "mixed"
  | "mostly_false"
  | "false"
  | "unverifiable"
  | "opinion";

export interface Evidence {
  url: string;
  title: string | null;
  publisher: string | null;
  publisher_credibility: number | null;
  tier: string | null;
  stance: string | null;
  cited: boolean;
  snippet: string | null;
}

export interface Claim {
  id: string;
  idx: number;
  text: string;
  claim_type: string;
  checkworthiness: number;
  t_start: number | null;
  t_end: number | null;
  source: string;
  verdict: Verdict | null;
  confidence: number | null;
  rationale: string | null;
  evidence: Evidence[];
}

export interface ForensicSignal {
  signal: string;
  raw_score: number | null;
  calibrated_prob: number | null;
  confidence: number | null;
  detail: Record<string, unknown> | null;
}

export interface Report {
  id: string;
  status: "queued" | "running" | "done" | "failed";
  stage: string | null;
  error: string | null;
  created_at: string;
  validity_score: number | null;
  validity_ci_low: number | null;
  validity_ci_high: number | null;
  summary: string | null;
  lean_applicable: boolean;
  lean_economic: number | null;
  lean_social: number | null;
  lean_confidence: number | null;
  lean_rationale: string | null;
  forensics_score: number | null;
  forensics_confidence: number | null;
  subscores: Record<string, any> | null;
  transcript: { text?: string; segments?: { start: number; end: number; text: string }[] } | null;
  video_analysis: Record<string, any> | null;
  media: {
    shortcode: string;
    permalink: string | null;
    caption: string | null;
    posted_at: string | null;
    ingest_path: string;
    like_count: number | null;
    comment_count: number | null;
    view_count: number | null;
    creator: {
      handle: string;
      display_name: string | null;
      followers: number | null;
      is_professional: boolean;
      verified: boolean;
    } | null;
  } | null;
  claims: Claim[];
  forensics: ForensicSignal[];
}

export interface StageEvent {
  stage: string;
  status: string;
  [k: string]: unknown;
}

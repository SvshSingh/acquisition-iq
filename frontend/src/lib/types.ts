/** Mirrors the Pydantic schemas in `backend/app/schemas.py`. */

export type FactorKey =
  | "succession"
  | "buy_box"
  | "digital_gap"
  | "fragmentation"
  | "contactability"
  | "health";

export type Confidence = "high" | "medium" | "low";
export type VerificationStatus = "verified" | "risky" | "invalid" | "unknown";

export interface Evidence {
  label: string;
  detail: string;
  source_url: string | null;
  impact: number;
}

export interface FactorResult {
  key: FactorKey;
  label: string;
  score: number;
  confidence: Confidence;
  evidence: Evidence[];
  missing_signals: string[];
  /** Whether this score is a measurement or a fallback prior. Unmeasured
   *  factors contribute nothing and have their weight redistributed. */
  measured: boolean;
}

export interface Contact {
  name: string | null;
  title: string | null;
  email: string | null;
  email_status: VerificationStatus;
  phone: string | null;
  phone_valid: boolean | null;
  linkedin_url: string | null;
  is_decision_maker: boolean;
}

export interface WebSignals {
  fetched_at: string | null;
  https: boolean | null;
  mobile_viewport: boolean | null;
  has_analytics: boolean | null;
  generator: string | null;
  copyright_year: number | null;
  latest_content_year: number | null;
  page_bytes: number | null;
  tech_hints: string[];
  has_careers_page: boolean;
  has_team_page: boolean;
  founded_year: number | null;
  owner_mentions: string[];
  pe_backed_mentions: string[];
  raw_text_excerpt: string | null;
}

export interface Company {
  id: string;
  name: string;
  domain: string | null;
  website: string | null;
  /** How the website was established — a source that published it, or
   *  "inferred:phone" / "inferred:name" where it was derived and then proved. */
  website_source: string | null;
  website_evidence: string | null;
  industry: string | null;
  city: string | null;
  state: string | null;
  postcode: string | null;
  latitude: number | null;
  longitude: number | null;
  employee_count: number | null;
  revenue_usd: number | null;
  founded_year: number | null;
  business_type: string | null;
  has_employees: boolean | null;
  licence_number: string | null;
  licence_issued: string | null;
  licence_classifications: string[];
  contacts: Contact[];
  web: WebSignals;
  peer_count_in_niche: number | null;
  sibling_location_count: number | null;
  source: string;
  source_url: string | null;
  /** How complete this record is, scored separately from acquisition fit.
   *  A thin record on a great business must not be marked down for our
   *  ignorance, so the two never mix. */
  data_quality: number | null;
  quality_issues: string[];
}

export type Weights = Record<FactorKey, number>;

export interface ScoreResult {
  company_id: string;
  score: number;
  confidence: Confidence;
  factors: FactorResult[];
  weights: Weights;
  effective_weights: Partial<Weights>;
  /** Share of the declared thesis that had evidence behind it. */
  covered_weight: number;
  scored_at: string;
  engine_version: string;
}

export interface ScoredCompany {
  company: Company;
  score: ScoreResult;
}

export interface SearchResponse {
  results: ScoredCompany[];
  total: number;
  took_ms: number;
  from_cache: boolean;
  source: string;
}

export interface FactorMeta {
  key: FactorKey;
  label: string;
  description: string;
  default_weight: number;
}

export interface Meta {
  market: { key?: string; label?: string; state?: string };
  generated_at: string | null;
  sources: string[];
  count: number;
  engine_version: string;
  factors: FactorMeta[];
  buy_box: Record<string, number>;
  filters: { industry: string[]; city: string[]; business_type: string[] };
  crm_presets: string[];
}

export const FACTOR_ORDER: FactorKey[] = [
  "succession",
  "buy_box",
  "digital_gap",
  "fragmentation",
  "contactability",
  "health",
];

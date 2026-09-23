export interface ExperienceEntry {
  title: string;
  company: string;
  start_date: string;
  end_date: string;
}

export interface ResumeState {
  has_resume: boolean;
  file_name: string | null;
  file_size: number | null;
  file_hash: string | null;
  parsed_at: string | null;
  expanded_at: string | null;
  cached: boolean | null;
  skills: string[];
  roles: string[];
  experience: ExperienceEntry[];
  total_experience_years: number;
  my_yoe: number;
  roles_expanded: string[] | null;
}

export interface SettingsInfo {
  llm: {
    provider: string;
    model: string;
    configured: boolean;
    source?: "runtime" | "env" | null;
  };
  location_targets?: {
    countries: string[];
    available: string[];
  };
  linkedin: {
    status: string;
    detail: string;
    docker_installed: boolean;
    session_dir_present: boolean;
  };
  search_source?: { name: "mcp" | "playwright"; available_sources: string[] };
  drafts: { max_per_day: number; sent_today: number };
  retention: {
    dedup_entries: number;
    oldest_entry_age_days: number;
    resume_cached: boolean;
    drafts_new?: number;
    drafts_total?: number;
  };
}

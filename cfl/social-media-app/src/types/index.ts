export interface SocialJob {
  id: string;
  name: string;
  url: string;
  screenshot_selector: string;
  text_selector: string;
  template_name: string;
  platforms: string[];
  schedule_time: string;
  is_active: boolean;
  created_at: string;
  last_run: string | null;
  last_status: string | null;
}

export interface PlatformResult {
  platform: string;
  success: boolean;
  message: string;
  postId?: string;
  url?: string;
}

export interface PostHistory {
  id: string;
  job_id: string;
  job_name: string;
  caption: string | null;
  hashtags: string | null;
  screenshot_path: string | null;
  extracted_text: string | null;
  video_path: string | null;
  export_path: string | null;
  captions_path: string | null;
  voiceover_path: string | null;
  template_name: string | null;
  credits_used: number;
  watermark_applied: boolean;
  video_notes: string[] | null;
  platforms: string[];
  status: 'success' | 'partial' | 'pending' | 'error';
  platform_results: PlatformResult[] | null;
  created_at: string;
}

export interface CreateJobInput {
  name: string;
  url: string;
  screenshot_selector: string;
  text_selector: string;
  template_name: string;
  platforms: string[];
  schedule_time: string;
}

export interface RunJobResult {
  success: boolean;
  postId?: string;
  hook?: string;
  script?: string;
  voiceoverText?: string;
  caption?: string;
  hashtags?: string;
  screenshotPath?: string | null;
  videoPath?: string | null;
  exportPath?: string | null;
  captionsPath?: string | null;
  voiceoverPath?: string | null;
  extractedText?: string | null;
  creditsUsed?: number;
  creditsRemaining?: number;
  videoNotes?: string[];
  platformResults?: PlatformResult[];
  status?: string;
  error?: string;
}

export interface UnlimitedSeriesFeatures {
  autoPostVideos: boolean;
  voiceovers: boolean;
  aiGeneratedContent: boolean;
  scriptAndHookGeneration: boolean;
  backgroundMusic: boolean;
  aiEffectsZoomsTransitions: boolean;
  cinematicCaptions: boolean;
  noWatermark: boolean;
  downloadVideos: boolean;
  unlimitedExports: boolean;
  unlimitedCustomTemplates: boolean;
}

export interface PlanSnapshot {
  planCode: string;
  planName: string;
  monthlyCredits: number;
  creditsUsedThisMonth: number;
  creditsRemaining: number;
  monthKey: string;
  activeTemplateCount: number;
  features: UnlimitedSeriesFeatures;
}

import fs from 'fs';
import path from 'path';

export interface CaptionResult {
  caption: string;
  hashtags: string;
}

export interface ScriptHookResult {
  hook: string;
  script: string;
  voiceover: string;
}

export interface VoiceoverAudioResult {
  audioPath: string | null;
  reason?: string;
}

export async function generateCaption(
  extractedText: string,
  jobName: string,
  platforms: string[],
): Promise<CaptionResult> {
  const apiKey = process.env.OPENAI_API_KEY;

  if (!apiKey) {
    // Fallback when OpenAI is not configured
    return {
      caption: `🎯 Today's predictions are live! Check out ${jobName} — expert picks powered by AI. Visit underdogs.bet`,
      hashtags:
        '#SportsPredictions #SportsBetting #Underdogs #AIPicks #WinningPicks',
    };
  }

  try {
    const { default: OpenAI } = await import('openai');
    const openai = new OpenAI({ apiKey });

    const platformList = platforms.join(', ');
    const textSnippet = extractedText.slice(0, 1_500);

    const response = await openai.chat.completions.create({
      model: 'gpt-4o-mini',
      messages: [
        {
          role: 'system',
          content:
            'You are a sports betting social media manager for underdogs.bet, a professional ML-powered sports predictions platform. Write engaging, concise posts that excite fans.',
        },
        {
          role: 'user',
          content: `Generate a social media caption and hashtags for: ${platformList}

Content from our predictions page:
"""
${textSnippet}
"""

Rules:
- Caption: max 240 characters, engaging, include a call-to-action (e.g. "Check the link!")
- Hashtags: 6–8 tags relevant to sports betting/predictions
- Tone: confident, professional, exciting
- Always mention underdogs.bet

Respond as JSON: {"caption": "...", "hashtags": "..."}`,
        },
      ],
      response_format: { type: 'json_object' },
      max_tokens: 300,
    });

    const result = JSON.parse(
      response.choices[0].message.content ?? '{}',
    ) as Record<string, string>;

    return {
      caption: result.caption ?? '',
      hashtags: result.hashtags ?? '',
    };
  } catch (err) {
    console.error('[AI] Caption generation failed:', err);
    return {
      caption: `🎯 Today's ${jobName} predictions are live on underdogs.bet! Don't miss our AI-powered picks.`,
      hashtags: '#SportsPredictions #SportsBetting #Underdogs #AIPicks',
    };
  }
}

export async function generateScriptHookVoiceover(
  extractedText: string,
  jobName: string,
  templateName: string,
): Promise<ScriptHookResult> {
  const apiKey = process.env.OPENAI_API_KEY;
  const textSnippet = extractedText.slice(0, 2_000);

  if (!apiKey) {
    return {
      hook: `🚨 ${jobName}: Today's edge starts here.`,
      script: `Welcome back to underdogs.bet. Here are today's top predictions from ${jobName}. Follow for daily AI-powered picks and sharp insights.`,
      voiceover: `Welcome back to underdogs dot bet. Here are today's top predictions from ${jobName}. Follow for daily AI-powered picks and sharp insights.`,
    };
  }

  try {
    const { default: OpenAI } = await import('openai');
    const openai = new OpenAI({ apiKey });

    const response = await openai.chat.completions.create({
      model: 'gpt-4o-mini',
      messages: [
        {
          role: 'system',
          content:
            'You create short-form sports betting video scripts that are punchy, clear, and conversion-focused.',
        },
        {
          role: 'user',
          content: `Generate a hook + short script + voiceover narration for a ${templateName} video.

Source text:
"""
${textSnippet}
"""

Rules:
- Hook: <= 90 chars, attention-grabbing
- Script: 3-5 short sentences, crisp and energetic
- Voiceover: natural narration that matches the script
- Mention underdogs.bet once

Respond as JSON: {"hook":"...","script":"...","voiceover":"..."}`,
        },
      ],
      response_format: { type: 'json_object' },
      max_tokens: 450,
    });

    const parsed = JSON.parse(
      response.choices[0].message.content ?? '{}',
    ) as Record<string, string>;

    return {
      hook: parsed.hook ?? `🚨 ${jobName}: Today's edge starts here.`,
      script:
        parsed.script ??
        `Today's top model signals are in for ${jobName}. Check underdogs.bet for the complete slate.`,
      voiceover:
        parsed.voiceover ??
        `Today's top model signals are in for ${jobName}. Check underdogs dot bet for the complete slate.`,
    };
  } catch (err) {
    console.error('[AI] Script/hook generation failed:', err);
    return {
      hook: `🚨 ${jobName}: Today's edge starts here.`,
      script: `Today's top model signals are in for ${jobName}. Check underdogs.bet for the complete slate.`,
      voiceover: `Today's top model signals are in for ${jobName}. Check underdogs dot bet for the complete slate.`,
    };
  }
}

export async function synthesizeVoiceoverAudio(
  voiceoverText: string,
  baseName: string,
): Promise<VoiceoverAudioResult> {
  const apiKey = process.env.OPENAI_API_KEY;
  if (!apiKey) {
    return {
      audioPath: null,
      reason: 'N/A — OPENAI_API_KEY is not configured, so voiceover audio could not be synthesized.',
    };
  }

  try {
    const { default: OpenAI } = await import('openai');
    const openai = new OpenAI({ apiKey });
    const response = await openai.audio.speech.create({
      model: 'tts-1',
      voice: 'alloy',
      input: voiceoverText.slice(0, 4_000),
    });

    const bytes = Buffer.from(await response.arrayBuffer());
    const voiceoversDir = path.join(process.cwd(), 'public', 'voiceovers');
    fs.mkdirSync(voiceoversDir, { recursive: true });

    const filename = `${baseName}.mp3`;
    const fullPath = path.join(voiceoversDir, filename);
    fs.writeFileSync(fullPath, bytes);

    return { audioPath: `/voiceovers/${filename}` };
  } catch (err) {
    console.error('[AI] Voiceover synthesis failed:', err);
    return {
      audioPath: null,
      reason: `N/A — voiceover synthesis failed: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
  }
}

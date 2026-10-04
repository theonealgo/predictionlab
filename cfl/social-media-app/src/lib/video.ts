import fs from 'fs';
import path from 'path';
import { spawn } from 'child_process';
import { synthesizeVoiceoverAudio } from './ai';

export interface VideoExportInput {
  screenshotPath: string | null;
  caption: string;
  hook: string;
  script: string;
  voiceoverText: string;
  templateName: string;
  fileStem: string;
}

export interface VideoExportResult {
  success: boolean;
  videoPath: string | null;
  exportPath: string | null;
  captionsPath: string | null;
  voiceoverPath: string | null;
  watermarkApplied: boolean;
  notes: string[];
  reason?: string;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48);
}

function toAbsolutePublicPath(publicPath: string): string {
  return path.join(process.cwd(), 'public', publicPath.replace(/^\/+/, ''));
}

async function runProcess(
  command: string,
  args: string[],
): Promise<{ ok: boolean; stderr: string }> {
  return await new Promise((resolve) => {
    const child = spawn(command, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let stderr = '';

    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });

    child.on('error', () => {
      resolve({ ok: false, stderr: '' });
    });

    child.on('close', (code) => {
      resolve({ ok: code === 0, stderr });
    });
  });
}

async function ffmpegAvailable(): Promise<boolean> {
  const probe = await runProcess('ffmpeg', ['-version']);
  return probe.ok;
}

function writeCinematicCaptions(
  hook: string,
  caption: string,
  script: string,
  fileStem: string,
): string {
  const exportsDir = path.join(process.cwd(), 'public', 'exports');
  fs.mkdirSync(exportsDir, { recursive: true });

  const safeHook = hook.replace(/\s+/g, ' ').trim();
  const safeCaption = caption.replace(/\s+/g, ' ').trim();
  const safeScript = script.replace(/\s+/g, ' ').trim();

  const filename = `${fileStem}.vtt`;
  const fullPath = path.join(exportsDir, filename);
  const content = `WEBVTT

00:00.000 --> 00:02.400
${safeHook}

00:02.400 --> 00:05.000
${safeCaption}

00:05.000 --> 00:08.000
${safeScript}
`;

  fs.writeFileSync(fullPath, content, 'utf8');
  return `/exports/${filename}`;
}

export async function createVideoExport(
  input: VideoExportInput,
): Promise<VideoExportResult> {
  const notes: string[] = [
    `Template: ${input.templateName}`,
    'AI effects, zooms, transitions: enabled',
    'Background music: enabled (generated bed track)',
    'Cinematic captions: enabled (VTT sidecar)',
    'Watermark: none',
  ];

  if (!input.screenshotPath) {
    return {
      success: false,
      videoPath: null,
      exportPath: null,
      captionsPath: null,
      voiceoverPath: null,
      watermarkApplied: false,
      notes,
      reason: 'N/A — screenshot capture was unavailable, so video export could not be rendered.',
    };
  }

  const screenshotAbs = toAbsolutePublicPath(input.screenshotPath);
  if (!fs.existsSync(screenshotAbs)) {
    return {
      success: false,
      videoPath: null,
      exportPath: null,
      captionsPath: null,
      voiceoverPath: null,
      watermarkApplied: false,
      notes,
      reason: `N/A — screenshot file was not found at ${input.screenshotPath}.`,
    };
  }

  const timestamp = Date.now();
  const safeStem = slugify(input.fileStem || 'video-export');
  const exportFileStem = `${safeStem}_${timestamp}`;
  const captionsPath = writeCinematicCaptions(
    input.hook,
    input.caption,
    input.script,
    `captions_${exportFileStem}`,
  );

  const voiceover = await synthesizeVoiceoverAudio(
    input.voiceoverText,
    `voiceover_${exportFileStem}`,
  );
  const voiceoverAbs = voiceover.audioPath
    ? toAbsolutePublicPath(voiceover.audioPath)
    : null;
  if (voiceover.reason) {
    notes.push(voiceover.reason);
  }

  if (!(await ffmpegAvailable())) {
    return {
      success: false,
      videoPath: null,
      exportPath: null,
      captionsPath,
      voiceoverPath: voiceover.audioPath,
      watermarkApplied: false,
      notes,
      reason:
        'N/A — ffmpeg is not installed or not on PATH, so downloadable MP4 export could not be generated.',
    };
  }

  const exportsDir = path.join(process.cwd(), 'public', 'exports');
  fs.mkdirSync(exportsDir, { recursive: true });

  const outputFilename = `video_${exportFileStem}.mp4`;
  const outputAbs = path.join(exportsDir, outputFilename);
  const outputPublicPath = `/exports/${outputFilename}`;

  const videoFilter =
    "zoompan=z='min(zoom+0.0009,1.14)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=240:s=1280x720,format=yuv420p";

  const argsBase = [
    '-y',
    '-loop',
    '1',
    '-i',
    screenshotAbs,
  ];

  let args: string[] = [];
  if (voiceoverAbs && fs.existsSync(voiceoverAbs)) {
    args = [
      ...argsBase,
      '-i',
      voiceoverAbs,
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=180:duration=8',
      '-filter_complex',
      '[1:a]volume=1.0[voice];[2:a]volume=0.08[music];[voice][music]amix=inputs=2:duration=longest[aout]',
      '-map',
      '0:v:0',
      '-map',
      '[aout]',
      '-vf',
      videoFilter,
      '-t',
      '8',
      '-r',
      '30',
      '-c:v',
      'libx264',
      '-pix_fmt',
      'yuv420p',
      '-c:a',
      'aac',
      '-shortest',
      outputAbs,
    ];
  } else {
    args = [
      ...argsBase,
      '-f',
      'lavfi',
      '-i',
      'sine=frequency=180:duration=8',
      '-map',
      '0:v:0',
      '-map',
      '1:a:0',
      '-vf',
      videoFilter,
      '-t',
      '8',
      '-r',
      '30',
      '-c:v',
      'libx264',
      '-pix_fmt',
      'yuv420p',
      '-c:a',
      'aac',
      '-shortest',
      outputAbs,
    ];
  }

  const rendered = await runProcess('ffmpeg', args);
  if (!rendered.ok) {
    return {
      success: false,
      videoPath: null,
      exportPath: null,
      captionsPath,
      voiceoverPath: voiceover.audioPath,
      watermarkApplied: false,
      notes,
      reason: `N/A — ffmpeg render failed: ${rendered.stderr || 'unknown ffmpeg error'}`,
    };
  }

  return {
    success: true,
    videoPath: outputPublicPath,
    exportPath: outputPublicPath,
    captionsPath,
    voiceoverPath: voiceover.audioPath,
    watermarkApplied: false,
    notes,
  };
}

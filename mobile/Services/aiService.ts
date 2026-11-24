
import { analyzePhoto as analyzeMockPhoto } from './mockAi';

export type AiAnalysisRequest = {
  photoUri: string;
  question?: string;
};

export type AiAnalysisResponse = {
  answer: string;
  source: 'mock' | 'backend';
};

export async function requestAiAnalysis(
  request: AiAnalysisRequest
): Promise<AiAnalysisResponse> {
  if (!request.photoUri) {
    throw new Error('No photo was provided.');
  }

  // For now, use our fake AI.
  const answer = await analyzeMockPhoto(request.photoUri);

  return {
    answer,
    source: 'mock',
  };
}

export async function analyzePhoto(
  photoUri: string
): Promise<string> {
  const result = await requestAiAnalysis({ photoUri });
  return result.answer;
}

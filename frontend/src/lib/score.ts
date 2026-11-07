/** Brand Score + channel settings (backend: pipeline/score.py, pipeline/channels.py, routers/channels.py).
 *  Kept out of api.ts on purpose: the shared types are extended here by module augmentation. */
import { request, type Channel, type Citation, type ItemDetail, type Severity } from "./api";

export type ScoreBand = "good" | "fair" | "poor";

export interface ScoreDeduction { severity: Severity; band: "flag" | "human" | "pass"; count: number; weight: number; points: number }
export interface ScoreOut {
  score: number;
  band: ScoreBand;
  label: string; // On brand | Needs work | Off brand
  formula: string; // "100 − 2×10 (major, flagged) = 80"
  deductions: ScoreDeduction[];
}
export interface ScoreDay { day: string; avg_score: number; versions: number }

declare module "./api" {
  interface ItemSummary {
    score?: number | null; // latest version's Brand Score; null until checked
    score_band?: ScoreBand | null;
    target_score?: number;
    submit_reason?: string | null; // why it was submitted below target
  }
  interface VersionSummary { score?: ScoreOut | null }
  interface InsightsOut { avg_score?: number | null; scored_items?: number; score_trend?: ScoreDay[] }
}

export interface ChannelLimit {
  code: string; key: string; statement: string; value: number | number[]; default: number | number[];
  overridden: boolean; kind: "hard" | "soft"; severity: Severity; citation: Citation;
}
export interface ChannelSettings { channel: Channel; target_score: number; default_target_score: number; limits: ChannelLimit[] }
export interface ChannelsOut { channels: ChannelSettings[]; not_included: string[] }
export interface ChannelSettingsIn { target_score?: number | null; limits?: Record<string, number | number[] | null> }

export const scoreApi = {
  channels: () => request<ChannelsOut>("GET", "/brand/channels"),
  saveChannel: (channel: Channel, body: ChannelSettingsIn) => request<ChannelsOut>("PUT", `/brand/channels/${channel}`, { json: body }),
  submit: (itemId: number, reason?: string) => request<ItemDetail>("POST", `/items/${itemId}/submit`, { json: reason ? { reason } : {} }),
};

export const SCORE_LABEL: Record<ScoreBand, string> = { good: "On brand", fair: "Needs work", poor: "Off brand" };
export function scoreBand(score: number): ScoreBand {
  return score >= 80 ? "good" : score >= 60 ? "fair" : "poor";
}

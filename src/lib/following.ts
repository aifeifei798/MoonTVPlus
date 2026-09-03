export interface UnwatchedEpisodesInput {
  totalEpisodes: number;
  watchedEpisodes: number;
}

export function computeUnwatchedEpisodes({
  totalEpisodes,
  watchedEpisodes,
}: UnwatchedEpisodesInput): number {
  const safeTotal = Number.isFinite(totalEpisodes)
    ? Math.max(0, Math.floor(totalEpisodes))
    : 0;
  const safeWatched = Number.isFinite(watchedEpisodes)
    ? Math.max(0, Math.floor(watchedEpisodes))
    : 0;

  if (safeTotal <= 0) {
    return 0;
  }

  return Math.max(0, safeTotal - Math.min(safeWatched, safeTotal));
}

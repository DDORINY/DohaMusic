"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Redo2, Scissors, Trash2, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Button, ErrorAlert, Input } from "@/components/ui";
import { usePlayerStore } from "@/stores/player-store";
import { ApiError, userErrorMessage } from "@/services/api-client";
import { dohaApi } from "@/services/doha-api";
import type { CompositionReadItemDto, WorkingClipDto, WorkingCompositionDto, WorkingTrackDto } from "@/types/api";
import { buildWaveformPath, loadWaveformPeaks, type WaveformLoader } from "./waveform";
import {
  projectWaveformWindow,
  useWorkingWaveforms,
  waveformProjectionSignature,
  type MediaSourceResolver,
} from "./working-waveform";
import { newIdempotencyKey } from "./working-composition-history";
import { WorkingPreviewControl } from "./working-preview-control";

const MIN_PIXELS_PER_SECOND = 32;
const MAX_PIXELS_PER_SECOND = 128;
const DEFAULT_PIXELS_PER_SECOND = 64;
const MIN_CLIP_GAIN_DB = -24;
const MAX_CLIP_GAIN_DB = 24;
const CLIP_GAIN_STEP_DB = 0.01;
const CLIP_FADE_STEP_SECONDS = 0.000001;

export function WorkingCompositionEditor({
  projectId,
  snapshotId,
  sources,
  mediaSourceResolver,
  waveformLoader = loadWaveformPeaks,
}: {
  projectId: string;
  snapshotId: string;
  sources: CompositionReadItemDto[];
  mediaSourceResolver?: MediaSourceResolver;
  waveformLoader?: WaveformLoader;
}) {
  return <WorkingCompositionEditorSession
    key={projectId}
    projectId={projectId}
    snapshotId={snapshotId}
    sources={sources}
    mediaSourceResolver={mediaSourceResolver}
    waveformLoader={waveformLoader}
  />;
}

function WorkingCompositionEditorSession({
  projectId,
  snapshotId,
  sources,
  mediaSourceResolver,
  waveformLoader,
}: {
  projectId: string;
  snapshotId: string;
  sources: CompositionReadItemDto[];
  mediaSourceResolver?: MediaSourceResolver;
  waveformLoader: WaveformLoader;
}) {
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [recoveryState, setRecoveryState] = useState<"idle" | "conflict_recovering" | "ready">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedTrackId, setSelectedTrackId] = useState<string | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [trackName, setTrackName] = useState("");
  const [sourceVersionId, setSourceVersionId] = useState(sources[0]?.asset_version.asset_version_id ?? "");
  const [sourceIn, setSourceIn] = useState("0");
  const [sourceOut, setSourceOut] = useState("10");
  const [timelineStart, setTimelineStart] = useState("0");
  const [copyTargetTrackId, setCopyTargetTrackId] = useState("");
  const [copyTimelineStart, setCopyTimelineStart] = useState("");
  const [draggedTrackId, setDraggedTrackId] = useState<string | null>(null);
  const [pixelsPerSecond, setPixelsPerSecond] = useState(DEFAULT_PIXELS_PER_SECOND);
  const playhead = usePlayerStore((state) => state.currentTime);
  const recoveryGeneration = useRef(0);
  useEffect(() => () => { recoveryGeneration.current += 1; }, []);
  const queryKey = useMemo(() => ["working-composition", projectId] as const, [projectId]);
  const working = useQuery({
    queryKey,
    queryFn: ({ signal }) => dohaApi.getWorkingComposition(projectId, signal),
    retry: false,
  });
  const noWorkingComposition = working.error instanceof ApiError
    && working.error.code === "WORKING_COMPOSITION_NOT_FOUND";
  const data = working.data;
  const historyKey = useMemo(
    () => ["working-composition-history", projectId, data?.working_composition_id] as const,
    [data?.working_composition_id, projectId],
  );
  const history = useQuery({
    queryKey: historyKey,
    queryFn: ({ signal }) => dohaApi.getWorkingCompositionHistory(projectId, data!.working_composition_id, signal),
    enabled: Boolean(data),
    retry: false,
  });

  const reconcile = useCallback(async (conflictRecovery = false) => {
    const generation = ++recoveryGeneration.current;
    if (conflictRecovery) setRecoveryState("conflict_recovering");
    try {
      const canonical = await dohaApi.getWorkingComposition(projectId);
      const canonicalHistory = await dohaApi.getWorkingCompositionHistory(
        projectId,
        canonical.working_composition_id,
      );
      if (generation !== recoveryGeneration.current) return null;
      queryClient.setQueryData(queryKey, canonical);
      queryClient.setQueryData(
        ["working-composition-history", projectId, canonical.working_composition_id],
        canonicalHistory,
      );
      setSelectedClipId((current) => (
        current && canonical.clips.some((clip) => clip.clip_id === current) ? current : null
      ));
      return canonical;
    } finally {
      if (conflictRecovery && generation === recoveryGeneration.current) setRecoveryState("ready");
    }
  }, [projectId, queryClient, queryKey]);

  const fail = useCallback(async (cause: unknown, preserveHistory = false) => {
    void preserveHistory;
    const apiError = cause instanceof ApiError ? cause : null;
    if (apiError?.code === "WORKING_COMPOSITION_REVISION_CONFLICT"
      || apiError?.code === "SPLIT_STRUCTURE_CONFLICT"
      || apiError?.code === "NETWORK_ERROR"
      || apiError?.code === "REQUEST_TIMEOUT") {
      try {
        await reconcile(true);
        setMessage("다른 편집자의 변경을 감지해 서버의 최신 편집 상태와 Undo/Redo 기록을 불러왔습니다. 변경 내용을 확인한 뒤 다시 적용해 주세요.");
      } catch {
        // The original structured error remains the useful failure.
      }
    }
    setError(workingErrorMessage(cause));
  }, [reconcile]);

  async function mutate<T extends { completed_revision: number }>(
    operation: () => Promise<T>,
    _legacyHistoryMetadata?: (
      result: T,
      before: WorkingCompositionDto,
      canonical: WorkingCompositionDto,
    ) => unknown,
    _clearAfter = false,
    preserveHistoryOnFailure = false,
  ) {
    void _clearAfter;
    void preserveHistoryOnFailure;
    if (!data || pending) return false;
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      const result = await operation();
      queryClient.setQueryData<WorkingCompositionDto>(queryKey, { ...data, revision: result.completed_revision });
      await reconcile();
      return true;
    } catch (cause) {
      await fail(cause, preserveHistoryOnFailure);
      return false;
    } finally {
      setPending(false);
    }
  }

  async function initialize() {
    if (pending) return;
    setPending(true);
    setError(null);
    const key = newIdempotencyKey();
    try {
      const result = await retryIdempotent(() => dohaApi.initializeWorkingComposition(projectId, key));
      await reconcile();
      setMessage(`편집 공간을 시작했습니다. revision ${result.completed_revision}`);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "WORKING_COMPOSITION_ALREADY_EXISTS") {
        try { await reconcile(); } catch { /* retain product conflict */ }
      }
      await fail(cause);
    } finally {
      setPending(false);
    }
  }

  async function commitComposition() {
    if (!data || pending || data.clips.length === 0) return;
    const success = await mutate(
      () => withIdempotency((key) => dohaApi.commitWorkingComposition(
        projectId,
        data.revision,
        key,
      )),
      undefined,
      true,
      true,
    );
    if (!success) return;
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["project-composition", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["project-composition-snapshots", projectId] }),
    ]);
    setMessage("현재 편집 상태를 새 버전으로 저장했습니다. Undo/Redo 기록이 초기화되었습니다.");
  }

  async function copySelectedClip() {
    if (!data || !selectedClip || pending || !copyTargetTrackId || copyTimelineStart === "") return;
    let copiedClipId: string | null = null;
    const success = await mutate(
      () => withIdempotency((key) => dohaApi.copyWorkingClip(
        projectId,
        selectedClip.clip_id,
        {
          working_composition_id: data.working_composition_id,
          expected_revision: data.revision,
          target_track_id: copyTargetTrackId,
          target_timeline_start: copyTimelineStart,
        },
        key,
      )).then((result) => {
        copiedClipId = result.clip_id;
        return result;
      }),
      (result) => ({
        type: "CLIP_COPY",
        sourceClipId: selectedClip.clip_id,
        copiedClipId: result.clip_id,
        targetTrackId: copyTargetTrackId,
        targetTimelineStart: copyTimelineStart,
      }),
    );
    if (success && copiedClipId) {
      setSelectedClipId(copiedClipId);
      setMessage(`Clip을 ${copyTimelineStart}s 위치에 복사했습니다.`);
    }
  }

  async function runHistory(direction: "undo" | "redo") {
    if (!data || pending) return;
    if (direction === "undo" ? !history.data?.can_undo : !history.data?.can_redo) return;
    setPending(true);
    setError(null);
    try {
      const operation = direction === "undo"
        ? dohaApi.undoWorkingCompositionHistory
        : dohaApi.redoWorkingCompositionHistory;
      const result = await withIdempotency((key) => operation(
        projectId,
        { working_composition_id: data.working_composition_id, expected_revision: data.revision },
        key,
      ));
      queryClient.setQueryData<WorkingCompositionDto>(queryKey, { ...data, revision: result.completed_revision });
      await reconcile();
    } catch (cause) {
      await fail(cause);
    } finally {
      setPending(false);
    }
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isEditableTarget(event.target) || pending || (!event.ctrlKey && !event.metaKey)) return;
      const undo = event.key.toLowerCase() === "z" && !event.shiftKey;
      const redo = (event.key.toLowerCase() === "z" && event.shiftKey) || event.key.toLowerCase() === "y";
      if (!undo && !redo) return;
      event.preventDefault();
      void runHistory(undo ? "undo" : "redo");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  if (working.isPending) return <p role="status">WorkingComposition을 불러오는 중입니다.</p>;
  if (noWorkingComposition) {
    return (
      <section className="working-editor-empty" aria-labelledby="working-editor-title">
        <h4 id="working-editor-title">Clip 편집 공간</h4>
        <p>아직 WorkingComposition이 없습니다. 자동 생성하지 않으며, 아래 버튼으로 명시적으로 시작합니다.</p>
        {error && <ErrorAlert title="편집 공간을 시작하지 못했습니다." message={error} />}
        <Button type="button" disabled aria-label="Working Preview 만들기">Preview 만들기</Button>
        <Button type="button" disabled={pending} onClick={() => void initialize()}>
          {pending ? "시작 중…" : "WorkingComposition 시작"}
        </Button>
      </section>
    );
  }
  if (working.error || !data) {
    return <ErrorAlert title="WorkingComposition을 불러오지 못했습니다." message={workingErrorMessage(working.error)} />;
  }

  const selectedTrack = data.tracks.find((track) => track.track_id === selectedTrackId) ?? data.tracks[0];
  const selectedClip = data.clips.find((clip) => clip.clip_id === selectedClipId) ?? null;
  const selectedSplitAt = selectedClip
    ? Number(selectedClip.source_in) + playhead - Number(selectedClip.timeline_start)
    : 0;
  const base = { working_composition_id: data.working_composition_id, expected_revision: data.revision };

  return (
    <section className="working-editor" aria-labelledby="working-editor-title">
      <header className="working-editor-toolbar">
        <div>
          <p className="eyebrow">WORKING COMPOSITION</p>
          <h4 id="working-editor-title">Track / Clip Editor</h4>
          <span>revision {data.revision} · 저장됨 · Undo/Redo 기록은 서버에 안전하게 보존</span>
        </div>
        <div className="working-history-controls">
          <div className="working-zoom-controls" aria-label="Clip Timeline 확대 및 축소">
            <button
              type="button"
              aria-label="Clip Timeline 축소"
              disabled={pixelsPerSecond === MIN_PIXELS_PER_SECOND}
              onClick={() => setPixelsPerSecond((value) => Math.max(MIN_PIXELS_PER_SECOND, value - 16))}
            ><ZoomOut aria-hidden="true" /></button>
            <output aria-label="Clip Timeline 배율">{pixelsPerSecond}px/s</output>
            <button
              type="button"
              aria-label="Clip Timeline 확대"
              disabled={pixelsPerSecond === MAX_PIXELS_PER_SECOND}
              onClick={() => setPixelsPerSecond((value) => Math.min(MAX_PIXELS_PER_SECOND, value + 16))}
            ><ZoomIn aria-hidden="true" /></button>
          </div>
          <button type="button" aria-label="편집 실행 취소" disabled={pending || !history.data?.can_undo} onClick={() => void runHistory("undo")}><Undo2 aria-hidden="true" /> Undo</button>
          <button type="button" aria-label="편집 다시 실행" disabled={pending || !history.data?.can_redo} onClick={() => void runHistory("redo")}><Redo2 aria-hidden="true" /> Redo</button>
          <Button
            type="button"
            disabled={pending || data.clips.length === 0}
            onClick={() => void commitComposition()}
          >현재 편집 상태를 새 버전으로 저장</Button>
          <Button type="button" disabled={pending} onClick={() => void mutate(
            () => withIdempotency((key) => dohaApi.checkoutWorkingComposition(projectId, { ...base, composition_snapshot_id: snapshotId }, key)),
            undefined,
            true,
          )}>현재 Snapshot Checkout</Button>
        </div>
      </header>
      {recoveryState === "conflict_recovering" && (
        <p className="working-editor-notice" role="status">다른 편집자의 변경을 반영하는 중입니다.</p>
      )}
      {message && <p className="working-editor-notice" role="status">{message}</p>}
      {error && <ErrorAlert title="편집을 적용하지 못했습니다." message={error} />}
      {data.clips.length === 0 && (
        <p className="working-editor-notice">활성 Clip을 하나 이상 배치하면 새 버전으로 저장할 수 있습니다.</p>
      )}

      <WorkingPreviewControl
        projectId={projectId}
        workingCompositionId={data.working_composition_id}
        currentRevision={data.revision}
        clipCount={data.clips.length}
        onRevisionConflict={async () => {
          await reconcile();
          setMessage("최신 편집 상태를 불러왔습니다. Preview를 다시 실행해 주세요.");
        }}
      />

      <form className="working-add-track" onSubmit={(event) => {
        event.preventDefault();
        const name = trackName.trim();
        if (!name) return;
        void mutate(
          () => withIdempotency((key) => dohaApi.createWorkingTrack(projectId, { ...base, name }, key)),
          (result) => ({ type: "TRACK_CREATE", trackId: result.track_id, trackOrder: data.tracks.length }),
        ).then((success) => { if (success) setTrackName(""); });
      }}>
        <Input aria-label="새 Track 이름" value={trackName} disabled={pending} onChange={(event) => setTrackName(event.target.value)} />
        <Button type="submit" disabled={pending || !trackName.trim()}>Track 추가</Button>
      </form>

      <div className="working-track-list" role="list" aria-label="편집 Track 목록">
        {data.tracks.map((track) => (
          <TrackRow
            key={track.track_id}
            track={track}
            selected={track.track_id === selectedTrack?.track_id}
            pending={pending}
            onSelect={() => setSelectedTrackId(track.track_id)}
            onRename={(name) => void mutate(
              () => dohaApi.renameWorkingTrack(projectId, track.track_id, { ...base, name }),
              () => ({ type: "TRACK_RENAME", trackId: track.track_id, before: track.name, after: name }),
            )}
            onDelete={() => void mutate(
              () => withIdempotency((key) => dohaApi.deleteWorkingTrack(projectId, track.track_id, base, key)),
              () => ({ type: "TRACK_DELETE", trackId: track.track_id, trackOrder: track.track_order }),
            )}
            onDragStart={() => setDraggedTrackId(track.track_id)}
            onDrop={() => {
              if (!draggedTrackId || draggedTrackId === track.track_id) return;
              const before = data.tracks.map((item) => item.track_id);
              const after = moveBefore(before, draggedTrackId, track.track_id);
              setDraggedTrackId(null);
              void mutate(
                () => dohaApi.reorderWorkingTracks(projectId, { ...base, ordered_track_ids: after }),
                () => ({ type: "TRACK_REORDER", before, after }),
              );
            }}
          />
        ))}
      </div>

      {selectedTrack ? (
        <>
          <form className="working-add-clip" onSubmit={(event) => {
            event.preventDefault();
            void mutate(
              () => withIdempotency((key) => dohaApi.createWorkingClip(projectId, {
                ...base,
                track_id: selectedTrack.track_id,
                source_asset_version_id: sourceVersionId,
                timeline_start: timelineStart,
                source_in: sourceIn,
                source_out: sourceOut,
              }, key)),
              (result) => ({ type: "CLIP_CREATE", clipId: result.clip_id }),
            );
          }}>
            <label>exact AssetVersion<select aria-label="Clip source AssetVersion" value={sourceVersionId} disabled={pending} onChange={(event) => setSourceVersionId(event.target.value)}>{sources.map((item) => <option key={item.asset_version.asset_version_id} value={item.asset_version.asset_version_id}>{item.item_role} · v{item.asset_version.version_number}</option>)}</select></label>
            <label>Timeline start<Input aria-label="Clip timeline start" type="number" min="0" step="0.001" value={timelineStart} onChange={(event) => setTimelineStart(event.target.value)} /></label>
            <label>Source in<Input aria-label="Clip source in" type="number" min="0" step="0.001" value={sourceIn} onChange={(event) => setSourceIn(event.target.value)} /></label>
            <label>Source out<Input aria-label="Clip source out" type="number" min="0.001" step="0.001" value={sourceOut} onChange={(event) => setSourceOut(event.target.value)} /></label>
            <Button type="submit" disabled={pending || !sourceVersionId}>Clip 배치</Button>
          </form>
          <ClipLane
            projectId={projectId}
            workingCompositionId={data.working_composition_id}
            track={selectedTrack}
            clips={data.clips.filter((clip) => clip.track_id === selectedTrack.track_id)}
            selectedClipId={selectedClipId}
            pending={pending}
            pixelsPerSecond={pixelsPerSecond}
            playhead={playhead}
            mediaSourceResolver={mediaSourceResolver}
            waveformLoader={waveformLoader}
            onSelect={(clipId) => {
              const clip = data.clips.find((item) => item.clip_id === clipId);
              setSelectedClipId(clipId);
              setCopyTargetTrackId(clip?.track_id ?? "");
              setCopyTimelineStart("");
            }}
            onMove={(clip, next) => void mutate(
              () => dohaApi.moveWorkingClip(projectId, clip.clip_id, { ...base, timeline_start: next }),
              () => ({ type: "CLIP_MOVE", clipId: clip.clip_id, before: clip.timeline_start, after: next }),
            )}
            onTrimStart={(clip, nextTimeline, nextSource) => void mutate(
              () => dohaApi.trimWorkingClipStart(projectId, clip.clip_id, { ...base, timeline_start: nextTimeline, source_in: nextSource }),
              () => ({ type: "CLIP_TRIM_START", clipId: clip.clip_id, before: { timelineStart: clip.timeline_start, sourceIn: clip.source_in }, after: { timelineStart: nextTimeline, sourceIn: nextSource } }),
            )}
            onTrimEnd={(clip, next) => void mutate(
              () => dohaApi.trimWorkingClipEnd(projectId, clip.clip_id, { ...base, source_out: next }),
              () => ({ type: "CLIP_TRIM_END", clipId: clip.clip_id, before: clip.source_out, after: next }),
            )}
          />
        </>
      ) : <p className="working-editor-notice">Clip을 배치하려면 Track을 먼저 추가하세요.</p>}

      {selectedClip && (
        <div className="working-clip-controls" aria-label="선택 Clip 편집">
          <strong>선택 Clip {shortId(selectedClip.clip_id)}</strong>
          <ClipGainControl
            key={`${selectedClip.clip_id}:${selectedClip.gain_db}`}
            clipId={selectedClip.clip_id}
            canonicalGainDb={selectedClip.gain_db}
            pending={pending}
            onCommit={(nextGainDb) => mutate(
              () => withIdempotency((key) => dohaApi.updateWorkingClipGain(
                projectId,
                selectedClip.clip_id,
                { ...base, gain_db: Number(nextGainDb) },
                key,
              )),
              () => ({
                type: "CLIP_GAIN",
                clipId: selectedClip.clip_id,
                beforeGainDb: selectedClip.gain_db,
                afterGainDb: nextGainDb,
              }),
            )}
          />
          <ClipFadeControl
            key={`${selectedClip.clip_id}:${selectedClip.fade_in}:${selectedClip.fade_out}:${selectedClip.timeline_duration}`}
            clipId={selectedClip.clip_id}
            canonicalFadeIn={selectedClip.fade_in}
            canonicalFadeOut={selectedClip.fade_out}
            clipDuration={Number(selectedClip.timeline_duration)}
            pending={pending}
            onCommit={(nextFadeIn, nextFadeOut) => mutate(
              () => withIdempotency((key) => dohaApi.updateWorkingClipFade(
                projectId,
                selectedClip.clip_id,
                { ...base, fade_in: Number(nextFadeIn), fade_out: Number(nextFadeOut) },
                key,
              )),
              () => ({
                type: "CLIP_FADE",
                clipId: selectedClip.clip_id,
                before: { fadeIn: selectedClip.fade_in, fadeOut: selectedClip.fade_out },
                after: { fadeIn: nextFadeIn, fadeOut: nextFadeOut },
              }),
            )}
          />
          <ClipLoopControl
            key={`${selectedClip.clip_id}:${selectedClip.loop_enabled}:${selectedClip.timeline_duration}:${selectedClip.fade_in}:${selectedClip.fade_out}`}
            clipId={selectedClip.clip_id}
            enabled={selectedClip.loop_enabled}
            timelineDuration={selectedClip.timeline_duration}
            sourceWindow={microsecondsToSeconds(
              secondsToMicroseconds(selectedClip.source_out) - secondsToMicroseconds(selectedClip.source_in),
            )}
            fadeIn={selectedClip.fade_in}
            fadeOut={selectedClip.fade_out}
            pending={pending}
            onCommit={(enabled, timelineDuration) => mutate(
              () => withIdempotency((key) => dohaApi.updateWorkingClipLoop(
                projectId,
                selectedClip.clip_id,
                { ...base, loop_enabled: enabled, timeline_duration: Number(timelineDuration) },
                key,
              )),
              (_result, before, canonical) => {
                const beforeClip = before.clips.find((clip) => clip.clip_id === selectedClip.clip_id)!;
                const afterClip = canonical.clips.find((clip) => clip.clip_id === selectedClip.clip_id)!;
                return {
                  type: "CLIP_LOOP",
                  clipId: selectedClip.clip_id,
                  before: clipLoopState(beforeClip),
                  after: clipLoopState(afterClip),
                };
              },
            )}
          />
          <Button type="button" disabled={pending || !(selectedSplitAt > Number(selectedClip.source_in) && selectedSplitAt < Number(selectedClip.source_out))} onClick={() => void mutate(
            () => withIdempotency((key) => dohaApi.splitWorkingClip(projectId, selectedClip.clip_id, { ...base, split_at: seconds(selectedSplitAt) }, key)),
            (result) => ({ type: "CLIP_SPLIT", originalClipId: result.original_clip_id, leftClipId: result.left_clip_id, rightClipId: result.right_clip_id }),
          ).then((success) => { if (success) setSelectedClipId(null); })}><Scissors aria-hidden="true" /> Playhead에서 Split ({seconds(playhead)}s)</Button>
          <Button type="button" disabled={pending} onClick={() => void mutate(
            () => withIdempotency((key) => dohaApi.deleteWorkingClip(projectId, selectedClip.clip_id, base, key)),
            () => ({ type: "CLIP_DELETE", clipId: selectedClip.clip_id }),
          ).then((success) => { if (success) setSelectedClipId(null); })}><Trash2 aria-hidden="true" /> Clip 삭제</Button>
        </div>
      )}
      <div className="working-clip-controls" aria-label="Clip Copy destination">
        <strong>Clip Copy</strong>
        <p id="clip-copy-help">
          {!selectedClip
            ? "Copy할 Clip을 먼저 선택하세요."
            : copyTimelineStart === ""
              ? "대상 Track과 Timeline 위치를 명시하거나 현재 Playhead를 사용하세요."
              : `대상 위치 ${copyTimelineStart}s를 확인한 뒤 복사하세요.`}
        </p>
        <label>
          Copy 대상 Track
          <select
            aria-label="Copy 대상 Track"
            value={copyTargetTrackId}
            disabled={pending || !selectedClip}
            onChange={(event) => setCopyTargetTrackId(event.target.value)}
          >
            <option value="">Track 선택</option>
            {data.tracks.map((track) => (
              <option key={track.track_id} value={track.track_id}>{track.name}</option>
            ))}
          </select>
        </label>
        <label>
          Copy Timeline start
          <Input
            aria-label="Copy Timeline start"
            type="number"
            min="0"
            step="0.001"
            value={copyTimelineStart}
            disabled={pending || !selectedClip}
            onChange={(event) => setCopyTimelineStart(event.target.value)}
          />
        </label>
        <Button
          type="button"
          disabled={pending || !selectedClip}
          onClick={() => setCopyTimelineStart(seconds(playhead))}
        >현재 Playhead {seconds(playhead)}s 사용</Button>
        <Button
          type="button"
          aria-label="선택 Clip을 명시한 위치에 복사"
          aria-describedby="clip-copy-help"
          disabled={pending || !selectedClip || !copyTargetTrackId || copyTimelineStart === "" || Number(copyTimelineStart) < 0}
          onClick={() => void copySelectedClip()}
        ><Copy aria-hidden="true" /> Clip 복사</Button>
      </div>
    </section>
  );
}

function ClipGainControl({ clipId, canonicalGainDb, pending, onCommit }: {
  clipId: string;
  canonicalGainDb: string;
  pending: boolean;
  onCommit: (gainDb: string) => Promise<boolean>;
}) {
  const canonical = canonicalizeGainDb(canonicalGainDb);
  const [draft, setDraft] = useState(canonical ?? canonicalGainDb);
  const [localError, setLocalError] = useState<string | null>(null);
  const committing = useRef(false);

  if (!canonical) {
    return <p className="working-gain-error" role="alert">Clip Gain 값을 표시할 수 없습니다.</p>;
  }

  const commit = async (rawValue: string) => {
    if (pending || committing.current) return;
    const next = canonicalizeGainDb(rawValue);
    if (!next) {
      setDraft(canonical);
      setLocalError("Clip Gain은 -24.00 dB부터 +24.00 dB까지 0.01 dB 단위로 입력해 주세요.");
      return;
    }
    setDraft(next);
    setLocalError(null);
    if (next === canonical) return;
    committing.current = true;
    const success = await onCommit(next);
    committing.current = false;
    if (!success) setDraft(canonical);
  };

  return (
    <fieldset className="working-clip-gain" disabled={pending}>
      <legend>Clip gain</legend>
      <label htmlFor={`clip-gain-slider-${clipId}`}>Clip gain slider</label>
      <input
        id={`clip-gain-slider-${clipId}`}
        aria-label="Clip gain"
        type="range"
        min={MIN_CLIP_GAIN_DB}
        max={MAX_CLIP_GAIN_DB}
        step={CLIP_GAIN_STEP_DB}
        value={canonicalizeGainDb(draft) ?? canonical}
        onChange={(event) => {
          setDraft(event.target.value);
          setLocalError(null);
        }}
        onPointerUp={(event) => void commit(event.currentTarget.value)}
        onPointerCancel={() => {
          setDraft(canonical);
          setLocalError(null);
        }}
        onKeyUp={(event) => {
          if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) {
            void commit(event.currentTarget.value);
          }
        }}
      />
      <output htmlFor={`clip-gain-slider-${clipId} clip-gain-input-${clipId}`} aria-live="polite">
        {formatGainDb(canonicalizeGainDb(draft) ?? canonical)}
      </output>
      <label htmlFor={`clip-gain-input-${clipId}`}>Clip gain exact value</label>
      <Input
        id={`clip-gain-input-${clipId}`}
        aria-label="Clip gain exact value"
        type="number"
        inputMode="decimal"
        min={MIN_CLIP_GAIN_DB}
        max={MAX_CLIP_GAIN_DB}
        step={CLIP_GAIN_STEP_DB}
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          setLocalError(null);
        }}
        onBlur={(event) => void commit(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraft(canonical);
            setLocalError(null);
            event.currentTarget.blur();
          }
        }}
      />
      <Button
        type="button"
        aria-label="Clip gain을 0 dB로 재설정"
        disabled={pending || canonical === "0.00"}
        onClick={() => void commit("0.00")}
      >0 dB reset</Button>
      <small>-24.00 dB to +24.00 dB · 0.01 dB</small>
      {localError && <small className="field-error" role="alert">{localError}</small>}
    </fieldset>
  );
}

function ClipFadeControl({
  clipId,
  canonicalFadeIn,
  canonicalFadeOut,
  clipDuration,
  pending,
  onCommit,
}: {
  clipId: string;
  canonicalFadeIn: string;
  canonicalFadeOut: string;
  clipDuration: number;
  pending: boolean;
  onCommit: (fadeIn: string, fadeOut: string) => Promise<boolean>;
}) {
  const canonicalIn = canonicalizeFadeSeconds(canonicalFadeIn);
  const canonicalOut = canonicalizeFadeSeconds(canonicalFadeOut);
  const validDuration = Number.isFinite(clipDuration) && clipDuration > 0;
  const [draftIn, setDraftIn] = useState(canonicalIn ?? canonicalFadeIn);
  const [draftOut, setDraftOut] = useState(canonicalOut ?? canonicalFadeOut);
  const [localError, setLocalError] = useState<string | null>(null);
  const committing = useRef(false);
  const helpId = `clip-fade-help-${clipId}`;
  const errorId = `clip-fade-error-${clipId}`;

  if (!canonicalIn || !canonicalOut || !validDuration) {
    return <p className="working-fade-error" role="alert">Clip Fade 값을 표시할 수 없습니다.</p>;
  }

  const validate = (rawIn: string, rawOut: string) => {
    const fadeIn = canonicalizeFadeSeconds(rawIn);
    const fadeOut = canonicalizeFadeSeconds(rawOut);
    if (!fadeIn || !fadeOut) {
      return { error: "Fade는 0 이상의 초 단위 숫자로 소수점 이하 최대 6자리까지 입력해 주세요." } as const;
    }
    const clipDurationMicroseconds = Math.round(clipDuration * 1_000_000);
    if (fadeSecondsToMicroseconds(fadeIn) + fadeSecondsToMicroseconds(fadeOut) > clipDurationMicroseconds) {
      return { error: `Fade In과 Fade Out의 합은 Clip 길이 ${formatFadeSeconds(clipDuration)}초 이하여야 합니다.` } as const;
    }
    return { fadeIn, fadeOut } as const;
  };

  const commit = async (nextIn: string, nextOut: string) => {
    if (pending || committing.current) return;
    const validated = validate(nextIn, nextOut);
    if ("error" in validated && validated.error) {
      setLocalError(validated.error);
      return;
    }
    setDraftIn(validated.fadeIn);
    setDraftOut(validated.fadeOut);
    setLocalError(null);
    if (Number(validated.fadeIn) === Number(canonicalIn)
      && Number(validated.fadeOut) === Number(canonicalOut)) return;
    committing.current = true;
    const success = await onCommit(validated.fadeIn, validated.fadeOut);
    committing.current = false;
    if (!success) {
      setDraftIn(canonicalIn);
      setDraftOut(canonicalOut);
    }
  };

  const draftInValue = canonicalizeFadeSeconds(draftIn);
  const draftOutValue = canonicalizeFadeSeconds(draftOut);
  const describedBy = localError ? `${helpId} ${errorId}` : helpId;

  return (
    <fieldset className="working-clip-fade" disabled={pending} aria-describedby={describedBy}>
      <legend>Clip fade</legend>
      <label htmlFor={`clip-fade-in-${clipId}`}>Fade In</label>
      <Input
        id={`clip-fade-in-${clipId}`}
        aria-label="Fade In exact value"
        aria-invalid={Boolean(localError)}
        aria-describedby={describedBy}
        type="number"
        inputMode="decimal"
        min="0"
        max={Math.max(0, clipDuration - Number(draftOutValue ?? canonicalOut))}
        step={CLIP_FADE_STEP_SECONDS}
        value={draftIn}
        onChange={(event) => {
          setDraftIn(event.target.value);
          setLocalError(null);
        }}
        onBlur={(event) => void commit(event.currentTarget.value, draftOut)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraftIn(canonicalIn);
            setDraftOut(canonicalOut);
            setLocalError(null);
            event.currentTarget.blur();
          }
        }}
      />
      <output htmlFor={`clip-fade-in-${clipId}`} aria-live="polite">
        {draftInValue ?? draftIn} s
      </output>
      <label htmlFor={`clip-fade-out-${clipId}`}>Fade Out</label>
      <Input
        id={`clip-fade-out-${clipId}`}
        aria-label="Fade Out exact value"
        aria-invalid={Boolean(localError)}
        aria-describedby={describedBy}
        type="number"
        inputMode="decimal"
        min="0"
        max={Math.max(0, clipDuration - Number(draftInValue ?? canonicalIn))}
        step={CLIP_FADE_STEP_SECONDS}
        value={draftOut}
        onChange={(event) => {
          setDraftOut(event.target.value);
          setLocalError(null);
        }}
        onBlur={(event) => void commit(draftIn, event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
          if (event.key === "Escape") {
            setDraftIn(canonicalIn);
            setDraftOut(canonicalOut);
            setLocalError(null);
            event.currentTarget.blur();
          }
        }}
      />
      <output htmlFor={`clip-fade-out-${clipId}`} aria-live="polite">
        {draftOutValue ?? draftOut} s
      </output>
      <small id={helpId}>초 단위 · 각 값 최대 소수 6자리 · 두 Fade의 합은 Clip 길이 {formatFadeSeconds(clipDuration)}초 이하</small>
      {localError && <small id={errorId} className="field-error" role="alert">{localError}</small>}
    </fieldset>
  );
}

function ClipLoopControl({ clipId, enabled, timelineDuration, sourceWindow, fadeIn, fadeOut, pending, onCommit }: {
  clipId: string;
  enabled: boolean;
  timelineDuration: string;
  sourceWindow: string;
  fadeIn: string;
  fadeOut: string;
  pending: boolean;
  onCommit: (enabled: boolean, timelineDuration: string) => Promise<boolean>;
}) {
  const canonical = canonicalizePositiveSeconds(timelineDuration);
  const [draft, setDraft] = useState(canonical ?? timelineDuration);
  const [localError, setLocalError] = useState<string | null>(null);
  const committing = useRef(false);
  const helpId = `clip-loop-help-${clipId}`;
  const errorId = `clip-loop-error-${clipId}`;

  if (!canonical) return <p className="working-loop-error" role="alert">Clip Loop 값을 표시할 수 없습니다.</p>;

  const commitDuration = async (raw: string) => {
    if (!enabled || pending || committing.current) return;
    const next = canonicalizePositiveSeconds(raw);
    if (!next) {
      setLocalError("Timeline Duration은 0보다 큰 초 단위 숫자로 소수점 이하 최대 6자리까지 입력해 주세요.");
      return;
    }
    if (secondsToMicroseconds(fadeIn) + secondsToMicroseconds(fadeOut) > secondsToMicroseconds(next)) {
      setLocalError("Timeline Duration은 Fade In과 Fade Out의 합보다 짧을 수 없습니다.");
      return;
    }
    setDraft(next);
    setLocalError(null);
    if (secondsToMicroseconds(next) === secondsToMicroseconds(canonical)) return;
    committing.current = true;
    const success = await onCommit(true, next);
    committing.current = false;
    if (!success) setDraft(canonical);
  };

  return <fieldset className="working-clip-loop" disabled={pending} aria-describedby={localError ? `${helpId} ${errorId}` : helpId}>
    <legend>Clip loop</legend>
    <label htmlFor={`clip-loop-enabled-${clipId}`}>Loop</label>
    <input
      id={`clip-loop-enabled-${clipId}`}
      aria-label="Clip loop enabled"
      type="checkbox"
      role="switch"
      checked={enabled}
      onChange={(event) => void onCommit(event.currentTarget.checked, event.currentTarget.checked ? canonical : sourceWindow)}
    />
    <label htmlFor={`clip-loop-duration-${clipId}`}>Timeline Duration</label>
    <Input
      id={`clip-loop-duration-${clipId}`}
      aria-label="Clip loop timeline duration"
      aria-invalid={Boolean(localError)}
      type="number"
      inputMode="decimal"
      min="0.000001"
      step="0.000001"
      value={draft}
      disabled={pending || !enabled}
      onChange={(event) => { setDraft(event.target.value); setLocalError(null); }}
      onBlur={(event) => void commitDuration(event.currentTarget.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
        if (event.key === "Escape") {
          setDraft(canonical);
          setLocalError(null);
          event.currentTarget.blur();
        }
      }}
    />
    <output htmlFor={`clip-loop-duration-${clipId}`}>{draft} s</output>
    <small id={helpId}>Loop가 켜진 동안 source window보다 짧거나 같거나 긴 duration을 사용할 수 있습니다.</small>
    {localError && <small id={errorId} className="field-error" role="alert">{localError}</small>}
  </fieldset>;
}

function TrackRow({ track, selected, pending, onSelect, onRename, onDelete, onDragStart, onDrop }: {
  track: WorkingTrackDto; selected: boolean; pending: boolean; onSelect: () => void; onRename: (name: string) => void; onDelete: () => void; onDragStart: () => void; onDrop: () => void;
}) {
  return <article role="listitem" className={`working-track-row${selected ? " selected" : ""}`} draggable={!pending} onDragStart={onDragStart} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
    <button type="button" aria-pressed={selected} aria-label={`${track.name} Track 선택`} onClick={onSelect}>↕ {track.track_order + 1}</button>
    <Input key={track.name} aria-label={`${track.name} Track 이름`} defaultValue={track.name} disabled={pending} onBlur={(event) => { const next = event.currentTarget.value.trim(); if (next && next !== track.name) onRename(next); }} />
    <button type="button" aria-label={`${track.name} Track 삭제`} disabled={pending} onClick={onDelete}><Trash2 aria-hidden="true" /></button>
  </article>;
}

function ClipLane({ projectId, workingCompositionId, track, clips, selectedClipId, pending, pixelsPerSecond, playhead, mediaSourceResolver, waveformLoader, onSelect, onMove, onTrimStart, onTrimEnd }: {
  projectId: string; workingCompositionId: string; track: WorkingTrackDto; clips: WorkingClipDto[]; selectedClipId: string | null; pending: boolean;
  pixelsPerSecond: number; playhead: number; mediaSourceResolver?: MediaSourceResolver; waveformLoader: WaveformLoader;
  onSelect: (id: string) => void; onMove: (clip: WorkingClipDto, value: string) => void;
  onTrimStart: (clip: WorkingClipDto, timeline: string, source: string) => void; onTrimEnd: (clip: WorkingClipDto, source: string) => void;
}) {
  const waveformFor = useWorkingWaveforms({
    projectId,
    workingCompositionId,
    assetVersionIds: clips.map((clip) => clip.source_asset_version_id),
    resolver: mediaSourceResolver,
    loader: waveformLoader,
  });
  const [preview, setPreview] = useState<{ clipId: string; mode: "move" | "start" | "end"; delta: number } | null>(null);
  const drag = useRef<{ pointerId: number; startX: number; clip: WorkingClipDto; mode: "move" | "start" | "end" } | null>(null);
  const finish = (event: ReactPointerEvent<HTMLElement>, cancelled = false) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    const delta = (event.clientX - active.startX) / pixelsPerSecond;
    setPreview(null);
    if (cancelled || Math.abs(delta) < 0.001) return;
    if (active.mode === "move") onMove(active.clip, seconds(Math.max(0, Number(active.clip.timeline_start) + delta)));
    if (active.mode === "start") {
      const bounded = Math.max(
        -Math.min(Number(active.clip.timeline_start), Number(active.clip.source_in)),
        Math.min(delta, Number(active.clip.source_out) - Number(active.clip.source_in) - 0.001),
      );
      onTrimStart(active.clip, seconds(Math.max(0, Number(active.clip.timeline_start) + bounded)), seconds(Number(active.clip.source_in) + bounded));
    }
    if (active.mode === "end") {
      const bounded = Math.max(
        Number(active.clip.source_in) - Number(active.clip.source_out) + 0.001,
        Math.min(delta, Number(active.clip.source_duration) - Number(active.clip.source_out)),
      );
      onTrimEnd(active.clip, seconds(Number(active.clip.source_out) + bounded));
    }
  };
  const start = (event: ReactPointerEvent<HTMLElement>, clip: WorkingClipDto, mode: "move" | "start" | "end") => {
    if (pending) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    drag.current = { pointerId: event.pointerId, startX: event.clientX, clip, mode };
    setPreview({ clipId: clip.clip_id, mode, delta: 0 });
  };
  const move = (event: ReactPointerEvent<HTMLElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    setPreview({ clipId: active.clip.clip_id, mode: active.mode, delta: (event.clientX - active.startX) / pixelsPerSecond });
  };
  const width = Math.max(720, ...clips.map((clip) => (Number(clip.timeline_start) + Number(clip.timeline_duration)) * pixelsPerSecond + 80));
  return <div className="working-clip-scroll" data-testid="working-clip-scroll"><div className="working-clip-lane" style={{ width, backgroundSize: `${pixelsPerSecond}px 100%` }} aria-label={`${track.name} Clip lane`}>
    {clips.map((clip) => {
      const delta = preview?.clipId === clip.clip_id ? preview.delta : 0;
      const startDelta = preview?.mode === "start" ? delta : 0;
      const endDelta = preview?.mode === "end" ? delta : 0;
      const left = (Number(clip.timeline_start) + (preview?.mode === "move" ? delta : startDelta)) * pixelsPerSecond;
      const duration = Number(clip.timeline_duration) - startDelta + endDelta;
      const sourceIn = Number(clip.source_in) + startDelta;
      const sourceOut = Number(clip.source_out) + endDelta;
      const waveform = waveformFor(clip.source_asset_version_id);
      const projection = waveform.status === "ready"
        ? projectWaveformWindow(
          waveform.waveform,
          sourceIn,
          sourceOut,
          undefined,
          Number(clip.source_duration),
        )
        : [];
      return <button key={clip.clip_id} type="button" className={`working-clip${selectedClipId === clip.clip_id ? " selected" : ""}`} style={{ left, width: Math.max(duration * pixelsPerSecond, 24) }} aria-label={`Clip ${shortId(clip.clip_id)} 선택 및 이동`} aria-pressed={selectedClipId === clip.clip_id} onClick={() => onSelect(clip.clip_id)} onPointerDown={(event) => start(event, clip, "move")} onPointerMove={move} onPointerUp={finish} onPointerCancel={(event) => finish(event, true)}>
        <span className="working-trim-handle start" aria-label={`Clip ${shortId(clip.clip_id)} 시작 Trim`} onPointerDown={(event) => start(event, clip, "start")} />
        <span
          className={`working-clip-waveform ${waveform.status}`}
          aria-hidden="true"
          data-testid={`clip-waveform-${clip.clip_id}`}
          data-waveform-status={waveform.status}
          data-source-window={`${seconds(sourceIn)}:${seconds(sourceOut)}`}
          data-waveform-signature={projection.length ? waveformProjectionSignature(projection) : undefined}
        >
          {waveform.status === "loading" && <span>loading</span>}
          {waveform.status === "unavailable" && <span>unavailable</span>}
          {waveform.status === "ready" && projection.length > 0 && (
            <svg viewBox="0 0 1000 64" preserveAspectRatio="none">
              <path d={buildWaveformPath(projection, 1000, 64)} />
            </svg>
          )}
        </span>
        <strong>{shortId(clip.clip_id)}</strong><small>{seconds(duration)}s</small>
        <span className="working-trim-handle end" aria-label={`Clip ${shortId(clip.clip_id)} 끝 Trim`} onPointerDown={(event) => start(event, clip, "end")} />
      </button>;
    })}
    <span
      className="working-clip-playhead"
      aria-hidden="true"
      style={{ left: Math.max(0, playhead) * pixelsPerSecond }}
    />
  </div></div>;
}

async function retryIdempotent<T>(operation: () => Promise<T>): Promise<T> {
  try { return await operation(); } catch (error) {
    if (error instanceof ApiError && ["NETWORK_ERROR", "REQUEST_TIMEOUT"].includes(error.code)) return operation();
    throw error;
  }
}

function withIdempotency<T>(operation: (key: string) => Promise<T>): Promise<T> {
  const key = newIdempotencyKey();
  return retryIdempotent(() => operation(key));
}

function workingErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const messages: Record<string, string> = {
      WORKING_COMPOSITION_ALREADY_EXISTS: "이미 편집 공간이 있습니다. 서버 상태를 다시 불러왔습니다.",
      WORKING_COMPOSITION_REVISION_CONFLICT: "다른 변경으로 revision이 달라졌습니다.",
      WORKING_COMPOSITION_EMPTY: "활성 Clip을 하나 이상 배치한 뒤 새 버전으로 저장해 주세요.",
      TRACK_NOT_EMPTY: "Clip이 있는 Track은 삭제할 수 없습니다.",
      TRACK_NOT_FOUND: "선택한 Track을 찾을 수 없습니다. 최신 편집 상태를 다시 확인해 주세요.",
      CLIP_NOT_FOUND: "선택한 Clip을 찾을 수 없습니다. 최신 편집 상태를 다시 확인해 주세요.",
      CLIP_OVERLAP: "같은 Track의 다른 Clip과 겹칩니다.",
      INVALID_CLIP_RANGE: "Clip 시간 범위를 확인해 주세요.",
      SOURCE_ASSET_UNAVAILABLE: "선택한 source AssetVersion을 현재 사용할 수 없습니다.",
      SOURCE_ARTIFACT_AMBIGUOUS: "사용 가능한 source Artifact를 하나로 확정할 수 없습니다.",
      SOURCE_DURATION_UNAVAILABLE: "신뢰할 수 있는 source 길이가 없습니다.",
      SPLIT_STRUCTURE_CONFLICT: "Split 이후 구조가 달라져 Undo/Redo할 수 없습니다.",
      CLIP_GAIN_OUT_OF_RANGE: "Clip Gain은 -24.00 dB부터 +24.00 dB까지 0.01 dB 단위로 설정해 주세요.",
      CLIP_FADE_OUT_OF_RANGE: "Fade In과 Fade Out의 합이 Clip 길이를 넘지 않도록 설정해 주세요.",
      CLIP_LOOP_GEOMETRY_INVALID: "Loop와 Timeline Duration 값을 확인해 주세요.",
      INVALID_INPUT: "Fade 또는 Clip 입력값을 확인해 주세요.",
      IDEMPOTENCY_KEY_REUSED: "동일 요청 키가 다른 편집에 사용되었습니다.",
      IDEMPOTENCY_IN_PROGRESS: "같은 편집 요청이 아직 처리 중입니다.",
    };
    return messages[error.code] ?? userErrorMessage(error);
  }
  return userErrorMessage(error);
}

function seconds(value: number): string { return Math.max(0, value).toFixed(3); }
function canonicalizeGainDb(value: string): string | null {
  const trimmed = value.trim();
  if (!/^[+-]?(?:\d+|\d*\.\d{1,2})$/.test(trimmed)) return null;
  const number = Number(trimmed);
  if (!Number.isFinite(number) || number < MIN_CLIP_GAIN_DB || number > MAX_CLIP_GAIN_DB) return null;
  return number.toFixed(2);
}
function formatGainDb(value: string): string {
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)} dB`;
}
function canonicalizeFadeSeconds(value: string): string | null {
  const trimmed = value.trim();
  if (!/^(?:\d+|\d*\.\d{1,6})$/.test(trimmed)) return null;
  const number = Number(trimmed);
  if (!Number.isFinite(number) || number < 0) return null;
  return trimmed.startsWith(".") ? `0${trimmed}` : trimmed;
}
function canonicalizePositiveSeconds(value: string): string | null {
  const canonical = canonicalizeFadeSeconds(value);
  return canonical && secondsToMicroseconds(canonical) > 0 ? canonical : null;
}
function formatFadeSeconds(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(6).replace(/0+$/, "").replace(/\.$/, "");
}
function fadeSecondsToMicroseconds(value: string): number {
  const [whole, fraction = ""] = value.split(".");
  return Number(whole) * 1_000_000 + Number(fraction.padEnd(6, "0"));
}
function secondsToMicroseconds(value: string): number { return fadeSecondsToMicroseconds(value); }
function microsecondsToSeconds(value: number): string {
  const whole = Math.floor(value / 1_000_000);
  const fraction = String(value % 1_000_000).padStart(6, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : String(whole);
}
function clipLoopState(clip: WorkingClipDto) {
  return { enabled: clip.loop_enabled, timelineDuration: clip.timeline_duration, phase: clip.loop_phase };
}
function shortId(value: string): string { return value.slice(0, 8); }
function moveBefore(items: string[], source: string, target: string): string[] {
  const next = items.filter((item) => item !== source);
  next.splice(next.indexOf(target), 0, source);
  return next;
}
function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));
}

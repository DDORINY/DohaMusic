import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WorkingCompositionEditor } from "@/features/composition/working-composition-editor";
import type { MediaSourceResolver } from "@/features/composition/working-waveform";
import type { WaveformLoader } from "@/features/composition/waveform";
import { ApiError } from "@/services/api-client";
import { dohaApi } from "@/services/doha-api";
import type { CompositionReadItemDto, WorkingCompositionDto } from "@/types/api";

afterEach(() => vi.restoreAllMocks());
beforeEach(() => {
  vi.spyOn(dohaApi, "getWorkingCompositionHistory").mockResolvedValue({
    ...historyBoth(),
  });
});

describe("WorkingComposition editor", () => {
  it("Clip waveform loading, ready와 same-source decode dedup을 표시한다", async () => {
    const clips = [
      { ...working.clips[0], clip_id: "clip-left", source_out: "5.000", source_duration: "10.000" },
      { ...working.clips[0], clip_id: "clip-right", timeline_start: "5.000", source_in: "5.000", source_duration: "10.000" },
    ];
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue({ ...working, clips });
    let finish!: (peaks: number[]) => void;
    const loader = vi.fn<WaveformLoader>(() => new Promise((resolve) => { finish = resolve; }));
    const resolver = vi.fn<MediaSourceResolver>().mockResolvedValue(mediaSource);
    renderEditor({ resolver, loader });
    expect((await screen.findByTestId("clip-waveform-clip-left")).dataset.waveformStatus).toBe("loading");
    finish([0.1, 0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 1]);
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-left")).toHaveAttribute("data-waveform-status", "ready"));
    expect(screen.getByTestId("clip-waveform-clip-left").dataset.waveformSignature)
      .not.toBe(screen.getByTestId("clip-waveform-clip-right").dataset.waveformSignature);
    expect(resolver).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("move는 source projection을 유지하고 trim preview와 zoom은 projection/geometry만 갱신한다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const loader = vi.fn<WaveformLoader>().mockResolvedValue([0.1, 0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 1]);
    renderEditor({ loader });
    const waveform = await screen.findByTestId("clip-waveform-clip-1");
    await waitFor(() => expect(waveform).toHaveAttribute("data-waveform-status", "ready"));
    const clip = screen.getByRole("button", { name: /Clip clip-1 선택 및 이동/ });
    const initialSignature = waveform.dataset.waveformSignature;
    const initialLeft = clip.style.left;
    fireEvent.pointerDown(clip, { pointerId: 11, clientX: 100 });
    fireEvent.pointerMove(clip, { pointerId: 11, clientX: 164 });
    expect(clip.style.left).not.toBe(initialLeft);
    expect(waveform.dataset.waveformSignature).toBe(initialSignature);
    fireEvent.pointerCancel(clip, { pointerId: 11, clientX: 164 });

    const start = screen.getByLabelText("Clip clip-1 시작 Trim");
    fireEvent.pointerDown(start, { pointerId: 12, clientX: 100 });
    fireEvent.pointerMove(clip, { pointerId: 12, clientX: 164 });
    expect(waveform.dataset.sourceWindow).toBe("1.000:10.000");
    expect(waveform.dataset.waveformSignature).not.toBe(initialSignature);
    fireEvent.pointerCancel(clip, { pointerId: 12, clientX: 164 });

    const widthBeforeZoom = clip.style.width;
    fireEvent.click(screen.getByRole("button", { name: "Clip Timeline 확대" }));
    expect(clip.style.width).not.toBe(widthBeforeZoom);
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("split child projection과 unsplit/delete/restore 원본 projection을 derived state로 복원한다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const loader = vi.fn<WaveformLoader>().mockResolvedValue([0.1, 0.2, 0.3, 0.4, 0.7, 0.8, 0.9, 1]);
    const { client } = renderEditor({ loader });
    const original = await screen.findByTestId("clip-waveform-clip-1");
    await waitFor(() => expect(original).toHaveAttribute("data-waveform-status", "ready"));
    const originalSignature = original.dataset.waveformSignature;
    const split = {
      ...working,
      revision: 3,
      clips: [
        { ...working.clips[0], clip_id: "clip-left", source_out: "5.000", split_from_clip_id: "clip-1" },
        { ...working.clips[0], clip_id: "clip-right", timeline_start: "5.000", source_in: "5.000", split_from_clip_id: "clip-1" },
      ],
    };
    act(() => client.setQueryData(["working-composition", "project-1"], split));
    const left = await screen.findByTestId("clip-waveform-clip-left");
    const right = await screen.findByTestId("clip-waveform-clip-right");
    expect(left.dataset.waveformSignature).not.toBe(right.dataset.waveformSignature);
    expect(screen.queryByTestId("clip-waveform-clip-1")).not.toBeInTheDocument();

    act(() => client.setQueryData(["working-composition", "project-1"], { ...working, revision: 4 }));
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-1").dataset.waveformSignature).toBe(originalSignature));
    act(() => client.setQueryData(["working-composition", "project-1"], { ...working, revision: 5, clips: [] }));
    await waitFor(() => expect(screen.queryByTestId("clip-waveform-clip-1")).not.toBeInTheDocument());
    act(() => client.setQueryData(["working-composition", "project-1"], { ...working, revision: 6 }));
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-1").dataset.waveformSignature).toBe(originalSignature));
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("resolver/decode failure를 unavailable로 격리해 Clip 편집 controls를 유지한다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    renderEditor({ loader: vi.fn<WaveformLoader>().mockRejectedValue(new Error("secret decode")) });
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-1")).toHaveAttribute("data-waveform-status", "unavailable"));
    expect(screen.getByRole("button", { name: /Clip clip-1 선택 및 이동/ })).toBeEnabled();
    expect(screen.queryByText("secret decode")).not.toBeInTheDocument();
  });

  it("GET no-data에서 자동 생성하지 않고 explicit initialize 후 revision을 reconcile한다", async () => {
    const get = vi.spyOn(dohaApi, "getWorkingComposition")
      .mockRejectedValueOnce(new ApiError(404, "WORKING_COMPOSITION_NOT_FOUND", "missing"))
      .mockResolvedValueOnce(emptyWorking);
    const initialize = vi.spyOn(dohaApi, "initializeWorkingComposition")
      .mockResolvedValue({ working_composition_id: "working-1", completed_revision: 0, replayed: false });
    const user = userEvent.setup();
    renderEditor();
    expect(await screen.findByText(/아직 WorkingComposition이 없습니다/)).toBeVisible();
    expect(initialize).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "WorkingComposition 시작" }));
    expect(await screen.findByText("편집 공간을 시작했습니다. revision 0")).toBeVisible();
    expect(get).toHaveBeenCalledTimes(2);
    expect(initialize.mock.calls[0][1]).toMatch(/[0-9a-f-]{36}/);
  });

  it("initialize response loss retry는 동일 Idempotency-Key를 재사용한다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockRejectedValueOnce(new ApiError(404, "WORKING_COMPOSITION_NOT_FOUND", "missing"))
      .mockResolvedValueOnce(emptyWorking);
    const initialize = vi.spyOn(dohaApi, "initializeWorkingComposition")
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce({ working_composition_id: "working-1", completed_revision: 0, replayed: true });
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: "WorkingComposition 시작" }));
    await screen.findByText("편집 공간을 시작했습니다. revision 0");
    expect(initialize).toHaveBeenCalledTimes(2);
    expect(initialize.mock.calls[0][1]).toBe(initialize.mock.calls[1][1]);
  });

  it("여러 move pointermove는 preview만 하고 pointerup에서 mutation 한 번 호출한다", async () => {
    const afterMove = { ...working, revision: 3, clips: [{ ...working.clips[0], timeline_start: "2.000" }] };
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(working).mockResolvedValueOnce(afterMove);
    const move = vi.spyOn(dohaApi, "moveWorkingClip").mockResolvedValue({ clip_id: "clip-1", completed_revision: 3, replayed: false });
    renderEditor();
    const clip = await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ });
    fireEvent.pointerDown(clip, { pointerId: 1, clientX: 100 });
    fireEvent.pointerMove(clip, { pointerId: 1, clientX: 164 });
    fireEvent.pointerMove(clip, { pointerId: 1, clientX: 228 });
    expect(move).not.toHaveBeenCalled();
    fireEvent.pointerUp(clip, { pointerId: 1, clientX: 228 });
    await waitFor(() => expect(move).toHaveBeenCalledTimes(1));
    expect(move.mock.calls[0][2]).toMatchObject({ expected_revision: 2, timeline_start: "2.000" });
  });

  it("pointercancel은 Clip move mutation을 만들지 않는다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const move = vi.spyOn(dohaApi, "moveWorkingClip");
    renderEditor();
    const clip = await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ });
    fireEvent.pointerDown(clip, { pointerId: 2, clientX: 100 });
    fireEvent.pointerMove(clip, { pointerId: 2, clientX: 164 });
    fireEvent.pointerCancel(clip, { pointerId: 2, clientX: 164 });
    expect(move).not.toHaveBeenCalled();
  });

  it("Clip Copy는 선택과 명시적 목적지가 모두 있어야 활성화되고 새 ID를 history에 기록한다", async () => {
    const copied = {
      ...working,
      revision: 3,
      clips: [
        ...working.clips,
        { ...working.clips[0], clip_id: "clip-copy", timeline_start: "10.000" },
      ],
      timeline_duration: "20.000",
    };
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(copied)
      .mockResolvedValueOnce({ ...copied, revision: 4, clips: working.clips });
    const copy = vi.spyOn(dohaApi, "copyWorkingClip").mockResolvedValue({
      clip_id: "clip-copy", completed_revision: 3, replayed: false,
    });
    vi.mocked(dohaApi.getWorkingCompositionHistory)
      .mockResolvedValueOnce(historyBoth())
      .mockResolvedValueOnce(historyBarrier(3));
    const loader = vi.fn<WaveformLoader>().mockResolvedValue([0.1, 0.4, 0.8, 0.2]);
    const user = userEvent.setup();
    renderEditor({ loader });
    const copyButton = await screen.findByRole("button", { name: "선택 Clip을 명시한 위치에 복사" });
    expect(copyButton).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Clip clip-1/ }));
    expect(screen.getByLabelText("Copy 대상 Track")).toHaveValue("track-1");
    expect(copyButton).toBeDisabled();
    await user.type(screen.getByLabelText("Copy Timeline start"), "10");
    expect(copyButton).toBeEnabled();
    await user.click(copyButton);
    await screen.findByText("Clip을 10s 위치에 복사했습니다.");
    expect(copy).toHaveBeenCalledWith(
      "project-1",
      "clip-1",
      {
        working_composition_id: "working-1",
        expected_revision: 2,
        target_track_id: "track-1",
        target_timeline_start: "10",
      },
      expect.stringMatching(/[0-9a-f-]{36}/),
    );
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-copy"))
      .toHaveAttribute("data-waveform-status", "ready"));
    expect(loader).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  });

  it("Clip Copy response-loss retry는 같은 key를 재사용하고 revision conflict는 history를 비운다", async () => {
    const copied = {
      ...working,
      revision: 3,
      clips: [...working.clips, { ...working.clips[0], clip_id: "clip-copy", timeline_start: "10.000" }],
    };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBarrier(3)).mockResolvedValueOnce(historyBarrier(8));
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(copied)
      .mockResolvedValueOnce({ ...copied, revision: 8 });
    const copy = vi.spyOn(dohaApi, "copyWorkingClip")
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce({ clip_id: "clip-copy", completed_revision: 3, replayed: true })
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale"));
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1/ }));
    await user.type(screen.getByLabelText("Copy Timeline start"), "10");
    await user.click(screen.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }));
    await screen.findByText("Clip을 10s 위치에 복사했습니다.");
    expect(copy).toHaveBeenCalledTimes(2);
    expect(copy.mock.calls[0][3]).toBe(copy.mock.calls[1][3]);
    await user.clear(screen.getByLabelText("Copy Timeline start"));
    await user.type(screen.getByLabelText("Copy Timeline start"), "20");
    await user.click(screen.getByRole("button", { name: "선택 Clip을 명시한 위치에 복사" }));
    await waitFor(() => expect(screen.getByText(/revision 8/)).toBeVisible());
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  });

  it("Clip Gain drag는 local value만 preview하고 pointerup 한 번으로 PATCH와 Preview stale을 만든다", async () => {
    const afterGain = { ...working, revision: 3, clips: [{ ...working.clips[0], gain_db: "3.00" }] };
    const afterUndo = { ...working, revision: 4 };
    const afterRedo = { ...working, revision: 5, clips: [{ ...working.clips[0], gain_db: "3.00" }] };
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(afterGain)
      .mockResolvedValueOnce(afterUndo)
      .mockResolvedValueOnce(afterRedo);
    const update = vi.spyOn(dohaApi, "updateWorkingClipGain")
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 3, replayed: true })
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 4, replayed: false })
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 5, replayed: false });
    const undoGain = vi.spyOn(dohaApi, "undoWorkingCompositionHistory")
      .mockResolvedValue({ clip_id: "clip-1", completed_revision: 4, replayed: false });
    const redoGain = vi.spyOn(dohaApi, "redoWorkingCompositionHistory")
      .mockResolvedValue({ clip_id: "clip-1", completed_revision: 5, replayed: false });
    vi.spyOn(dohaApi, "createWorkingPreview").mockResolvedValue({
      job_id: "job-preview", preview_render_id: "render-preview",
      working_composition_id: "working-1", rendered_revision: 2,
      status: "queued", replayed: false,
    });
    vi.spyOn(dohaApi, "getWorkspaceJob").mockResolvedValue({
      job_id: "job-preview", job_type: "working_preview_render", status: "succeeded",
      project_id: "project-1", composition_snapshot_id: null,
      provider_id: null, model_manifest_id: null, progress_percent: 100,
      stage: null, retry_of_job_id: null, inputs: [],
      outputs: [{ output_role: "working_preview", output_order: 0, artifact_id: "artifact-preview", asset_version_id: "version-preview" }],
      model_usages: [], error_code: null, error_message: null, error_retryable: null, error_details_id: null,
      created_at: "2026-08-29T00:00:00Z", started_at: null, completed_at: "2026-08-29T00:00:01Z",
    });
    const resolver = vi.fn<MediaSourceResolver>().mockResolvedValue(mediaSource);
    const loader = vi.fn<WaveformLoader>().mockResolvedValue([0.2, 0.8]);
    const user = userEvent.setup();
    renderEditor({ resolver, loader });
    await user.click(await screen.findByRole("button", { name: "Working Preview 만들기" }));
    await screen.findByRole("button", { name: "Working Preview revision 2 재생" });
    await user.click(screen.getByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const slider = screen.getByRole("slider", { name: "Clip gain" });
    expect(slider).toHaveAttribute("min", "-24");
    expect(slider).toHaveAttribute("max", "24");
    expect(slider).toHaveAttribute("step", "0.01");
    expect(screen.getByText("0.00 dB")).toBeVisible();
    fireEvent.change(slider, { target: { value: "2.00" } });
    fireEvent.pointerCancel(slider, { pointerId: 1 });
    expect(screen.getByText("0.00 dB")).toBeVisible();
    expect(update).not.toHaveBeenCalled();
    fireEvent.change(slider, { target: { value: "1.00" } });
    fireEvent.change(slider, { target: { value: "2.00" } });
    fireEvent.change(slider, { target: { value: "3.00" } });
    expect(screen.getByText("+3.00 dB")).toBeVisible();
    expect(update).not.toHaveBeenCalled();
    fireEvent.pointerUp(slider, { pointerId: 1 });
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[0][2]).toMatchObject({ expected_revision: 2, gain_db: 3 });
    expect(update.mock.calls[0][3]).toBe(update.mock.calls[1][3]);
    expect(screen.getByText("Preview가 최신 편집본과 다릅니다.")).toBeVisible();
    expect(vi.mocked(dohaApi.createWorkingPreview)).toHaveBeenCalledTimes(1);
    expect(resolver).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "편집 실행 취소" }));
    await waitFor(() => expect(undoGain).toHaveBeenCalledWith(
      "project-1", { working_composition_id: "working-1", expected_revision: 3 }, expect.any(String),
    ));
    expect(update).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "편집 다시 실행" }));
    await waitFor(() => expect(redoGain).toHaveBeenCalledWith(
      "project-1", { working_composition_id: "working-1", expected_revision: 4 }, expect.any(String),
    ));
    expect(update).toHaveBeenCalledTimes(2);
  });

  it("pending Clip A Gain response는 선택된 Clip B control에 leak하지 않는다", async () => {
    const twoClips = {
      ...working,
      clips: [
        { ...working.clips[0], gain_db: "3.00" },
        { ...working.clips[0], clip_id: "clip-2", timeline_start: "12.000", gain_db: "-4.00" },
      ],
      timeline_duration: "22.000",
    };
    const after = {
      ...twoClips,
      revision: 3,
      clips: [{ ...twoClips.clips[0], gain_db: "5.00" }, twoClips.clips[1]],
    };
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(twoClips).mockResolvedValueOnce(after);
    let finish!: (value: { clip_id: string; completed_revision: number; replayed: boolean }) => void;
    vi.spyOn(dohaApi, "updateWorkingClipGain").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const input = screen.getByLabelText("Clip gain exact value");
    await user.clear(input);
    await user.type(input, "5");
    fireEvent.blur(input);
    await user.click(screen.getByRole("button", { name: /Clip clip-2 선택 및 이동/ }));
    expect(screen.getByLabelText("Clip gain exact value")).toHaveValue(-4);
    finish({ clip_id: "clip-1", completed_revision: 3, replayed: false });
    await waitFor(() => expect(screen.getByText(/revision 3/)).toBeVisible());
    expect(screen.getByLabelText("Clip gain exact value")).toHaveValue(-4);
  });

  it("Clip Gain exact input/reset은 bounds를 지키고 backend failure에서 canonical 값으로 돌아간다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const update = vi.spyOn(dohaApi, "updateWorkingClipGain")
      .mockRejectedValue(new ApiError(422, "CLIP_GAIN_OUT_OF_RANGE", "raw backend detail"));
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const input = screen.getByLabelText("Clip gain exact value");
    expect(input).toHaveValue(0);
    expect(screen.getByRole("button", { name: "Clip gain을 0 dB로 재설정" })).toBeDisabled();

    await user.clear(input);
    await user.type(input, "24.001");
    fireEvent.blur(input);
    expect(await screen.findByText(/0.01 dB 단위로 입력/)).toBeVisible();
    expect(update).not.toHaveBeenCalled();
    expect(input).toHaveValue(0);

    await user.clear(input);
    await user.type(input, "-3.25");
    fireEvent.blur(input);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/-24.00 dB부터 \+24.00 dB/)).toBeVisible();
    expect(screen.queryByText("raw backend detail")).not.toBeInTheDocument();
    expect(input).toHaveValue(0);
  });

  it("Clip Gain revision conflict는 GET reconcile하고 selected Clip canonical 값과 history를 갱신한다", async () => {
    const changed = { ...working, revision: 3, clips: [{ ...working.clips[0], gain_db: "3.00" }] };
    const external = { ...working, revision: 8, clips: [{ ...working.clips[0], gain_db: "-2.00" }] };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBoth(3)).mockResolvedValueOnce(historyBarrier(8));
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(changed)
      .mockResolvedValueOnce(external);
    vi.spyOn(dohaApi, "updateWorkingClipGain")
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 3, replayed: false })
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale"));
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const input = screen.getByLabelText("Clip gain exact value");
    await user.clear(input);
    await user.type(input, "3");
    fireEvent.blur(input);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "편집 실행 취소" }));
    expect(await screen.findByText(/최신 편집 상태와 Undo\/Redo 기록/)).toBeVisible();
    expect(screen.getByText("-2.00 dB")).toBeVisible();
    expect(screen.getByLabelText("Clip gain exact value")).toHaveValue(-2);
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  });

  it("Clip Fade exact input은 6자리 precision과 invariant를 지키고 response-loss/Undo/Redo를 absolute pair로 처리한다", async () => {
    const afterFade = { ...working, revision: 3, clips: [{ ...working.clips[0], fade_in: "0.123456", fade_out: "0.75" }] };
    const afterUndo = { ...working, revision: 4 };
    const afterRedo = { ...afterFade, revision: 5 };
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(afterFade)
      .mockResolvedValueOnce(afterUndo)
      .mockResolvedValueOnce(afterRedo);
    const update = vi.spyOn(dohaApi, "updateWorkingClipFade")
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 3, replayed: true })
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 4, replayed: false })
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 5, replayed: false });
    const undoFade = vi.spyOn(dohaApi, "undoWorkingCompositionHistory")
      .mockResolvedValue({ clip_id: "clip-1", completed_revision: 4, replayed: false });
    const redoFade = vi.spyOn(dohaApi, "redoWorkingCompositionHistory")
      .mockResolvedValue({ clip_id: "clip-1", completed_revision: 5, replayed: false });
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const fadeIn = screen.getByLabelText("Fade In exact value");
    const fadeOut = screen.getByLabelText("Fade Out exact value");
    expect(fadeIn).toHaveValue(0);
    expect(fadeOut).toHaveValue(0);
    expect(fadeIn).toHaveAttribute("step", "0.000001");
    expect(fadeIn).toHaveAttribute("max", "10");

    fireEvent.change(fadeIn, { target: { value: "-1" } });
    fireEvent.blur(fadeIn);
    expect(await screen.findByText(/0 이상의 초 단위 숫자/)).toBeVisible();
    expect(update).not.toHaveBeenCalled();

    fireEvent.change(fadeIn, { target: { value: "9.5" } });
    fireEvent.change(fadeOut, { target: { value: "1" } });
    fireEvent.blur(fadeOut);
    expect((await screen.findAllByText(/합은 Clip 길이 10초 이하/)).at(-1)).toBeVisible();
    expect(update).not.toHaveBeenCalled();

    fireEvent.change(fadeIn, { target: { value: "0.123456" } });
    fireEvent.change(fadeOut, { target: { value: "0.75" } });
    fireEvent.blur(fadeOut);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[0][2]).toMatchObject({ expected_revision: 2, fade_in: 0.123456, fade_out: 0.75 });
    expect(update.mock.calls[0][3]).toBe(update.mock.calls[1][3]);
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0.123456);
    expect(screen.getByLabelText("Fade Out exact value")).toHaveValue(0.75);

    await user.click(screen.getByRole("button", { name: "편집 실행 취소" }));
    await waitFor(() => expect(undoFade).toHaveBeenCalledWith(
      "project-1", { working_composition_id: "working-1", expected_revision: 3 }, expect.any(String),
    ));
    expect(update).toHaveBeenCalledTimes(2);
    await user.click(screen.getByRole("button", { name: "편집 다시 실행" }));
    await waitFor(() => expect(redoFade).toHaveBeenCalledWith(
      "project-1", { working_composition_id: "working-1", expected_revision: 4 }, expect.any(String),
    ));
    expect(update).toHaveBeenCalledTimes(2);
  });

  it("Clip Fade Backend 422는 clamp 없이 safe error를 표시하고 failed forward history를 만들지 않는다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const update = vi.spyOn(dohaApi, "updateWorkingClipFade")
      .mockRejectedValue(new ApiError(422, "CLIP_FADE_OUT_OF_RANGE", "raw SQL/path detail"));
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const fadeIn = screen.getByLabelText("Fade In exact value");
    fireEvent.change(fadeIn, { target: { value: "0.5" } });
    fireEvent.blur(fadeIn);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/합이 Clip 길이를 넘지 않도록/)).toBeVisible();
    expect(screen.queryByText(/raw SQL\/path detail/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0);
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
  });

  it("Clip Loop는 typing 중 mutation하지 않고 canonical response와 history restore를 사용한다", async () => {
    const looped = { ...working, revision: 3, clips: [{ ...working.clips[0], loop_enabled: true, timeline_duration: "4", loop_phase: "0" }] };
    const resized = { ...working, revision: 4, clips: [{ ...working.clips[0], loop_enabled: true, timeline_duration: "1.5", loop_phase: "0" }] };
    const restored = { ...working, revision: 5 };
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(working).mockResolvedValueOnce(looped).mockResolvedValueOnce(resized).mockResolvedValueOnce(restored);
    const update = vi.spyOn(dohaApi, "updateWorkingClipLoop")
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 3, replayed: false })
      .mockResolvedValueOnce({ clip_id: "clip-1", completed_revision: 4, replayed: false });
    const undoLoop = vi.spyOn(dohaApi, "undoWorkingCompositionHistory").mockResolvedValue({ clip_id: "clip-1", completed_revision: 5, replayed: false });
    const restore = vi.spyOn(dohaApi, "restoreWorkingClipLoop");
    fireEvent.click((renderEditor(), await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ })));
    fireEvent.click(screen.getByRole("switch", { name: "Clip loop enabled" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][2]).toMatchObject({ loop_enabled: true, timeline_duration: 10 });
    expect(update.mock.calls[0][2]).not.toHaveProperty("loop_phase");
    const duration = screen.getByLabelText("Clip loop timeline duration");
    fireEvent.change(duration, { target: { value: "1.5" } });
    expect(update).toHaveBeenCalledTimes(1);
    fireEvent.blur(duration);
    await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
    expect(update.mock.calls[1][2]).toMatchObject({ loop_enabled: true, timeline_duration: 1.5 });
    await userEvent.setup().click(screen.getByRole("button", { name: "편집 실행 취소" }));
    await waitFor(() => expect(undoLoop).toHaveBeenCalledWith(
      "project-1", { working_composition_id: "working-1", expected_revision: 4 }, expect.any(String),
    ));
    expect(restore).not.toHaveBeenCalled();
  });

  it("Loop disable은 current D가 아니라 exact source window W를 한 번 제출한다", async () => {
    const looped = { ...working, clips: [{ ...working.clips[0], loop_enabled: true, timeline_duration: "17.25", loop_phase: "1.25" }] };
    const disabled = { ...working, revision: 3 };
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(looped).mockResolvedValueOnce(disabled);
    const update = vi.spyOn(dohaApi, "updateWorkingClipLoop").mockResolvedValue({ clip_id: "clip-1", completed_revision: 3, replayed: false });
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    fireEvent.click(screen.getByRole("switch", { name: "Clip loop enabled" }));
    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][2]).toMatchObject({ loop_enabled: false, timeline_duration: 10 });
  });

  it("pending Clip A Fade response는 selected Clip B Fade draft를 덮지 않는다", async () => {
    const twoClips = {
      ...working,
      clips: [
        { ...working.clips[0], fade_in: "0.1", fade_out: "0.2" },
        { ...working.clips[0], clip_id: "clip-2", timeline_start: "12.000", fade_in: "0.3", fade_out: "0.4" },
      ],
      timeline_duration: "22.000",
    };
    const after = {
      ...twoClips,
      revision: 3,
      clips: [{ ...twoClips.clips[0], fade_in: "0.5" }, twoClips.clips[1]],
    };
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(twoClips).mockResolvedValueOnce(after);
    let finish!: (value: { clip_id: string; completed_revision: number; replayed: boolean }) => void;
    vi.spyOn(dohaApi, "updateWorkingClipFade").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const inputA = screen.getByLabelText("Fade In exact value");
    fireEvent.change(inputA, { target: { value: "0.5" } });
    fireEvent.blur(inputA);
    expect(screen.getByLabelText("Fade In exact value")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Clip clip-2 선택 및 이동/ }));
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0.3);
    finish({ clip_id: "clip-1", completed_revision: 3, replayed: false });
    await waitFor(() => expect(screen.getByText(/revision 3/)).toBeVisible());
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0.3);
    expect(screen.getByLabelText("Fade Out exact value")).toHaveValue(0.4);
  });

  it("Project A pending Fade response는 Project B editor state를 오염시키지 않는다", async () => {
    const projectB = {
      ...working,
      project_id: "project-2",
      working_composition_id: "working-2",
      revision: 9,
      clips: [{ ...working.clips[0], clip_id: "clip-b", fade_in: "0.75", fade_out: "1.25" }],
    };
    vi.spyOn(dohaApi, "getWorkingComposition").mockImplementation(async (projectId) => projectId === "project-2" ? projectB : working);
    let finish!: (value: { clip_id: string; completed_revision: number; replayed: boolean }) => void;
    vi.spyOn(dohaApi, "updateWorkingClipFade").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const view = render(
      <QueryClientProvider client={client}>
        <WorkingCompositionEditor projectId="project-1" snapshotId="snapshot-1" sources={[source]} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const inputA = screen.getByLabelText("Fade In exact value");
    fireEvent.change(inputA, { target: { value: "0.5" } });
    fireEvent.blur(inputA);
    view.rerender(
      <QueryClientProvider client={client}>
        <WorkingCompositionEditor projectId="project-2" snapshotId="snapshot-2" sources={[source]} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Clip clip-b 선택 및 이동/ }));
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0.75);
    finish({ clip_id: "clip-1", completed_revision: 3, replayed: false });
    await waitFor(() => expect(screen.getByText(/revision 9/)).toBeVisible());
    expect(screen.getByLabelText("Fade In exact value")).toHaveValue(0.75);
    expect(screen.getByLabelText("Fade Out exact value")).toHaveValue(1.25);
  });

  it("newer recovery wins when older workspace and history responses resolve last", async () => {
    const initial = { ...working, revision: 10 };
    const recoveryAWorkspace = { ...working, revision: 11, tracks: [{ ...working.tracks[0], name: "Recovery A" }] };
    const recoveryBWorkspace = {
      ...working,
      revision: 12,
      tracks: [{ ...working.tracks[0], name: "Recovery B" }],
      clips: [],
      timeline_duration: "0",
    };
    const recoveryAHistory = {
      working_composition_id: "working-1", revision: 11, cursor: 0, command_count: 1,
      can_undo: false, can_redo: true,
    };
    const recoveryBHistory = {
      working_composition_id: "working-1", revision: 12, cursor: 1, command_count: 1,
      can_undo: true, can_redo: false,
    };
    let resolveAWorkspace!: (value: WorkingCompositionDto) => void;
    const aWorkspace = new Promise<WorkingCompositionDto>((resolve) => { resolveAWorkspace = resolve; });
    let resolveAHistory!: (value: typeof recoveryAHistory) => void;
    const aHistory = new Promise<typeof recoveryAHistory>((resolve) => { resolveAHistory = resolve; });
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(initial)
      .mockImplementationOnce(() => aWorkspace)
      .mockResolvedValueOnce(recoveryBWorkspace);
    vi.mocked(dohaApi.getWorkingCompositionHistory)
      .mockResolvedValueOnce(historyBoth(10))
      .mockResolvedValueOnce(recoveryBHistory)
      .mockImplementationOnce(() => aHistory);
    vi.spyOn(dohaApi, "updateWorkingClipGain")
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale A"));
    vi.spyOn(dohaApi, "createWorkingPreview")
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale B"));
    const user = userEvent.setup();
    const { client } = renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const gain = screen.getByLabelText("Clip gain exact value");
    await user.clear(gain);
    await user.type(gain, "3");
    fireEvent.blur(gain);
    expect(await screen.findByText("다른 편집자의 변경을 반영하는 중입니다.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Working Preview 만들기" }));
    expect(await screen.findByText(/revision 12/)).toBeVisible();
    expect(screen.getByLabelText("Recovery B Track 이름")).toBeVisible();
    expect(screen.queryByLabelText("선택 Clip 편집")).not.toBeInTheDocument();
    expect(client.getQueryData(["working-composition-history", "project-1", "working-1"]))
      .toEqual(recoveryBHistory);

    await act(async () => { resolveAWorkspace(recoveryAWorkspace); });
    await waitFor(() => expect(dohaApi.getWorkingCompositionHistory).toHaveBeenCalledTimes(3));
    await act(async () => { resolveAHistory(recoveryAHistory); });

    await waitFor(() => expect(screen.queryByText("다른 편집자의 변경을 반영하는 중입니다.")).not.toBeInTheDocument());
    expect(screen.getByText(/revision 12/)).toBeVisible();
    expect(screen.getByLabelText("Recovery B Track 이름")).toBeVisible();
    expect(screen.queryByLabelText("Recovery A Track 이름")).not.toBeInTheDocument();
    expect(screen.queryByText("다른 변경으로 revision이 달라졌습니다.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
    expect(screen.queryByLabelText("선택 Clip 편집")).not.toBeInTheDocument();
  });
  it("multi-user conflict recovery는 deleted selection을 clear하고 stale intent를 retry하지 않는다", async () => {
    const external = { ...working, revision: 7, clips: [] };
    vi.mocked(dohaApi.getWorkingCompositionHistory)
      .mockResolvedValueOnce(historyBoth())
      .mockResolvedValueOnce(historyBarrier(7));
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(external);
    const update = vi.spyOn(dohaApi, "updateWorkingClipGain")
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale"));
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: /Clip clip-1 선택 및 이동/ }));
    const input = screen.getByLabelText("Clip gain exact value");
    await user.clear(input);
    await user.type(input, "3");
    fireEvent.blur(input);
    expect(await screen.findByText(/다른 편집자의 변경을 감지해/)).toBeVisible();
    expect(screen.queryByLabelText("선택 Clip 편집")).not.toBeInTheDocument();
    expect(screen.getByText(/revision 7/)).toBeVisible();
    expect(update).toHaveBeenCalledTimes(1);
  });
  it("revision conflict는 GET reconcile하고 Undo/Redo 버튼을 비운다", async () => {
    const renamed = { ...working, revision: 3, tracks: [{ ...working.tracks[0], name: "Renamed" }] };
    const external = { ...renamed, revision: 7, tracks: [{ ...working.tracks[0], name: "External" }] };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBoth(3)).mockResolvedValueOnce(historyBarrier(7));
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(working).mockResolvedValueOnce(renamed).mockResolvedValueOnce(external);
    vi.spyOn(dohaApi, "renameWorkingTrack")
      .mockResolvedValueOnce({ track_id: "track-1", completed_revision: 3, replayed: false })
      .mockRejectedValueOnce(new ApiError(409, "WORKING_COMPOSITION_REVISION_CONFLICT", "stale"));
    const user = userEvent.setup();
    renderEditor();
    const input = await screen.findByLabelText("Track 1 Track 이름");
    await user.clear(input);
    await user.type(input, "Renamed");
    fireEvent.blur(input);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "편집 실행 취소" }));
    expect(await screen.findByText(/최신 편집 상태와 Undo\/Redo 기록/)).toBeVisible();
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
    expect(screen.getByText(/revision 7/)).toBeVisible();
  });

  it("checkout 성공은 history barrier로 Undo/Redo stack을 비운다", async () => {
    const renamed = { ...working, revision: 3, tracks: [{ ...working.tracks[0], name: "Renamed" }] };
    const checkedOut = { ...emptyWorking, revision: 4, base_composition_snapshot_id: "snapshot-1" };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBoth(3)).mockResolvedValueOnce(historyBarrier(4));
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValueOnce(working).mockResolvedValueOnce(renamed).mockResolvedValueOnce(checkedOut);
    vi.spyOn(dohaApi, "renameWorkingTrack").mockResolvedValue({ track_id: "track-1", completed_revision: 3, replayed: false });
    vi.spyOn(dohaApi, "checkoutWorkingComposition").mockResolvedValue({ working_composition_id: "working-1", base_composition_snapshot_id: "snapshot-1", completed_revision: 4, replayed: false });
    const user = userEvent.setup();
    renderEditor();
    const input = await screen.findByLabelText("Track 1 Track 이름");
    await user.clear(input);
    await user.type(input, "Renamed");
    fireEvent.blur(input);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "현재 Snapshot Checkout" }));
    await waitFor(() => expect(screen.getByText(/revision 4/)).toBeVisible());
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  });

  it("빈 WorkingComposition은 Commit을 비활성화하고 이유를 안내한다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(emptyWorking);
    renderEditor();
    expect(await screen.findByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }))
      .toBeDisabled();
    expect(screen.getByText("활성 Clip을 하나 이상 배치하면 새 버전으로 저장할 수 있습니다."))
      .toBeVisible();
  });

  it("Commit 성공만 history barrier가 되고 다음 edit부터 새 history를 시작한다", async () => {
    const renamed = { ...working, revision: 3, tracks: [{ ...working.tracks[0], name: "Before commit" }] };
    const committed = { ...renamed, revision: 4, base_composition_snapshot_id: "snapshot-2" };
    const afterCommitEdit = { ...committed, revision: 5, tracks: [{ ...working.tracks[0], name: "After commit" }] };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBoth(3)).mockResolvedValueOnce(historyBarrier(4)).mockResolvedValueOnce(historyBoth(5));
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(renamed)
      .mockResolvedValueOnce(committed)
      .mockResolvedValueOnce(afterCommitEdit);
    vi.spyOn(dohaApi, "renameWorkingTrack")
      .mockResolvedValueOnce({ track_id: "track-1", completed_revision: 3, replayed: false })
      .mockResolvedValueOnce({ track_id: "track-1", completed_revision: 5, replayed: false });
    const commit = vi.spyOn(dohaApi, "commitWorkingComposition").mockResolvedValue({
      working_composition_id: "working-1",
      composition_snapshot_id: "snapshot-2",
      completed_revision: 4,
      replayed: false,
    });
    const user = userEvent.setup();
    renderEditor();
    const input = await screen.findByLabelText("Track 1 Track 이름");
    await user.clear(input);
    await user.type(input, "Before commit");
    fireEvent.blur(input);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }));
    await screen.findByText("현재 편집 상태를 새 버전으로 저장했습니다. Undo/Redo 기록이 초기화되었습니다.");
    expect(commit).toHaveBeenCalledWith("project-1", 3, expect.stringMatching(/[0-9a-f-]{36}/));
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();

    const afterCommitInput = screen.getByLabelText("Before commit Track 이름");
    await user.clear(afterCommitInput);
    await user.type(afterCommitInput, "After commit");
    fireEvent.blur(afterCommitInput);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());
  });

  it("Commit 실패는 기존 history를 보존하고 response-loss retry는 같은 key를 사용한다", async () => {
    const renamed = { ...working, revision: 3, tracks: [{ ...working.tracks[0], name: "Pending" }] };
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValueOnce(historyBoth()).mockResolvedValueOnce(historyBoth(3)).mockResolvedValueOnce(historyBarrier(4));
    vi.spyOn(dohaApi, "getWorkingComposition")
      .mockResolvedValueOnce(working)
      .mockResolvedValueOnce(renamed)
      .mockResolvedValueOnce({ ...renamed, revision: 4, base_composition_snapshot_id: "snapshot-2" });
    vi.spyOn(dohaApi, "renameWorkingTrack").mockResolvedValue({
      track_id: "track-1", completed_revision: 3, replayed: false,
    });
    const commit = vi.spyOn(dohaApi, "commitWorkingComposition")
      .mockRejectedValueOnce(new ApiError(500, "COMMIT_FAILED", "failed"))
      .mockRejectedValueOnce(new ApiError(0, "NETWORK_ERROR", "lost"))
      .mockResolvedValueOnce({
        working_composition_id: "working-1",
        composition_snapshot_id: "snapshot-2",
        completed_revision: 4,
        replayed: true,
      });
    const user = userEvent.setup();
    renderEditor();
    const input = await screen.findByLabelText("Track 1 Track 이름");
    await user.clear(input);
    await user.type(input, "Pending");
    fireEvent.blur(input);
    await waitFor(() => expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }));
    await waitFor(() => expect(commit).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "현재 편집 상태를 새 버전으로 저장" }));
    await screen.findByText("현재 편집 상태를 새 버전으로 저장했습니다. Undo/Redo 기록이 초기화되었습니다.");
    expect(commit).toHaveBeenCalledTimes(3);
    expect(commit.mock.calls[1][2]).toBe(commit.mock.calls[2][2]);
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
  });

  it("Preview 생성 성공은 편집 revision과 Undo/Redo history를 변경하지 않는다", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    vi.mocked(dohaApi.getWorkingCompositionHistory).mockResolvedValue(historyBarrier(2));
    vi.spyOn(dohaApi, "createWorkingPreview").mockResolvedValue({
      job_id: "job-preview", preview_render_id: "render-preview",
      working_composition_id: "working-1", rendered_revision: 2,
      status: "queued", replayed: false,
    });
    vi.spyOn(dohaApi, "getWorkspaceJob").mockResolvedValue({
      job_id: "job-preview", job_type: "working_preview_render", status: "succeeded",
      project_id: "project-1", composition_snapshot_id: null,
      provider_id: null, model_manifest_id: null, progress_percent: 100,
      stage: null, retry_of_job_id: null,
      inputs: [],
      outputs: [{ output_role: "working_preview", output_order: 0, artifact_id: "artifact-preview", asset_version_id: "version-preview" }],
      model_usages: [],
      error_code: null, error_message: null, error_retryable: null, error_details_id: null,
      created_at: "2026-08-29T00:00:00Z", started_at: "2026-08-29T00:00:01Z",
      completed_at: "2026-08-29T00:00:02Z",
    });
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: "Working Preview 만들기" }));
    await screen.findByRole("button", { name: "Working Preview revision 2 재생" });
    expect(screen.getByText("revision 2 · 저장됨 · Undo/Redo 기록은 서버에 안전하게 보존")).toBeVisible();
    expect(screen.getByRole("button", { name: "편집 실행 취소" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "편집 다시 실행" })).toBeDisabled();
  });
  it("StrictMode remount recovers clip waveform after aborted request", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const resolver = vi.fn<MediaSourceResolver>().mockResolvedValue(mediaSource);
    const loader = vi.fn<WaveformLoader>()
      .mockRejectedValueOnce(new DOMException("Aborted", "AbortError"))
      .mockResolvedValueOnce([0.1, 0.2, 0.3, 0.4]);
    const { client, unmount } = renderEditor({ resolver, loader, strict: true });
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-1")).toHaveAttribute("data-waveform-status", "loading"));
    unmount();
    const secondRender = render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <WorkingCompositionEditor
            projectId="project-1"
            snapshotId="snapshot-1"
            sources={[source]}
            mediaSourceResolver={resolver}
            waveformLoader={loader}
          />
        </QueryClientProvider>
      </StrictMode>,
    );
    await waitFor(() => expect(secondRender.getByTestId("clip-waveform-clip-1")).toHaveAttribute("data-waveform-status", "ready"));
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("clip race keeps latest clip waveform state after old request resolves", async () => {
    vi.spyOn(dohaApi, "getWorkingComposition").mockResolvedValue(working);
    const resolver = vi.fn<MediaSourceResolver>()
      .mockImplementation(async (_, assetVersionId: string) => ({ ...mediaSource, asset_version_id: assetVersionId }));
    const loader = vi.fn<WaveformLoader>()
      .mockRejectedValueOnce(new DOMException("Aborted", "AbortError"))
      .mockResolvedValue([0.3, 0.4]);
    const { client } = renderEditor({ resolver, loader });
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    act(() => {
      client.setQueryData(["working-composition", "project-1"], {
        ...working,
        revision: 3,
        clips: [{ ...working.clips[0], clip_id: "clip-2", source_asset_version_id: "version-2", timeline_start: "0.000", source_in: "0.000", source_out: "10.000", source_duration: "10.000" }],
      });
    });
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-2")).toHaveAttribute("data-waveform-status", "ready"));
    expect(screen.queryByTestId("clip-waveform-clip-1")).not.toBeInTheDocument();
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("project race keeps latest project clip waveform state after old project resolves", async () => {
    const projectTwo: WorkingCompositionDto = {
      ...working,
      working_composition_id: "working-2",
      project_id: "project-2",
      revision: 9,
      clips: [{ ...working.clips[0], clip_id: "clip-b", source_asset_version_id: "version-2", source_in: "0.000", source_out: "10.000", source_duration: "10.000" }],
    };
    vi.spyOn(dohaApi, "getWorkingComposition").mockImplementation(async (projectId) => projectId === "project-2" ? projectTwo : working);
    const resolver = vi.fn<MediaSourceResolver>()
      .mockImplementation(async (_, assetVersionId: string) => ({
        ...mediaSource,
        asset_version_id: assetVersionId,
        artifact_id: assetVersionId,
      }));
    const loader = vi.fn<WaveformLoader>()
      .mockRejectedValueOnce(new DOMException("Aborted", "AbortError"))
      .mockResolvedValue([0.3, 0.4]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const editor = (projectId: string) => (
      <QueryClientProvider client={client}>
        <WorkingCompositionEditor
          projectId={projectId}
          snapshotId={projectId === "project-2" ? "snapshot-2" : "snapshot-1"}
          sources={[source, { ...source, asset_version: { ...source.asset_version, asset_version_id: "version-2" } }]}
          mediaSourceResolver={resolver}
          waveformLoader={loader}
        />
      </QueryClientProvider>
    );
    const { rerender } = render(editor("project-1"));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    rerender(editor("project-2"));
    await waitFor(() => expect(screen.getByTestId("clip-waveform-clip-b")).toHaveAttribute("data-waveform-status", "ready"));
    expect(screen.queryByTestId("clip-waveform-clip-1")).not.toBeInTheDocument();
    expect(loader).toHaveBeenCalledTimes(2);
    client.clear();
  });
});

function renderEditor({
  resolver = vi.fn<MediaSourceResolver>().mockResolvedValue(mediaSource),
  loader = vi.fn<WaveformLoader>().mockResolvedValue([0.2, 0.8]),
  strict = false,
}: { resolver?: MediaSourceResolver; loader?: WaveformLoader; strict?: boolean } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const editor = (
    <QueryClientProvider client={client}>
      <WorkingCompositionEditor projectId="project-1" snapshotId="snapshot-1" sources={[source]} mediaSourceResolver={resolver} waveformLoader={loader} />
    </QueryClientProvider>
  );
  return {
    ...render(strict ? <StrictMode>{editor}</StrictMode> : editor),
    client,
  };
}

function historyBoth(revision = 2) {
  return { working_composition_id: "working-1", revision, cursor: 1, command_count: 2, can_undo: true, can_redo: true };
}
function historyBarrier(revision: number) {
  return { working_composition_id: "working-1", revision, cursor: 0, command_count: 0, can_undo: false, can_redo: false };
}

const emptyWorking: WorkingCompositionDto = {
  working_composition_id: "working-1", project_id: "project-1", base_composition_snapshot_id: null,
  revision: 0, mix_settings: {}, tracks: [], clips: [], timeline_duration: "0",
};
const working: WorkingCompositionDto = {
  ...emptyWorking,
  revision: 2,
  tracks: [{ track_id: "track-1", track_type: "audio", name: "Track 1", track_order: 0 }],
  clips: [{ clip_id: "clip-1", track_id: "track-1", source_asset_version_id: "version-1", timeline_start: "0.000", source_in: "0.000", source_out: "10.000", source_duration: "10.000", timeline_duration: "10.000", loop_enabled: false, loop_phase: "0", gain_db: "0.00", fade_in: "0", fade_out: "0", split_from_clip_id: null }],
  timeline_duration: "10.000",
};
const source: CompositionReadItemDto = {
  snapshot_item_id: "item-1", item_role: "music", sort_order: 0,
  asset_version: { asset_version_id: "version-1", asset_id: "asset-1", version_number: 1, version_origin: "provider", parent_asset_version_id: null, processing_chain_id: null, provider_id: null, model_manifest_id: null, settings_snapshot: {}, created_at: "2026-08-26T00:00:00Z" },
  artifacts: [],
};
const mediaSource = {
  asset_version_id: "version-1", artifact_id: "artifact-1", media_type: "audio/wav" as const,
  size_bytes: 48, artifact_checksum: "a".repeat(64), duration_seconds: "10",
  content_url: "/api/v1/artifacts/artifact-1/content",
};

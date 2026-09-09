import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WorkingMixer, canonicalTrackMixer } from "@/features/composition/working-mixer";
import type { WorkingTrackDto } from "@/types/api";

const track: WorkingTrackDto = { track_id: "track-1", track_type: "audio", name: "Lead", track_order: 0, gain_db: "0.00", pan: "0.00", muted: false, solo: false };

describe("Working Mixer", () => {
  it("canonical state와 고유한 accessible controls를 표시한다", () => {
    renderMixer();
    expect(screen.getByRole("slider", { name: "Track Lead gain" })).toHaveAttribute("min", "-24");
    expect(screen.getByRole("slider", { name: "Track Lead pan" })).toHaveAttribute("max", "1");
    expect(screen.getByRole("button", { name: "Track Lead mute" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Track Lead solo" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("slider", { name: "Master gain" })).toHaveValue("0");
    expect(canonicalTrackMixer(track)).toEqual({ gainDb: 0, pan: 0, muted: false, solo: false });
  });

  it("20 pointer moves는 PATCH intent를 만들지 않고 pointerup 한 번만 commit한다", () => {
    const onTrackCommit = vi.fn().mockResolvedValue(true);
    renderMixer({ onTrackCommit });
    const gain = screen.getByRole("slider", { name: "Track Lead gain" });
    for (let index = 1; index <= 20; index += 1) fireEvent.change(gain, { target: { value: String(index / 2) } });
    expect(onTrackCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(gain);
    expect(onTrackCommit).toHaveBeenCalledTimes(1);
    expect(onTrackCommit.mock.calls[0][1]).toMatchObject({ gainDb: 10, pan: 0, muted: false, solo: false });
  });

  it("Gain, Pan, Mute, Solo와 Master를 canonical absolute intent로 전달한다", () => {
    const onTrackCommit = vi.fn().mockResolvedValue(true);
    const onMasterCommit = vi.fn().mockResolvedValue(true);
    renderMixer({ onTrackCommit, onMasterCommit });
    const pan = screen.getByRole("slider", { name: "Track Lead pan" });
    fireEvent.change(pan, { target: { value: "-0.5" } }); fireEvent.pointerUp(pan);
    fireEvent.click(screen.getByRole("button", { name: "Track Lead mute" }));
    fireEvent.click(screen.getByRole("button", { name: "Track Lead solo" }));
    const master = screen.getByRole("slider", { name: "Master gain" });
    fireEvent.change(master, { target: { value: "6" } }); fireEvent.pointerUp(master);
    expect(onTrackCommit.mock.calls[0][1]).toEqual({ gainDb: 0, pan: -0.5, muted: false, solo: false });
    expect(onTrackCommit.mock.calls[1][1]).toEqual({ gainDb: 0, pan: -0.5, muted: true, solo: false });
    expect(onTrackCommit.mock.calls[2][1]).toEqual({ gainDb: 0, pan: -0.5, muted: false, solo: true });
    expect(onMasterCommit).toHaveBeenCalledWith(6);
  });

  it("pending target만 비활성화하고 다른 Track과 Master는 유지한다", () => {
    const second = { ...track, track_id: "track-2", name: "Bass" };
    render(<WorkingMixer tracks={[track, second]} masterGainDb="0.00" pendingTargets={new Set([track.track_id])} onTrackCommit={vi.fn().mockResolvedValue(true)} onMasterCommit={vi.fn().mockResolvedValue(true)} />);
    expect(screen.getByRole("slider", { name: "Track Lead gain" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "Track Bass gain" })).toBeEnabled();
    expect(screen.getByRole("slider", { name: "Master gain" })).toBeEnabled();
  });
});

function renderMixer(overrides: {
  onTrackCommit?: (track: WorkingTrackDto, state: ReturnType<typeof canonicalTrackMixer>) => Promise<boolean>;
  onMasterCommit?: (gainDb: number) => Promise<boolean>;
} = {}) {
  return render(<WorkingMixer tracks={[track]} masterGainDb="0.00" pendingTargets={new Set()} onTrackCommit={overrides.onTrackCommit ?? vi.fn().mockResolvedValue(true)} onMasterCommit={overrides.onMasterCommit ?? vi.fn().mockResolvedValue(true)} />);
}

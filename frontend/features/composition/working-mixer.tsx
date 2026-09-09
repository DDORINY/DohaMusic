"use client";

import { Headphones, Volume2, VolumeX } from "lucide-react";
import { useState } from "react";
import type { WorkingTrackDto } from "@/types/api";

const MIN_GAIN = -24;
const MAX_GAIN = 24;

export type TrackMixerState = { gainDb: number; pan: number; muted: boolean; solo: boolean };

export function canonicalTrackMixer(track: WorkingTrackDto): TrackMixerState {
  return {
    gainDb: Number(track.gain_db ?? 0),
    pan: Number(track.pan ?? 0),
    muted: track.muted ?? false,
    solo: track.solo ?? false,
  };
}

export function TrackMixerButtons({ track, pending, quick = false, onCommit }: {
  track: WorkingTrackDto;
  pending: boolean;
  quick?: boolean;
  onCommit: (state: TrackMixerState) => Promise<boolean>;
}) {
  const state = canonicalTrackMixer(track);
  return <div className="track-mixer-buttons" aria-label={`${track.name} quick mixer`}>
    <button type="button" className={state.muted ? "active" : ""} aria-label={`Track ${track.name} mute${quick ? " quick control" : ""}`} aria-pressed={state.muted} disabled={pending} onClick={() => void onCommit({ ...state, muted: !state.muted })}><VolumeX aria-hidden="true" />M</button>
    <button type="button" className={state.solo ? "active solo" : ""} aria-label={`Track ${track.name} solo${quick ? " quick control" : ""}`} aria-pressed={state.solo} disabled={pending} onClick={() => void onCommit({ ...state, solo: !state.solo })}><Headphones aria-hidden="true" />S</button>
  </div>;
}

export function WorkingMixer({ tracks, masterGainDb, pendingTargets, onTrackCommit, onMasterCommit }: {
  tracks: WorkingTrackDto[];
  masterGainDb: string;
  pendingTargets: ReadonlySet<string>;
  onTrackCommit: (track: WorkingTrackDto, state: TrackMixerState) => Promise<boolean>;
  onMasterCommit: (gainDb: number) => Promise<boolean>;
}) {
  return <section className="working-mixer" aria-labelledby="working-mixer-title">
    <header><div><p className="eyebrow">FROZEN MIX PATH</p><h5 id="working-mixer-title">Mixer</h5></div><span>Track processing → sum → master</span></header>
    <div className="mixer-strip-scroll">
      <div className="mixer-track-strips">
        {tracks.map((track) => <TrackStrip key={`${track.track_id}:${track.gain_db}:${track.pan}:${track.muted}:${track.solo}`} track={track} pending={pendingTargets.has(track.track_id)} onCommit={(state) => onTrackCommit(track, state)} />)}
      </div>
      <MasterStrip key={masterGainDb} value={Number(masterGainDb)} pending={pendingTargets.has("master")} onCommit={onMasterCommit} />
    </div>
  </section>;
}

function TrackStrip({ track, pending, onCommit }: { track: WorkingTrackDto; pending: boolean; onCommit: (state: TrackMixerState) => Promise<boolean> }) {
  const canonical = canonicalTrackMixer(track);
  const [gainDb, setGainDb] = useState(canonical.gainDb);
  const [pan, setPan] = useState(canonical.pan);
  return <article className="mixer-strip" aria-label={`${track.name} mixer strip`}>
    <strong title={track.name}>{track.name}</strong>
    <span className="mixer-value" aria-label={`Track ${track.name} gain value`}>{gainDb.toFixed(2)} dB</span>
    <label>Gain<input aria-label={`Track ${track.name} gain`} type="range" min={MIN_GAIN} max={MAX_GAIN} step="0.01" value={gainDb} disabled={pending} onChange={(event) => setGainDb(Number(event.target.value))} onPointerUp={() => void onCommit({ ...canonical, gainDb })} onKeyUp={(event) => { if (event.key.startsWith("Arrow") || event.key === "Home" || event.key === "End") void onCommit({ ...canonical, gainDb }); }} /></label>
    <span className="mixer-value" aria-label={`Track ${track.name} pan value`}>{pan === 0 ? "C" : `${pan < 0 ? "L" : "R"} ${Math.abs(pan).toFixed(2)}`}</span>
    <label>Pan<input aria-label={`Track ${track.name} pan`} type="range" min="-1" max="1" step="0.01" value={pan} disabled={pending} onChange={(event) => setPan(Number(event.target.value))} onPointerUp={() => void onCommit({ ...canonical, gainDb, pan })} onKeyUp={(event) => { if (event.key.startsWith("Arrow") || event.key === "Home" || event.key === "End") void onCommit({ ...canonical, gainDb, pan }); }} /></label>
    <TrackMixerButtons track={{ ...track, gain_db: String(gainDb), pan: String(pan), muted: canonical.muted, solo: canonical.solo }} pending={pending} onCommit={(state) => onCommit({ ...state, gainDb, pan })} />
  </article>;
}

function MasterStrip({ value, pending, onCommit }: { value: number; pending: boolean; onCommit: (gainDb: number) => Promise<boolean> }) {
  const [gainDb, setGainDb] = useState(Number.isFinite(value) ? value : 0);
  return <article className="mixer-strip master" aria-label="Master mixer strip">
    <strong><Volume2 aria-hidden="true" /> Master</strong><span className="mixer-value" aria-label="Master gain value">{gainDb.toFixed(2)} dB</span>
    <label>Gain<input aria-label="Master gain" type="range" min={MIN_GAIN} max={MAX_GAIN} step="0.01" value={gainDb} disabled={pending} onChange={(event) => setGainDb(Number(event.target.value))} onPointerUp={() => void onCommit(gainDb)} onKeyUp={(event) => { if (event.key.startsWith("Arrow") || event.key === "Home" || event.key === "End") void onCommit(gainDb); }} /></label>
    <button type="button" disabled={pending || gainDb === 0} onClick={() => { setGainDb(0); void onCommit(0); }}>0 dB</button>
  </article>;
}

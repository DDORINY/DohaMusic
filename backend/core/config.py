"""Environment-backed application configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class Settings(BaseModel):
    """Validated runtime settings with safe local defaults."""

    model_config = ConfigDict(frozen=True)

    app_name: str = "DohaMusic Backend"
    database_url: str = "sqlite:///./backend/storage/doha_music.db"
    auto_migrate: bool = False
    cursor_signing_key: SecretStr = SecretStr("")
    storage_root: Path = Path("backend/storage")
    artifact_root: Path | None = None
    artifact_staging_root: Path | None = None
    music_generator: str = "mock"
    model_name: str = "mock-music-generator"
    model_version: str = "mock"
    worker_max_threads: int = Field(default=1, ge=1, le=8)
    export_worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    mock_generation_delay_seconds: float = Field(default=3.0, ge=0, le=60)
    stem_provider: str = "mock"
    mock_stem_delay_seconds: float = Field(default=0.1, ge=0, le=60)
    voice_provider: str = "mock"
    mock_voice_delay_seconds: float = Field(default=0.1, ge=0, le=60)
    dohavocal_base_url: str = "http://127.0.0.1:8080"
    dohavocal_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=300)
    dohavocal_read_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    dohavocal_write_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    dohavocal_pool_timeout_seconds: float = Field(default=2.0, gt=0, le=300)
    dohavocal_payload_max_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024)
    pipeline_version: str = "1"
    pipeline_max_retries: int = Field(default=1, ge=0, le=5)
    pipeline_step_timeout_seconds: float = Field(default=900, ge=0.01, le=7_200)
    audio_mixer: Literal["mock", "default"] = "default"
    lyrics_provider: str = "template"
    lyrics_model: str = "gpt-5-mini-2025-08-07"
    lyrics_api_key: str = ""
    lyrics_base_url: str = "https://api.openai.com/v1"
    lyrics_timeout_seconds: float = Field(default=2.0, ge=0.1, le=4.5)
    lyrics_total_deadline_seconds: float = Field(default=5.0, ge=0.5, le=5.0)
    lyrics_max_retries: int = Field(default=1, ge=0, le=2)
    lyrics_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    lyrics_max_output_tokens: int = Field(default=2_000, ge=256, le=8_000)
    lyrics_input_cost_per_million: float | None = Field(default=None, ge=0.0)
    lyrics_output_cost_per_million: float | None = Field(default=None, ge=0.0)
    lyrics_pricing_version: str = ""
    lyrics_max_cost_per_request: float | None = Field(default=None, ge=0.0)
    mixer_vocal_gain_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    mixer_instrumental_gain_db: float = Field(default=0.0, ge=-24.0, le=24.0)
    mixer_headroom_db: float = Field(default=1.0, ge=0.1, le=12.0)
    mixer_normalization: Literal["off", "peak"] = "peak"
    mixer_limiter: Literal["bypass", "soft"] = "soft"
    mixer_fade_in_ms: float = Field(default=10.0, ge=0.0, le=5_000.0)
    mixer_fade_out_ms: float = Field(default=10.0, ge=0.0, le=5_000.0)
    ace_step_runtime_python: str = ""
    ace_step_runner_path: str = ""
    ace_step_project_root: str = ""
    ace_step_checkpoint_path: str = ""
    ace_step_model_variant: str = "acestep-v15-turbo"
    ace_step_model_version: str = "v0.1.8"
    ace_step_device: str = "cuda"
    ace_step_quantization: str | None = "int8_weight_only"
    ace_step_cpu_offload: bool = True
    ace_step_dit_cpu_offload: bool = True
    ace_step_timeout_seconds: int = Field(default=900, ge=10, le=7_200)
    demucs_runtime_python: str = ""
    demucs_runner_path: str = "ai_worker/scripts/run_demucs_separation.py"
    demucs_model_cache_path: str = ""
    demucs_model_name: str = "htdemucs"
    demucs_model_version: str = "4.1.0"
    demucs_device: str = "cuda"
    demucs_segment_seconds: float = Field(default=7.0, ge=1.0, le=7.8)
    demucs_shifts: int = Field(default=1, ge=0, le=10)
    demucs_overlap: float = Field(default=0.25, ge=0.0, lt=1.0)
    demucs_timeout_seconds: int = Field(default=900, ge=10, le=7_200)
    seed_vc_runtime_python: str = ""
    seed_vc_runner_path: str = "ai_worker/scripts/run_seed_vc_conversion.py"
    seed_vc_project_root: str = ""
    seed_vc_checkpoint_path: str = ""
    seed_vc_config_path: str = ""
    seed_vc_model_cache_path: str = ""
    seed_vc_model_name: str = "seed-uvit-whisper-base-f0-44k"
    seed_vc_model_version: str = "51383efd921027683c89e5348211d93ff12ac2a8"
    seed_vc_device: str = "cuda"
    seed_vc_diffusion_steps: int = Field(default=30, ge=1, le=100)
    seed_vc_timeout_seconds: int = Field(default=1800, ge=10, le=7_200)
    voice_ffmpeg_executable: str = "ffmpeg"
    voice_normalization_timeout_seconds: int = Field(default=30, ge=1, le=300)
    voice_enrollment_max_file_bytes: int = Field(
        default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024
    )
    voice_enrollment_max_samples: int = Field(default=10, ge=1, le=50)
    voice_enrollment_min_duration_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    voice_enrollment_max_duration_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    voice_enrollment_sliding_expiry_hours: int = Field(default=24, ge=1, le=168)
    voice_enrollment_absolute_expiry_days: int = Field(default=7, ge=1, le=30)
    voice_cleanup_interval_seconds: int = Field(default=300, ge=1, le=86_400)
    voice_expiration_scan_interval_seconds: int = Field(default=300, ge=1, le=86_400)
    voice_orphan_scan_interval_seconds: int = Field(default=3600, ge=1, le=86_400)
    voice_delete_retry_limit: int = Field(default=3, ge=1, le=20)
    voice_delete_retry_delay_seconds: int = Field(default=60, ge=0, le=86_400)
    voice_orphan_grace_seconds: int = Field(default=86_400, ge=0, le=604_800)
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("dohavocal_base_url")
    @classmethod
    def validate_dohavocal_base_url(cls, value: str) -> str:
        candidate = value.strip()
        try:
            parsed = urlsplit(candidate)
            _port = parsed.port
        except ValueError as error:
            raise ValueError("invalid DohaVocal base URL") from error
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DohaVocal base URL must be an HTTP(S) origin")
        return candidate.rstrip("/")

    @field_validator("artifact_root", "artifact_staging_root", mode="before")
    @classmethod
    def normalize_optional_artifact_root(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "audio_mixer",
        "lyrics_provider",
        "mixer_normalization",
        "mixer_limiter",
        mode="before",
    )
    @classmethod
    def normalize_choice(cls, value: str) -> str:
        return value.strip().lower()

    @classmethod
    def from_environment(cls) -> Settings:
        """Read supported settings without loading or exposing secrets."""

        values: dict[str, object] = {}
        mapping = {
            "APP_NAME": "app_name",
            "DATABASE_URL": "database_url",
            "DOHAMUSIC_AUTO_MIGRATE": "auto_migrate",
            "DOHAMUSIC_CURSOR_SIGNING_KEY": "cursor_signing_key",
            "AUDIO_STORAGE_ROOT": "storage_root",
            "DOHA_ARTIFACT_ROOT": "artifact_root",
            "DOHA_ARTIFACT_STAGING_ROOT": "artifact_staging_root",
            "DOHAMUSIC_MUSIC_GENERATOR": "music_generator",
            "MODEL_NAME": "model_name",
            "MODEL_VERSION": "model_version",
            "WORKER_MAX_THREADS": "worker_max_threads",
            "DOHAMUSIC_EXPORT_WORKER_POLL_INTERVAL_SECONDS": (
                "export_worker_poll_interval_seconds"
            ),
            "MOCK_GENERATION_DELAY_SECONDS": "mock_generation_delay_seconds",
            "DOHAMUSIC_STEM_PROVIDER": "stem_provider",
            "MOCK_STEM_DELAY_SECONDS": "mock_stem_delay_seconds",
            "DOHAMUSIC_VOICE_PROVIDER": "voice_provider",
            "MOCK_VOICE_DELAY_SECONDS": "mock_voice_delay_seconds",
            "DOHAVOCAL_BASE_URL": "dohavocal_base_url",
            "DOHAVOCAL_CONNECT_TIMEOUT_SECONDS": "dohavocal_connect_timeout_seconds",
            "DOHAVOCAL_READ_TIMEOUT_SECONDS": "dohavocal_read_timeout_seconds",
            "DOHAVOCAL_WRITE_TIMEOUT_SECONDS": "dohavocal_write_timeout_seconds",
            "DOHAVOCAL_POOL_TIMEOUT_SECONDS": "dohavocal_pool_timeout_seconds",
            "DOHAVOCAL_PAYLOAD_MAX_BYTES": "dohavocal_payload_max_bytes",
            "DOHAMUSIC_PIPELINE_VERSION": "pipeline_version",
            "DOHAMUSIC_PIPELINE_MAX_RETRIES": "pipeline_max_retries",
            "DOHAMUSIC_PIPELINE_STEP_TIMEOUT_SECONDS": "pipeline_step_timeout_seconds",
            "DOHAMUSIC_AUDIO_MIXER": "audio_mixer",
            "DOHAMUSIC_LYRICS_PROVIDER": "lyrics_provider",
            "DOHAMUSIC_LYRICS_MODEL": "lyrics_model",
            "DOHAMUSIC_LYRICS_API_KEY": "lyrics_api_key",
            "DOHAMUSIC_LYRICS_BASE_URL": "lyrics_base_url",
            "DOHAMUSIC_LYRICS_TIMEOUT_SECONDS": "lyrics_timeout_seconds",
            "DOHAMUSIC_LYRICS_TOTAL_DEADLINE_SECONDS": "lyrics_total_deadline_seconds",
            "DOHAMUSIC_LYRICS_MAX_RETRIES": "lyrics_max_retries",
            "DOHAMUSIC_LYRICS_TEMPERATURE": "lyrics_temperature",
            "DOHAMUSIC_LYRICS_MAX_OUTPUT_TOKENS": "lyrics_max_output_tokens",
            "DOHAMUSIC_LYRICS_INPUT_COST_PER_MILLION": "lyrics_input_cost_per_million",
            "DOHAMUSIC_LYRICS_OUTPUT_COST_PER_MILLION": "lyrics_output_cost_per_million",
            "DOHAMUSIC_LYRICS_PRICING_VERSION": "lyrics_pricing_version",
            "DOHAMUSIC_LYRICS_MAX_COST_PER_REQUEST": "lyrics_max_cost_per_request",
            "DOHAMUSIC_MIXER_VOCAL_GAIN_DB": "mixer_vocal_gain_db",
            "DOHAMUSIC_MIXER_INSTRUMENTAL_GAIN_DB": "mixer_instrumental_gain_db",
            "DOHAMUSIC_MIXER_HEADROOM_DB": "mixer_headroom_db",
            "DOHAMUSIC_MIXER_NORMALIZATION": "mixer_normalization",
            "DOHAMUSIC_MIXER_LIMITER": "mixer_limiter",
            "DOHAMUSIC_MIXER_FADE_IN_MS": "mixer_fade_in_ms",
            "DOHAMUSIC_MIXER_FADE_OUT_MS": "mixer_fade_out_ms",
            "DOHAMUSIC_AI_ACE_STEP_RUNTIME_PYTHON": "ace_step_runtime_python",
            "DOHAMUSIC_AI_ACE_STEP_RUNNER_PATH": "ace_step_runner_path",
            "DOHAMUSIC_AI_ACE_STEP_PROJECT_ROOT": "ace_step_project_root",
            "DOHAMUSIC_AI_ACE_STEP_CHECKPOINT_PATH": "ace_step_checkpoint_path",
            "DOHAMUSIC_AI_ACE_STEP_MODEL_VARIANT": "ace_step_model_variant",
            "DOHAMUSIC_AI_ACE_STEP_MODEL_VERSION": "ace_step_model_version",
            "DOHAMUSIC_AI_ACE_STEP_DEVICE": "ace_step_device",
            "DOHAMUSIC_AI_ACE_STEP_QUANTIZATION": "ace_step_quantization",
            "DOHAMUSIC_AI_ACE_STEP_CPU_OFFLOAD": "ace_step_cpu_offload",
            "DOHAMUSIC_AI_ACE_STEP_DIT_CPU_OFFLOAD": "ace_step_dit_cpu_offload",
            "DOHAMUSIC_AI_ACE_STEP_TIMEOUT_SECONDS": "ace_step_timeout_seconds",
            "DOHAMUSIC_STEM_DEMUCS_RUNTIME_PYTHON": "demucs_runtime_python",
            "DOHAMUSIC_STEM_DEMUCS_RUNNER_PATH": "demucs_runner_path",
            "DOHAMUSIC_STEM_DEMUCS_MODEL_CACHE_PATH": "demucs_model_cache_path",
            "DOHAMUSIC_STEM_DEMUCS_MODEL_NAME": "demucs_model_name",
            "DOHAMUSIC_STEM_DEMUCS_MODEL_VERSION": "demucs_model_version",
            "DOHAMUSIC_STEM_DEMUCS_DEVICE": "demucs_device",
            "DOHAMUSIC_STEM_DEMUCS_SEGMENT_SECONDS": "demucs_segment_seconds",
            "DOHAMUSIC_STEM_DEMUCS_SHIFTS": "demucs_shifts",
            "DOHAMUSIC_STEM_DEMUCS_OVERLAP": "demucs_overlap",
            "DOHAMUSIC_STEM_DEMUCS_TIMEOUT_SECONDS": "demucs_timeout_seconds",
            "DOHAMUSIC_VOICE_SEED_VC_RUNTIME_PYTHON": "seed_vc_runtime_python",
            "DOHAMUSIC_VOICE_SEED_VC_RUNNER_PATH": "seed_vc_runner_path",
            "DOHAMUSIC_VOICE_SEED_VC_PROJECT_ROOT": "seed_vc_project_root",
            "DOHAMUSIC_VOICE_SEED_VC_CHECKPOINT_PATH": "seed_vc_checkpoint_path",
            "DOHAMUSIC_VOICE_SEED_VC_CONFIG_PATH": "seed_vc_config_path",
            "DOHAMUSIC_VOICE_SEED_VC_MODEL_CACHE_PATH": "seed_vc_model_cache_path",
            "DOHAMUSIC_VOICE_SEED_VC_MODEL_NAME": "seed_vc_model_name",
            "DOHAMUSIC_VOICE_SEED_VC_MODEL_VERSION": "seed_vc_model_version",
            "DOHAMUSIC_VOICE_SEED_VC_DEVICE": "seed_vc_device",
            "DOHAMUSIC_VOICE_SEED_VC_DIFFUSION_STEPS": "seed_vc_diffusion_steps",
            "DOHAMUSIC_VOICE_SEED_VC_TIMEOUT_SECONDS": "seed_vc_timeout_seconds",
            "DOHAMUSIC_VOICE_FFMPEG_EXECUTABLE": "voice_ffmpeg_executable",
            "DOHAMUSIC_VOICE_NORMALIZATION_TIMEOUT_SECONDS": "voice_normalization_timeout_seconds",
            "DOHAMUSIC_VOICE_ENROLLMENT_MAX_FILE_BYTES": "voice_enrollment_max_file_bytes",
            "DOHAMUSIC_VOICE_ENROLLMENT_MAX_SAMPLES": "voice_enrollment_max_samples",
            "DOHAMUSIC_VOICE_ENROLLMENT_MIN_DURATION_SECONDS": (
                "voice_enrollment_min_duration_seconds"
            ),
            "DOHAMUSIC_VOICE_ENROLLMENT_MAX_DURATION_SECONDS": (
                "voice_enrollment_max_duration_seconds"
            ),
            "DOHAMUSIC_VOICE_ENROLLMENT_SLIDING_EXPIRY_HOURS": (
                "voice_enrollment_sliding_expiry_hours"
            ),
            "DOHAMUSIC_VOICE_ENROLLMENT_ABSOLUTE_EXPIRY_DAYS": (
                "voice_enrollment_absolute_expiry_days"
            ),
            "DOHAMUSIC_VOICE_CLEANUP_INTERVAL_SECONDS": "voice_cleanup_interval_seconds",
            "DOHAMUSIC_VOICE_EXPIRATION_SCAN_INTERVAL_SECONDS": (
                "voice_expiration_scan_interval_seconds"
            ),
            "DOHAMUSIC_VOICE_ORPHAN_SCAN_INTERVAL_SECONDS": "voice_orphan_scan_interval_seconds",
            "DOHAMUSIC_VOICE_DELETE_RETRY_LIMIT": "voice_delete_retry_limit",
            "DOHAMUSIC_VOICE_DELETE_RETRY_DELAY_SECONDS": "voice_delete_retry_delay_seconds",
            "DOHAMUSIC_VOICE_ORPHAN_GRACE_SECONDS": "voice_orphan_grace_seconds",
            "LOG_LEVEL": "log_level",
        }
        for environment_name, field_name in mapping.items():
            value = os.getenv(environment_name)
            if value is not None:
                values[field_name] = value
        return cls.model_validate(values)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()

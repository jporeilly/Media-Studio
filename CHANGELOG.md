# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- [scaffold] Initial React + FastAPI skeleton for **Media Studio Enterprise**, the successor to Slide Studio Enterprise (NiceGUI). FastAPI backend (app factory, `/api/system/health`, session-cookie auth skeleton) reusing the carried-over Python media engine (`core/`, `utils/`, `services/processing.py`); React + Vite + TypeScript frontend on the OpenSight design system (theme tokens, shell, primitives, fetch client) with a login page and a dashboard landing state. Torch/Argos-free — translation runs on Ollama, transcription on faster-whisper/CTranslate2 — #rebuild

## [0.1.0] - 2026-09-13

- Project created.

# Dependency security review — 2026-09-27

Scope: the locked web application and the preserved training environments.
An advisory scan is a point-in-time check, not proof that a package is safe.
No paid services, model downloads or GPU runs were used for this review.

| Environment | Result | Action and practical impact |
|---|---|---|
| Frontend, `npm audit` | **0 reported vulnerabilities** after a clean install | Retained Next 15.5.26 and the initial Phase 12 compatible fixes. Overrode only Next's PostCSS dependency to **8.5.28**, within major 8, replacing 8.4.31. `npm ls postcss --all` confirms every path uses 8.5.28. The complete interaction suite, typecheck and production build pass. No forced Next 16 upgrade. |
| Backend, `pip-audit`, 40 exact packages | **0 reported vulnerabilities** | Added `backend/requirements-lock.txt` with the audited versions and package hashes. Python 3.12 is the verified runtime. CI and startup use this lock; direct requirements remain in `requirements.txt`. No application dependency was added. |
| Training CPU / GPU pins, 40 / 59 entries | **Two unique setuptools findings; torch not covered** | Preserved the exact Phase 8–10 locks and evidence. The scanner reports each setuptools advisory twice; those aliases are not four separate defects. Details below. |

## Fixed frontend findings

Next's old PostCSS copy had these maintainer advisories:

- [GHSA-qx2v-qp2m-jg93](https://github.com/postcss/postcss/security/advisories/GHSA-qx2v-qp2m-jg93): unsafe HTML style termination during stringification.
- [GHSA-6g55-p6wh-862q](https://github.com/postcss/postcss/security/advisories/GHSA-6g55-p6wh-862q), [GHSA-fxqj-rqcc-2cmp](https://github.com/postcss/postcss/security/advisories/GHSA-fxqj-rqcc-2cmp) and [GHSA-r28c-9q8g-f849](https://github.com/postcss/postcss/security/advisories/GHSA-r28c-9q8g-f849): file/source-map traversal through attacker-controlled CSS source-map references, including an incomplete earlier fix.

This app builds repository CSS and has no user-supplied CSS compiler. That limited exposure did not justify keeping an affected version once a compatible major-8 override passed verification. Keep the override until Next itself requires a patched PostCSS version, then remove it and repeat the checks.

## Preserved training findings and coverage limits

1. **`setuptools==78.1.0`: [GHSA-5rjg-fvgr-3xxf / CVE-2025-47273](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf), PYSEC-2025-49.** A hostile legacy `PackageIndex` download can escape the download directory and write files, potentially leading to code execution. The reported fixed version is 78.1.1. The app does not call this API; the recorded training procedure uses pip/uv, not setuptools' legacy downloader. These are version pins, **not hash-locked training wheels**. Avoid the vulnerable API and refresh/re-verify a new training environment before reusing it.
2. **[GHSA-h35f-9h28-mq5c / CVE-2026-59890](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c), PYSEC-2026-3447.** Unicode normalization differences can bypass `MANIFEST.in` exclusions when building an sdist, especially on macOS APFS/HFS+, potentially publishing excluded secrets. This repository's recorded Linux training runs do not build/publish that kind of source distribution, so no affected path was identified here. The audit database lists 83.0.0 as fixed, while the maintainer advisory still says no patched version: verify the release and the exclusion behavior when preparing a new environment; do not call that version independently verified here.
3. **Torch coverage gap:** `2.14.0+cpu` and `2.14.0+cu126` are absent from PyPI's audit lookup and were skipped. A clean scan of the other packages does not cover those vendor wheels, their native libraries, GPU driver or CUDA runtime. Review the vendor advisories and exact wheel provenance before a future model run. No training environment was installed or altered in this follow-up.

These findings remain open for future training/serving work. The web app does not import the training stack. They do not invalidate the preserved model evaluation files, and changing their recorded locks would not repair an already completed run.

## Repeat the audit

From the repository root, using a disposable audit tool:

```bash
(cd frontend && npm ci && npm audit && npm ls postcss --all)
uv tool run pip-audit --no-deps --disable-pip -r backend/requirements-lock.txt
uv tool run pip-audit --no-deps --disable-pip -r training/requirements-lock-cpu.txt
uv tool run pip-audit --no-deps --disable-pip -r training/requirements-gpu-lock.txt
```

Training scans intentionally exit nonzero for the recorded findings. Audits contact public package/advisory services, not AI providers. To update backend dependencies, resolve `requirements.txt` in a fresh Python 3.12 environment, compile exact versions with hashes, review the diff and repeat the audit and release workflow. Never resolve new versions automatically during application startup.

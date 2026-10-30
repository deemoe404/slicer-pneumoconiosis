# Third-party components and signing eligibility

Reviewed for application preparation on 2026-10-30. This is a provenance inventory, not a declaration that SignPath has accepted the distribution or that every third-party artifact has the same license as this repository.

The project's original code and documentation use [BSD-3-Clause](../LICENSE). Upstream programs, libraries, translations and weights retain their own terms. The [Windows build](../scripts/build_windows_installer.py) preserves bundled license files and writes `THIRD-PARTY-NOTICES.txt`, `build-manifest.json` and per-file hashes. The public [0.3.1 release](https://github.com/deemoe404/slicer-pneumoconiosis/releases/tag/windows-v0.3.1-8634275-r14) provides the exact manifest for that existing, unsigned installer.

## Main distribution components

| Component | Pinned source / evidence | License and handling |
| --- | --- | --- |
| 3D Slicer 5.13.0, revision 35392 | [Build inputs](../packaging/windows/inputs.json); [upstream license at the recorded source revision](https://github.com/Slicer/Slicer/blob/46891eee252d28fa8c2a89ddd98d8f85e8fc7064/License.txt) | Custom Slicer BSD-style license and separately licensed bundled components. Preserve the complete distribution's licenses. Do not label the entire Slicer distribution BSD-3-Clause. |
| ExtraMarkups, source `f5c833b` | [Platform package hashes](../packaging/extra-markups.json); [upstream source](https://github.com/chir-set/SlicerExtraMarkups/tree/f5c833b) | [CeCILL-B](../packaging/licenses/ExtraMarkups-LICENSE.txt); original upstream binary and provenance retained. |
| Standalone Python 3.12.12 | [Project runtime version](../mise.toml); Python license retained under `runtime/python/` | PSF/Python terms and bundled-component notices. |
| TorchXRayVision 1.5.7 library | [Runtime requirements](../requirements-runtime.txt); installed distribution's `licenses/LICENSE`; [upstream library license](https://github.com/mlmed/torchxrayvision/blob/master/LICENSE) | Apache-2.0 for the library. This entry does not settle the separate weight file's redistribution terms. |
| PyTorch, torchvision and numerical/image/audio libraries | [Windows dependency lock](../requirements-windows-lock.txt); exact versions and notices in the release manifest and package distributions | Preserve distribution-specific license files, including bundled native-library notices; do not infer licensing from a package's name alone. |
| Slicer and CTK Chinese translations | [Pinned URLs and hashes](../PneumoconiosisAnnotator/Resources/Translations/sources.json) | Upstream license retained as `LICENSE.SlicerTranslations` alongside editable translation sources. |
| Qt Chinese translations | [Pinned Qt 6.10.0 URLs and hashes](../PneumoconiosisAnnotator/Resources/Translations/sources.json) | GPL-3.0-only with Qt-GPL-exception-1.0 as supplied upstream; both license texts and editable `.ts` sources retained. |
| App-local Microsoft runtime DLLs | Copied from the verified Slicer distribution by the build script | Microsoft redistribution terms, not the project's BSD license. Ask SignPath to confirm treatment under its system-library exception. |
| Inno Setup 7.1.0 | [Pinned compiler download and hash](../packaging/windows/inputs.json); [upstream source and license](https://github.com/jrsoftware/issrc) | Build tool with its own license; its installer/uninstaller engine is included in our generated installer. |

Inno Setup compiler downloads, test-only Mesa binaries, build caches, real DICOM images and external standard-reference collections are not installed as application payloads. Mesa is injected only into the isolated Windows GUI test installation and removed during test cleanup.

## Pretrained anatomical model: terms to confirm

The application uses **ChestX-Det PSPNet**, not the separate CXAS/ChestXRayAnatomy model. The latter's noncommercial license statement must not be assigned to this model just because they share a library.

- Artifact: `pspnet_chestxray_best_model_4.pth`.
- Download: [TorchXRayVision release asset](https://github.com/mlmed/torchxrayvision/releases/download/v1/pspnet_chestxray_best_model_4.pth).
- SHA-256: `019b167eac6b729fc1bb92bbbc185fc1730aaa65819f4e3fe718186cadc044fc`.
- Project binding: [model_config.py](../PneumoconiosisAnnotator/PneumoconiosisLib/model_config.py).
- Upstream description: [ChestX-Det Segmentation](https://mlmed.org/torchxrayvision/models.html#chestx-det-segmentation), [loader source](https://github.com/mlmed/torchxrayvision/blob/master/torchxrayvision/baseline_models/chestx_det/__init__.py), and [referenced ChestX-Det dataset](https://github.com/Deepwise-AILab/ChestX-Det-Dataset).

The reviewed model-specific documentation identifies the download and model but does not explicitly state the redistribution license for this exact weight file. The TorchXRayVision library and the referenced dataset repository publish Apache-2.0 licenses; those facts alone are not an explicit grant covering this separately distributed model artifact. Record the weight license as **not established / NOASSERTION** pending upstream clarification. The application uses the model locally for editable lung-mask initialization, not for diagnosis.

Before representing the whole installer as eligible for SignPath's open-source program, obtain clarification of the exact weights' redistribution terms and ask SignPath whether the complete bundle, including Slicer's custom terms and system runtime DLLs, meets its conditions. Do not silently remove, replace or relicense components to assert eligibility.

## Signing boundaries and evidence

Only our own launcher and generated installer/uninstaller are proposed for the project's signing policy. Preserve third-party binaries and any upstream signatures; inclusion in our installer is not permission to sign another project's DLL as our own.

An empty package metadata `License` field does not mean there is no license. Consult its retained `LICENSE`, `COPYING`, `NOTICE` files and license classifiers. Future manifests include those file locations and classifiers, using `NOASSERTION` when the metadata lacks a license expression. This improves the inventory without inventing a license determination.

Signing eligibility, release approval, antivirus analysis and physical-device acceptance are separate checks. See the [Code signing policy](../CODE_SIGNING.md), [Privacy policy](../PRIVACY.md), and [SignPath Foundation conditions](https://signpath.org/terms.html).

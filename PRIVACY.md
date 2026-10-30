# Privacy policy

Effective date: 2026-10-30. This policy describes the PneumoconiosisAnnotator research annotation workflow in this repository. It does not replace an institution's rules for handling medical images.

## Research data stays on the user's computer

The annotator reads user-selected local DICOM chest radiographs and optional reference images. Reading, lung-mask initialization, annotation, scoring, recording and export run locally. The project has no remote inference endpoint, account service, advertising, usage analytics, or automatic research-data upload. Background model and audio processes exchange data with the interface through local files and process pipes.

Images, patient metadata, annotations, reader identifiers and voice recordings are not sent to the maintainer, SignPath or another hosted service by this annotation workflow. Using a cloud-synchronized project/export directory, manually sharing files, or operating other Slicer modules can transfer data outside the computer under the user's control.

## What is stored locally

| Information | Location and purpose |
| --- | --- |
| Original images and reference images | Read from the folders selected by the user; source files are not modified by the annotator. |
| Annotation project | User-selected project folder, including SQLite records, DICOM-derived metadata, reader IDs, history, audio, masks and temporary processing files. |
| Preferences and diagnostics | Windows: `%LOCALAPPDATA%/PneumoconiosisAnnotator/`; source launch: `local-data/` in the checkout. Settings and diagnostic files can include image/project paths, software versions and exception details. |
| Model weights | Bundled in the Windows application; source preparation stores verified weights in `local-data/models/`. |
| Exports | Explicitly selected local destination. They are not automatically delivered to a researcher or server. |

The module distinguishes reader records but does not provide user authentication, encryption, access control or automatic retention limits. Project directories and operating-system permissions determine access. Data remains until the user deletes it. Uninstalling the Windows application preserves external annotation projects and source images; application preferences and logs may also remain. Close the application before backing up or deleting an entire project.

## Recordings and exports can identify people

Microphone capture starts when the user requests a recording. Recordings remain as local audio; they are not automatically transcribed or de-identified.

The research delivery export omits original DICOM images, reference images, source absolute paths, the patient-name field and original DICOM UIDs. It retains image filenames, relative directories, image hashes, reader IDs, annotations and selected attachments; these can still identify or link individuals. Recordings can contain spoken identifiers.

The separate LabelMe export produces image PNGs and annotation JSON. PNGs retain image pixels and any burned-in text. Neither export is an anonymization service. Review the actual files under the institution's sharing rules before sending them elsewhere.

## Network access and third-party services

The Windows installer bundles the model and runtime dependencies; the annotation workflow can operate offline. Source users explicitly running preparation scripts download dependencies, matching ExtraMarkups files and verified model weights from PyPI, PyTorch download servers, Slicer/Kitware and GitHub. These services receive ordinary connection information such as the requester IP address and requested asset. The preparation scripts do not upload local patient data.

Slicer is an included, unmodified third-party platform. It has extension/download facilities and [DICOM network-transfer modules](https://slicer.readthedocs.io/en/latest/user_guide/modules/dicom.html). Those facilities are not used to send images by the annotator workflow. This policy does not assert that the entire Slicer process never attempts a network connection, or cover additional modules/extensions that a user operates. The installed-package offline tests establish that the tested workflow works with outbound traffic blocked; they are not a complete network-traffic audit.

Related service information: [GitHub privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement), [PyPI privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/), [Slicer documentation](https://slicer.readthedocs.io/), and [third-party component provenance](docs/third-party.md).

## Public project and signing services

Public repository examples and automated tests use synthetic chest images and synthetic DICOM metadata. Patient images, reference-image collections and real annotation projects are not intended for inclusion in the public repository, release assets or a signing-service submission.

GitHub hosts source code and software releases. If SignPath access is approved, the signing service will receive software build artifacts and their build provenance, not patient datasets or the user's annotation project.

For privacy questions, use [GitHub Issues](https://github.com/deemoe404/slicer-pneumoconiosis/issues) without including patient data. Policies and implementations are reviewed when the software's data handling changes.

---
name: robodojo-spatial-localize
description: Locate object points from calibrated RGB using a known tabletop/support plane (one or more views), multi-view correspondences, or explicit surface geometry.
---

# Locate a physical semantic point

## Normal task workflow: use the packaged observation

Call `observe`, then read `observation.spatial_localization.case_path`. The host has already paired the captured RGB with render-projection intrinsics and the actual optical camera pose, converted to `environment_origin` metres. Wrist movement is handled on the host. Do not reconstruct calibration from URDF, search simulator files, copy old matrices or install dependencies during a task.

Inspect the attached original images and choose the same physical point in each view. Pixel coordinates are **u,v = column,row** on the original image grid. Use the camera names from `spatial_localization.camera_names`. For an unknown-height point visible in two views:

```bash
python3 .agents/skills/robodojo-spatial-localize/scripts/run_localize.py \
  --case /observations/evaluation-0001/00000_spatial_case.json \
  --object red_block --point top_center --description "visible center of the red block top face" \
  --observation cam_head,320,240 --observation cam_left_wrist,280,220
```

Replace the case path and pixel annotations with the current observation. This one command validates image/calibration checksums, runs geometry, prints concise results and merges accepted points into `space.json`. For a known support-plane point, use one or more views and explicitly add `--plane-z Z --height H`; `H=0` means the annotated point really touches that plane. Do not project an elevated face onto the table. For advanced geometry or multiple targets, supply `--targets annotations.json` instead of the object/pixel flags; see [inputs.md](inputs.md).

If geometry rejects a target, inspect the short reason and obtain better overlapping views or correct the physical-point annotation. Do not read helper implementation or debug camera calibration in model context. If `spatial_localization` is absent, treat calibration as unavailable and report the missing host capability once; do not invent metric coordinates or repeatedly search the workspace. The deployment must provide calibrated observations before this workflow is used.

Choose the object instance and physical point before computing coordinates. Preserve this definition through matching and geometric construction. Reference crops can help select the instance; precise measurement uses the original current images. Keep results as object points; derive TCP approach poses separately.

## Core workflow: use a known support plane when applicable

First decide whether the requested physical point is on the tabletop, at a known height above it, or at an unknown height. For an on-plane point or known-height point, use `kind: support_plane` as the primary method: intersect its calibrated RGB pixel ray with the declared plane. One view suffices geometrically; overlapping additional views provide a consistency check. Use multi-view geometry below when the point's height is unknown.

Declare the plane in `case.support_planes`, including its named frame, unit upward normal, offset, height uncertainty and source. Each target explicitly supplies `support_plane` and `height_above_plane_m` (`0` for a true tabletop point). Read [the plane input and example](inputs.md#known-tabletop-or-support-plane-core-mode). If height is approximate, supply `target_height_sigma_m`; it contributes to the reported uncertainty.

The current RoboDojo **default** scene has tabletop `z=0.765 m` relative to the environment origin (center `0.74` plus half-thickness `0.025`). Confirm the loaded scene/layout before declaring that prior. Conveyor tasks and overridden table layouts do not inherit it. A World/environment plane requires the matching `T_base_plane_frame` to transform it into `case.frame`; never copy `0.765` directly into an arbitrary robot Base frame. Camera and plane coordinates must use the same environment origin.

"The object rests on the table" establishes its support, not the height of its visible top, center, handle or opening. Annotate a visible contact point, or supply the independently known target height; otherwise use multi-view localization. Never silently project an elevated surface point onto the table or describe the result as an object center. Plane-height and target-height errors shift XY as well as Z under oblique views.

Inspect `estimate.accepted`, per-view intersections, ray/plane angles, reprojection errors and conditional position uncertainty. Near-parallel rays, behind-camera intersections, disagreement and excessive estimated uncertainty are rejected. Single-view reprojection is a geometric identity, not independent evidence that the selected point lies on the plane. Calibration/plane-tilt and semantic errors are not fully modeled.

## Other geometry modes

For unknown-height points, use two-view `direct` correspondences or recover two endpoints for a `midpoint`. Circular faces, symmetric curved strips and visible support-axis/surface intersections have dedicated modes documented in [inputs.md](inputs.md). Choose the same physical point across views; an image ellipse center or hidden asset anchor is not automatically the requested 3D point. Acquire better real mounted-camera views if identity or construction remains ambiguous.

Run:

```bash
python3 scripts/localize_rgb.py --case /path/case.json --targets /path/targets.json --output /path/new-output
```

Inspect `points3d.json`, acceptance reasons, overlays, priors, and raw support evidence. Reprojection and boundary agreement are conditional checks, not proof of semantic identity. If the views lack a common visible surface or the construction is ambiguous, ask the host for another real mounted-camera observation with overlap, useful parallax and adequate resolution; keep the target unchanged.

The runtime consumes RGB, calibration and explicitly declared support-plane priors only. Environment depth, object poses, native anchors, CAD/mesh, simulator segmentation IDs and evaluation truth are not localization inputs. Camera-relative Z computed by triangulation is an output of geometry. TCP/EEF conversion belongs to the host plugin.

## Task spatial memory

`/workspace/space.json` is the shared, writable object-position memory for this task runtime. Read it before reusing a location; update it after accepted localization. All stored points use `environment_origin` in metres. Use stable object IDs and distinguish contact points, centers, handles and openings. A stale point is historical evidence, not a current motion target.

Import accepted geometry results (set `case.frame` to `environment_origin` and provide matching calibration):

```bash
python3 .agents/skills/robodojo-spatial-localize/scripts/space_memory.py import --memory space.json --result localization/points3d.json --step 42
python3 .agents/skills/robodojo-spatial-localize/scripts/space_memory.py stale --memory space.json --object red_block
```

The helper merges named points, records the evidence file and native control step, and marks rejected re-estimates stale without promoting them. Mark affected objects stale after manipulation, slippage or uncertain execution; re-observe and localize to refresh them. Do not transform coordinates by renaming their frame. You may also read, write or edit this JSON directly, preserving its task/frame metadata and concise object/point structure. Keep only current estimates, uncertainty and evidence references here; raw images, overlays and histories stay in separate files. Reuse the file after same-episode recovery; never import memory from another task or reset scene.

Use `run_localize.py` for normal tasks; the lower-level CLI is for host diagnostics. The RGB geometry helpers are bundled locally. The model-tool runtime installs the pinned NumPy, SciPy and OpenCV dependencies. This package exposes the RGB CLI above; legacy wrappers tied to the source machine are not installed.

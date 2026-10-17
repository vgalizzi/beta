# Beta: bouldering video analysis

"Beta" is climbing slang for the sequence of moves that solves a problem.

You film a bouldering attempt with a phone on a tripod. Beta finds the holds on the wall, tracks the climber's pose frame by frame, works out which limb is on which hold and when, and returns an annotated video with numbers that describe the climb.

> **Status:** early development. The architecture and the evaluation plan are settled, and implementation starts with the infrastructure (phase 0 of the [roadmap](#roadmap)). Nothing in this README is a result yet.

## Why I'm building it

I climb, and I want to see what actually happens during an attempt: where I lose time, which holds cost me the most, whether my successful attempts look different from the failed ones. I can produce the data myself, and the output is visual.

It is also an end-to-end ML engineering project, so the design favors engineering quality over features. A narrow scope that is typed, tested and deployed is worth more than a sprawling prototype.

## What it does

The target workflow at the end of phase 2. Similarity search and written feedback come with phase 3.

1. I take a photo of a wall section. Beta detects the holds, classifies each one into the gym's color scale and proposes groupings. I confirm or correct them by selecting holds. Color alone cannot isolate a problem (see [Hold detection](#hold-detection)), so this is a proposal, not a detection.
2. I film an attempt with the phone on a tripod, upload the video and link it to a problem.
3. Processing runs in the background: hold detection, pose estimation, limb-to-hold association, metrics.
4. I get back an annotated video (skeleton, holds in use highlighted), a timeline of moves and a metrics sheet.
5. I compare the attempt with my other attempts on the same problem, or with attempts that have a similar movement profile (phase 3).
6. An agent writes feedback in plain language from the metrics and a corpus of training material (phase 3).

## Scope

In scope for the first version:

- Bouldering only, no roped climbing.
- One fixed camera: a phone on a tripod. Handheld footage is out of scope. Without a stable viewpoint, a center-of-mass trajectory means nothing, and the wall would need one homography per frame instead of one per attempt.
- No constraint on how the video starts. There is no need to film the empty wall first, because the background image is reconstructed from the video itself (see [Hold detection](#hold-detection)).
- One climber in frame at a time.
- Indoor walls, gym lighting.
- Asynchronous processing. Nothing runs in real time.

Out of scope:

- Multi-view capture. It is the natural next step and a direct continuation of my published work on uncalibrated multi-view 3D human pose estimation, but it multiplies the acquisition complexity. Left for later.
- Real-time processing. There is no functional need for it, and the engineering cost is high.
- Outdoor climbing. Holds are not standardized and the background is natural rock, so the hold detector would not transfer.
- A native mobile app. The interface is a web page, possibly responsive.
- Automatic grading. At the gym where I climb, the difficulty is carried by the color of the problem, which is already known. A model that predicts the color from the structure of a problem stays in the backlog: it needs far more data than I will have for a long time.
- Comparisons across gyms. Each gym chain has its own color scale, and conversions to Fontainebleau grades are unofficial. One gym at a time.

## Design decisions

These decisions are fixed from the start. Each one gets a short decision record in [`docs/decisions/`](docs/decisions/) when it is implemented.

| Decision | Reason |
|---|---|
| Multi-user schema from phase 0 | Going from single-user to multi-user later is a painful migration that adds nothing. Foreign keys cost nothing now. |
| Direct upload to object storage through presigned URLs | The video never goes through the API, which stays light and easy to scale. This is the usual production pattern. |
| S3-compatible storage locally, S3 in production, same `boto3` code | No AWS account is needed during development, and switching only changes the `endpoint_url`. The local server is still to be chosen: MinIO's community edition was archived in April 2026, and SeaweedFS (Apache 2.0) is the main candidate. |
| Keypoints stored as Parquet on object storage, not in the database | A 30 s video at 30 fps with 17 keypoints gives tens of thousands of rows per attempt. The database keeps pointers and aggregates. |
| A `processing_job` table from phase 0, with model versions | Every result can be traced to the models that produced it. |
| Color scale stored per gym, with the color as the grade | At Arkose, the color of a problem is its grade, and every gym chain has its own scale. A small `gym_color_scale` table avoids hard-coding one chain's convention, and the grade becomes a free, reliable label instead of manual input. |
| A problem is a machine proposal confirmed by a person | Several problems of the same color share a wall section, and no automatic signal separates them reliably. Designing for a human in the loop from the start is cheaper than chasing an automation that does not exist. |
| Holds detected on a background image rebuilt from the video, not on a separate photo | A temporal median over the frames removes the climber, who moves, and keeps the wall, which does not. The holds are then in the video's own image coordinates, so the limb-to-hold association needs no registration, and videos shot before the system existed remain usable. |
| Homography stored per attempt, canonical wall map stored per wall | The wall map holds the problem definitions and makes attempts comparable. The homography links its coordinates to each video. One matrix per attempt is enough while the camera is fixed. The column exists from phase 0 and is filled in phase 2. |

## Architecture

```
                    ┌──────────────┐
   browser ─────────►   FastAPI    │◄──── Prometheus /metrics
        │           │  (Pydantic)  │
        │           └──┬────────┬──┘
        │              │        │
        │         ┌────▼───┐  ┌─▼──────┐
        │         │Postgres│  │ Valkey │ task queue + cache
        │         └────────┘  └─┬──────┘
        │                       │
        │                  ┌────▼─────────┐     ┌──────────┐
        └─ presigned ─────►│   Worker     │◄────┤  MLflow  │ model registry
           upload          │  (Celery)    │     └──────────┘
                │          │  holds       │
         ┌──────▼──────┐   │  pose        │     ┌──────────┐
         │ S3 storage  │◄──┤  metrics     ├────►│  Qdrant  │ similarity
         │ videos      │   └──────┬───────┘     └──────────┘
         │ parquet     │          │
         │ overlays    │     ┌────▼─────────┐   ┌──────────┐
         └─────────────┘     │ Coach agent  ├──►│ Langfuse │ traces
                             │  (LangGraph) │   └──────────┘
                             └──────────────┘
```

The API answers in milliseconds: it validates the request, writes to the database and queues a task. All the heavy computation happens in the worker, which can be scaled or moved to a GPU machine on its own. This split is the most important architectural decision in the project, and it is in place from phase 0, before there is anything to compute.

## Stack

| Component | Role | Reason |
|---|---|---|
| Python 3.12 with strict typing | | `mypy --strict` on all application code. |
| FastAPI and Pydantic v2 | HTTP API | Validation at the system boundary, versioned schemas, OpenAPI for free. |
| PostgreSQL, SQLAlchemy 2.0 (async), Alembic | Persistence | The data is relational: attempts, problems, holds and moves are tightly linked. Every schema change goes through an Alembic migration, never `create_all()`. |
| Valkey | Task queue and cache | Celery broker, and cache for computed wall maps. Valkey is the BSD-licensed fork of Redis maintained under the Linux Foundation and speaks the same protocol. Redis moved to a tri-license that includes the AGPL with version 8.0. |
| Celery | Background worker | Long, blocking tasks with retries and state tracking. `arq` was considered: it suits async code better, but this work is CPU and GPU bound, so async brings nothing. |
| S3-compatible storage, then S3 | Object storage | Videos, Parquet keypoints, overlays, wall images. |
| Docker and Docker Compose | Environment | `docker compose up` works on a clean machine on the first try. This is an acceptance criterion. |
| pytest and testcontainers | Tests | Integration tests run against a real Postgres and a real object store. The database is never mocked. |
| GitHub Actions | CI/CD | Lint, type checks and tests on every pull request. Image build and push in phase 4. |
| MLflow | Tracking and model registry | Tracks the hold detector's training runs. The worker loads models from the registry by alias, never from a file path. |
| Qdrant | Similarity search | Finds similar attempts and problems from sequence embeddings. See the caveat under [Risks](#risks). |
| LangGraph | Coach agent | An explicit graph: read the metrics, retrieve context, compare with successful attempts, write the feedback. |
| Langfuse | LLM observability | Traces, cost, latency, and evaluation of feedback quality. |
| Prometheus and Grafana | System observability | API latency, processing time, failure rate, drift in input distributions. |
| AWS | Deployment | S3, ECR, ECS Fargate for the API, EC2 for the worker if it needs a GPU. Deployed by hand and documented first, then scripted in CI. |

Terraform is deliberately left out for now. I have not used it before, and I want to deploy by hand first: it is easier to see what an infrastructure-as-code tool automates after doing the work without one. It may come later, outside the current plan.

## Data model

```
user ──┬──< attempt >──── route ────< route_hold >──── hold ──── wall ──── gym
       │        │
       │        ├──< move >──── hold
       │        ├──── attempt_metrics
       │        ├──── attempt_embedding   (Qdrant pointer)
       │        └──< processing_job
```

| Table | Contents | Notes |
|---|---|---|
| `user` | id, email, display name | Minimal authentication in phase 0, real authentication in phase 3 |
| `gym`, `wall` | gym, wall section, angle, reference photo | |
| `gym_color_scale` | the gym's scale: color, difficulty rank, reference color in Lab space, optional Fontainebleau equivalent | The color carries the grade and the scale differs between gym chains, so it lives in the database. It also gives the color classifier its anchors. |
| `hold` | detected hold: bounding box, mask, color, detector model, confidence | Attached to the wall, not to a problem |
| `route` | problem: name, color (a key into `gym_color_scale`, and therefore the grade), set and removal dates | There is no separate grade field. The color is the grade, read from the holds rather than typed in. |
| `route_hold` | link between a problem and a hold, with a role (start, zone, top, off) | |
| `attempt` | attempt: video, date, outcome (flash, sent, failed), notes, wall-to-video homography (3×3), key of the background image | Columns created in phase 0, filled in phase 2 |
| `move` | move: index, limb, target hold, start time, end time | Small and easy to query. This is the core of the analysis. |
| `attempt_metrics` | duration, number of moves, path length, smoothness, ... | One row per attempt |
| `processing_job` | status, current stage, model versions (jsonb), timestamps, error | Makes every result traceable |

Raw keypoints are stored as Parquet on object storage, and their path is recorded in `processing_job`. The reconstructed background image is stored there too. It could be derived from the video again, but storing it costs less than recomputing it, and it lets a new detector run without touching the video.

## Processing pipeline

### Hold detection

Holds are never detected on every frame: the wall is static, so one image is enough. Two different images serve two different purposes.

The canonical wall map belongs to the `wall`. It is built once from a photo of the wall section, holds the problem definitions (`route_hold`) and makes attempts comparable. It has its own lifetime: when the wall is reset, its problems are no longer valid.

The attempt's background image is reconstructed from the video itself with a temporal median. Over N frames spread across the attempt, the per-pixel median removes the climber, who moves, and keeps the wall, which does not. When the climber stays on the same pixels for more than half the video (typically at the start position), the median is computed over the pixels outside the climber's detected box.

Holds are therefore detected in the video's own image coordinates, and the limb-to-hold association works in image space without any registration. The climber has nothing special to do, and older videos remain usable. A hold hidden by the body for the whole attempt is still located, since it was detected on the background.

A homography links the two coordinate systems. It is estimated with RANSAC on matched hold centers (a wall section is a plane, and a set of holds gives plenty of points) and stored on the `attempt`.

- The detector is RF-DETR with instance segmentation, restricted to its Apache 2.0 variants: the XL and 2XL variants in the same repository use the PML 1.0 license. Ultralytics models are licensed under the AGPL-3.0, which would extend to the whole codebase, so they are not used. Fallbacks are RTMDet (MIT) and `torchvision` Mask R-CNN (BSD).
- Public data comes first. Roboflow Universe hosts more than 10,000 annotated hold images, about 4,300 of them under CC BY 4.0. The detector is pre-trained on those, then fine-tuned on 50 to 100 images of my own walls.
- Pre-annotation works with the model in the loop: the detector trained on public data proposes, I correct, and the model is retrained. Each round is an MLflow run. SAM 3 would be faster, but it ships under a custom Meta license rather than Apache, so any use of it will be recorded in a decision record.
- Color is a classification problem, not a clustering one. At Arkose, the gym where I climb, the color of a problem is its grade. In order of difficulty: yellow, green, blue, red, black and purple, sometimes with white below yellow. Every gym chain has its own scale, so the scale is stored in the database. Each hold is classified into a known palette of six or seven reference colors, by distance in Lab space to the gym's anchors, with a reject class. This is better posed than clustering, it can be evaluated with a confusion matrix, and it is easier to debug.
- Color does not identify a problem, though. At Arkose, several problems of the same color share a wall section, so a color only gives a difficulty band. Proposing a problem needs a second signal: spatial grouping (DBSCAN on hold positions within one color class), which works when problems are spread apart and fails when they interleave. The machine proposes and a person confirms. That is the normal path, not a fallback.
- Volumes and large structural holds can often be used on any problem whatever their color, so they need a class outside the palette, or the grouping will miss them every time. How Arkose marks start holds still has to be checked on site. If the marking can be detected, it is the signal that separates two problems of the same color.
- Segmentation is preferred over bounding boxes: inside a box, the wall behind the hold pollutes the color histogram.

### Pose estimation

This is the most interesting technical problem in the project, and the one most likely to go wrong. Climbing is a hard case for pose estimation: the climber faces away from the camera, limbs are hidden by the body or the wall, and the postures are far from the training distributions (COCO is full of people standing upright).

- Pose is estimated top-down with RTMPose or ViTPose (Apache 2.0), on the climber's box tracked from frame to frame with `roboflow/trackers` (ByteTrack, Apache 2.0).
- Models are served by `rtmlib` (Apache 2.0), which runs RTMPose, RTMO, RTMW and ViTPose on ONNX Runtime without `mmcv` or `mmpose`. That means four dependencies instead of a Docker image of several gigabytes, and the ONNX export planned for phase 5 is already half done.
- Temporal smoothing is required, with a Kalman filter or a confidence-weighted moving average.
- One bias has to be compensated rather than smoothed out. Pose models output wrists and ankles, while contact happens at the fingers and toes. The area of interest around each keypoint therefore depends on both the limb and the model, and it is a configuration parameter. RTMW (whole body, hands and feet included) gives real extremity keypoints, which none of the published baselines use.
- Quality is tracked per attempt as the share of keypoints below the confidence threshold. If it explodes on some walls or camera angles, that is a limitation to document, not to hide. The published figure for a vertical wall is 50% average occlusion of the hands, and overhangs will be worse.

### Limb-to-hold association

This is the core of the system, and it is a geometric heuristic rather than a learned model.

1. Holds come from the attempt's background image, so they are already in the video's image coordinates. No registration is needed at this stage.
2. For each frame, compute the distance between each extremity keypoint (wrists, ankles) and each hold mask.
3. Declare a contact when the distance stays below a threshold for N consecutive frames, with hysteresis on release to avoid flickering. The published reference value is 0.5 s, or 12.5 frames at 25 fps.
4. Output the sequence of moves as `(limb, hold, t_start, t_end)`.
5. Separately, the wall-to-video homography maps the detected holds to the wall's `hold` records, which makes the attempt comparable with others.

### Metrics

These metrics are realistic from a single view:

- total duration, number of moves, pace
- contact time per hold, and the holds that cost the most
- order of the moves, and repeated touches of the same hold, a sign of hesitation
- center-of-mass trajectory, estimated as a weighted sum of body segments from anthropometric tables. It will first be compared with a simpler estimate, the mean of four torso keypoints: if the difference is negligible on my attempts, the simple version wins.
- length of the center-of-mass path relative to the height of the problem, as a measure of efficiency
- smoothness, as the normalized jerk of the 2D center of mass
- ratio of static time to moving time

Hip-to-wall distance would be the most telling technique metric, but it cannot be measured reliably from one view. An approximation from changes in apparent scale is possible, and it will be validated or dropped. Beta will not report a measurement it cannot make.

## Evaluation

The limb-to-hold association is the part that needs serious evaluation, and there is public ground truth for it. *The Way Up* (Maschek and Schedl, CVPR 2025 Workshops, CC BY 4.0) provides 940 annotated hold usages over 22 videos, with the limb, start and end frames, occlusions, hold boxes and homography matrices.

The metric is temporal IoU at the 0.0 and 0.5 thresholds, rather than a plain binary accuracy. The paper reports these baselines:

| Pose model | Accuracy at tIoU > 0.0 |
|---|---|
| ViTPose-L | 86.6% |
| MediaPipe Heavy | 83.5% |
| YOLOv8-pose X | 75.3% |

At tIoU > 0.5, all three fall between 62% and 69%. The homography matrices in the dataset also make it possible to measure the error of my own homography estimates instead of assuming they are right.

The dataset covers lead climbing on a vertical wall, not bouldering. Postures are less extreme than on overhangs and occlusions are milder, so a score on it is a floor, not a guarantee.

After that, I will annotate ten of my own attempts by hand. They measure how the method transfers to my walls and my equipment, not whether the method works. The hold detector is evaluated on a test split made by wall, never a random one, since a random split would put crops of the same wall on both sides.

Results will be published here as they come, including the bad ones and the failure cases.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Time spent annotating holds | Medium | Public datasets cut in-house annotation to 50 to 100 images. Model-in-the-loop pre-annotation. Scope limited to one or two gyms. |
| Pose quality under occlusion | High | Temporal smoothing, confidence thresholds, RTMW for extremities, evaluation on *The Way Up* and then on hand-annotated attempts, documented failure cases. Reference figure: hands occluded 50% of the time on a vertical wall. |
| Separating two problems of the same color | Medium | This comes from the gym's color system, not from a model defect. Spatial grouping as a proposal, confirmation in the interface, start-hold markings if they can be detected. Grouping is never presented as detection. |
| Registration between the wall map and the video | Medium | The limb-to-hold association does not depend on it; only comparisons between attempts do. The homography can be checked against the matrices provided with *The Way Up*. |
| Camera not fixed | Medium | Explicit scope constraint, detected automatically. If the homography between the first and last frames drifts beyond a threshold, the attempt is rejected with a clear message instead of producing wrong metrics. |
| Scope creep | High | Closed phases with written acceptance criteria. A phase does not start until the previous one is done. |
| AWS cost | Medium | Infrastructure started on demand rather than kept running, spot instances for the worker, a budget estimated before deploying anything. |
| Licenses of models and libraries | Handled | Apache 2.0 for every imported ML component: RF-DETR for detection, RTMPose and ViTPose through `rtmlib` for pose, `roboflow/trackers` for tracking. No AGPL dependency, and the PML 1.0 variants of RF-DETR are excluded. |
| Qdrant is oversized | Low | Accepted. With a few hundred attempts, a brute-force NumPy search would be enough. Qdrant is a deliberate learning choice, stated here rather than dressed up as a necessity. |
| Personal data in videos of other climbers | Low while the project stays personal | No public release of anyone's footage without explicit consent, and deletion on request. |

## Roadmap

Deliverables and acceptance criteria for each phase are in [`CAHIER-DES-CHARGES.md`](CAHIER-DES-CHARGES.md), in French.

| Phase | Content | Visible deliverable |
|---|---|---|
| 0 | Infrastructure, no ML | `docker compose up` and a complete upload → database → worker chain |
| 1 | Hold detection and MLflow | Automatic wall map from a photo |
| 2 | Pose, moves, metrics | Annotated video and analysis sheet for an attempt |
| 3 | Qdrant, coach agent, Langfuse | Written feedback and search for similar attempts |
| 4 | AWS, CI/CD, monitoring | The application online, with dashboards |
| 5 | Optional: ONNX, multi-view | |

Each phase is a valid stopping point. After phase 2, the project is already complete and usable.

## Installation

Not available yet.

## References

- A. Maschek and D. Schedl, *The Way Up: A Dataset for Hold Usage Detection in Sport Climbing*, CVPR 2025 Workshops. [Paper](https://arxiv.org/abs/2505.12854), [dataset](https://zenodo.org/records/15196867) (CC BY 4.0).
- [RF-DETR](https://github.com/roboflow/rf-detr), real-time detection and segmentation.
- [rtmlib](https://github.com/Tau-J/rtmlib), RTMPose, RTMW and ViTPose on ONNX Runtime.
- [roboflow/trackers](https://github.com/roboflow/trackers), multi-object tracking.
- [Roboflow Universe](https://universe.roboflow.com/), public climbing hold datasets.

## License

See [LICENSE](LICENSE).

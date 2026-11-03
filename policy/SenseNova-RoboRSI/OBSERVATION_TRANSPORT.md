# Observation transport alignment

The native evaluation observation path already transports raw RGB arrays
losslessly. It does not call `encode_image_bit` before RPC:

1. Official `env/observation_manager/obs_manager.py`, `ObsManager.get_obs`,
   takes the renderer's RGB annotator array and retains the first three channels.
2. Official `src/eval_client/eval_env.py`, `get_obs_batch`, copies those arrays;
   `get_obs` delegates to the same batch path for environment 0.
3. This policy's `deploy.py` forwards that observation with
   `model_client.call("update_obs", ...)`.
4. `client_server/ws/model_client.py`, `WsModelClient.call`, forwards the array
   to the protocol client. `client_server/ws/protocol/codec.py` serializes it
   with `msgpack_numpy.encode`, preserving shape, dtype, channel order and bytes.
5. `client_server/ws/model_server.py`, `PolicyServer._handle_call`, invokes
   `decode_obs_images` before the model. That shared helper passes already
   decoded arrays through unchanged.

Model-facing controller images are JPEG files (quality 95, 4:4:4, no resizing)
and tool content uses `image/jpeg`. Physical replay reuses the controller's
head-camera JPEG, without writing any additional PNG checkpoint. Old sealed PNG
records remain supported. Pose and decoded RGB error thresholds stay unchanged;
comparison receipts identify both image files. Older PNG tool images remain
readable. This file encoding does not change the raw RGB RPC above.

The standalone native wrapper uses `native_rgb_episode.py` and
`native_rgb_adapter.py`. The adapter snapshots renderer RGB buffers once,
archives JPEG95/4:4:4 files and sends the exact snapshot bytes over private
JSON-lines IPC. Raw bytes are base64-framed with shape/dtype/SHA256 metadata,
then reconstructed as immutable uint8 arrays by `NativeRgbBackend`. The byte
payload is removed before transitions are logged or exposed to Model. No
PNG, image-codec round trip, resize or channel swap occurs on the live path.
Malformed/checksum-mismatched or stale responses fail closed, including
invalidating the last frame after an uncertain step request.

The simulator's original stepping, reward/score and video-finalization methods
are inherited unchanged. JPEG archive files are plain RGB photos, separate
from serialized XPolicyLab image-bit buffers. `rgb_frames.jsonl` distinguishes
live RGB byte hashes from JPEG file hashes. Moving archives from public cloud
to M is a file-byte copy checked against the latter hashes, with no extra
image encoding. Old frozen PNG-based wrappers are not rewritten.

The shared `encode_image_bit` helper creates marked JPEG buffers when a caller explicitly encodes images,
including `DEBUG_OBS_ENCODED=1` fixtures and dataset storage. JPEG quality 100
does not guarantee pixel equality. That optional encoded path must not be
confused with the native raw observation RPC above; legacy and marked JPEGs
continue to decode only through the shared helper.

`tests/test_observation_transport.py` runs the real synchronous client, protocol
framing and server dispatch against an in-memory transport. It checks exact
pixel equality for nonuniform, strided RGB frames, multiple cameras and batch
observations, and verifies both JPEG formats retain shared decoder semantics.
It opens no sockets and does not run the simulator.
